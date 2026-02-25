"""API для Telegram бота: привязка аккаунта и работа с грузами."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import create_access_token, get_current_user, verify_password
from app.db.database import get_db
from app.models.models import User, Load, Deal
from app.services.cargo_status import (
    apply_cargo_status_filter,
    cargo_loading_date,
    expire_outdated_cargos,
    is_active_status,
    normalize_cargo_status,
)
from app.trust.service import recalc_company_trust

router = APIRouter()


class LinkRequest(BaseModel):
    phone: str
    password: str
    telegram_id: int
    telegram_username: str = ""


@router.post("/link")
async def link_telegram(data: LinkRequest, db: Session = Depends(get_db)):
    """Привязать Telegram (телефон + пароль)."""
    user = db.query(User).filter(User.phone == data.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Не найден")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный пароль")

    user.telegram_id = data.telegram_id
    user.telegram_username = data.telegram_username or None
    db.commit()

    token = create_access_token(data={"sub": str(user.id)})

    return {
        "success": True,
        "access_token": token,
        "message": f"Добро пожаловать в ГрузПоток, {user.organization_name or user.fullname}!"
    }


@router.get("/loads")
async def get_loads(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Список грузов."""
    expire_outdated_cargos(db)
    loads = apply_cargo_status_filter(db.query(Load), "active").order_by(Load.created_at.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "from_city": l.from_city,
            "to_city": l.to_city,
            "price": l.price,
            "status": normalize_cargo_status(l.status),
            "loading_date": cargo_loading_date(l).isoformat() if cargo_loading_date(l) else None,
        }
        for l in loads
    ]


@router.get("/loads/{load_id}")
async def get_load(
    load_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Детали груза."""
    expire_outdated_cargos(db)
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Не найдено")
    return {
        "id": load.id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": load.price,
        "weight": load.weight,
        "truck_type": None,
        "status": normalize_cargo_status(load.status),
        "loading_date": cargo_loading_date(load).isoformat() if cargo_loading_date(load) else None,
        "contact_phone": None,
        "description": None,
    }


@router.post("/loads/{load_id}/take")
async def take_load(
    load_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Взять груз (создать сделку)."""
    expire_outdated_cargos(db)
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Не найдено")
    if not is_active_status(load.status):
        raise HTTPException(status_code=409, detail="Груз недоступен")

    deal = Deal(
        cargo_id=load_id,
        shipper_id=load.user_id,
        carrier_id=current_user.id,
        status="IN_PROGRESS",
        created_at=datetime.utcnow(),
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)

    try:
        recalc_company_trust(db, int(load.user_id))
        recalc_company_trust(db, int(current_user.id))
    except Exception:
        pass

    return {"success": True, "deal_id": deal.id, "message": "Взято!"}
