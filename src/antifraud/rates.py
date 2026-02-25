from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.antifraud.normalize import norm_city
from src.core.config import settings
from src.core.models import RouteRateProfile, RouteRateStats


@dataclass
class _CacheEntry:
    expires_at: float
    stats_version_hash: str
    profile: dict[str, Any]


class RouteRateCache:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], _CacheEntry] = {}

    def get(self, from_city_norm: str, to_city_norm: str, stats_version_hash: str) -> dict[str, Any] | None:
        key = (from_city_norm, to_city_norm)
        entry = self._store.get(key)
        if not entry:
            return None
        if time.time() >= entry.expires_at:
            self._store.pop(key, None)
            return None
        if entry.stats_version_hash != stats_version_hash:
            self._store.pop(key, None)
            return None
        profile = dict(entry.profile)
        profile["cache"] = "hit"
        return profile

    def set(self, from_city_norm: str, to_city_norm: str, stats_version_hash: str, profile: dict[str, Any]) -> None:
        ttl = max(1, int(settings.route_rate_cache_ttl_sec))
        key = (from_city_norm, to_city_norm)
        self._store[key] = _CacheEntry(
            expires_at=time.time() + ttl,
            stats_version_hash=stats_version_hash,
            profile=dict(profile),
        )

    def size(self) -> int:
        now = time.time()
        expired = [k for k, v in self._store.items() if now >= v.expires_at]
        for key in expired:
            self._store.pop(key, None)
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


rate_cache = RouteRateCache()
route_rate_cache = rate_cache


def _parse_tier_map() -> list[dict[str, Any]]:
    raw = settings.route_rate_tier_map_json
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    tiers: list[dict[str, Any]] = []
    for item in parsed.values():
        if not isinstance(item, dict):
            continue
        try:
            tiers.append(
                {
                    "max_km": float(item.get("max_km", 0)),
                    "min": int(item.get("min", settings.route_rate_fallback_min)),
                    "max": int(item.get("max", settings.route_rate_fallback_max)),
                }
            )
        except (TypeError, ValueError):
            continue
    tiers.sort(key=lambda item: item["max_km"])
    return tiers


def _tier_fallback(distance_km: float) -> dict[str, int]:
    tiers = _parse_tier_map()
    if tiers:
        for tier in tiers:
            if distance_km <= tier["max_km"]:
                return {"min_rate_per_km": tier["min"], "max_rate_per_km": tier["max"]}
        return {"min_rate_per_km": tiers[-1]["min"], "max_rate_per_km": tiers[-1]["max"]}
    return {
        "min_rate_per_km": int(settings.route_rate_fallback_min),
        "max_rate_per_km": int(settings.route_rate_fallback_max),
    }


def _serialize_stats(row: RouteRateStats | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "mean_rate": float(row.mean_rate) if row.mean_rate is not None else None,
        "median_rate": float(row.median_rate) if row.median_rate is not None else None,
        "std_dev": float(row.std_dev) if row.std_dev is not None else None,
        "p25": float(row.p25) if row.p25 is not None else None,
        "p75": float(row.p75) if row.p75 is not None else None,
        "sample_size": int(row.sample_size or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _stats_version_hash(row: RouteRateStats | None) -> str:
    if row is None:
        return "none"
    stamp = row.updated_at.isoformat() if row.updated_at else "none"
    return f"{stamp}:{int(row.sample_size or 0)}:{float(row.mean_rate or 0.0):.6f}:{float(row.std_dev or 0.0):.6f}"


async def get_route_rate_profile(
    db: AsyncSession,
    *,
    from_city: str | None,
    to_city: str | None,
    distance_km: float | int | None,
) -> dict[str, Any]:
    from_city_norm = norm_city(from_city)
    to_city_norm = norm_city(to_city)
    distance = float(distance_km or 0.0)

    stats_row = None
    if from_city_norm and to_city_norm:
        stats_result = await db.execute(
            select(RouteRateStats).where(
                RouteRateStats.from_city_norm == from_city_norm,
                RouteRateStats.to_city_norm == to_city_norm,
            )
        )
        stats_row = stats_result.scalar_one_or_none()

    stats_hash = _stats_version_hash(stats_row)
    cached = rate_cache.get(from_city_norm, to_city_norm, stats_hash)
    if cached is not None:
        return cached

    row = None
    if from_city_norm and to_city_norm:
        result = await db.execute(
            select(RouteRateProfile).where(
                RouteRateProfile.from_city_norm == from_city_norm,
                RouteRateProfile.to_city_norm == to_city_norm,
            )
        )
        row = result.scalar_one_or_none()

    if row is not None:
        profile = {
            "from_city_norm": from_city_norm,
            "to_city_norm": to_city_norm,
            "min_rate_per_km": int(row.min_rate_per_km),
            "max_rate_per_km": int(row.max_rate_per_km),
            "source": "db",
            "cache": "miss",
            "stats": _serialize_stats(stats_row),
        }
    else:
        fallback = _tier_fallback(distance)
        profile = {
            "from_city_norm": from_city_norm,
            "to_city_norm": to_city_norm,
            "min_rate_per_km": int(fallback["min_rate_per_km"]),
            "max_rate_per_km": int(fallback["max_rate_per_km"]),
            "source": "tier_fallback",
            "cache": "miss",
            "stats": _serialize_stats(stats_row),
        }

    rate_cache.set(from_city_norm, to_city_norm, stats_hash, profile)
    return profile
