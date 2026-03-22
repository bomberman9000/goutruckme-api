from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import City
from app.services.geo import is_supported_city, normalize_city_name

router = APIRouter()

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
    return is_supported_city(city)


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


# ── /api/map/data ─────────────────────────────────────────────────────────────

@router.get("/map/data")
def get_map_data(
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Public map endpoint — cargos + vehicles with coordinates."""
    from app.models.models import Load, Vehicle, City
    from sqlalchemy.orm import aliased
    from sqlalchemy import and_, or_, func

    # ── Cargos — use pickup_lat/lon directly, fallback to City join ──
    FromCity = aliased(City)
    ToCity   = aliased(City)

    loads = (
        db.query(
            Load.id,
            Load.from_city,
            Load.to_city,
            Load.weight_t,
            Load.total_price,
            Load.required_body_type,
            Load.status,
            Load.pickup_lat,
            Load.pickup_lon,
            Load.delivery_lat,
            Load.delivery_lon,
            FromCity.lat.label("city_from_lat"),
            FromCity.lon.label("city_from_lon"),
            ToCity.lat.label("city_to_lat"),
            ToCity.lon.label("city_to_lon"),
        )
        .outerjoin(FromCity, Load.from_city_id == FromCity.id)
        .outerjoin(ToCity,   Load.to_city_id   == ToCity.id)
        .filter(Load.status.in_(["new", "active", "pending"]))
        .filter(
            or_(
                Load.pickup_lat.isnot(None),
                FromCity.lat.isnot(None),
            )
        )
        .order_by(Load.id.desc())
        .limit(limit)
        .all()
    )

    cargos = []
    for row in loads:
        from_lat = row.pickup_lat or row.city_from_lat
        from_lon = row.pickup_lon or row.city_from_lon
        if not from_lat or not from_lon:
            continue
        to_lat = row.delivery_lat or row.city_to_lat
        to_lon = row.delivery_lon or row.city_to_lon
        cargos.append({
            "id": row.id,
            "from_city": row.from_city or "",
            "to_city":   row.to_city   or "",
            "weight_t":  row.weight_t,
            "price":     row.total_price,
            "body_type": row.required_body_type or "",
            "status":    str(row.status or ""),
            "from_lat":  from_lat,
            "from_lon":  from_lon,
            "to_lat":    to_lat,
            "to_lon":    to_lon,
        })

    # ── Vehicles ──
    VCity = aliased(City)
    vehicles_q = (
        db.query(
            Vehicle.id,
            Vehicle.body_type,
            Vehicle.capacity_tons,
            Vehicle.location_city,
            Vehicle.status,
            VCity.lat.label("lat"),
            VCity.lon.label("lon"),
        )
        .outerjoin(VCity, Vehicle.city_id == VCity.id)
        .filter(Vehicle.status.in_(["active", "available", "free"]))
        .order_by(Vehicle.id.desc())
        .limit(500)
        .all()
    )

    vehicles = []
    for v in vehicles_q:
        if v.lat is None or v.lon is None:
            continue
        vehicles.append({
            "id":            v.id,
            "body_type":     v.body_type or "",
            "capacity_tons": v.capacity_tons,
            "city":          v.location_city or "",
            "available":     v.status in ("active", "available", "free"),
            "lat":           v.lat,
            "lon":           v.lon,
        })

    # ── Live drivers from tg-bot ──
    live_drivers = []
    try:
        import httpx
        from app.core.config import settings
        resp = httpx.get(
            f"{settings.TG_BOT_INTERNAL_URL}/api/webapp/live-trucks",
            timeout=2,
        )
        if resp.status_code == 200:
            live_drivers = resp.json().get("trucks", [])
    except Exception:
        pass

    return {"cargos": cargos, "vehicles": vehicles, "live_drivers": live_drivers}
