from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.antifraud.normalize import norm_city
from src.antifraud.rates import route_rate_cache
from src.core.models import ClosedDealStat, RouteRateProfile, RouteRateStats


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])

    q = max(0.0, min(1.0, float(q)))
    sorted_values = sorted(float(v) for v in values)
    pos = (len(sorted_values) - 1) * q
    left = int(math.floor(pos))
    right = int(math.ceil(pos))
    if left == right:
        return sorted_values[left]

    weight = pos - left
    return sorted_values[left] * (1.0 - weight) + sorted_values[right] * weight


async def record_closed_deal(db: AsyncSession, deal: dict[str, Any]) -> ClosedDealStat | None:
    payload = _as_dict(deal)
    route = _as_dict(payload.get("route"))
    price = _as_dict(payload.get("price"))

    from_city_norm = norm_city(route.get("from_city"))
    to_city_norm = norm_city(route.get("to_city"))

    distance_km = _to_float(route.get("distance_km"), 0.0)
    total_rub = _to_float(price.get("total_rub"), 0.0)
    rate_per_km = _to_float(price.get("rate_per_km"), 0.0)

    if rate_per_km <= 0 and distance_km > 0 and total_rub > 0:
        rate_per_km = total_rub / distance_km

    if not from_city_norm or not to_city_norm or distance_km <= 0 or rate_per_km <= 0:
        return None

    row = ClosedDealStat(
        from_city_norm=from_city_norm,
        to_city_norm=to_city_norm,
        distance_km=distance_km,
        rate_per_km=rate_per_km,
        total_rub=total_rub if total_rub > 0 else None,
        closed_at=datetime.utcnow(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def recompute_route_stats(db: AsyncSession) -> dict[str, Any]:
    result = await db.execute(
        select(
            ClosedDealStat.from_city_norm,
            ClosedDealStat.to_city_norm,
            ClosedDealStat.rate_per_km,
        )
    )
    route_rows = list(result.all())

    grouped: dict[tuple[str, str], list[float]] = {}
    for from_city_norm, to_city_norm, rate_per_km in route_rows:
        key = (str(from_city_norm or "").strip(), str(to_city_norm or "").strip())
        if not key[0] or not key[1]:
            continue
        rate_value = _to_float(rate_per_km, 0.0)
        if rate_value <= 0:
            continue
        grouped.setdefault(key, []).append(rate_value)

    updated = 0
    for (from_city_norm, to_city_norm), rates in grouped.items():
        if not rates:
            continue

        sample_size = len(rates)
        mean_rate = float(statistics.fmean(rates))
        median_rate = float(statistics.median(rates))
        std_dev = float(statistics.stdev(rates)) if sample_size >= 2 else 0.0
        p25 = float(_percentile(rates, 0.25))
        p75 = float(_percentile(rates, 0.75))

        stats_result = await db.execute(
            select(RouteRateStats).where(
                RouteRateStats.from_city_norm == from_city_norm,
                RouteRateStats.to_city_norm == to_city_norm,
            )
        )
        stats_row = stats_result.scalar_one_or_none()
        if stats_row is None:
            stats_row = RouteRateStats(
                from_city_norm=from_city_norm,
                to_city_norm=to_city_norm,
            )
            db.add(stats_row)

        stats_row.mean_rate = mean_rate
        stats_row.median_rate = median_rate
        stats_row.std_dev = std_dev
        stats_row.p25 = p25
        stats_row.p75 = p75
        stats_row.sample_size = sample_size
        stats_row.updated_at = datetime.utcnow()

        profile_result = await db.execute(
            select(RouteRateProfile).where(
                RouteRateProfile.from_city_norm == from_city_norm,
                RouteRateProfile.to_city_norm == to_city_norm,
            )
        )
        profile_row = profile_result.scalar_one_or_none()
        if profile_row is None:
            profile_row = RouteRateProfile(
                from_city_norm=from_city_norm,
                to_city_norm=to_city_norm,
            )
            db.add(profile_row)

        learned_min = max(int(round(p25 if p25 > 0 else median_rate)), 1)
        learned_max = max(int(round(p75 if p75 > 0 else mean_rate)), learned_min + 1)

        profile_row.min_rate_per_km = learned_min
        profile_row.max_rate_per_km = learned_max
        profile_row.median_rate_per_km = int(round(median_rate)) if median_rate > 0 else None
        profile_row.samples_count = sample_size
        profile_row.updated_at = datetime.utcnow()

        updated += 1

    await db.commit()
    route_rate_cache.clear()

    return {
        "routes_total": len(grouped),
        "routes_updated": updated,
        "closed_deals_total": len(route_rows),
    }
