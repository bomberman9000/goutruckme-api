"""
API эндпоинты для работы с грузами через бота.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import CargoStatus, User, Load, Deal
from app.ai.scoring import MarketStats, compute_ai_score
from app.services.cargo_status import (
    apply_cargo_status_filter,
    cargo_loading_date,
    expire_outdated_cargos,
    is_active_status,
    is_terminal_status,
    normalize_cargo_status,
)
from app.services.geo import is_city_like_name
from app.trust.service import recalc_company_trust

router = APIRouter()


def _recalc_trust_safely(db: Session, *company_ids: int) -> None:
    seen: set[int] = set()
    for company_id in company_ids:
        if not company_id or company_id in seen:
            continue
        seen.add(company_id)
        try:
            recalc_company_trust(db, int(company_id))
        except Exception:
            # Trust-пересчёт не должен ломать основной сценарий.
            pass


def _is_public_cargo(load: Load | None) -> bool:
    if load is None:
        return False
    return is_city_like_name(load.from_city) and is_city_like_name(load.to_city)


# ============================================
# Схемы
# ============================================

class CargoListItem(BaseModel):
    id: int
    from_city_id: Optional[int] = None
    to_city_id: Optional[int] = None
    from_city: str
    to_city: str
    price: float
    distance: Optional[float] = None
    total_price: Optional[float] = None
    price_per_km: Optional[float] = None
    weight: Optional[float] = None
    truck_type: Optional[str] = None
    loading_date: str
    loading_time: Optional[str] = None
    status: str
    ai_risk: Optional[str] = None
    ai_score: Optional[int] = None
    ai_explain: Optional[str] = None
    ai_flags: Optional[list[str]] = None

    class Config:
        from_attributes = True


class CargoDetail(BaseModel):
    id: int
    from_city_id: Optional[int] = None
    to_city_id: Optional[int] = None
    from_city: str
    to_city: str
    price: float
    total_price: Optional[float] = None
    price_per_km: Optional[float] = None
    distance: Optional[float] = None
    weight: Optional[float] = None
    volume: Optional[float] = None
    truck_type: Optional[str] = None
    loading_date: str
    loading_time: Optional[str] = None
    cargo_type: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_telegram: Optional[str] = None
    description: Optional[str] = None
    status: str
    ai_risk: Optional[str] = None
    ai_score: Optional[int] = None
    ai_explain: Optional[str] = None
    ai_flags: Optional[list[str]] = None

    class Config:
        from_attributes = True


class CargoResponseRequest(BaseModel):
    carrier_id: Optional[int] = None
    message: Optional[str] = None


class SelectCarrierRequest(BaseModel):
    carrier_id: int


def _load_to_list_item(load: Load, ai_payload: Optional[dict] = None) -> dict:
    """Преобразовать Load в CargoListItem."""
    ai_payload = ai_payload or {}
    distance_km = load.distance_km if load.distance_km is not None else ai_payload.get("distance_km")
    total_price = load.total_price if load.total_price is not None else load.price
    rate_per_km = load.rate_per_km
    if rate_per_km is None and isinstance(distance_km, (int, float)) and distance_km > 0 and isinstance(total_price, (int, float)):
        rate_per_km = round(float(total_price) / float(distance_km), 1)
    if rate_per_km is None:
        rate_per_km = ai_payload.get("rate_per_km")
    loading_date = cargo_loading_date(load)
    return {
        "id": load.id,
        "from_city_id": load.from_city_id,
        "to_city_id": load.to_city_id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": float(total_price) if total_price is not None else 0,
        "total_price": float(total_price) if total_price is not None else 0,
        "distance": distance_km,
        "price_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
        "weight": load.weight,
        "truck_type": None,
        "loading_date": loading_date.isoformat() if loading_date else "",
        "loading_time": load.loading_time,
        "status": normalize_cargo_status(load.status),
        "ai_risk": ai_payload.get("ai_risk") or "low",
        "ai_score": int(ai_payload.get("ai_score") or 0),
        "ai_explain": ai_payload.get("ai_explain") or "",
        "ai_flags": ai_payload.get("ai_flags") or [],
    }


def _load_to_detail(load: Load, ai_payload: Optional[dict] = None) -> dict:
    """Преобразовать Load в CargoDetail."""
    ai_payload = ai_payload or {}
    distance_km = load.distance_km if load.distance_km is not None else ai_payload.get("distance_km")
    total_price = load.total_price if load.total_price is not None else load.price
    rate_per_km = load.rate_per_km
    if rate_per_km is None and isinstance(distance_km, (int, float)) and distance_km > 0 and isinstance(total_price, (int, float)):
        rate_per_km = round(float(total_price) / float(distance_km), 1)
    if rate_per_km is None:
        rate_per_km = ai_payload.get("rate_per_km")
    loading_date = cargo_loading_date(load)
    return {
        "id": load.id,
        "from_city_id": load.from_city_id,
        "to_city_id": load.to_city_id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": float(total_price) if total_price is not None else 0,
        "total_price": float(total_price) if total_price is not None else 0,
        "price_per_km": round(float(rate_per_km), 1) if isinstance(rate_per_km, (int, float)) else None,
        "distance": distance_km,
        "weight": load.weight,
        "volume": load.volume,
        "truck_type": None,
        "loading_date": loading_date.isoformat() if loading_date else "",
        "loading_time": load.loading_time,
        "cargo_type": None,
        "contact_phone": None,
        "contact_telegram": None,
        "description": None,
        "status": normalize_cargo_status(load.status),
        "ai_risk": ai_payload.get("ai_risk") or "low",
        "ai_score": int(ai_payload.get("ai_score") or 0),
        "ai_explain": ai_payload.get("ai_explain") or "",
        "ai_flags": ai_payload.get("ai_flags") or [],
    }


# ============================================
# Эндпоинты
# ============================================

@router.get("/cargos", response_model=List[CargoListItem])
async def get_cargos(
    status: Optional[str] = Query("active", description="active|expired|all"),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список грузов.
    По умолчанию возвращает только активные.
    """
    expire_outdated_cargos(db)
    query = db.query(Load)
    status_value = normalize_cargo_status(status)
    if status and str(status).strip().lower() not in {"active", "expired", "all", "closed", "cancelled", "open", "covered"}:
        raise HTTPException(status_code=422, detail="status должен быть active|expired|all")
    query = apply_cargo_status_filter(query, status_value, default=CargoStatus.active.value)

    loads = query.order_by(Load.created_at.desc()).limit(limit).all()
    stats = MarketStats.from_db(db, lookback_days=60)
    return [
        _load_to_list_item(l, compute_ai_score(l, stats))
        for l in loads
        if _is_public_cargo(l)
    ]


@router.get("/cargos/{cargo_id}", response_model=CargoDetail)
async def get_cargo_detail(
    cargo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получить детали груза."""
    expire_outdated_cargos(db)
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not _is_public_cargo(cargo):
        raise HTTPException(status_code=404, detail="Груз не найден")
    stats = MarketStats.from_db(db, lookback_days=60)
    return _load_to_detail(cargo, compute_ai_score(cargo, stats))


@router.post("/cargos/{cargo_id}/responses")
async def create_cargo_response(
    cargo_id: int,
    data: CargoResponseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создать отклик на груз (перевозчик откликается).
    """
    expire_outdated_cargos(db)
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not cargo:
        raise HTTPException(status_code=404, detail="Груз не найден")
    if not is_active_status(cargo.status):
        raise HTTPException(status_code=409, detail="Груз недоступен для отклика")

    deal = Deal(
        cargo_id=cargo_id,
        shipper_id=cargo.user_id,
        carrier_id=current_user.id,
        status="IN_PROGRESS",
        carrier_message=data.message
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)
    _recalc_trust_safely(db, cargo.user_id, current_user.id)

    return {
        "success": True,
        "deal_id": deal.id,
        "message": "Отклик отправлен"
    }


@router.post("/cargos/{cargo_id}/select_carrier")
async def select_carrier(
    cargo_id: int,
    data: SelectCarrierRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Выбрать перевозчика для груза (грузовладелец выбирает).
    """
    expire_outdated_cargos(db)
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not cargo:
        raise HTTPException(status_code=404, detail="Груз не найден")
    if cargo.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Не ваш груз")
    if is_terminal_status(cargo.status):
        raise HTTPException(status_code=409, detail="Груз уже закрыт или истёк")

    deal = db.query(Deal).filter(
        Deal.cargo_id == cargo_id,
        Deal.carrier_id == data.carrier_id
    ).first()

    if not deal:
        deal = Deal(
            cargo_id=cargo_id,
            shipper_id=current_user.id,
            carrier_id=data.carrier_id,
            status="CONFIRMED"
        )
        db.add(deal)
    else:
        deal.status = "CONFIRMED"

    cargo.status = CargoStatus.closed.value
    db.commit()
    db.refresh(deal)
    _recalc_trust_safely(db, current_user.id, data.carrier_id)

    try:
        from app.services.bot_webhooks import notify_carrier_selected
        import asyncio
        carrier_user = db.query(User).filter(User.id == data.carrier_id).first()
        if carrier_user and carrier_user.telegram_id:
            asyncio.create_task(notify_carrier_selected(deal.id, carrier_user.telegram_id))
    except Exception:
        pass

    return {
        "success": True,
        "deal_id": deal.id,
        "message": "Перевозчик выбран"
    }
