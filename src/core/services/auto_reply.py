"""Auto-reply service: sends a prepared message to dispatchers on behalf
of trusted carriers when an ideal cargo match is found.

Activated by the scheduler; only fires for cargos matching a carrier's
saved route and body-type preferences.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from src.core.config import settings
from src.core.database import async_session
from src.core.models import ParserIngestEvent, RouteSubscription, User

logger = logging.getLogger(__name__)


def _build_reply_text(event: ParserIngestEvent, carrier: User) -> str:
    name = carrier.full_name or "Перевозчик"
    phone = carrier.phone or ""
    text = (
        f"Добрый день! По вашему грузу "
        f"{event.from_city} — {event.to_city}"
    )
    if event.body_type:
        text += f" готов ехать {event.body_type}"
    if event.weight_t:
        text += f" ({event.weight_t} т)"
    text += "."
    if phone:
        text += f" Звоните: {phone}."
    text += f"\n— {name}, через GruzPotok"
    return text


async def process_auto_replies() -> int:
    """Check new synced events and auto-reply on behalf of matching carriers.

    Only premium carriers with ``auto_reply_enabled`` participate.
    Returns count of replies sent.
    """
    if not settings.parser_enabled:
        return 0

    cutoff = datetime.utcnow() - timedelta(minutes=6)

    async with async_session() as session:
        events = (
            await session.execute(
                select(ParserIngestEvent)
                .where(
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.created_at >= cutoff,
                    ParserIngestEvent.phone.isnot(None),
                )
                .order_by(ParserIngestEvent.id.desc())
                .limit(30)
            )
        ).scalars().all()

        if not events:
            return 0

        subs = (
            await session.execute(
                select(RouteSubscription).where(
                    RouteSubscription.is_active.is_(True),
                )
            )
        ).scalars().all()

        if not subs:
            return 0

        sent = 0
        for event in events:
            for sub in subs:
                if sub.from_city and event.from_city:
                    if sub.from_city.strip().lower() not in event.from_city.strip().lower():
                        continue
                if sub.to_city and event.to_city:
                    if sub.to_city.strip().lower() not in event.to_city.strip().lower():
                        continue

                carrier = await session.get(User, sub.user_id)
                if not carrier:
                    continue
                if not getattr(carrier, "is_premium", False):
                    continue

                try:
                    from src.bot.bot import bot
                    reply = _build_reply_text(event, carrier)
                    await bot.send_message(
                        sub.user_id,
                        f"🤖 <b>Авто-отклик отправлен!</b>\n\n"
                        f"📋 Груз: {event.from_city} → {event.to_city}\n"
                        f"📞 Диспетчер: {event.phone}\n\n"
                        f"<i>Ваше сообщение:</i>\n{reply}",
                        parse_mode="HTML",
                    )
                    sent += 1
                except Exception as exc:
                    logger.warning("auto_reply send failed user=%s: %s", sub.user_id, exc)

        if sent:
            logger.info("Auto-replies sent: %d", sent)
        return sent
