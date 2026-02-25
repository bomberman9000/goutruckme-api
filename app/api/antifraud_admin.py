from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import case, desc, func
from sqlalchemy.orm import Session

from app.antifraud.graph import component_cache
from app.antifraud.rates import route_rate_cache
from app.antifraud.learning import recompute_route_stats
from app.antifraud.service import get_average_review_duration_ms
from app.db.database import get_db
from app.models.models import ClosedDealStat, CounterpartyList, CounterpartyRiskHistory, RouteRateStats


router = APIRouter()


@router.get("/antifraud/admin/stats/routes")
async def antifraud_admin_routes_stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = (
        db.query(RouteRateStats)
        .order_by(RouteRateStats.sample_size.desc(), RouteRateStats.updated_at.desc())
        .limit(20)
        .all()
    )

    items = [
        {
            "from_city_norm": row.from_city_norm,
            "to_city_norm": row.to_city_norm,
            "mean_rate": float(row.mean_rate) if row.mean_rate is not None else None,
            "median_rate": float(row.median_rate) if row.median_rate is not None else None,
            "std_dev": float(row.std_dev) if row.std_dev is not None else None,
            "sample_size": int(row.sample_size or 0),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]

    return {"items": items}


@router.get("/antifraud/admin/stats/counterparties")
async def antifraud_admin_counterparties_stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    high_case = case((CounterpartyRiskHistory.risk_level == "high", 1), else_=0)

    rows = (
        db.query(
            CounterpartyRiskHistory.counterparty_inn.label("counterparty_inn"),
            func.count(CounterpartyRiskHistory.id).label("deals_total"),
            func.avg(CounterpartyRiskHistory.score_total).label("avg_score"),
            func.sum(high_case).label("high_count"),
        )
        .group_by(CounterpartyRiskHistory.counterparty_inn)
        .order_by(desc("high_count"), desc("avg_score"))
        .limit(20)
        .all()
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        deals_total = int(row.deals_total or 0)
        high_count = int(row.high_count or 0)
        ratio = (high_count / deals_total) if deals_total > 0 else 0.0
        items.append(
            {
                "counterparty_inn": row.counterparty_inn,
                "deals_total": deals_total,
                "avg_score": round(float(row.avg_score or 0.0), 3),
                "high_count": high_count,
                "high_risk_ratio": round(ratio, 4),
            }
        )

    return {"items": items}


@router.get("/antifraud/admin/health")
async def antifraud_admin_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    closed_deals_total = int(db.query(func.count(ClosedDealStat.id)).scalar() or 0)
    route_rate_stats_total = int(db.query(func.count(RouteRateStats.id)).scalar() or 0)
    blacklist_entries_total = int(
        db.query(func.count(CounterpartyList.id)).filter(CounterpartyList.list_type == "black").scalar() or 0
    )

    return {
        "total_closed_deal_stats": closed_deals_total,
        "total_route_rate_stats": route_rate_stats_total,
        "total_blacklist_entries": blacklist_entries_total,
        "cache_size": int(route_rate_cache.size() + component_cache.size()),
        "route_rate_cache_size": route_rate_cache.size(),
        "component_cache_size": component_cache.size(),
        "avg_review_duration_ms": round(get_average_review_duration_ms(), 3),
    }


@router.post("/antifraud/admin/jobs/recompute-routes")
async def antifraud_admin_recompute_routes(db: Session = Depends(get_db)) -> dict[str, Any]:
    return await recompute_route_stats(db)
