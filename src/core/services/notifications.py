"""Push-notification service: cargo notifications for matching carriers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from src.core.database import async_session
from src.core.logger import logger
from src.core.models import (
    Cargo,
    CompanyDetails,
    RouteSubscription,
    User,
    UserVehicle,
)


async def collect_matching_route_subscriber_ids(session, cargo: Cargo) -> list[int]:
    result = await session.execute(
        select(RouteSubscription)
        .where(RouteSubscription.is_active.is_(True))
        .where(
            or_(
                RouteSubscription.from_city.is_(None),
                RouteSubscription.from_city.ilike(f"%{cargo.from_city}%"),
            )
        )
        .where(
            or_(
                RouteSubscription.to_city.is_(None),
                RouteSubscription.to_city.ilike(f"%{cargo.to_city}%"),
            )
        )
    )
    subscribers = result.scalars().all()
    return [int(sub.user_id) for sub in subscribers if int(sub.user_id) != int(cargo.owner_id)]


async def collect_matching_available_vehicle_user_ids(session, cargo: Cargo) -> list[int]:
    rows = (
        await session.execute(
            select(UserVehicle).where(
                UserVehicle.is_available.is_(True),
                UserVehicle.location_city.is_not(None),
                UserVehicle.location_city.ilike(f"%{cargo.from_city}%"),
            )
        )
    ).scalars().all()

    matches: list[int] = []
    cargo_body = (cargo.cargo_type or "").strip().lower()
    for vehicle in rows:
        if int(vehicle.user_id) == int(cargo.owner_id):
            continue
        if vehicle.capacity_tons and cargo.weight and float(cargo.weight) > float(vehicle.capacity_tons):
            continue
        vehicle_body = (vehicle.body_type or "").strip().lower()
        if cargo_body and vehicle_body:
            if cargo_body not in vehicle_body and vehicle_body not in cargo_body:
                continue
        matches.append(int(vehicle.user_id))
    return matches


def _build_cargo_notification_text(
    cargo: Cargo,
    owner_company: CompanyDetails | None,
    owner: User | None,
    ai_badge: str | None = None,
    price_hint: str | None = None,
) -> str:
    text = "🔔 <b>Новый груз по вашему маршруту!</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type} | {cargo.weight} т\n"
    text += f"💰 {cargo.price:,} ₽"
    if price_hint:
        text += f"  <i>{price_hint}</i>"
    text += "\n"
    text += f"📅 {cargo.load_date.strftime('%d.%m.%Y')}"
    if cargo.load_time:
        text += f" в {cargo.load_time}"
    text += "\n"

    if owner_company:
        rating = owner_company.total_rating
        stars = "⭐" * rating + "☆" * (10 - rating)
        name = owner_company.company_name or "Компания"
        text += f"\n🏢 {name} | {stars} ({rating}/10)\n"
    elif owner:
        text += f"\n👤 {owner.full_name}\n"

    if ai_badge:
        text += f"\n{ai_badge}"

    return text


async def _get_ai_notification_extras(cargo: Cargo) -> tuple[str | None, str | None]:
    """Return (ai_badge, price_hint) from cached antifraud + market data. Never raises."""
    ai_badge: str | None = None
    price_hint: str | None = None

    # 1. Antifraud badge from Redis cache
    try:
        from src.services.cargo_antifraud import get_antifraud_result
        af = await get_antifraud_result(cargo.id)
        if af and "risk_score" in af:
            score = int(af["risk_score"])
            rec = af.get("recommendation", "accept")
            if score < 35:
                ai_badge = f"🟢 AI: низкий риск ({score}/100)"
            elif score < 70:
                ai_badge = f"🟡 AI: средний риск ({score}/100)"
            else:
                ai_badge = f"🔴 AI: высокий риск ({score}/100) — {rec}"
    except Exception:
        pass

    # 2. Price hint vs market average
    try:
        from src.core.services.price_predict import predict_route_price
        market = await predict_route_price(cargo.from_city, cargo.to_city)
        if market.get("available") and market.get("current_avg") and cargo.price:
            avg = int(market["current_avg"])
            diff_pct = round((cargo.price - avg) / avg * 100)
            if diff_pct >= 10:
                price_hint = f"(+{diff_pct}% рынка 📈)"
            elif diff_pct <= -10:
                price_hint = f"({diff_pct}% рынка 📉)"
    except Exception:
        pass

    return ai_badge, price_hint


async def dispatch_cargo_notification(cargo: Cargo, user_ids: list[int]) -> int:
    if not user_ids:
        return 0

    import asyncio
    from src.bot.bot import bot
    from src.bot.keyboards import notification_kb

    async with async_session() as session:
        owner_company = await session.scalar(
            select(CompanyDetails).where(CompanyDetails.user_id == cargo.owner_id)
        )
        owner = await session.scalar(select(User).where(User.id == cargo.owner_id))

    ai_badge, price_hint = await _get_ai_notification_extras(cargo)
    text = _build_cargo_notification_text(cargo, owner_company, owner, ai_badge, price_hint)
    kb = notification_kb(cargo.id)

    sent = 0
    for user_id in user_ids:
        try:
            await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb)
            sent += 1
        except Exception:
            pass

    # Send Kimi AI insight as follow-up in background (non-blocking)
    if sent > 0:
        asyncio.create_task(_send_ai_insight(cargo, user_ids[:sent]))

    return sent


async def _send_ai_insight(cargo: Cargo, user_ids: list[int]) -> None:
    """Send short Kimi AI insight about the cargo as a follow-up message."""
    try:
        from src.services.ai_kimi import kimi_service
        from src.bot.bot import bot

        cargo_text = (
            f"{cargo.from_city} — {cargo.to_city}, "
            f"{cargo.cargo_type}, {cargo.weight} т, {cargo.price} руб"
        )
        result = await kimi_service.logist_mode(cargo_text)

        lines: list[str] = ["💡 <b>AI-инсайт по грузу:</b>"]
        risks = result.get("risks") or []
        if risks:
            lines.append(f"⚠️ {', '.join(str(r) for r in risks[:2])}")
        questions = result.get("questions") or []
        if questions:
            lines.append(f"❓ Уточни: {questions[0]}")
        if result.get("vehicle"):
            lines.append(f"🚛 Рекомендуемый ТС: {result['vehicle']}")

        if len(lines) == 1:
            return  # nothing useful to say

        insight_text = "\n".join(lines)
        for user_id in user_ids:
            try:
                await bot.send_message(user_id, insight_text, parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        from src.core.logger import logger
        logger.warning("notifications.ai_insight error cargo_id=%d error=%s", cargo.id, e)


async def notify_subscribers(cargo: Cargo) -> int:
    """Send cargo notifications to route subscribers only."""
    async with async_session() as session:
        target_ids = await collect_matching_route_subscriber_ids(session, cargo)
    sent = await dispatch_cargo_notification(cargo, target_ids)
    if sent:
        async with async_session() as session:
            current = await session.get(Cargo, cargo.id)
            if current:
                current.notified_at = datetime.utcnow()
                await session.commit()

    logger.info(
        "Notified %d route subscribers for cargo #%d",
        sent,
        cargo.id,
    )
    return sent
