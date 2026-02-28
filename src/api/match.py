from __future__ import annotations

from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import Cargo, UserVehicle
from src.core.services.matching_engine import (
    build_match_summary,
    find_matches_for_cargo,
    find_matches_for_vehicle,
)

router = APIRouter(tags=["match"])


def _to_payload(value: object) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError("unsupported match payload")


class VehicleMatchItem(BaseModel):
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    weight_t: float | None
    rate_rub: int | None
    rate_per_km: float | None
    load_date: str | None
    is_hot_deal: bool = False
    freshness: str | None
    match_score: int
    distance_to_pickup_km: int | None = None
    match_reasons: list[str] = Field(default_factory=list)
    verified_payment: bool = False


class VehicleMatchResponse(BaseModel):
    vehicle_id: int
    location_city: str | None
    matched: list[VehicleMatchItem] = Field(default_factory=list)
    total: int = 0


class CargoMatchVehicleItem(BaseModel):
    vehicle_id: int
    body_type: str
    capacity_tons: float
    location_city: str | None
    is_available: bool
    plate_number: str | None
    match_score: int
    distance_to_pickup_km: int | None = None
    match_reasons: list[str] = Field(default_factory=list)


class CargoMatchResponse(BaseModel):
    cargo_id: int
    matched: list[CargoMatchVehicleItem] = Field(default_factory=list)
    total: int = 0


class MatchSummaryResponse(BaseModel):
    vehicle_match_count: int
    cargo_match_count: int
    best_vehicle_match_score: int
    best_cargo_match_score: int


@router.get("/api/v1/match/vehicle/{vehicle_id}", response_model=VehicleMatchResponse)
async def get_vehicle_matches(
    vehicle_id: int,
    limit: int = Query(default=10, ge=1, le=20),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> VehicleMatchResponse:
    async with async_session() as session:
        vehicle = await session.scalar(
            select(UserVehicle).where(
                UserVehicle.id == vehicle_id,
                UserVehicle.user_id == tma_user.user_id,
            )
        )
        if not vehicle:
            raise HTTPException(status_code=404, detail="vehicle not found")
        matches = await find_matches_for_vehicle(session, vehicle, limit=limit)
    return VehicleMatchResponse(
        vehicle_id=int(vehicle.id),
        location_city=vehicle.location_city,
        matched=[VehicleMatchItem(**_to_payload(match)) for match in matches],
        total=len(matches),
    )


@router.get("/api/v1/match/cargo/{cargo_id}", response_model=CargoMatchResponse)
async def get_cargo_matches(
    cargo_id: int,
    limit: int = Query(default=10, ge=1, le=20),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> CargoMatchResponse:
    async with async_session() as session:
        cargo = await session.scalar(
            select(Cargo).where(
                Cargo.id == cargo_id,
                Cargo.owner_id == tma_user.user_id,
            )
        )
        if not cargo:
            raise HTTPException(status_code=404, detail="cargo not found")
        matches = await find_matches_for_cargo(session, cargo, limit=limit)
    return CargoMatchResponse(
        cargo_id=int(cargo.id),
        matched=[CargoMatchVehicleItem(**_to_payload(match)) for match in matches],
        total=len(matches),
    )


@router.get("/api/v1/match/summary", response_model=MatchSummaryResponse)
async def get_match_summary(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> MatchSummaryResponse:
    async with async_session() as session:
        summary = await build_match_summary(session, tma_user.user_id)
    return MatchSummaryResponse(**_to_payload(summary))
