"""
API грузов для бота (JWT, существующие модели Load и Deal).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import User, Load, Deal

router = APIRouter()


@router.get("/loads")
async def get_loads(
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Список грузов."""
    loads_list = db.query(Load).order_by(Load.created_at.desc()).limit(limit).all()
    return [
        {
            "id": item.id,
            "from_city": item.from_city,
            "to_city": item.to_city,
            "price": item.price,
            "loading_date": item.created_at.isoformat() if item.created_at else None,
        }
        for item in loads_list
    ]


@router.get("/loads/{load_id}")
async def get_load_detail(
    load_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Детали груза."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Груз не найден")

    return {
        "id": load.id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "price": load.price,
        "weight": load.weight,
        "truck_type": None,
        "loading_date": (
            load.created_at.isoformat() if load.created_at else None
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
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Груз не найден")

    deal = Deal(
        cargo_id=load_id,
        shipper_id=load.user_id,
        carrier_id=current_user.id,
        status="IN_PROGRESS"
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)

    return {
        "success": True,
        "deal_id": deal.id,
        "message": "Груз взят в работу"
    }
