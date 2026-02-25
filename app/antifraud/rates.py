from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.antifraud.normalize import norm_city
from app.core.config import settings
from app.models.models import RouteRateProfile, RouteRateStats


class RouteRateCache:
    def __init__(self) -> None:
        self._storage: dict[tuple[str, str], tuple[float, str, dict[str, Any]]] = {}

    def get(self, key: tuple[str, str], stats_version_hash: str) -> dict[str, Any] | None:
        now = time.time()
        payload = self._storage.get(key)
        if not payload:
            return None

        expires_at, cached_version_hash, profile = payload
        if expires_at <= now or cached_version_hash != stats_version_hash:
            self._storage.pop(key, None)
            return None

        return dict(profile)

    def set(self, key: tuple[str, str], stats_version_hash: str, profile: dict[str, Any], ttl_sec: int) -> None:
        expires_at = time.time() + max(int(ttl_sec), 1)
        self._storage[key] = (expires_at, stats_version_hash, dict(profile))

    def clear(self) -> None:
        self._storage.clear()

    def size(self) -> int:
        return len(self._storage)


route_rate_cache = RouteRateCache()


def _safe_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_tier_map() -> list[dict[str, int]]:
    raw = str(settings.ROUTE_RATE_TIER_MAP_JSON or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []

    if not isinstance(parsed, dict):
        return []

    tiers: list[dict[str, int]] = []
    for _, tier in parsed.items():
        if not isinstance(tier, dict):
            continue
        max_km = _safe_int(tier.get("max_km"), -1)
        min_rate = _safe_int(tier.get("min"), -1)
        max_rate = _safe_int(tier.get("max"), -1)
        if max_km < 0 or min_rate <= 0 or max_rate <= 0:
            continue
        tiers.append(
            {
                "max_km": max_km,
                "min": min_rate,
                "max": max_rate,
            }
        )

    tiers.sort(key=lambda item: item["max_km"])
    return tiers


def _fallback_profile(from_city_norm: str, to_city_norm: str, distance_km: float) -> dict[str, Any]:
    tiers = _parse_tier_map()
    min_rate = int(settings.ROUTE_RATE_FALLBACK_MIN)
    max_rate = int(settings.ROUTE_RATE_FALLBACK_MAX)

    if tiers:
        selected = tiers[-1]
        for tier in tiers:
            if distance_km <= float(tier["max_km"]):
                selected = tier
                break
        min_rate = int(selected["min"])
        max_rate = int(selected["max"])

    return {
        "from_city_norm": from_city_norm,
        "to_city_norm": to_city_norm,
        "min_rate_per_km": min_rate,
        "max_rate_per_km": max_rate,
        "source": "tier_fallback",
    }


def _serialize_stats(row: RouteRateStats | None) -> dict[str, Any]:
    if not row:
        return {
            "mean_rate": None,
            "median_rate": None,
            "std_dev": None,
            "p25": None,
            "p75": None,
            "sample_size": 0,
            "updated_at": None,
        }

    updated_at = row.updated_at
    if isinstance(updated_at, datetime):
        updated_at_iso = updated_at.isoformat()
    else:
        updated_at_iso = None

    return {
        "mean_rate": float(row.mean_rate) if row.mean_rate is not None else None,
        "median_rate": float(row.median_rate) if row.median_rate is not None else None,
        "std_dev": float(row.std_dev) if row.std_dev is not None else None,
        "p25": float(row.p25) if row.p25 is not None else None,
        "p75": float(row.p75) if row.p75 is not None else None,
        "sample_size": int(row.sample_size or 0),
        "updated_at": updated_at_iso,
    }


def _stats_version_hash(row: RouteRateStats | None) -> str:
    if not row:
        return "none"

    updated_at = row.updated_at.isoformat() if isinstance(row.updated_at, datetime) else "none"
    return ":".join(
        [
            updated_at,
            str(int(row.sample_size or 0)),
            f"{float(row.mean_rate or 0.0):.6f}",
            f"{float(row.std_dev or 0.0):.6f}",
        ]
    )


async def get_route_rate_profile(
    db: Session,
    from_city: str | None,
    to_city: str | None,
    distance_km: float | int | None,
) -> dict[str, Any]:
    from_city_norm = norm_city(from_city)
    to_city_norm = norm_city(to_city)
    key = (from_city_norm, to_city_norm)

    stats_row = (
        db.query(RouteRateStats)
        .filter(
            RouteRateStats.from_city_norm == from_city_norm,
            RouteRateStats.to_city_norm == to_city_norm,
        )
        .first()
    )
    stats_payload = _serialize_stats(stats_row)
    version_hash = _stats_version_hash(stats_row)

    cached = route_rate_cache.get(key, version_hash)
    if cached:
        cached["cache"] = "hit"
        return cached

    row = (
        db.query(RouteRateProfile)
        .filter(
            RouteRateProfile.from_city_norm == from_city_norm,
            RouteRateProfile.to_city_norm == to_city_norm,
        )
        .first()
    )

    if row:
        profile = {
            "from_city_norm": from_city_norm,
            "to_city_norm": to_city_norm,
            "min_rate_per_km": _safe_int(row.min_rate_per_km, int(settings.ROUTE_RATE_FALLBACK_MIN)),
            "max_rate_per_km": _safe_int(row.max_rate_per_km, int(settings.ROUTE_RATE_FALLBACK_MAX)),
            "source": "db",
            "stats": stats_payload,
        }
    else:
        profile = _fallback_profile(
            from_city_norm=from_city_norm,
            to_city_norm=to_city_norm,
            distance_km=_safe_float(distance_km, 0.0),
        )
        profile["stats"] = stats_payload

    route_rate_cache.set(key, version_hash, profile, int(settings.ROUTE_RATE_CACHE_TTL_SEC))
    payload = dict(profile)
    payload["cache"] = "miss"
    return payload
