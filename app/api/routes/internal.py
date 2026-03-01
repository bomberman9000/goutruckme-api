from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db
from app.dicts.vehicles import LEGACY_BODY_KIND
from app.models.models import CargoStatus, City, Load, User, Vehicle
from app.services.login_tokens import create_login_token, verify_login_token
from app.services.geo import is_city_like_name, is_supported_city, normalize_city_name
from app.services.sync_warmup import get_warmup_context, warmup_search_context


router = APIRouter()


class InternalSyncEvent(BaseModel):
    event_id: str | None = None
    event_type: str
    source: str = "unknown"
    search_id: str | None = None
    user_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    order: dict[str, Any] | None = None
    vehicle: dict[str, Any] | None = None


class CreateLoginTokenRequest(BaseModel):
    telegram_user_id: int
    user_id: int | None = None
    search_id: str | None = None
    redirect_path: str | None = None


class VerifyLoginTokenRequest(BaseModel):
    token: str
    telegram_user_id: int | None = None


class InternalTokenPayload(BaseModel):
    token: str
    web_url: str
    expires_in_sec: int


def _require_internal_token(x_internal_token: str | None) -> None:
    expected = (settings.INTERNAL_TOKEN or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_TOKEN is not configured")
    if not x_internal_token or x_internal_token != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


def _normalize_redirect_path(raw_path: str | None) -> str:
    path = (raw_path or "").strip()
    if not path:
        return "/dashboard"
    if not path.startswith("/"):
        return "/dashboard"
    if path.startswith("//"):
        return "/dashboard"
    return path


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_loading_time(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if len(raw) == 5 and raw[2] == ":":
        hours = _coerce_int(raw[:2])
        minutes = _coerce_int(raw[3:])
        if hours is not None and minutes is not None and 0 <= hours <= 23 and 0 <= minutes <= 59:
            return f"{hours:02d}:{minutes:02d}"
    return None


def _map_load_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "new": CargoStatus.active.value,
        "active": CargoStatus.active.value,
        "in_progress": CargoStatus.active.value,
        "completed": CargoStatus.closed.value,
        "closed": CargoStatus.closed.value,
        "cancelled": CargoStatus.cancelled.value,
        "canceled": CargoStatus.cancelled.value,
        "archived": CargoStatus.expired.value,
        "expired": CargoStatus.expired.value,
    }
    return mapping.get(raw, CargoStatus.active.value)


def _normalize_sync_city(db: Session, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not is_city_like_name(raw):
        return None

    normalized = normalize_city_name(raw)
    city = (
        db.query(City)
        .filter(City.name_norm == normalized)
        .order_by(City.population.desc().nullslast(), City.name.asc())
        .first()
    )
    if is_supported_city(city):
        return city.name
    return None


def _normalize_phone_digits(value: Any) -> str | None:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not raw:
        return None
    if len(raw) >= 11:
        return raw[-11:]
    return raw


def _resolve_sync_user(db: Session, raw_user_id: Any, *, phone: Any | None = None) -> User | None:
    numeric_user_id = _coerce_int(raw_user_id)
    if not numeric_user_id:
        user = None
    else:
        user = db.query(User).filter(User.id == numeric_user_id).first()
        if user:
            return user

        user = db.query(User).filter(User.telegram_id == numeric_user_id).first()
        if user:
            return user

    normalized_phone = _normalize_phone_digits(phone)
    if not normalized_phone:
        return None

    for candidate in db.query(User).filter(User.phone.isnot(None)).all():
        if _normalize_phone_digits(candidate.phone) == normalized_phone:
            return candidate
    return None


def _resolve_user_for_login_token(db: Session, req: CreateLoginTokenRequest) -> User:
    user = db.query(User).filter(User.telegram_id == req.telegram_user_id).first()
    if user:
        return user

    if req.user_id:
        user = db.query(User).filter(User.id == req.user_id).first()
        if user:
            if user.telegram_id and int(user.telegram_id) != int(req.telegram_user_id):
                raise HTTPException(status_code=409, detail="Telegram аккаунт привязан к другому пользователю")
            user.telegram_id = int(req.telegram_user_id)
            db.commit()
            db.refresh(user)
            return user

    raise HTTPException(status_code=404, detail="Пользователь с таким telegram_user_id не найден")


def _upsert_order_from_sync(
    db: Session,
    *,
    order: dict[str, Any],
    fallback_user_id: int | None,
) -> dict[str, Any]:
    order_id = _coerce_int(order.get("id"))
    from_city_raw = str(order.get("from_city") or "").strip()
    to_city_raw = str(order.get("to_city") or "").strip()
    from_city = _normalize_sync_city(db, from_city_raw)
    to_city = _normalize_sync_city(db, to_city_raw)
    price = _coerce_float(order.get("price_rub") or order.get("total_price") or order.get("price"))
    weight = _coerce_float(order.get("weight_t") or order.get("weight"))
    cargo_description = str(order.get("cargo_description") or "").strip() or None
    payment_terms = str(order.get("payment_terms") or "").strip() or None
    dimensions = str(order.get("dimensions") or "").strip() or None
    phone = str(order.get("phone") or "").strip() or None
    inn = str(order.get("inn") or "").strip() or None
    suggested_response = str(order.get("suggested_response") or "").strip() or None
    source = str(order.get("source") or "").strip() or None
    is_hot_deal = _coerce_bool(order.get("is_hot_deal"))
    is_direct_customer = _coerce_bool(order.get("is_direct_customer"))
    required_body_type = str(order.get("required_body_type") or order.get("body_type") or "").strip() or None
    distance_km = _coerce_float(order.get("distance_km"))
    rate_per_km = _coerce_float(order.get("rate_per_km"))
    pickup_lat = _coerce_float(order.get("pickup_lat") or order.get("from_lat"))
    pickup_lon = _coerce_float(order.get("pickup_lon") or order.get("from_lon"))
    delivery_lat = _coerce_float(order.get("delivery_lat") or order.get("to_lat"))
    delivery_lon = _coerce_float(order.get("delivery_lon") or order.get("to_lon"))
    loading_date = _coerce_date(order.get("loading_date") or order.get("load_date"))
    loading_time = _normalize_loading_time(order.get("loading_time") or order.get("load_time"))
    owner = _resolve_sync_user(db, order.get("user_id"), phone=order.get("phone")) or _resolve_sync_user(
        db,
        fallback_user_id,
        phone=order.get("phone"),
    )
    owner_id = int(owner.id) if owner else None

    if not from_city or not to_city:
        return {"saved": False, "reason": "invalid_route"}

    if price is None or price <= 0:
        price = 1.0

    load = db.query(Load).filter(Load.id == order_id).first() if order_id else None
    created = False

    if not load:
        load = Load(
            id=order_id,
            user_id=owner_id,
            from_city=from_city,
            to_city=to_city,
            weight=float(weight) if weight is not None else 0.0,
            weight_t=float(weight) if weight is not None else 0.0,
            volume=0.0,
            volume_m3=0.0,
            price=float(price),
            total_price=float(price),
            cargo_description=cargo_description,
            payment_terms=payment_terms,
            is_direct_customer=is_direct_customer,
            dimensions=dimensions,
            is_hot_deal=is_hot_deal if is_hot_deal is not None else False,
            phone=phone,
            inn=inn,
            suggested_response=suggested_response,
            source=source,
            required_body_type=required_body_type,
            distance_km=distance_km,
            rate_per_km=rate_per_km,
            pickup_lat=pickup_lat,
            pickup_lon=pickup_lon,
            delivery_lat=delivery_lat,
            delivery_lon=delivery_lon,
            loading_date=loading_date,
            loading_time=loading_time,
            status=_map_load_status(order.get("status")),
        )
        db.add(load)
        created = True
    else:
        load.user_id = owner_id or load.user_id
        load.from_city = from_city
        load.to_city = to_city
        load.weight = float(weight) if weight is not None else load.weight
        load.weight_t = float(weight) if weight is not None else load.weight_t
        load.price = float(price)
        load.total_price = float(price)
        load.cargo_description = cargo_description or load.cargo_description
        load.payment_terms = payment_terms or load.payment_terms
        if is_direct_customer is not None:
            load.is_direct_customer = is_direct_customer
        load.dimensions = dimensions or load.dimensions
        if is_hot_deal is not None:
            load.is_hot_deal = is_hot_deal
        load.phone = phone or load.phone
        load.inn = inn or load.inn
        load.suggested_response = suggested_response or load.suggested_response
        load.source = source or load.source
        load.required_body_type = required_body_type or load.required_body_type
        if distance_km is not None and distance_km > 0:
            load.distance_km = distance_km
        if rate_per_km is not None and rate_per_km > 0:
            load.rate_per_km = rate_per_km
        if pickup_lat is not None:
            load.pickup_lat = pickup_lat
        if pickup_lon is not None:
            load.pickup_lon = pickup_lon
        if delivery_lat is not None:
            load.delivery_lat = delivery_lat
        if delivery_lon is not None:
            load.delivery_lon = delivery_lon
        if loading_date is not None:
            load.loading_date = loading_date
        if loading_time is not None:
            load.loading_time = loading_time
        load.status = _map_load_status(order.get("status") or load.status)

    return {
        "saved": True,
        "created": created,
        "load_id": int(load.id) if load.id is not None else None,
    }


def _upsert_vehicle_from_sync(
    db: Session,
    *,
    vehicle: dict[str, Any],
    fallback_user_id: int | None,
    event_source: str | None,
) -> dict[str, Any]:
    vehicle_id = _coerce_int(vehicle.get("id"))
    if not vehicle_id:
        return {"saved": False, "reason": "missing_vehicle_id"}

    user = _resolve_sync_user(db, vehicle.get("user_id"), phone=vehicle.get("owner_phone")) or _resolve_sync_user(
        db,
        fallback_user_id,
        phone=vehicle.get("owner_phone"),
    )
    if not user:
        return {"saved": False, "reason": "missing_user_id"}

    source_normalized = (event_source or vehicle.get("source") or "").strip().lower()
    sync_vehicle_id = vehicle_id + 900_000_000 if source_normalized == "tg-bot" else vehicle_id

    from_city_raw = str(vehicle.get("from_city") or vehicle.get("location_city") or "").strip()
    to_city_raw = str(vehicle.get("to_city") or vehicle.get("location_region") or "").strip() or None
    from_city = _normalize_sync_city(db, from_city_raw)
    to_city = _normalize_sync_city(db, to_city_raw) if to_city_raw else None
    if not from_city:
        return {"saved": False, "reason": "invalid_location_city"}

    body_type = str(vehicle.get("body_type") or "тент").strip() or "тент"
    vehicle_kind = LEGACY_BODY_KIND.get(body_type, "EUROFURA_TENT_20T")
    capacity_tons = _coerce_float(vehicle.get("capacity_t") or vehicle.get("capacity_tons")) or 20.0
    volume_m3 = _coerce_float(vehicle.get("volume_m3") or vehicle.get("volume")) or 82.0
    plate_number = str(vehicle.get("plate_number") or "").strip() or None
    is_available = _coerce_bool(vehicle.get("is_available"))
    available_from = date.today() if is_available is not False else date.today() + timedelta(days=1)
    status = str(vehicle.get("status") or "active").strip() or "active"

    obj = None
    if plate_number:
        obj = (
            db.query(Vehicle)
            .filter(Vehicle.owner_user_id == int(user.id), Vehicle.plate_number == plate_number)
            .first()
        )
    if not obj:
        obj = db.query(Vehicle).filter(Vehicle.id == sync_vehicle_id).first()
    created = False

    if not obj:
        obj = Vehicle(
            id=sync_vehicle_id,
            carrier_id=int(user.id),
            owner_user_id=int(user.id),
            name=str(vehicle.get("name") or f"{body_type.title()} {capacity_tons:g}т").strip(),
            vehicle_kind=vehicle_kind,
            body_type=body_type,
            capacity_tons=float(capacity_tons),
            volume_m3=float(volume_m3),
            plate_number=plate_number,
            location_city=from_city,
            location_region=to_city,
            available_from=available_from,
            status=status,
        )
        db.add(obj)
        created = True
    else:
        obj.carrier_id = int(user.id)
        obj.owner_user_id = int(user.id)
        obj.name = str(vehicle.get("name") or obj.name or f"{body_type.title()} {capacity_tons:g}т").strip()
        obj.vehicle_kind = str(vehicle.get("vehicle_kind") or obj.vehicle_kind or vehicle_kind).strip() or vehicle_kind
        obj.body_type = body_type
        obj.capacity_tons = float(capacity_tons)
        obj.volume_m3 = float(volume_m3)
        obj.plate_number = plate_number or obj.plate_number
        obj.location_city = from_city
        obj.location_region = to_city
        obj.available_from = available_from
        obj.status = status or obj.status or "active"

    return {
        "saved": True,
        "created": created,
        "vehicle_id": int(obj.id),
    }


@router.post("/internal/sync")
@router.post("/internal/sync-data")
def internal_sync(
    body: InternalSyncEvent,
    db: Session = Depends(get_db),
    x_internal_token: str | None = Header(default=None),
):
    _require_internal_token(x_internal_token)

    event_type = (body.event_type or "").strip().lower()
    metadata = body.metadata if isinstance(body.metadata, dict) else {}
    search_id = body.search_id or metadata.get("search_id")

    from_city = metadata.get("from_city")
    to_city = metadata.get("to_city")

    if not from_city and isinstance(body.order, dict):
        from_city = body.order.get("from_city")
    if not to_city and isinstance(body.order, dict):
        to_city = body.order.get("to_city")

    order_sync = {"saved": False}
    if isinstance(body.order, dict):
        order_sync = _upsert_order_from_sync(db, order=body.order, fallback_user_id=body.user_id)

    vehicle_sync = {"saved": False}
    if isinstance(body.vehicle, dict):
        vehicle_sync = _upsert_vehicle_from_sync(
            db,
            vehicle=body.vehicle,
            fallback_user_id=body.user_id,
            event_source=body.source,
        )

    db.commit()

    warmed = None
    if search_id and event_type.startswith("search."):
        warmed = warmup_search_context(
            db,
            search_id=search_id,
            user_id=body.user_id,
            from_city=str(from_city).strip() if from_city else None,
            to_city=str(to_city).strip() if to_city else None,
            query_text=str(metadata.get("query") or "").strip() or None,
        )

    return {
        "ok": True,
        "event_id": body.event_id,
        "event_type": event_type,
        "search_id": search_id,
        "warmed": bool(warmed),
        "recommendation_count": len(warmed["recommendations"]) if warmed else 0,
        "order_sync": order_sync,
        "vehicle_sync": vehicle_sync,
    }


@router.post("/internal/auth/create-login-token", response_model=InternalTokenPayload)
@router.post("/internal/auth/create-magic-link", response_model=InternalTokenPayload)
def create_login_token_endpoint(
    body: CreateLoginTokenRequest,
    db: Session = Depends(get_db),
    x_internal_token: str | None = Header(default=None),
):
    _require_internal_token(x_internal_token)

    user = _resolve_user_for_login_token(db, body)

    redirect_path = _normalize_redirect_path(body.redirect_path)
    if redirect_path == "/dashboard" and body.search_id:
        redirect_path = f"/dashboard?search_id={body.search_id}"

    ttl = max(60, int(settings.LOGIN_TOKEN_TTL_SECONDS or 300))
    payload = create_login_token(
        user_id=int(user.id),
        telegram_user_id=int(body.telegram_user_id),
        search_id=body.search_id,
        redirect_path=redirect_path,
        ttl_seconds=ttl,
    )

    base = (settings.PUBLIC_BASE_URL or "http://144.31.64.130:8000").rstrip("/")
    return {
        "token": payload.token,
        "web_url": f"{base}/auth/telegram-autologin?token={payload.token}",
        "expires_in_sec": ttl,
    }


@router.post("/internal/auth/verify-login-token")
def verify_login_token_endpoint(
    body: VerifyLoginTokenRequest,
    db: Session = Depends(get_db),
    x_internal_token: str | None = Header(default=None),
):
    _require_internal_token(x_internal_token)

    payload = verify_login_token(body.token, consume=False)
    if not payload:
        raise HTTPException(status_code=401, detail="Невалидный или просроченный login token")

    if body.telegram_user_id is not None and int(body.telegram_user_id) != int(payload.telegram_user_id):
        raise HTTPException(status_code=403, detail="Токен не соответствует telegram_user_id")

    user = db.query(User).filter(User.id == int(payload.user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user.telegram_id and int(user.telegram_id) != int(payload.telegram_user_id):
        raise HTTPException(status_code=409, detail="Telegram аккаунт уже привязан к другому пользователю")

    if not user.telegram_id:
        user.telegram_id = int(payload.telegram_user_id)
        db.commit()

    base = (settings.PUBLIC_BASE_URL or "http://144.31.64.130:8000").rstrip("/")
    return {
        "ok": True,
        "user": {
            "id": int(user.id),
            "phone": user.phone,
            "organization_name": user.organization_name or user.company or user.fullname,
            "telegram_id": int(payload.telegram_user_id),
        },
        "search_id": payload.search_id,
        "redirect_path": payload.redirect_path,
        "web_url": f"{base}/auth/telegram-autologin?token={body.token}",
    }


@router.get("/internal/sync/warmup/{search_id}")
def get_sync_warmup(
    search_id: str,
    x_internal_token: str | None = Header(default=None),
):
    _require_internal_token(x_internal_token)
    payload = get_warmup_context(search_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Search warmup not found")

    recommendations = payload.get("recommendations") or []
    return {
        "ok": True,
        "search_id": search_id,
        "from_city": payload.get("from_city"),
        "to_city": payload.get("to_city"),
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
    }
