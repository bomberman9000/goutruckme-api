"""Marketing engine — auto-post best cargos to Telegram channels.

Runs as a scheduler job. Picks top hot deals and high-rate cargos,
formats them as attractive posts, and sends to configured channels.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from src.core.config import settings
from src.core.database import async_session
from src.core.geo import city_coords, haversine_km
from src.core.models import ParserIngestEvent

logger = logging.getLogger(__name__)


async def build_marketing_post(limit: int = 5) -> str | None:
    """Build an attractive marketing post from the best recent cargos."""
    cutoff = datetime.utcnow() - timedelta(hours=6)

    async with async_session() as session:
        hot_deals = (
            await session.execute(
                select(ParserIngestEvent)
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_hot_deal.is_(True),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .order_by(ParserIngestEvent.rate_rub.desc())
                .limit(limit)
            )
        ).scalars().all()

        if not hot_deals:
            hot_deals = (
                await session.execute(
                    select(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.is_spam.is_(False),
                        ParserIngestEvent.status == "synced",
                        ParserIngestEvent.rate_rub.isnot(None),
                        ParserIngestEvent.created_at >= cutoff,
                    )
                    .order_by(ParserIngestEvent.rate_rub.desc())
                    .limit(limit)
                )
            ).scalars().all()

        total = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent)
            .where(
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.created_at >= cutoff,
            )
        ) or 0

    if not hot_deals:
        return None

    now = datetime.utcnow()
    text = "🚛 <b>ГрузПоток — Лучшие грузы прямо сейчас</b>\n"
    text += f"📊 {total} грузов за последние 6 часов\n\n"

    from src.core.ai import calculate_market_rate

    for i, ev in enumerate(hot_deals, 1):
        hot = "🔥 " if ev.is_hot_deal else ""

        fc = city_coords(ev.from_city) if ev.from_city else None
        tc = city_coords(ev.to_city) if ev.to_city else None
        dist = haversine_km(fc[0], fc[1], tc[0], tc[1]) if fc and tc else None

        # Рекомендованная рыночная цена
        rec = None
        if dist and dist > 50:
            try:
                mr = calculate_market_rate(
                    from_city=ev.from_city,
                    to_city=ev.to_city,
                    distance_km=dist,
                    weight=ev.weight_t or 20.0,
                    body_type=ev.body_type,
                )
                rec = mr.get("price")
            except Exception:
                pass

        rpk = f" ({int(ev.rate_rub / dist)} ₽/км)" if ev.rate_rub and dist and dist > 10 else ""
        rate_str = f"{ev.rate_rub:,}".replace(",", " ") if ev.rate_rub else None

        text += f"{i}. {hot}<b>{ev.from_city} → {ev.to_city}</b>\n"
        text += f"   {ev.body_type or '?'} • {ev.weight_t or 0}т"
        if rate_str:
            text += f" • {rate_str} ₽{rpk}"
        text += "\n"
        if rec:
            rec_str = f"{rec:,}".replace(",", " ")
            diff = ""
            if ev.rate_rub and rec:
                pct = round((ev.rate_rub - rec) / rec * 100)
                if pct > 10:
                    diff = f" 🟢 +{pct}% к рынку"
                elif pct < -10:
                    diff = f" 🔴 {pct}% к рынку"
            text += f"   📊 Рек. цена: {rec_str} ₽{diff}\n"
        if ev.load_date:
            text += f"   📅 {ev.load_date}\n"
        text += "\n"

    text += "━━━━━━━━━━━━━━━━━━━━\n"
    text += "📱 Полная лента: @gruzpotok_bot\n"
    text += f"⏱ {now.strftime('%d.%m.%Y %H:%M')} UTC"

    return text


async def post_to_channel() -> bool:
    """Build and post marketing content to the admin/channel."""
    if not settings.admin_id:
        return False

    text = await build_marketing_post()
    if not text:
        logger.info("marketing: no cargos for post")
        return False

    try:
        from src.bot.bot import bot
        await bot.send_message(settings.admin_id, text, parse_mode="HTML")
        logger.info("marketing: post sent to admin")
        return True
    except Exception as exc:
        logger.error("marketing: post failed: %s", exc)
        return False
