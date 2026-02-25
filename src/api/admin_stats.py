"""Admin Eagle Eye Dashboard — live platform metrics."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from src.core.database import async_session
from src.core.models import (
    FeedComplaint,
    Favorite,
    ParserIngestEvent,
    User,
    UserVehicle,
    CounterpartyList,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-stats"])


class PlatformStats(BaseModel):
    total_events: int = 0
    synced_events: int = 0
    spam_filtered: int = 0
    hot_deals: int = 0
    unique_phones: int = 0
    unique_routes: int = 0
    total_users: int = 0
    total_vehicles: int = 0
    available_vehicles: int = 0
    total_favorites: int = 0
    total_complaints: int = 0
    blacklisted_phones: int = 0
    events_today: int = 0
    events_this_week: int = 0


class ParserEfficiency(BaseModel):
    total_processed: int = 0
    llm_parsed: int = 0
    regex_fallback: int = 0
    duplicates_blocked: int = 0
    spam_blocked: int = 0
    avg_events_per_hour: float = 0.0


class BanCandidate(BaseModel):
    phone: str | None
    inn: str | None
    complaint_count: int
    last_complaint: datetime | None


class AdminDashboardResponse(BaseModel):
    stats: PlatformStats
    efficiency: ParserEfficiency
    ban_candidates: list[BanCandidate] = Field(default_factory=list)
    generated_at: datetime


@router.get("/stats", response_model=AdminDashboardResponse)
async def get_admin_stats():
    """Live platform metrics for the admin dashboard."""
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    async with async_session() as session:
        total = await session.scalar(select(func.count()).select_from(ParserIngestEvent)) or 0
        synced = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.status == "synced")
        ) or 0
        spam = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.is_spam.is_(True))
        ) or 0
        hot = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.is_hot_deal.is_(True))
        ) or 0
        dupes = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.status == "duplicate")
        ) or 0
        phones = await session.scalar(
            select(func.count(func.distinct(ParserIngestEvent.phone)))
            .where(ParserIngestEvent.phone.isnot(None))
        ) or 0
        routes = await session.scalar(
            select(func.count(func.distinct(
                ParserIngestEvent.from_city + ParserIngestEvent.to_city
            )))
        ) or 0
        users = await session.scalar(select(func.count()).select_from(User)) or 0
        vehicles_total = await session.scalar(select(func.count()).select_from(UserVehicle)) or 0
        vehicles_avail = await session.scalar(
            select(func.count()).select_from(UserVehicle)
            .where(UserVehicle.is_available.is_(True))
        ) or 0
        favs = await session.scalar(select(func.count()).select_from(Favorite)) or 0
        complaints = await session.scalar(select(func.count()).select_from(FeedComplaint)) or 0
        blacklisted = await session.scalar(
            select(func.count()).select_from(CounterpartyList)
            .where(CounterpartyList.list_type == "black")
        ) or 0
        today_count = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.created_at >= today)
        ) or 0
        week_count = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(ParserIngestEvent.created_at >= week_ago)
        ) or 0

        ban_rows = (
            await session.execute(
                select(
                    ParserIngestEvent.phone,
                    ParserIngestEvent.inn,
                    func.count().label("cnt"),
                    func.max(FeedComplaint.created_at).label("last"),
                )
                .join(FeedComplaint, FeedComplaint.feed_id == ParserIngestEvent.id)
                .group_by(ParserIngestEvent.phone, ParserIngestEvent.inn)
                .having(func.count() >= 2)
                .order_by(func.count().desc())
                .limit(20)
            )
        ).all()

    hours_active = max(1, (now - today).total_seconds() / 3600)

    return AdminDashboardResponse(
        stats=PlatformStats(
            total_events=total, synced_events=synced, spam_filtered=spam,
            hot_deals=hot, unique_phones=phones, unique_routes=routes,
            total_users=users, total_vehicles=vehicles_total,
            available_vehicles=vehicles_avail, total_favorites=favs,
            total_complaints=complaints, blacklisted_phones=blacklisted,
            events_today=today_count, events_this_week=week_count,
        ),
        efficiency=ParserEfficiency(
            total_processed=total,
            llm_parsed=synced,
            duplicates_blocked=dupes,
            spam_blocked=spam,
            avg_events_per_hour=round(today_count / hours_active, 1),
        ),
        ban_candidates=[
            BanCandidate(phone=r.phone, inn=r.inn, complaint_count=r.cnt, last_complaint=r.last)
            for r in ban_rows
        ],
        generated_at=now,
    )
