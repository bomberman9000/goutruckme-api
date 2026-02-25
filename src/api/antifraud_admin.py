from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select

from src.antifraud.learning import recompute_route_stats
from src.antifraud.rates import route_rate_cache
from src.antifraud.service import get_average_review_duration_ms
from src.core.database import async_session
from src.core.models import ClosedDealStat, CounterpartyList, CounterpartyRiskHistory, RouteRateStats


router = APIRouter(tags=["antifraud-admin"])
logger = logging.getLogger(__name__)

_recompute_task: asyncio.Task[None] | None = None
_recompute_started_at: datetime | None = None
_recompute_finished_at: datetime | None = None
_recompute_last_result: dict[str, Any] | None = None
_recompute_last_error: str | None = None


class RouteStatsItem(BaseModel):
    from_city_norm: str
    to_city_norm: str
    sample_size: int
    mean_rate: float | None
    std_dev: float | None
    updated_at: datetime | None


class CounterpartyStatsItem(BaseModel):
    counterparty_inn: str
    deals_count: int
    high_risk_count: int
    avg_score_total: float
    high_risk_ratio: float


@router.get("/antifraud/admin/stats/routes")
async def antifraud_admin_routes_stats(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    async with async_session() as session:
        result = await session.execute(
            select(RouteRateStats).order_by(RouteRateStats.sample_size.desc(), RouteRateStats.updated_at.desc()).limit(limit)
        )
        rows = list(result.scalars().all())

    items = [
        RouteStatsItem(
            from_city_norm=row.from_city_norm,
            to_city_norm=row.to_city_norm,
            sample_size=int(row.sample_size or 0),
            mean_rate=float(row.mean_rate) if row.mean_rate is not None else None,
            std_dev=float(row.std_dev) if row.std_dev is not None else None,
            updated_at=row.updated_at,
        ).model_dump()
        for row in rows
    ]
    return {"items": items, "count": len(items)}


@router.get("/antifraud/admin/stats/counterparties")
async def antifraud_admin_counterparties_stats(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    high_risk_case = case((CounterpartyRiskHistory.risk_level == "high", 1), else_=0)
    async with async_session() as session:
        result = await session.execute(
            select(
                CounterpartyRiskHistory.counterparty_inn,
                func.count(CounterpartyRiskHistory.id).label("deals_count"),
                func.sum(high_risk_case).label("high_risk_count"),
                func.avg(CounterpartyRiskHistory.score_total).label("avg_score_total"),
            )
            .group_by(CounterpartyRiskHistory.counterparty_inn)
            .order_by(func.sum(high_risk_case).desc(), func.avg(CounterpartyRiskHistory.score_total).desc())
            .limit(limit)
        )
        rows = list(result.all())

    items: list[dict[str, Any]] = []
    for row in rows:
        deals_count = int(row.deals_count or 0)
        high_risk_count = int(row.high_risk_count or 0)
        high_risk_ratio = (high_risk_count / deals_count) if deals_count > 0 else 0.0
        items.append(
            CounterpartyStatsItem(
                counterparty_inn=row.counterparty_inn,
                deals_count=deals_count,
                high_risk_count=high_risk_count,
                avg_score_total=float(row.avg_score_total or 0.0),
                high_risk_ratio=round(high_risk_ratio, 4),
            ).model_dump()
        )
    return {"items": items, "count": len(items)}


@router.get("/antifraud/admin/health")
async def antifraud_admin_health() -> dict[str, Any]:
    async with async_session() as session:
        total_closed_deal_stats = await session.scalar(select(func.count()).select_from(ClosedDealStat))
        total_route_rate_stats = await session.scalar(select(func.count()).select_from(RouteRateStats))
        total_blacklist_entries = await session.scalar(
            select(func.count()).select_from(CounterpartyList).where(CounterpartyList.list_type == "black")
        )

    return {
        "total_closed_deal_stats": int(total_closed_deal_stats or 0),
        "total_route_rate_stats": int(total_route_rate_stats or 0),
        "total_blacklist_entries": int(total_blacklist_entries or 0),
        "cache_size": route_rate_cache.size(),
        "avg_review_duration_ms": round(get_average_review_duration_ms(), 3),
    }


async def _run_recompute_routes_job() -> None:
    global _recompute_finished_at, _recompute_last_error, _recompute_last_result
    try:
        async with async_session() as session:
            _recompute_last_result = await recompute_route_stats(session)
            _recompute_last_error = None
    except Exception as exc:
        _recompute_last_result = None
        _recompute_last_error = str(exc)
        logger.exception("antifraud.admin.recompute_routes.failed")
    finally:
        _recompute_finished_at = datetime.utcnow()


@router.post("/antifraud/admin/jobs/recompute-routes")
async def antifraud_admin_job_recompute_routes() -> dict[str, Any]:
    global _recompute_task, _recompute_started_at
    if _recompute_task is not None and not _recompute_task.done():
        return {
            "status": "running",
            "started_at": _recompute_started_at.isoformat() if _recompute_started_at else None,
        }

    _recompute_started_at = datetime.utcnow()
    _recompute_task = asyncio.create_task(_run_recompute_routes_job())
    return {
        "status": "started",
        "started_at": _recompute_started_at.isoformat(),
    }


@router.get("/antifraud/admin/jobs/recompute-routes")
async def antifraud_admin_job_recompute_routes_status() -> dict[str, Any]:
    running = _recompute_task is not None and not _recompute_task.done()
    return {
        "status": "running" if running else "idle",
        "started_at": _recompute_started_at.isoformat() if _recompute_started_at else None,
        "finished_at": _recompute_finished_at.isoformat() if _recompute_finished_at else None,
        "last_result": _recompute_last_result,
        "last_error": _recompute_last_error,
    }
