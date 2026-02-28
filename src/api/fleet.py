"""Fleet Manager API — vehicle registration and reverse matching.

Carriers register their vehicles and press "I'm free in Kazan" —
the system finds best matching cargos from parser + platform.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.audit import log_audit_event
from src.core.database import async_session
from src.core.models import UserVehicle
from src.core.services.matching_engine import find_matches_for_vehicle

router = APIRouter(tags=["fleet"])


def _to_payload(value: object) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError("unsupported match payload")


class VehicleCreate(BaseModel):
    body_type: str
    capacity_tons: float = 20.0
    location_city: str | None = None
    plate_number: str | None = None


class VehicleResponse(BaseModel):
    id: int
    body_type: str
    capacity_tons: float
    location_city: str | None
    is_available: bool
    plate_number: str | None
    sts_verified: bool


class VehicleListResponse(BaseModel):
    vehicles: list[VehicleResponse] = Field(default_factory=list)


class MatchedCargo(BaseModel):
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    weight_t: float | None
    rate_rub: int | None
    rate_per_km: float | None = None
    load_date: str | None = None
    is_hot_deal: bool = False
    freshness: str | None = None
    match_score: int = 0
    distance_to_pickup_km: int | None = None
    match_reasons: list[str] = Field(default_factory=list)
    verified_payment: bool = False


class ReverseMatchResponse(BaseModel):
    vehicle_id: int
    location_city: str
    matched: list[MatchedCargo] = Field(default_factory=list)
    total: int = 0


@router.post("/api/v1/fleet/vehicles", response_model=VehicleResponse)
async def add_vehicle(
    body: VehicleCreate,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    async with async_session() as session:
        v = UserVehicle(
            user_id=tma_user.user_id,
            body_type=body.body_type,
            capacity_tons=body.capacity_tons,
            location_city=body.location_city,
            plate_number=body.plate_number,
        )
        session.add(v)
        await session.flush()
        log_audit_event(
            session,
            entity_type="vehicle",
            entity_id=int(v.id),
            action="vehicle_create",
            actor_user_id=tma_user.user_id,
            actor_role="user",
            meta={"body_type": body.body_type},
        )
        await session.commit()
        await session.refresh(v)
    return VehicleResponse(
        id=v.id, body_type=v.body_type, capacity_tons=v.capacity_tons,
        location_city=v.location_city, is_available=v.is_available,
        plate_number=v.plate_number, sts_verified=v.sts_verified,
    )


@router.get("/api/v1/fleet/vehicles", response_model=VehicleListResponse)
async def list_vehicles(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    async with async_session() as session:
        rows = (
            await session.execute(
                select(UserVehicle)
                .where(UserVehicle.user_id == tma_user.user_id)
                .order_by(UserVehicle.id.desc())
            )
        ).scalars().all()
    return VehicleListResponse(
        vehicles=[
            VehicleResponse(
                id=v.id, body_type=v.body_type, capacity_tons=v.capacity_tons,
                location_city=v.location_city, is_available=v.is_available,
                plate_number=v.plate_number, sts_verified=v.sts_verified,
            )
            for v in rows
        ]
    )


@router.post("/api/v1/fleet/vehicles/{vehicle_id}/available")
async def set_available(
    vehicle_id: int,
    city: str = Query(..., min_length=1),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Mark vehicle as available in a city — triggers reverse matching."""
    async with async_session() as session:
        v = await session.scalar(
            select(UserVehicle).where(
                UserVehicle.id == vehicle_id,
                UserVehicle.user_id == tma_user.user_id,
            )
        )
        if not v:
            raise HTTPException(status_code=404, detail="vehicle not found")
        v.is_available = True
        v.location_city = city.strip()
        log_audit_event(
            session,
            entity_type="vehicle",
            entity_id=int(v.id),
            action="vehicle_available",
            actor_user_id=tma_user.user_id,
            actor_role="user",
            meta={"city": v.location_city},
        )
        await session.commit()
        await session.refresh(v)

    async with async_session() as session:
        v = await session.scalar(
            select(UserVehicle).where(UserVehicle.id == vehicle_id)
        )
        matches = await _find_reverse_matches(session, v)
    return ReverseMatchResponse(
        vehicle_id=v.id,
        location_city=v.location_city or city,
        matched=matches,
        total=len(matches),
    )


@router.post("/api/v1/fleet/vehicles/{vehicle_id}/unavailable")
async def set_unavailable(
    vehicle_id: int,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    async with async_session() as session:
        v = await session.scalar(
            select(UserVehicle).where(
                UserVehicle.id == vehicle_id,
                UserVehicle.user_id == tma_user.user_id,
            )
        )
        if not v:
            raise HTTPException(status_code=404, detail="vehicle not found")
        v.is_available = False
        await session.commit()
    return {"ok": True}


async def _find_reverse_matches(session, vehicle: UserVehicle | None) -> list[MatchedCargo]:
    """Find cargos matching this vehicle's type and location."""
    if not vehicle or not (vehicle.location_city or "").strip():
        return []
    matches = await find_matches_for_vehicle(session, vehicle, limit=10)
    return [MatchedCargo(**_to_payload(match)) for match in matches]
