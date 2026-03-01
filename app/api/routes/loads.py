from datetime import datetime
import json
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from jose import jwt
from app.db.database import SessionLocal
from app.dicts.cargos import CARGO_KINDS
from app.models.models import CargoStatus, City, Load, User
from app.core.security import SECRET_KEY, ALGORITHM
from app.core.config import settings
from typing import Optional
from app.ai.scoring import MarketStats, compute_ai_score
from app.services.geo import canonicalize_city_name, is_city_like_name, is_supported_city, normalize_city_name
from app.services.cargo_status import (
    apply_cargo_status_filter,
    cargo_loading_date,
    expire_outdated_cargos,
    normalize_cargo_status,
)
from app.services.bot_webhooks import send_event_to_bot_sync
from app.services.cross_service_sync import notify_user_on_bot_sync

router = APIRouter()
logger = logging.getLogger(__name__)

_BODY_TYPE_MAP = {
    "тент": "тент",
    "tent": "тент",
    "реф": "реф",
    "рефрижератор": "реф",
    "ref": "реф",
    "площадка": "площадка",
    "platform": "площадка",
    "коники": "коники",
}
_CARGO_KIND_ALIASES = {
    key.lower(): key for key in CARGO_KINDS
}
_CARGO_KIND_ALIASES.update(
    {
        "general": "GENERAL",
        "palletized": "PALLETIZED",
        "food": "FOOD",
        "pharma": "PHARMA",
        "bulk": "BULK",
        "liquid": "LIQUID",
        "gas": "LIQUID",
        "container": "CONTAINER",
        "oversize": "OVERSIZE",
        "cars": "CARS",
        "timber": "TIMBER",
        "equipment": "EQUIPMENT",
        "генеральный": "GENERAL",
        "паллетированный": "PALLETIZED",
        "продукты": "FOOD",
        "фарма": "PHARMA",
        "сыпучий": "BULK",
        "наливной": "LIQUID",
        "контейнер": "CONTAINER",
        "негабарит": "OVERSIZE",
        "авто": "CARS",
        "лес": "TIMBER",
        "оборудование": "EQUIPMENT",
    }
)
_ALLOWED_CONTAINER_SIZES = {"20", "40", "45"}
_TIME_24H_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TIME_24H_SEPARATED_RE = re.compile(r"^(\d{1,2})[.:\s]+([0-5]\d)$")
_TIME_24H_COMPACT_RE = re.compile(r"^(\d{3,4})$")
_TIME_12H_RE = re.compile(r"^(\d{1,2})[.:\s]+([0-5]\d)\s*([AaPp][Mm])$")


def _normalize_time_24h(raw_value: Optional[str]) -> Optional[str]:
    value = str(raw_value or "").strip()
    if not value:
        return None

    full_match = _TIME_24H_RE.match(value)
    if full_match:
        return value

    separated_24h = _TIME_24H_SEPARATED_RE.match(value)
    if separated_24h:
        hour = int(separated_24h.group(1))
        minute = int(separated_24h.group(2))
        if 0 <= hour <= 23:
            return f"{hour:02d}:{minute:02d}"

    compact_24h = _TIME_24H_COMPACT_RE.match(value)
    if compact_24h:
        digits = compact_24h.group(1)
        hour = int(digits[:-2])
        minute = int(digits[-2:])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"

    ampm_match = _TIME_12H_RE.match(value)
    if ampm_match:
        hour = int(ampm_match.group(1))
        minute = int(ampm_match.group(2))
        marker = ampm_match.group(3).lower()
        if not (1 <= hour <= 12):
            raise HTTPException(status_code=422, detail="Некорректное время. Формат: 21:30")
        if marker == "pm" and hour != 12:
            hour += 12
        if marker == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    raise HTTPException(status_code=422, detail="Некорректное время. Формат: 21:30")


def _resolve_city_input(
    db: Session,
    *,
    field_name: str,
    city_id: Optional[int],
    city_name: str,
) -> tuple[Optional[int], str, str]:
    raw_city_name = (city_name or "").strip()
    parsed_city_id = int(city_id) if city_id is not None else None
    if parsed_city_id is not None and parsed_city_id > 0:
        city = db.query(City).filter(City.id == parsed_city_id).first()
        if not is_supported_city(city):
            raise HTTPException(status_code=422, detail=f"{field_name}_id не найден")
        canonical_name = city.name
        city_text = raw_city_name or city.name
        return int(city.id), canonical_name, city_text

    if not raw_city_name:
        raise HTTPException(status_code=422, detail=f"Укажите {field_name}")
    if not is_city_like_name(raw_city_name):
        raise HTTPException(status_code=422, detail=f"Некорректное значение {field_name}")

    normalized_name = normalize_city_name(raw_city_name)
    city = (
        db.query(City)
        .filter(City.name_norm == normalized_name)
        .order_by(City.population.desc().nullslast(), City.name.asc())
        .first()
    )
    if not is_supported_city(city):
        raise HTTPException(status_code=422, detail=f"Выберите {field_name} из списка")
    return int(city.id), city.name, raw_city_name


def _is_public_load_city(value: Optional[str]) -> bool:
    return is_city_like_name(value)


def _parse_optional_array(raw_value: Optional[str], *, field_name: str) -> list[str]:
    value = str(raw_value or "").strip()
    if not value:
        return []

    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except Exception:
            raise HTTPException(status_code=422, detail=f"Некорректный формат {field_name}")
        if not isinstance(parsed, list):
            raise HTTPException(status_code=422, detail=f"{field_name} должен быть списком")
        return [str(item).strip() for item in parsed if str(item).strip()]

    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_cargo_kind(raw_value: Optional[str]) -> Optional[str]:
    value = str(raw_value or "").strip()
    if not value:
        return None

    upper_value = value.upper()
    if upper_value in CARGO_KINDS:
        return upper_value

    alias_key = value.lower()
    if alias_key in _CARGO_KIND_ALIASES:
        return _CARGO_KIND_ALIASES[alias_key]

    allowed = ", ".join(sorted(CARGO_KINDS.keys()))
    raise HTTPException(status_code=422, detail=f"Некорректный cargo_kind. Допустимо: {allowed}")


def _normalize_container_size(raw_value: Optional[str]) -> Optional[str]:
    value = str(raw_value or "").strip()
    if not value:
        return None

    normalized = value.lower().replace("ft", "").replace("'", "").replace(" ", "")
    if normalized in _ALLOWED_CONTAINER_SIZES:
        return normalized

    raise HTTPException(status_code=422, detail="container_size должен быть 20, 40 или 45")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_from_token(authorization: Optional[str] = Header(None)):
    """Получить user_id из токена в заголовке Authorization"""
    if not authorization:
        return None
    try:
        # Bearer token
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["id"]
    except:
        return None


@router.post("/create")
def create_load(
    from_city: str,
    to_city: str,
    price: Optional[float] = None,
    total_price: Optional[float] = None,
    distance_km: Optional[float] = None,
    rate_per_km: Optional[float] = None,
    loading_time: Optional[str] = None,
    from_city_id: Optional[int] = None,
    to_city_id: Optional[int] = None,
    weight: Optional[float] = 0,
    volume: Optional[float] = 0,
    weight_t: Optional[float] = None,
    volume_m3: Optional[float] = None,
    loading_date: Optional[str] = None,
    truck_type: Optional[str] = None,
    required_body_type: Optional[str] = None,
    cargo_kind: Optional[str] = None,
    pickup_lat: Optional[float] = None,
    pickup_lon: Optional[float] = None,
    delivery_lat: Optional[float] = None,
    delivery_lon: Optional[float] = None,
    adr_class: Optional[str] = None,
    adr_classes: Optional[str] = None,
    required_vehicle_kinds: Optional[str] = None,
    required_options: Optional[str] = None,
    crew_required: Optional[bool] = False,
    container_size: Optional[str] = None,
    needs_crane: Optional[bool] = False,
    needs_dump: Optional[bool] = False,
    temp_required: Optional[bool] = False,
    temp_min: Optional[float] = None,
    temp_max: Optional[float] = None,
    loading_type: Optional[str] = None,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Создать новую заявку на груз"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    effective_total_price = float(total_price) if total_price is not None else float(price or 0)
    if effective_total_price <= 0:
        raise HTTPException(status_code=422, detail="Укажите корректную ставку (total_price)")

    effective_distance_km = float(distance_km) if distance_km is not None else None
    if effective_distance_km is not None and effective_distance_km <= 0:
        effective_distance_km = None

    if rate_per_km is not None:
        effective_rate_per_km = float(rate_per_km)
    elif effective_distance_km and effective_total_price > 0:
        effective_rate_per_km = round(effective_total_price / effective_distance_km, 1)
    else:
        effective_rate_per_km = None

    parsed_loading_date = None
    if loading_date:
        try:
            normalized_loading_date = str(loading_date).strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", normalized_loading_date):
                raise ValueError("invalid date format")
            parsed_loading_date = datetime.strptime(normalized_loading_date, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=422, detail="Некорректная дата. Формат: YYYY-MM-DD")

    parsed_loading_time = _normalize_time_24h(loading_time)
    parsed_required_vehicle_kinds = _parse_optional_array(required_vehicle_kinds, field_name="required_vehicle_kinds")
    parsed_required_options = _parse_optional_array(required_options, field_name="required_options")
    parsed_adr_classes = _parse_optional_array(adr_classes, field_name="adr_classes")
    parsed_cargo_kind = _normalize_cargo_kind(cargo_kind)
    parsed_container_size = _normalize_container_size(container_size)
    if not parsed_adr_classes and adr_class:
        parsed_adr_classes = [str(adr_class).strip()]
    parsed_adr_class = str(adr_class).strip() if adr_class else (parsed_adr_classes[0] if parsed_adr_classes else None)

    if temp_min is not None and temp_max is not None and float(temp_min) > float(temp_max):
        raise HTTPException(status_code=422, detail="temp_min не может быть больше temp_max")

    canonical_body_type = None
    raw_body = (required_body_type or truck_type or "").strip().lower()
    if raw_body:
        canonical_body_type = _BODY_TYPE_MAP.get(raw_body, raw_body)

    resolved_from_city_id, resolved_from_city, from_city_text = _resolve_city_input(
        db,
        field_name="from_city",
        city_id=from_city_id,
        city_name=from_city,
    )
    resolved_to_city_id, resolved_to_city, to_city_text = _resolve_city_input(
        db,
        field_name="to_city",
        city_id=to_city_id,
        city_name=to_city,
    )
    if normalize_city_name(resolved_from_city) == normalize_city_name(resolved_to_city):
        raise HTTPException(status_code=422, detail="Города отправления и назначения не могут совпадать")

    new_load = Load(
        user_id=user_id,
        from_city_id=resolved_from_city_id,
        to_city_id=resolved_to_city_id,
        from_city=resolved_from_city,
        to_city=resolved_to_city,
        from_city_text=from_city_text,
        to_city_text=to_city_text,
        weight=weight or 0,
        volume=volume or 0,
        weight_t=weight_t if weight_t is not None else weight,
        volume_m3=volume_m3 if volume_m3 is not None else volume,
        pickup_lat=pickup_lat,
        pickup_lon=pickup_lon,
        delivery_lat=delivery_lat,
        delivery_lon=delivery_lon,
        required_body_type=canonical_body_type,
        cargo_kind=parsed_cargo_kind,
        required_vehicle_kinds=parsed_required_vehicle_kinds or None,
        required_options=parsed_required_options or None,
        adr_class=(parsed_adr_class or None),
        adr_classes=parsed_adr_classes or None,
        crew_required=bool(crew_required) if crew_required is not None else False,
        container_size=parsed_container_size,
        needs_crane=bool(needs_crane) if needs_crane is not None else False,
        needs_dump=bool(needs_dump) if needs_dump is not None else False,
        temp_required=bool(temp_required) if temp_required is not None else bool(temp_min is not None or temp_max is not None),
        temp_min=float(temp_min) if temp_min is not None else None,
        temp_max=float(temp_max) if temp_max is not None else None,
        loading_type=(loading_type or None),
        price=effective_total_price,
        total_price=effective_total_price,
        distance_km=effective_distance_km,
        rate_per_km=effective_rate_per_km,
        loading_date=parsed_loading_date,
        loading_time=parsed_loading_time,
        status=CargoStatus.active.value,
    )

    db.add(new_load)
    db.commit()
    db.refresh(new_load)

    # Cross-notification: новый груз с веба -> push в tg-bot.
    try:
        send_event_to_bot_sync(
            "cargo.created",
            {
                "id": int(new_load.id),
                "load_id": int(new_load.id),
                "user_id": int(user_id),
                "from_city": new_load.from_city,
                "to_city": new_load.to_city,
                "price": float(effective_total_price),
                "distance_km": float(effective_distance_km) if effective_distance_km else None,
                "rate_per_km": float(effective_rate_per_km) if effective_rate_per_km else None,
                "loading_date": parsed_loading_date.isoformat() if parsed_loading_date else None,
                "action_link": f"{(settings.PUBLIC_BASE_URL or '').rstrip('/')}/?cargo_id={new_load.id}",
                "action_text": "Открыть на сайте",
            },
        )
    except Exception as exc:
        logger.warning("Failed to dispatch cargo.created to tg-bot: %s", str(exc)[:200])

    try:
        current_user = db.query(User).filter(User.id == user_id).first()
        if current_user and current_user.telegram_id:
            notify_user_on_bot_sync(
                user_id=int(current_user.telegram_id),
                message=f"📦 Груз #{new_load.id} опубликован на сайте.",
                action_link=f"{(settings.PUBLIC_BASE_URL or '').rstrip('/')}/dashboard?cargo_id={new_load.id}",
                action_text="Открыть кабинет",
            )
    except Exception as exc:
        logger.warning("Failed to notify creator in tg-bot: %s", str(exc)[:200])

    # Начисление баллов за создание заявки
    try:
        from app.services.rating_system import rating_system
        rating_system.on_load_created(db, user_id, new_load.id)
    except Exception as e:
        # Не критично, если не удалось начислить баллы
        pass

    return {"msg": "load created", "load_id": new_load.id}


@router.get("/list")
def list_loads(
    status: Optional[str] = Query("active", description="active|expired|all"),
    db: Session = Depends(get_db),
):
    """Список всех открытых заявок с информацией о пользователях."""
    expire_outdated_cargos(db)
    if status and str(status).strip().lower() not in {"active", "expired", "all", "closed", "cancelled", "open", "covered"}:
        raise HTTPException(status_code=422, detail="status должен быть active|expired|all")

    query = apply_cargo_status_filter(db.query(Load), normalize_cargo_status(status), default=CargoStatus.active.value)
    loads = query.all()
    stats = MarketStats.from_db(db, lookback_days=60)
    
    result = []
    for load in loads:
        if not _is_public_load_city(load.from_city) or not _is_public_load_city(load.to_city):
            continue
        creator = db.query(User).filter(User.id == load.user_id).first()
        ai_payload = compute_ai_score(load, stats)
        ai_distance_km = ai_payload.get("distance_km")
        ai_rate_per_km = ai_payload.get("rate_per_km")
        total_price = load.total_price if load.total_price is not None else load.price
        distance_km = load.distance_km if load.distance_km is not None else ai_distance_km
        rate_per_km = load.rate_per_km
        if rate_per_km is None and isinstance(distance_km, (int, float)) and distance_km > 0 and isinstance(total_price, (int, float)):
            rate_per_km = round(float(total_price) / float(distance_km), 1)
        if rate_per_km is None:
            rate_per_km = ai_rate_per_km
        loading_date = cargo_loading_date(load)
        load_dict = {
            "id": load.id,
            "from_city_id": load.from_city_id,
            "to_city_id": load.to_city_id,
            "from_city": canonicalize_city_name(load.from_city),
            "to_city": canonicalize_city_name(load.to_city),
            "weight": load.weight,
            "volume": load.volume,
            "weight_t": load.weight_t if load.weight_t is not None else load.weight,
            "volume_m3": load.volume_m3 if load.volume_m3 is not None else load.volume,
            "required_body_type": load.required_body_type,
            "cargo_kind": load.cargo_kind,
            "required_vehicle_kinds": load.required_vehicle_kinds or [],
            "required_options": load.required_options or [],
            "adr_classes": load.adr_classes or ([load.adr_class] if load.adr_class else []),
            "crew_required": bool(load.crew_required),
            "container_size": load.container_size,
            "needs_crane": bool(load.needs_crane),
            "needs_dump": bool(load.needs_dump),
            "temp_min": load.temp_min,
            "temp_max": load.temp_max,
            "price": total_price,
            "total_price": total_price,
            "distance": distance_km,
            "distance_km": distance_km,
            "price_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
            "rate_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
            "status": normalize_cargo_status(load.status),
            "loading_date": loading_date.isoformat() if loading_date else None,
            "loading_time": load.loading_time,
            "created_at": load.created_at.isoformat() if load.created_at else None,
            "ai_risk": ai_payload.get("ai_risk") or "low",
            "ai_score": int(ai_payload.get("ai_score") or 0),
            "ai_explain": ai_payload.get("ai_explain") or "",
            "ai_flags": ai_payload.get("ai_flags") or [],
            "market_rate_per_km": ai_payload.get("market_rate_per_km"),
            "creator": {
                "id": creator.id if creator else None,
                "fullname": creator.fullname if creator else "Неизвестно",
                "company": creator.company if creator else None,
                "rating": creator.rating if creator else 5.0,
                "points": creator.points if creator else 100,
                "trust_level": creator.trust_level if creator else "new",
                "verified": creator.verified if creator else False
            } if creator else None
        }
        result.append(load_dict)
    
    return result
