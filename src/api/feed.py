from __future__ import annotations

from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import Select, select

from src.core.auth.telegram_tma import TelegramTMAUser, get_optional_tma_user, get_required_tma_user
from src.core.cache import get_cached, set_cached
from src.core.database import async_session
from src.core.models import CallLog, ParserIngestEvent, User


router = APIRouter(tags=["feed"])


class FeedItem(BaseModel):
    id: int
    stream_entry_id: str
    from_city: str | None
    to_city: str | None
    body_type: str | None
    rate_rub: int | None
    weight_t: float | None
    phone: str | None
    phone_masked: bool = False
    can_view_contact: bool = False
    trust_score: int | None
    trust_verdict: str | None
    trust_comment: str | None
    provider: str | None
    status: str
    created_at: datetime
    load_date: str | None = None
    load_time: str | None = None
    cargo_description: str | None = None
    payment_terms: str | None = None
    is_direct_customer: bool | None = None
    dimensions: str | None = None
    is_hot_deal: bool = False
    rate_per_km: float | None = None
    distance_km: int | None = None
    freshness: str | None = None
    suggested_response: str | None = None
    reply_link: str | None = None
    phone_blacklisted: bool = False
    ati_link: str | None = None


class FeedResponse(BaseModel):
    items: list[FeedItem] = Field(default_factory=list)
    limit: int
    has_more: bool
    next_cursor: int | None


class FeedReportRequest(BaseModel):
    reason: str = "scam"
    comment: str | None = None


class FeedClickRequest(BaseModel):
    source: str = "twa"


class FeedClickResponse(BaseModel):
    ok: bool
    feed_id: int
    user_id: int
    created_at: datetime


def _normalize_verdicts(values: list[str] | None) -> list[str]:
    if not values:
        return ["green", "yellow"]
    allowed = {"green", "yellow", "red"}
    normalized = [v.strip().lower() for v in values if v and v.strip().lower() in allowed]
    return normalized or ["green", "yellow"]


def _base_feed_query() -> Select:
    return select(ParserIngestEvent).where(
        ParserIngestEvent.is_spam.is_(False),
        ParserIngestEvent.status == "synced",
    )


def _is_premium_active(user: User | None) -> bool:
    if not user or not user.is_premium:
        return False
    if user.premium_until is None:
        return True
    return user.premium_until >= datetime.now()


def _calc_rate_per_km(from_city: str | None, to_city: str | None, rate: int | None) -> tuple[float | None, int | None]:
    if not from_city or not to_city or not rate:
        return None, None
    from src.core.geo import city_coords, haversine_km
    fc = city_coords(from_city)
    tc = city_coords(to_city)
    if not fc or not tc:
        return None, None
    dist = int(haversine_km(fc[0], fc[1], tc[0], tc[1]))
    if dist < 10:
        return None, dist
    return round(rate / dist, 1), dist


def _freshness(created_at: datetime) -> str:
    now = datetime.utcnow()
    ca = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    delta = now - ca
    minutes = int(delta.total_seconds() / 60)
    if minutes < 5:
        return "🟢 только что"
    if minutes < 30:
        return f"🟢 {minutes} мин назад"
    if minutes < 60:
        return f"🟡 {minutes} мин назад"
    hours = minutes // 60
    if hours < 6:
        return f"🟡 {hours} ч назад"
    if hours < 24:
        return f"🟠 {hours} ч назад"
    days = hours // 24
    return f"🔴 {days} дн назад"


def _mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return phone
    masked = f"{digits[:-4]}****"
    return f"+{masked}" if phone.strip().startswith("+") else masked


@router.get("/api/v1/feed", response_model=FeedResponse)
async def get_feed(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: int | None = Query(default=None, ge=1),
    verdict: list[str] | None = Query(default=["green", "yellow"]),
    min_score: int | None = Query(default=None, ge=0, le=100),
    max_score: int | None = Query(default=None, ge=0, le=100),
    from_city: str | None = Query(default=None),
    to_city: str | None = Query(default=None),
    from_radius_km: int | None = Query(default=None, ge=1, le=500),
    to_radius_km: int | None = Query(default=None, ge=1, le=500),
    body_type: str | None = Query(default=None),
    load_date: str | None = Query(default=None),
    tma_user: TelegramTMAUser | None = Depends(get_optional_tma_user),
) -> FeedResponse:
    from src.core.rate_limit import check_rate_limit
    await check_rate_limit(request, limit=60, window_sec=60)

    cache_params = {
        "limit": limit, "cursor": cursor, "verdict": sorted(verdict or []),
        "min_score": min_score, "max_score": max_score,
        "from_city": from_city, "to_city": to_city,
        "from_radius_km": from_radius_km, "to_radius_km": to_radius_km,
        "body_type": body_type, "load_date": load_date,
        "uid": tma_user.user_id if tma_user else None,
    }
    cached = await get_cached("feed", cache_params)
    if cached:
        return Response(content=cached, media_type="application/json")

    verdicts = _normalize_verdicts(verdict)
    stmt = _base_feed_query()

    if cursor is not None:
        stmt = stmt.where(ParserIngestEvent.id < cursor)
    if verdicts:
        stmt = stmt.where(ParserIngestEvent.trust_verdict.in_(verdicts))
    if min_score is not None:
        stmt = stmt.where(ParserIngestEvent.trust_score >= min_score)
    if max_score is not None:
        stmt = stmt.where(ParserIngestEvent.trust_score <= max_score)
    if body_type:
        stmt = stmt.where(ParserIngestEvent.body_type.ilike(f"%{body_type.strip()}%"))
    if load_date:
        stmt = stmt.where(ParserIngestEvent.load_date == load_date.strip())

    from_coords = None
    to_coords = None
    if from_city:
        from src.core.geo import city_coords, resolve_region, region_center
        region_cities = resolve_region(from_city.strip())
        if region_cities:
            from_coords = region_center(from_city.strip())
            from_radius_km = from_radius_km or 300
        elif from_radius_km:
            from_coords = city_coords(from_city.strip())
        if not from_coords and not region_cities:
            stmt = stmt.where(ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"))
    if to_city:
        from src.core.geo import city_coords as _cc, resolve_region as _rr, region_center as _rc
        to_region_cities = _rr(to_city.strip())
        if to_region_cities:
            to_coords = _rc(to_city.strip())
            to_radius_km = to_radius_km or 300
        elif to_radius_km:
            to_coords = _cc(to_city.strip())
        if not to_coords and not to_region_cities:
            stmt = stmt.where(ParserIngestEvent.to_city.ilike(f"%{to_city.strip()}%"))

    if from_coords or to_coords:
        stmt = stmt.where(ParserIngestEvent.from_lat.isnot(None))

    stmt = stmt.order_by(ParserIngestEvent.id.desc()).limit((limit + 1) * (3 if from_coords or to_coords else 1))

    async with async_session() as session:
        current_user = await session.get(User, tma_user.user_id) if tma_user else None
        can_view_contact = _is_premium_active(current_user)
        rows = (await session.execute(stmt)).scalars().all()

    if from_coords or to_coords:
        from src.core.geo import haversine_km
        filtered = []
        for row in rows:
            if from_coords and row.from_lat is not None:
                dist = haversine_km(from_coords[0], from_coords[1], row.from_lat, row.from_lon)
                if dist > (from_radius_km or 100):
                    continue
            if to_coords and row.to_lat is not None:
                dist = haversine_km(to_coords[0], to_coords[1], row.to_lat, row.to_lon)
                if dist > (to_radius_km or 100):
                    continue
            filtered.append(row)
        rows = filtered

    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = items[-1].id if has_more and items else None

    feed_items = []
    for item in items:
        rpk, dist = _calc_rate_per_km(item.from_city, item.to_city, item.rate_rub)
        feed_items.append(FeedItem(
            id=item.id,
            stream_entry_id=item.stream_entry_id,
            from_city=item.from_city,
            to_city=item.to_city,
            body_type=item.body_type,
            rate_rub=item.rate_rub,
            weight_t=item.weight_t,
            phone=item.phone if can_view_contact else _mask_phone(item.phone),
            phone_masked=bool(item.phone and not can_view_contact),
            can_view_contact=can_view_contact,
            trust_score=item.trust_score,
            trust_verdict=item.trust_verdict,
            trust_comment=item.trust_comment,
            provider=item.provider,
            status=item.status,
            created_at=item.created_at,
            load_date=item.load_date,
            load_time=item.load_time,
            cargo_description=item.cargo_description,
            payment_terms=item.payment_terms,
            is_direct_customer=item.is_direct_customer,
            dimensions=item.dimensions,
            is_hot_deal=item.is_hot_deal,
            rate_per_km=rpk,
            distance_km=dist,
            freshness=_freshness(item.created_at),
            suggested_response=item.suggested_response if can_view_contact else None,
            reply_link=f"tel:{item.phone}" if can_view_contact and item.phone else None,
                phone_blacklisted=item.phone_blacklisted,
                ati_link=f"https://ati.su/firms?inn={item.inn}" if item.inn else None,
        ))

    response = FeedResponse(
        items=feed_items,
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
    )

    serialized = response.model_dump_json()
    await set_cached("feed", cache_params, serialized)
    return Response(content=serialized, media_type="application/json")


class CargoDetailResponse(BaseModel):
    """Full enriched cargo detail."""
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    rate_rub: int | None
    weight_t: float | None
    phone: str | None
    phone_masked: bool = False
    can_view_contact: bool = False
    inn: str | None = None
    trust_score: int | None
    trust_verdict: str | None
    trust_comment: str | None
    load_date: str | None = None
    load_time: str | None = None
    cargo_description: str | None = None
    payment_terms: str | None = None
    is_direct_customer: bool | None = None
    dimensions: str | None = None
    is_hot_deal: bool = False
    rate_per_km: float | None = None
    distance_km: int | None = None
    freshness: str | None = None
    suggested_response: str | None = None
    reply_link: str | None = None
    phone_blacklisted: bool = False
    ati_link: str | None = None
    created_at: datetime


@router.get("/api/v1/feed/{feed_id}", response_model=CargoDetailResponse)
async def get_cargo_detail(
    feed_id: int,
    tma_user: TelegramTMAUser | None = Depends(get_optional_tma_user),
) -> CargoDetailResponse:
    """Full enriched cargo card with all analytics fields."""
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="feed item not found")

        current_user = await session.get(User, tma_user.user_id) if tma_user else None
        can_view = _is_premium_active(current_user)

    rpk, dist = _calc_rate_per_km(event.from_city, event.to_city, event.rate_rub)

    return CargoDetailResponse(
        id=event.id,
        from_city=event.from_city,
        to_city=event.to_city,
        body_type=event.body_type,
        rate_rub=event.rate_rub,
        weight_t=event.weight_t,
        phone=event.phone if can_view else _mask_phone(event.phone),
        phone_masked=bool(event.phone and not can_view),
        can_view_contact=can_view,
        inn=event.inn if can_view else None,
        trust_score=event.trust_score,
        trust_verdict=event.trust_verdict,
        trust_comment=event.trust_comment,
        load_date=event.load_date,
        load_time=event.load_time,
        cargo_description=event.cargo_description,
        payment_terms=event.payment_terms,
        is_direct_customer=event.is_direct_customer,
        dimensions=event.dimensions,
        is_hot_deal=event.is_hot_deal,
        rate_per_km=rpk,
        distance_km=dist,
        freshness=_freshness(event.created_at),
        suggested_response=event.suggested_response if can_view else None,
        reply_link=f"tel:{event.phone}" if can_view and event.phone else None,
        phone_blacklisted=event.phone_blacklisted,
        ati_link=f"https://ati.su/firms?inn={event.inn}" if event.inn else None,
        created_at=event.created_at,
    )


class SimilarItem(BaseModel):
    id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    rate_rub: int | None
    weight_t: float | None
    load_date: str | None = None
    is_hot_deal: bool = False
    created_at: datetime


class SimilarResponse(BaseModel):
    items: list[SimilarItem] = Field(default_factory=list)


@router.get("/api/v1/feed/{feed_id}/similar", response_model=SimilarResponse)
async def get_similar(
    feed_id: int,
    limit: int = Query(default=3, ge=1, le=10),
) -> SimilarResponse:
    """Return cargos with similar routes (same from_city or to_city)."""
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="feed item not found")

        from sqlalchemy import or_

        stmt = (
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.is_spam.is_(False),
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.id != feed_id,
                or_(
                    ParserIngestEvent.from_city == event.from_city,
                    ParserIngestEvent.to_city == event.to_city,
                ),
            )
            .order_by(ParserIngestEvent.id.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()

    return SimilarResponse(
        items=[
            SimilarItem(
                id=r.id,
                from_city=r.from_city,
                to_city=r.to_city,
                body_type=r.body_type,
                rate_rub=r.rate_rub,
                weight_t=r.weight_t,
                load_date=r.load_date,
                is_hot_deal=r.is_hot_deal,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


class BackhaulResponse(BaseModel):
    """Return loads going back (B→A) or onward from destination."""
    return_loads: list[SimilarItem] = Field(default_factory=list)
    onward_loads: list[SimilarItem] = Field(default_factory=list)


@router.get("/api/v1/feed/{feed_id}/backhaul", response_model=BackhaulResponse)
async def get_backhaul(
    feed_id: int,
    radius_km: int = Query(default=100, ge=10, le=500),
    limit: int = Query(default=3, ge=1, le=10),
) -> BackhaulResponse:
    """Find return/onward loads for route triangulation.

    Given cargo A→B, returns:
    - **return_loads**: cargos from B back to A (or nearby)
    - **onward_loads**: cargos from B to anywhere else (for chaining)
    """
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="feed item not found")

        base_where = [
            ParserIngestEvent.is_spam.is_(False),
            ParserIngestEvent.status == "synced",
            ParserIngestEvent.id != feed_id,
        ]

        return_stmt = (
            select(ParserIngestEvent)
            .where(*base_where)
            .where(ParserIngestEvent.from_city == event.to_city)
            .order_by(ParserIngestEvent.id.desc())
            .limit(limit * 3)
        )
        return_rows = (await session.execute(return_stmt)).scalars().all()

        if event.to_lat is not None and event.from_lat is not None:
            from src.core.geo import haversine_km
            filtered = []
            for r in return_rows:
                if r.to_lat is not None and event.from_lat is not None:
                    dist = haversine_km(r.to_lat, r.to_lon, event.from_lat, event.from_lon)
                    if dist <= radius_km:
                        filtered.append(r)
                else:
                    if r.to_city and event.from_city and r.to_city.lower() == event.from_city.lower():
                        filtered.append(r)
            return_rows = filtered

        onward_stmt = (
            select(ParserIngestEvent)
            .where(*base_where)
            .where(ParserIngestEvent.from_city == event.to_city)
            .where(ParserIngestEvent.to_city != event.from_city)
            .order_by(ParserIngestEvent.id.desc())
            .limit(limit)
        )
        onward_rows = (await session.execute(onward_stmt)).scalars().all()

    def _to_item(r: ParserIngestEvent) -> SimilarItem:
        return SimilarItem(
            id=r.id, from_city=r.from_city, to_city=r.to_city,
            body_type=r.body_type, rate_rub=r.rate_rub, weight_t=r.weight_t,
            load_date=r.load_date, is_hot_deal=r.is_hot_deal, created_at=r.created_at,
        )

    return BackhaulResponse(
        return_loads=[_to_item(r) for r in return_rows[:limit]],
        onward_loads=[_to_item(r) for r in onward_rows[:limit]],
    )


class MapPoint(BaseModel):
    id: int
    from_city: str | None
    to_city: str | None
    lat: float
    lon: float
    point_type: str
    body_type: str | None = None
    rate_rub: int | None = None
    is_hot_deal: bool = False


class MapResponse(BaseModel):
    points: list[MapPoint] = Field(default_factory=list)
    total: int = 0


@router.get("/api/v1/feed/map", response_model=MapResponse)
async def get_feed_map(
    limit: int = Query(default=100, ge=1, le=500),
    from_city: str | None = Query(default=None),
    body_type: str | None = Query(default=None),
) -> MapResponse:
    """Return cargo origins/destinations as map points with coordinates."""
    stmt = (
        select(ParserIngestEvent)
        .where(
            ParserIngestEvent.is_spam.is_(False),
            ParserIngestEvent.status == "synced",
            ParserIngestEvent.from_lat.isnot(None),
        )
    )
    if from_city:
        stmt = stmt.where(ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"))
    if body_type:
        stmt = stmt.where(ParserIngestEvent.body_type.ilike(f"%{body_type.strip()}%"))
    stmt = stmt.order_by(ParserIngestEvent.id.desc()).limit(limit)

    async with async_session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    points: list[MapPoint] = []
    for r in rows:
        if r.from_lat is not None:
            points.append(MapPoint(
                id=r.id, from_city=r.from_city, to_city=r.to_city,
                lat=r.from_lat, lon=r.from_lon,
                point_type="origin", body_type=r.body_type,
                rate_rub=r.rate_rub, is_hot_deal=r.is_hot_deal,
            ))
        if r.to_lat is not None:
            points.append(MapPoint(
                id=r.id, from_city=r.from_city, to_city=r.to_city,
                lat=r.to_lat, lon=r.to_lon,
                point_type="destination", body_type=r.body_type,
                rate_rub=r.rate_rub, is_hot_deal=r.is_hot_deal,
            ))

    return MapResponse(points=points, total=len(rows))


@router.post("/api/v1/feed/{feed_id}/report")
async def report_feed_item(
    feed_id: int,
    body: FeedReportRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Report a feed item as scam/fake. Auto-hides after 3 reports."""
    from src.core.services.anti_scam import report_feed_item as do_report
    result = await do_report(
        feed_id=feed_id,
        user_id=tma_user.user_id,
        reason=body.reason if body else "scam",
        comment=body.comment if body else None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "not found"))
    return result


@router.post("/api/v1/feed/{feed_id}/click", response_model=FeedClickResponse)
async def feed_click(
    feed_id: int,
    body: FeedClickRequest | None = None,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> FeedClickResponse:
    _ = body  # reserved for future payload fields
    user_id = tma_user.user_id

    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="feed item not found")

        log = CallLog(user_id=user_id, cargo_id=feed_id)
        session.add(log)
        await session.commit()
        await session.refresh(log)

    return FeedClickResponse(
        ok=True,
        feed_id=feed_id,
        user_id=user_id,
        created_at=log.created_at,
    )


class HistoryItem(BaseModel):
    click_id: int
    feed_id: int
    from_city: str | None
    to_city: str | None
    body_type: str | None
    rate_rub: int | None
    clicked_at: datetime


class HistoryStatsResponse(BaseModel):
    total_clicks: int
    unique_cargos: int
    top_routes: list[dict]


class HistoryResponse(BaseModel):
    items: list[HistoryItem] = Field(default_factory=list)
    stats: HistoryStatsResponse


@router.get("/api/v1/feed/history", response_model=HistoryResponse)
async def get_user_history(
    limit: int = Query(default=20, ge=1, le=100),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
) -> HistoryResponse:
    """Return the user's click/response history with stats."""
    user_id = tma_user.user_id

    async with async_session() as session:
        logs = (
            await session.execute(
                select(CallLog)
                .where(CallLog.user_id == user_id)
                .order_by(CallLog.id.desc())
                .limit(limit)
            )
        ).scalars().all()

        from sqlalchemy import func, distinct
        stats_row = (
            await session.execute(
                select(
                    func.count().label("total"),
                    func.count(distinct(CallLog.cargo_id)).label("unique"),
                )
                .where(CallLog.user_id == user_id)
            )
        ).one()

        cargo_ids = [log.cargo_id for log in logs]
        events_map: dict[int, ParserIngestEvent] = {}
        if cargo_ids:
            events = (
                await session.execute(
                    select(ParserIngestEvent).where(ParserIngestEvent.id.in_(cargo_ids))
                )
            ).scalars().all()
            events_map = {e.id: e for e in events}

        route_counts = (
            await session.execute(
                select(
                    ParserIngestEvent.from_city,
                    ParserIngestEvent.to_city,
                    func.count().label("cnt"),
                )
                .join(CallLog, CallLog.cargo_id == ParserIngestEvent.id)
                .where(CallLog.user_id == user_id)
                .group_by(ParserIngestEvent.from_city, ParserIngestEvent.to_city)
                .order_by(func.count().desc())
                .limit(5)
            )
        ).all()

    items = []
    for log in logs:
        ev = events_map.get(log.cargo_id)
        items.append(HistoryItem(
            click_id=log.id,
            feed_id=log.cargo_id,
            from_city=ev.from_city if ev else None,
            to_city=ev.to_city if ev else None,
            body_type=ev.body_type if ev else None,
            rate_rub=ev.rate_rub if ev else None,
            clicked_at=log.created_at,
        ))

    return HistoryResponse(
        items=items,
        stats=HistoryStatsResponse(
            total_clicks=stats_row.total or 0,
            unique_cargos=stats_row.unique or 0,
            top_routes=[
                {"route": f"{r.from_city} → {r.to_city}", "clicks": r.cnt}
                for r in route_counts
            ],
        ),
    )
