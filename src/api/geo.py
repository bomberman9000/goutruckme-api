from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.cities import city_directory, resolve_city
from src.core.database import async_session
from src.core.geo import city_coords
from src.core.models import DriverTracking
from src.core.services.geo_service import get_geo_service

router = APIRouter(tags=["geo"])


class CitySuggestion(BaseModel):
    name: str
    full_name: str
    lat: float
    lon: float
    source: str


class CitySuggestionResponse(BaseModel):
    items: list[CitySuggestion] = Field(default_factory=list)
    limit: int


class CityResolveResponse(BaseModel):
    resolved: str | None = None
    suggestions: list[str] = Field(default_factory=list)


@router.get("/api/v1/geo/cities", response_model=CitySuggestionResponse)
async def suggest_cities(
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=5, ge=1, le=10),
) -> CitySuggestionResponse:
    service = get_geo_service()
    items = await service.suggest_cities(q, limit=limit)
    return CitySuggestionResponse(items=[CitySuggestion(**item) for item in items], limit=limit)


@router.get("/api/v1/geo/cities/directory", response_model=CitySuggestionResponse)
async def list_directory_cities(
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=10, ge=1, le=100),
) -> CitySuggestionResponse:
    items: list[CitySuggestion] = []
    names = city_directory(q, limit=limit if q.strip() else 1000)
    for name in names:
        coords = city_coords(name)
        if not coords:
            continue
        items.append(
            CitySuggestion(
                name=name,
                full_name=name,
                lat=float(coords[0]),
                lon=float(coords[1]),
                source="directory",
            )
        )
        if len(items) >= limit:
            break
    return CitySuggestionResponse(items=items, limit=limit)


@router.get("/api/v1/geo/live-drivers")
async def live_drivers(stale_minutes: int = Query(default=30, ge=1, le=120)):
    """Active drivers who shared live location via bot. Used by the map."""
    cutoff = datetime.utcnow() - timedelta(minutes=stale_minutes)
    async with async_session() as session:
        rows = (await session.execute(
            select(DriverTracking).where(
                DriverTracking.is_active.is_(True),
                DriverTracking.lat.isnot(None),
                DriverTracking.updated_at >= cutoff,
            )
        )).scalars().all()

    return {
        "drivers": [
            {
                "user_id": d.user_id,
                "name": d.full_name or "Водитель",
                "lat": d.lat,
                "lon": d.lon,
                "updated_at": d.updated_at.isoformat(),
            }
            for d in rows
        ],
        "total": len(rows),
    }


@router.get("/api/v1/geo/cities/resolve", response_model=CityResolveResponse)
async def resolve_directory_city(
    name: str = Query(min_length=2, max_length=160),
) -> CityResolveResponse:
    resolved, suggestions = resolve_city(name)
    return CityResolveResponse(resolved=resolved, suggestions=suggestions)
