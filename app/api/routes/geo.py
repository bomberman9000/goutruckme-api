from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import City
from app.services.geo import normalize_city_name

router = APIRouter()

_SUPPORTED_COUNTRIES = {"RU", "BY", "UZ", "KG", "KZ"}
_INVALID_CITY_TOKENS = (
    "sanatorium",
    "beach",
    "hotel",
    "resort",
    "tuman",
    "district",
    "oblast",
    "область",
    "район",
    "полигон",
    "poligoni",
    "chiqindi",
)

_CACHE_TTL_SEC = 600
_CACHE_MAX_ITEMS = 1024
_city_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_city_cache_lock = threading.Lock()


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    now = time.time()
    with _city_cache_lock:
        item = _city_cache.get(key)
        if not item:
            return None
        expires_at, payload = item
        if expires_at < now:
            _city_cache.pop(key, None)
            return None
        return payload


def _cache_set(key: str, payload: list[dict[str, Any]]) -> None:
    now = time.time()
    with _city_cache_lock:
        if len(_city_cache) >= _CACHE_MAX_ITEMS:
            stale_keys = [k for k, (exp, _) in _city_cache.items() if exp < now]
            for stale_key in stale_keys:
                _city_cache.pop(stale_key, None)
            if len(_city_cache) >= _CACHE_MAX_ITEMS:
                oldest_key = min(_city_cache, key=lambda k: _city_cache[k][0])
                _city_cache.pop(oldest_key, None)
        _city_cache[key] = (now + _CACHE_TTL_SEC, payload)


def _is_supported_catalog_city(city: City) -> bool:
    country = str(city.country or "").strip().upper()
    if country and country not in _SUPPORTED_COUNTRIES:
        return False

    normalized_name = normalize_city_name(city.name)
    if not normalized_name:
        return False

    if any(token in normalized_name for token in _INVALID_CITY_TOKENS):
        return False

    return True


@router.get("/geo/cities")
def search_cities(
    q: str = Query("", description="Поисковая строка"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    normalized_q = normalize_city_name(q)
    if len(normalized_q) < 2:
        return []

    cache_key = f"{normalized_q}|{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    prefix_pattern = f"{normalized_q}%"
    contains_pattern = f"%{normalized_q}%"

    prefix_rank = case((City.name_norm.like(prefix_pattern), 0), else_=1)
    length_penalty = func.length(City.name_norm)
    population_score = func.coalesce(City.population, 0)

    rows = (
        db.query(City)
        .filter(City.name_norm.like(contains_pattern))
        .order_by(prefix_rank.asc(), length_penalty.asc(), population_score.desc(), City.name.asc())
        .limit(min(limit * 10, 100))
        .all()
    )

    payload = [
        {
            "id": city.id,
            "name": city.name,
            "region": city.region,
            "country": city.country,
        }
        for city in rows
        if _is_supported_catalog_city(city)
    ][:limit]
    _cache_set(cache_key, payload)
    return payload
