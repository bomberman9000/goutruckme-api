from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CargoStatus, Load
from app.services.load_public import build_public_load_base, is_public_load


_CACHE_LOCK = Lock()
_SEARCH_WARMUP_CACHE: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _cache_ttl() -> int:
    return max(60, int(settings.SYNC_WARMUP_TTL_SEC))


def _cleanup_locked(now: datetime) -> None:
    expired_keys = [
        key
        for key, value in _SEARCH_WARMUP_CACHE.items()
        if isinstance(value.get("expires_at"), datetime) and value["expires_at"] <= now
    ]
    for key in expired_keys:
        _SEARCH_WARMUP_CACHE.pop(key, None)


def build_load_recommendations(
    db: Session,
    *,
    from_city: str | None,
    to_city: str | None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    query = db.query(Load).filter(Load.status == CargoStatus.active.value)

    if from_city:
        query = query.filter(Load.from_city.ilike(f"%{from_city.strip()}%"))
    if to_city:
        query = query.filter(Load.to_city.ilike(f"%{to_city.strip()}%"))

    loads = query.order_by(Load.created_at.desc()).limit(max(1, min(limit, 50))).all()
    return [
        {
            "id": int(base["id"]),
            "from_city": base["from_city"],
            "to_city": base["to_city"],
            "price": float(base["price"]),
            "distance_km": float(base["distance_km"]) if base["distance_km"] is not None else None,
            "rate_per_km": float(base["rate_per_km"]) if base["rate_per_km"] is not None else None,
        }
        for load in loads
        if is_public_load(load)
        for base in [build_public_load_base(load)]
    ]


def warmup_search_context(
    db: Session,
    *,
    search_id: str,
    user_id: int | None,
    from_city: str | None,
    to_city: str | None,
    query_text: str | None,
) -> dict[str, Any]:
    now = _utcnow()
    recommendations = build_load_recommendations(db, from_city=from_city, to_city=to_city, limit=20)
    record = {
        "search_id": search_id,
        "user_id": user_id,
        "from_city": from_city,
        "to_city": to_city,
        "query": query_text,
        "recommendations": recommendations,
        "updated_at": now,
        "expires_at": now + timedelta(seconds=_cache_ttl()),
    }

    with _CACHE_LOCK:
        _cleanup_locked(now)
        _SEARCH_WARMUP_CACHE[search_id] = record

    return record


def get_warmup_context(search_id: str | None) -> dict[str, Any] | None:
    if not search_id:
        return None

    now = _utcnow()
    with _CACHE_LOCK:
        _cleanup_locked(now)
        data = _SEARCH_WARMUP_CACHE.get(search_id)
        if not data:
            return None
        if data["expires_at"] <= now:
            _SEARCH_WARMUP_CACHE.pop(search_id, None)
            return None
        return data


def warmup_cache_size() -> int:
    with _CACHE_LOCK:
        _cleanup_locked(_utcnow())
        return len(_SEARCH_WARMUP_CACHE)
