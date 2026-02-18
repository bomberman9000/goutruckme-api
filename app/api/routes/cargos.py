"""
API эндпоинты для работы с грузами через бота.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import User, Load, Deal

router = APIRouter()


# ============================================
# Схемы
# ============================================

class CargoListItem(BaseModel):
    id: int
    from_city: str
    to_city: str
    price: float
    weight: Optional[float] = None
    truck_type: Optional[str] = None
    loading_date: str
    status: str

    class Config:
        from_attributes = True


class CargoDetail(BaseModel):
    id: int
    from_city: str
    to_city: str
    price: float
    price_per_km: Optional[int] = None
    distance: Optional[int] = None
    weight: Optional[float] = None
    volume: Optional[float] = None
    truck_type: Optional[str] = None
    loading_date: str
    cargo_type: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_telegram: Optional[str] = None
    description: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


class CargoResponseRequest(BaseModel):
    carrier_id: Optional[int] = None
    message: Optional[str] = None


class SelectCarrierRequest(BaseModel):
    carrier_id: int


def _load_to_list_item(load: Load) -> dict:
    """Преобразовать Load в CargoListItem."""
    return {
        "id": load.id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": int(load.price) if load.price else 0,
        "weight": load.weight,
        "truck_type": None,
        "loading_date": load.created_at.strftime("%Y-%m-%d") if load.created_at else "",
        "status": load.status or "open",
    }


def _load_to_detail(load: Load) -> dict:
    """Преобразовать Load в CargoDetail."""
    return {
        "id": load.id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": int(load.price) if load.price else 0,
        "price_per_km": None,
        "distance": None,
        "weight": load.weight,
        "volume": load.volume,
        "truck_type": None,
        "loading_date": load.created_at.strftime("%Y-%m-%d") if load.created_at else "",
        "cargo_type": None,
        "contact_phone": None,
        "contact_telegram": None,
        "description": None,
        "status": load.status or "open",
    }


# ============================================
# Эндпоинты
# ============================================

@router.get("/cargos", response_model=List[CargoListItem])
async def get_cargos(
    status: Optional[str] = Query("open", description="Статус груза"),
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список грузов.
    Статусы: open, covered, closed (active = open)
    """
    query = db.query(Load)
    if status and status == "active":
        query = query.filter(Load.status == "open")
    elif status:
        query = query.filter(Load.status == status)

    loads = query.order_by(Load.created_at.desc()).limit(limit).all()
    return [_load_to_list_item(l) for l in loads]


@router.get("/cargos/{cargo_id}", response_model=CargoDetail)
async def get_cargo_detail(
    cargo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получить детали груза."""
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not cargo:
        raise HTTPException(status_code=404, detail="Груз не найден")
    return _load_to_detail(cargo)


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
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not cargo:
        raise HTTPException(status_code=404, detail="Груз не найден")

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
    cargo = db.query(Load).filter(Load.id == cargo_id).first()
    if not cargo:
        raise HTTPException(status_code=404, detail="Груз не найден")
    if cargo.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Не ваш груз")

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

    cargo.status = "covered"
    db.commit()
    db.refresh(deal)

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
