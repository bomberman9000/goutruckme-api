"""Push-notifications for parser feed items matching route subscriptions."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from src.core.database import async_session
from src.core.geo import city_coords, haversine_km
from src.core.logger import logger
from src.core.models import ParserIngestEvent, RouteSubscription, User


def _is_premium_active(user: User | None) -> bool:
    if not user or not user.is_premium:
        return False
    if user.premium_until is None:
        return True
    return user.premium_until >= datetime.now()


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "скрыт"
    return f"{phone[:-4]}****"


async def notify_feed_subscribers() -> int:
    """Check new parser events and notify matching route subscribers.

    Only premium users receive the full phone number; others see it masked.
    Returns the number of notifications sent.
    """
    from src.bot.bot import bot

    cutoff = datetime.utcnow() - timedelta(minutes=6)

    async with async_session() as session:
        events_result = await session.execute(
            select(ParserIngestEvent)
            .where(
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.is_spam.is_(False),
                ParserIngestEvent.created_at >= cutoff,
            )
            .order_by(ParserIngestEvent.id.desc())
            .limit(50)
        )
        events = events_result.scalars().all()
        if not events:
            return 0

        subs_result = await session.execute(
            select(RouteSubscription).where(RouteSubscription.is_active.is_(True))
        )
        subs = subs_result.scalars().all()
        if not subs:
            return 0

        notified_user_ids: set[int] = set()
        total_sent = 0

        for event in events:
            matching_subs = _find_matching_subs(event, subs)
            for sub in matching_subs:
                if sub.user_id in notified_user_ids:
                    continue

                user = await session.get(User, sub.user_id)
                is_premium = _is_premium_active(user)
                text = _build_notification_text(event, is_premium)

                try:
                    await bot.send_message(
                        sub.user_id, text, parse_mode="HTML"
                    )
                    total_sent += 1
                    notified_user_ids.add(sub.user_id)
                except Exception:
                    pass

    if total_sent:
        logger.info("Feed notifications sent: %d", total_sent)
    return total_sent


def _find_matching_subs(
    event: ParserIngestEvent,
    subs: list[RouteSubscription],
) -> list[RouteSubscription]:
    matched = []
    for sub in subs:
        if sub.from_city and event.from_city:
            if not _city_matches(sub.from_city, event.from_city, event.from_lat, event.from_lon):
                continue
        if sub.to_city and event.to_city:
            if not _city_matches(sub.to_city, event.to_city, event.to_lat, event.to_lon):
                continue
        if sub.body_type and event.body_type:
            if sub.body_type.lower() not in event.body_type.lower():
                continue
        if sub.min_rate is not None and event.rate_rub is not None:
            if event.rate_rub < sub.min_rate:
                continue
        if sub.max_weight is not None and event.weight_t is not None:
            if event.weight_t > sub.max_weight:
                continue
        if sub.region:
            from src.core.geo import resolve_region
            region_cities = resolve_region(sub.region)
            if region_cities and event.from_city:
                if event.from_city.strip().lower() not in region_cities:
                    if not _city_matches(sub.region, event.from_city, event.from_lat, event.from_lon):
                        continue
        matched.append(sub)
    return matched


def _city_matches(
    sub_city: str,
    event_city: str,
    event_lat: float | None,
    event_lon: float | None,
    radius_km: float = 100.0,
) -> bool:
    if sub_city.strip().lower() in event_city.strip().lower():
        return True
    if event_lat is not None and event_lon is not None:
        sub_coords = city_coords(sub_city)
        if sub_coords:
            dist = haversine_km(sub_coords[0], sub_coords[1], event_lat, event_lon)
            return dist <= radius_km
    return False


def _build_notification_text(event: ParserIngestEvent, is_premium: bool) -> str:
    text = "🔔 <b>Новый груз по вашему маршруту!</b>\n\n"
    text += f"📍 {event.from_city} → {event.to_city}\n"
    if event.body_type:
        text += f"🚛 {event.body_type}"
        if event.weight_t:
            text += f" | {event.weight_t} т"
        text += "\n"
    if event.rate_rub:
        text += f"💰 {event.rate_rub:,} ₽\n"
    if event.load_date:
        text += f"📅 {event.load_date}"
        if event.load_time:
            text += f" в {event.load_time}"
        text += "\n"
    if event.cargo_description:
        text += f"📦 {event.cargo_description}\n"

    if event.phone:
        if is_premium:
            text += f"\n📞 {event.phone}\n"
        else:
            text += f"\n📞 {_mask_phone(event.phone)} (Premium для полного номера)\n"

    if event.trust_verdict:
        badge = {"green": "✅", "yellow": "⚠️", "red": "🔴"}.get(event.trust_verdict, "❓")
        text += f"\n{badge} Надёжность: {event.trust_score}/100\n"

    return text
