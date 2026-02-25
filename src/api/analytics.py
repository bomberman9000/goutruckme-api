"""Route price analytics API.

Provides market intelligence: average/min/max rates per route,
rate-per-km calculations, price trends, and demand heatmaps.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from src.core.cache import get_cached, set_cached
from src.core.database import async_session
from src.core.geo import city_coords, haversine_km
from src.core.models import ParserIngestEvent
from fastapi import Response


router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


class RoutePriceStats(BaseModel):
    from_city: str
    to_city: str
    count: int
    avg_rate: int
    min_rate: int
    max_rate: int
    avg_weight: float | None = None
    distance_km: int | None = None
    avg_rate_per_km: float | None = None
    min_rate_per_km: float | None = None
    max_rate_per_km: float | None = None
    period_days: int


class TopRouteItem(BaseModel):
    from_city: str
    to_city: str
    count: int
    avg_rate: int
    avg_rate_per_km: float | None = None


class TopRoutesResponse(BaseModel):
    routes: list[TopRouteItem] = Field(default_factory=list)
    period_days: int


class DemandPoint(BaseModel):
    city: str
    lat: float
    lon: float
    cargo_count: int
    point_type: str


class DemandResponse(BaseModel):
    points: list[DemandPoint] = Field(default_factory=list)
    period_days: int


class PriceTrendPoint(BaseModel):
    date: str
    avg_rate: int
    count: int


class PriceTrendResponse(BaseModel):
    from_city: str
    to_city: str
    points: list[PriceTrendPoint] = Field(default_factory=list)
    period_days: int


def _calc_distance(from_city: str, to_city: str) -> int | None:
    fc = city_coords(from_city)
    tc = city_coords(to_city)
    if not fc or not tc:
        return None
    return int(haversine_km(fc[0], fc[1], tc[0], tc[1]))


@router.get("/route", response_model=RoutePriceStats)
async def route_price_stats(
    request: Request,
    from_city: str = Query(..., min_length=1),
    to_city: str = Query(..., min_length=1),
    days: int = Query(default=30, ge=1, le=365),
):
    """Price analytics for a specific route."""
    from src.core.rate_limit import check_rate_limit
    await check_rate_limit(request, limit=30, window_sec=60)

    cache_params = {"from": from_city, "to": to_city, "days": days}
    cached = await get_cached("analytics_route", cache_params)
    if cached:
        return Response(content=cached, media_type="application/json")

    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        row = (
            await session.execute(
                select(
                    func.count().label("cnt"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg_rate"),
                    func.min(ParserIngestEvent.rate_rub).label("min_rate"),
                    func.max(ParserIngestEvent.rate_rub).label("max_rate"),
                    func.avg(ParserIngestEvent.weight_t).label("avg_weight"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.rate_rub.isnot(None),
                    ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"),
                    ParserIngestEvent.to_city.ilike(f"%{to_city.strip()}%"),
                    ParserIngestEvent.created_at >= cutoff,
                )
            )
        ).one()

    cnt = row.cnt or 0
    avg_rate = int(row.avg_rate or 0)
    min_rate = int(row.min_rate or 0)
    max_rate = int(row.max_rate or 0)
    avg_weight = round(float(row.avg_weight or 0), 1) if row.avg_weight else None
    distance = _calc_distance(from_city.strip(), to_city.strip())

    result = RoutePriceStats(
        from_city=from_city.strip(),
        to_city=to_city.strip(),
        count=cnt,
        avg_rate=avg_rate,
        min_rate=min_rate,
        max_rate=max_rate,
        avg_weight=avg_weight,
        distance_km=distance,
        avg_rate_per_km=round(avg_rate / distance, 1) if distance and avg_rate else None,
        min_rate_per_km=round(min_rate / distance, 1) if distance and min_rate else None,
        max_rate_per_km=round(max_rate / distance, 1) if distance and max_rate else None,
        period_days=days,
    )
    serialized = result.model_dump_json()
    await set_cached("analytics_route", cache_params, serialized, ttl=300)
    return Response(content=serialized, media_type="application/json")


@router.get("/top-routes", response_model=TopRoutesResponse)
async def top_routes(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Most popular routes by cargo count."""
    cache_params = {"days": days, "limit": limit}
    cached = await get_cached("analytics_top", cache_params)
    if cached:
        return Response(content=cached, media_type="application/json")

    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    ParserIngestEvent.from_city,
                    ParserIngestEvent.to_city,
                    func.count().label("cnt"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg_rate"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.from_city.isnot(None),
                    ParserIngestEvent.to_city.isnot(None),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .group_by(ParserIngestEvent.from_city, ParserIngestEvent.to_city)
                .order_by(func.count().desc())
                .limit(limit)
            )
        ).all()

    items = []
    for r in rows:
        avg = int(r.avg_rate or 0)
        dist = _calc_distance(r.from_city, r.to_city)
        items.append(TopRouteItem(
            from_city=r.from_city,
            to_city=r.to_city,
            count=r.cnt,
            avg_rate=avg,
            avg_rate_per_km=round(avg / dist, 1) if dist and avg else None,
        ))

    result = TopRoutesResponse(routes=items, period_days=days)
    serialized = result.model_dump_json()
    await set_cached("analytics_top", cache_params, serialized, ttl=300)
    return Response(content=serialized, media_type="application/json")


@router.get("/price-trend", response_model=PriceTrendResponse)
async def price_trend(
    from_city: str = Query(..., min_length=1),
    to_city: str = Query(..., min_length=1),
    days: int = Query(default=30, ge=1, le=90),
):
    """Daily price trend for a route."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    func.date(ParserIngestEvent.created_at).label("day"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg_rate"),
                    func.count().label("cnt"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.rate_rub.isnot(None),
                    ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"),
                    ParserIngestEvent.to_city.ilike(f"%{to_city.strip()}%"),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .group_by(func.date(ParserIngestEvent.created_at))
                .order_by(func.date(ParserIngestEvent.created_at))
            )
        ).all()

    return PriceTrendResponse(
        from_city=from_city.strip(),
        to_city=to_city.strip(),
        points=[
            PriceTrendPoint(
                date=str(r.day),
                avg_rate=int(r.avg_rate or 0),
                count=r.cnt,
            )
            for r in rows
        ],
        period_days=days,
    )


@router.get("/demand", response_model=DemandResponse)
async def demand_heatmap(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=30, ge=1, le=100),
):
    """Cargo demand heatmap data — which cities are busiest."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        origin_rows = (
            await session.execute(
                select(
                    ParserIngestEvent.from_city,
                    func.count().label("cnt"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.from_city.isnot(None),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .group_by(ParserIngestEvent.from_city)
                .order_by(func.count().desc())
                .limit(limit)
            )
        ).all()

    points = []
    for r in origin_rows:
        coords = city_coords(r.from_city)
        if coords:
            points.append(DemandPoint(
                city=r.from_city,
                lat=coords[0],
                lon=coords[1],
                cargo_count=r.cnt,
                point_type="origin",
            ))

    return DemandResponse(points=points, period_days=days)


# ---------------------------------------------------------------------------
# Dispatcher reputation
# ---------------------------------------------------------------------------

class DispatcherReputation(BaseModel):
    phone: str
    total_cargos: int
    avg_trust_score: float | None
    routes_count: int
    first_seen: datetime
    last_seen: datetime
    verdict: str


class DispatcherLookupResponse(BaseModel):
    dispatcher: DispatcherReputation | None = None
    recent_cargos: list[dict] = Field(default_factory=list)


@router.get("/dispatcher", response_model=DispatcherLookupResponse)
async def dispatcher_reputation(
    phone: str = Query(..., min_length=5),
):
    """Look up a dispatcher's reputation by phone number."""
    from src.core.services.phone_blacklist import is_phone_blacklisted

    normalized = phone.strip()
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if len(digits) >= 10:
        if len(digits) == 10:
            digits = f"7{digits}"
        elif digits.startswith("8") and len(digits) == 11:
            digits = f"7{digits[1:]}"
        normalized = f"+{digits}"

    async with async_session() as session:
        stats = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.avg(ParserIngestEvent.trust_score).label("avg_trust"),
                    func.count(func.distinct(
                        ParserIngestEvent.from_city + ParserIngestEvent.to_city
                    )).label("routes"),
                    func.min(ParserIngestEvent.created_at).label("first_seen"),
                    func.max(ParserIngestEvent.created_at).label("last_seen"),
                )
                .where(
                    ParserIngestEvent.phone == normalized,
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                )
            )
        ).one()

        if not stats.total:
            return DispatcherLookupResponse(dispatcher=None)

        recent = (
            await session.execute(
                select(
                    ParserIngestEvent.id,
                    ParserIngestEvent.from_city,
                    ParserIngestEvent.to_city,
                    ParserIngestEvent.rate_rub,
                    ParserIngestEvent.body_type,
                    ParserIngestEvent.created_at,
                )
                .where(ParserIngestEvent.phone == normalized, ParserIngestEvent.status == "synced")
                .order_by(ParserIngestEvent.id.desc())
                .limit(5)
            )
        ).all()

    is_blacklisted = await is_phone_blacklisted(normalized)

    avg_trust = round(float(stats.avg_trust or 0), 1) if stats.avg_trust else None
    if is_blacklisted:
        verdict_str = "🔴 В чёрном списке"
    elif stats.total >= 10 and avg_trust and avg_trust >= 60:
        verdict_str = "⭐ Проверенный диспетчер"
    elif stats.total >= 5:
        verdict_str = "🟡 Активный"
    else:
        verdict_str = "🔵 Новый"

    return DispatcherLookupResponse(
        dispatcher=DispatcherReputation(
            phone=normalized,
            total_cargos=stats.total,
            avg_trust_score=avg_trust,
            routes_count=stats.routes or 0,
            first_seen=stats.first_seen,
            last_seen=stats.last_seen,
            verdict=verdict_str,
        ),
        recent_cargos=[
            {
                "id": r.id,
                "route": f"{r.from_city} → {r.to_city}",
                "rate_rub": r.rate_rub,
                "body_type": r.body_type,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent
        ],
    )


# ---------------------------------------------------------------------------
# Demand forecast (day-of-week patterns)
# ---------------------------------------------------------------------------

class DayOfWeekStats(BaseModel):
    day_name: str
    day_number: int
    avg_cargos: float
    total_cargos: int


class ForecastResponse(BaseModel):
    from_city: str | None
    days: list[DayOfWeekStats] = Field(default_factory=list)
    period_days: int
    best_day: str | None = None
    worst_day: str | None = None


_DOW_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@router.get("/forecast", response_model=ForecastResponse)
async def demand_forecast(
    from_city: str | None = Query(default=None),
    days: int = Query(default=30, ge=7, le=90),
):
    """Day-of-week cargo volume patterns for demand forecasting."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    weeks = max(1, days / 7)

    async with async_session() as session:
        where_clauses = [
            ParserIngestEvent.is_spam.is_(False),
            ParserIngestEvent.status == "synced",
            ParserIngestEvent.created_at >= cutoff,
        ]
        if from_city:
            where_clauses.append(
                ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%")
            )

        rows = (
            await session.execute(
                select(
                    func.extract("dow", ParserIngestEvent.created_at).label("dow"),
                    func.count().label("cnt"),
                )
                .where(*where_clauses)
                .group_by(func.extract("dow", ParserIngestEvent.created_at))
                .order_by(func.extract("dow", ParserIngestEvent.created_at))
            )
        ).all()

    dow_map: dict[int, int] = {}
    for r in rows:
        pg_dow = int(r.dow)
        py_dow = (pg_dow - 1) % 7
        dow_map[py_dow] = r.cnt

    stats = []
    for i in range(7):
        total = dow_map.get(i, 0)
        stats.append(DayOfWeekStats(
            day_name=_DOW_NAMES[i],
            day_number=i,
            avg_cargos=round(total / weeks, 1),
            total_cargos=total,
        ))

    best = max(stats, key=lambda s: s.total_cargos) if stats else None
    worst = min(stats, key=lambda s: s.total_cargos) if stats else None

    return ForecastResponse(
        from_city=from_city,
        days=stats,
        period_days=days,
        best_day=best.day_name if best else None,
        worst_day=worst.day_name if worst else None,
    )


class PricePrediction(BaseModel):
    from_city: str
    to_city: str
    available: bool = False
    current_avg: int | None = None
    predicted_avg: int | None = None
    days_forecast: int | None = None
    pct_change: float | None = None
    trend: str | None = None
    recommendation: str | None = None
    slope_per_day: float | None = None
    data_points: int | None = None
    daily: list[dict] = Field(default_factory=list)


@router.get("/predict", response_model=PricePrediction)
async def predict_price(
    request: Request,
    from_city: str = Query(..., min_length=1),
    to_city: str = Query(..., min_length=1),
    days: int = Query(default=7, ge=1, le=30),
):
    """AI price prediction: trend + 7-day forecast via linear regression."""
    from src.core.rate_limit import check_rate_limit
    await check_rate_limit(request, limit=20, window_sec=60)

    from src.core.services.price_predict import predict_route_price
    return await predict_route_price(from_city, to_city, days_forecast=days)
