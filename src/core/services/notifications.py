"""
Push-notification service: rich cargo notifications to route subscribers.
"""

from sqlalchemy import select, or_

from src.core.database import async_session
from src.core.logger import logger
from src.core.models import (
    Cargo,
    CompanyDetails,
    RouteSubscription,
    User,
)


async def notify_subscribers(cargo: Cargo):
    """Send rich notifications to subscribers matching this cargo's route.

    Sets ``cargo.notified_at`` after successful dispatch.
    """
    from src.bot.bot import bot
    from src.bot.keyboards import notification_kb

    async with async_session() as session:
        # Find matching subscriptions
        subs_query = (
            select(RouteSubscription)
            .where(RouteSubscription.is_active.is_(True))
            .where(
                or_(
                    RouteSubscription.from_city.is_(None),
                    RouteSubscription.from_city.ilike(
                        f"%{cargo.from_city}%"
                    ),
                )
            )
            .where(
                or_(
                    RouteSubscription.to_city.is_(None),
                    RouteSubscription.to_city.ilike(
                        f"%{cargo.to_city}%"
                    ),
                )
            )
        )
        result = await session.execute(subs_query)
        subscribers = result.scalars().all()

        if not subscribers:
            return

        # Owner company rating
        owner_company = await session.scalar(
            select(CompanyDetails).where(
                CompanyDetails.user_id == cargo.owner_id
            )
        )
        owner = await session.scalar(
            select(User).where(User.id == cargo.owner_id)
        )

    # Build message
    text = "🔔 <b>Новый груз по вашему маршруту!</b>\n\n"
    text += f"📍 {cargo.from_city} → {cargo.to_city}\n"
    text += f"📦 {cargo.cargo_type} | {cargo.weight} т\n"
    text += f"💰 {cargo.price:,} ₽\n"
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

    kb = notification_kb(cargo.id)

    sent = 0
    for sub in subscribers:
        if sub.user_id == cargo.owner_id:
            continue
        try:
            await bot.send_message(sub.user_id, text, reply_markup=kb)
            sent += 1
        except Exception:
            pass

    logger.info(
        "Notified %d/%d subscribers for cargo #%d",
        sent,
        len(subscribers),
        cargo.id,
    )
