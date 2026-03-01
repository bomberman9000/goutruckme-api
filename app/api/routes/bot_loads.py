"""
API грузов для бота (JWT, существующие модели Load и Deal).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import User, Load, Deal
from app.services.cargo_status import (
    apply_cargo_status_filter,
    cargo_loading_date,
    expire_outdated_cargos,
    is_active_status,
    normalize_cargo_status,
)
from app.services.geo import canonicalize_city_name, is_city_like_name
from app.trust.service import recalc_company_trust

router = APIRouter()


def _is_public_load(load: Load | None) -> bool:
    if load is None:
        return False
    return is_city_like_name(load.from_city) and is_city_like_name(load.to_city)


@router.get("/loads")
async def get_loads(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Список грузов."""
    expire_outdated_cargos(db)
    loads_list = apply_cargo_status_filter(db.query(Load), "active").order_by(Load.created_at.desc()).limit(limit).all()
    return [
        {
            "id": item.id,
            "from_city": canonicalize_city_name(item.from_city),
            "to_city": canonicalize_city_name(item.to_city),
            "price": item.price,
            "status": normalize_cargo_status(item.status),
            "loading_date": cargo_loading_date(item).isoformat() if cargo_loading_date(item) else None,
        }
        for item in loads_list
        if _is_public_load(item)
    ]


@router.get("/loads/{load_id}")
async def get_load_detail(
    load_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Детали груза."""
    expire_outdated_cargos(db)
    load = db.query(Load).filter(Load.id == load_id).first()
    if not _is_public_load(load):
        raise HTTPException(status_code=404, detail="Груз не найден")

    return {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "price": load.price,
        "weight": load.weight,
        "truck_type": None,
        "status": normalize_cargo_status(load.status),
        "loading_date": (
            cargo_loading_date(load).isoformat() if cargo_loading_date(load) else None
        ),
        "contact_phone": None,
        "description": None,
    }


@router.post("/loads/{load_id}/take")
async def take_load(
    load_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Взять груз (создать сделку)."""
    expire_outdated_cargos(db)
    load = db.query(Load).filter(Load.id == load_id).first()
    if not _is_public_load(load):
        raise HTTPException(status_code=404, detail="Груз не найден")
    if not is_active_status(load.status):
        raise HTTPException(status_code=409, detail="Груз недоступен")

    deal = Deal(
        cargo_id=load_id,
        shipper_id=load.user_id,
        carrier_id=current_user.id,
        status="IN_PROGRESS"
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)

    try:
        recalc_company_trust(db, int(load.user_id))
        recalc_company_trust(db, int(current_user.id))
    except Exception:
        pass

    return {
        "success": True,
        "deal_id": deal.id,
        "message": "Груз взят в работу"
    }
