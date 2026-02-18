from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import Load, User, UserRole, Vehicle
from app.services.vehicle_ai import analyze_vehicle_submission, count_matching_loads

router = APIRouter()


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
_ALLOWED_STATUSES = {"active", "archived"}


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _canonical_body_type(body_type: str) -> str:
    normalized = _norm(body_type)
    canonical = _BODY_TYPE_MAP.get(normalized)
    if not canonical:
        allowed = ", ".join(sorted(set(_BODY_TYPE_MAP.values())))
        raise ValueError(f"body_type должен быть одним из: {allowed}")
    return canonical


def _vehicle_to_dict(vehicle: Vehicle, db: Session, *, matching_loads: Optional[int] = None) -> dict:
    carrier = vehicle.carrier
    vehicle_matching_loads = matching_loads
    if vehicle_matching_loads is None:
        vehicle_matching_loads = count_matching_loads(
            db,
            capacity_tons=float(vehicle.capacity_tons),
            location_city=vehicle.location_city,
            location_region=vehicle.location_region,
            available_from=vehicle.available_from,
        )

    return {
        "id": vehicle.id,
        "carrier_id": vehicle.carrier_id,
        "body_type": vehicle.body_type,
        "capacity_tons": vehicle.capacity_tons,
        "volume_m3": vehicle.volume_m3,
        "location_city": vehicle.location_city,
        "location_region": vehicle.location_region,
        "available_from": vehicle.available_from.isoformat() if vehicle.available_from else None,
        "rate_per_km": vehicle.rate_per_km,
        "status": vehicle.status,
        "created_at": vehicle.created_at.isoformat() if vehicle.created_at else None,
        "carrier": {
            "id": carrier.id if carrier else None,
            "organization_name": (carrier.organization_name if carrier else None) or (carrier.company if carrier else None),
            "rating": carrier.rating if carrier else None,
            "verified": carrier.verified if carrier else False,
            "trust_level": carrier.trust_level if carrier else None,
        },
        "ai": {
            "risk_level": vehicle.ai_risk_level or "low",
            "score": vehicle.ai_score or 0,
            "warnings": vehicle.ai_warnings or [],
            "market_rate_per_km": vehicle.ai_market_rate,
            "idle_ratio": vehicle.ai_idle_ratio,
        },
        "matching_loads": vehicle_matching_loads,
    }


class VehicleCreateRequest(BaseModel):
    body_type: str
    capacity_tons: float = Field(gt=0)
    volume_m3: float = Field(gt=0)
    location_city: str = Field(min_length=2, max_length=120)
    location_region: Optional[str] = Field(default=None, max_length=120)
    available_from: date
    rate_per_km: Optional[float] = Field(default=None, gt=0)
    carrier_id: Optional[int] = None

    @field_validator("body_type")
    @classmethod
    def validate_body_type(cls, value: str) -> str:
        return _canonical_body_type(value)

    @field_validator("location_city")
    @classmethod
    def normalize_city(cls, value: str) -> str:
        return value.strip()

    @field_validator("location_region")
    @classmethod
    def normalize_region(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class VehicleStatusUpdateRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        status = _norm(value)
        if status not in _ALLOWED_STATUSES:
            allowed = ", ".join(sorted(_ALLOWED_STATUSES))
            raise ValueError(f"status должен быть одним из: {allowed}")
        return status


@router.get("/vehicles")
def list_vehicles(
    city: Optional[str] = Query(default=None, description="Город/регион"),
    body_type: Optional[str] = Query(default=None, description="Тип кузова"),
    min_capacity_tons: Optional[float] = Query(default=None, ge=0),
    available_today: bool = Query(default=False),
    status: str = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    status_normalized = _norm(status) or "active"
    if status_normalized not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="status должен быть active или archived")

    query = db.query(Vehicle).filter(Vehicle.status == status_normalized)

    if city:
        city_like = f"%{city.strip()}%"
        query = query.filter(
            or_(Vehicle.location_city.ilike(city_like), Vehicle.location_region.ilike(city_like))
        )

    if body_type:
        try:
            canonical_body_type = _canonical_body_type(body_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        query = query.filter(Vehicle.body_type == canonical_body_type)

    if min_capacity_tons is not None:
        query = query.filter(Vehicle.capacity_tons >= min_capacity_tons)

    if available_today:
        query = query.filter(Vehicle.available_from <= date.today())

    vehicles = query.order_by(Vehicle.created_at.desc()).limit(limit).all()
    return [_vehicle_to_dict(vehicle, db) for vehicle in vehicles]


@router.get("/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")
    return _vehicle_to_dict(vehicle, db)


@router.post("/vehicles", status_code=201)
def create_vehicle(
    payload: VehicleCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    carrier_id = payload.carrier_id or current_user.id
    is_admin = current_user.role == UserRole.admin
    if carrier_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Можно добавлять машины только для своего профиля")

    carrier = db.query(User).filter(User.id == carrier_id).first()
    if not carrier:
        raise HTTPException(status_code=404, detail="Перевозчик не найден")

    ai_report = analyze_vehicle_submission(
        db,
        carrier=carrier,
        body_type=payload.body_type,
        capacity_tons=payload.capacity_tons,
        location_city=payload.location_city,
        location_region=payload.location_region,
        available_from=payload.available_from,
        rate_per_km=payload.rate_per_km,
    )

    vehicle = Vehicle(
        carrier_id=carrier.id,
        body_type=payload.body_type,
        capacity_tons=payload.capacity_tons,
        volume_m3=payload.volume_m3,
        location_city=payload.location_city,
        location_region=payload.location_region,
        available_from=payload.available_from,
        rate_per_km=payload.rate_per_km,
        status="active",
        ai_risk_level=ai_report["risk_level"],
        ai_score=ai_report["score"],
        ai_warnings=ai_report["warnings"],
        ai_market_rate=ai_report["market_rate_per_km"],
        ai_idle_ratio=ai_report["idle_ratio"],
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)

    return _vehicle_to_dict(vehicle, db, matching_loads=ai_report["matching_loads"])


@router.patch("/vehicles/{vehicle_id}/status")
def update_vehicle_status(
    vehicle_id: int,
    payload: VehicleStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    is_admin = current_user.role == UserRole.admin
    if vehicle.carrier_id != current_user.id and not is_admin:
        raise HTTPException(status_code=403, detail="Недостаточно прав")

    vehicle.status = payload.status
    db.commit()
    db.refresh(vehicle)
    return _vehicle_to_dict(vehicle, db)


@router.get("/vehicles/{vehicle_id}/matching-loads")
def get_vehicle_matching_loads(vehicle_id: int, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    count = count_matching_loads(
        db,
        capacity_tons=float(vehicle.capacity_tons),
        location_city=vehicle.location_city,
        location_region=vehicle.location_region,
        available_from=vehicle.available_from,
    )

    sample_loads = (
        db.query(Load)
        .filter(Load.status == "open")
        .filter((Load.weight.is_(None)) | (Load.weight <= vehicle.capacity_tons))
        .order_by(Load.created_at.desc())
        .limit(5)
        .all()
    )
    return {
        "vehicle_id": vehicle.id,
        "matching_loads_count": count,
        "sample_loads": [
            {
                "id": load.id,
                "from_city": load.from_city,
                "to_city": load.to_city,
                "weight": load.weight,
                "price": load.price,
            }
            for load in sample_loads
        ],
    }
