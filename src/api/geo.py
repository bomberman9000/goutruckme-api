from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

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


@router.get("/api/v1/geo/cities", response_model=CitySuggestionResponse)
async def suggest_cities(
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=5, ge=1, le=10),
) -> CitySuggestionResponse:
    service = get_geo_service()
    items = await service.suggest_cities(q, limit=limit)
    return CitySuggestionResponse(items=[CitySuggestion(**item) for item in items], limit=limit)
