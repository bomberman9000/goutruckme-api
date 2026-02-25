"""Fleet Manager API — vehicle registration and reverse matching.

Carriers register their vehicles and press "I'm free in Kazan" —
the system finds best matching cargos from parser + platform.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, or_

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import ParserIngestEvent, UserVehicle

router = APIRouter(tags=["fleet"])


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
        await session.commit()
        await session.refresh(v)

    matches = await _find_reverse_matches(v)
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


async def _find_reverse_matches(vehicle: UserVehicle) -> list[MatchedCargo]:
    """Find cargos matching this vehicle's type and location."""
    from datetime import datetime

    city = (vehicle.location_city or "").strip()
    if not city:
        return []

    async with async_session() as session:
        stmt = (
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.is_spam.is_(False),
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.from_city.ilike(f"%{city}%"),
            )
        )

        if vehicle.body_type:
            stmt = stmt.where(
                or_(
                    ParserIngestEvent.body_type.ilike(f"%{vehicle.body_type}%"),
                    ParserIngestEvent.body_type.is_(None),
                )
            )

        rows = (
            await session.execute(stmt.order_by(ParserIngestEvent.id.desc()).limit(20))
        ).scalars().all()

    def _fresh(created_at) -> str:
        now = datetime.utcnow()
        ca = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
        m = int((now - ca).total_seconds() / 60)
        if m < 60:
            return f"{m}м"
        h = m // 60
        return f"{h}ч" if h < 24 else f"{h // 24}д"

    results = []
    for r in rows:
        if vehicle.capacity_tons and r.weight_t and r.weight_t > vehicle.capacity_tons:
            continue
        rpk = None
        if r.rate_rub and r.from_lat and r.to_lat:
            from src.core.geo import haversine_km
            dist = haversine_km(r.from_lat, r.from_lon, r.to_lat, r.to_lon)
            if dist > 10:
                rpk = round(r.rate_rub / dist, 1)
        results.append(MatchedCargo(
            id=r.id, from_city=r.from_city, to_city=r.to_city,
            body_type=r.body_type, weight_t=r.weight_t, rate_rub=r.rate_rub,
            rate_per_km=rpk, load_date=r.load_date, is_hot_deal=r.is_hot_deal,
            freshness=_fresh(r.created_at),
        ))

    return results[:10]
