"""Weekly market report generator.

Produces a PDF/text summary of market activity: top routes, price
trends, most active dispatchers.  Designed to be posted to a Telegram
channel automatically.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from src.core.database import async_session
from src.core.geo import city_coords, haversine_km
from src.core.models import ParserIngestEvent

logger = logging.getLogger(__name__)


async def generate_weekly_report_text() -> str:
    """Generate a text market report for the last 7 days."""
    cutoff = datetime.utcnow() - timedelta(days=7)

    async with async_session() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(ParserIngestEvent)
            .where(
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.is_spam.is_(False),
                ParserIngestEvent.created_at >= cutoff,
            )
        )

        top_routes = (
            await session.execute(
                select(
                    ParserIngestEvent.from_city,
                    ParserIngestEvent.to_city,
                    func.count().label("cnt"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg_rate"),
                )
                .where(
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.created_at >= cutoff,
                    ParserIngestEvent.rate_rub.isnot(None),
                )
                .group_by(ParserIngestEvent.from_city, ParserIngestEvent.to_city)
                .order_by(func.count().desc())
                .limit(5)
            )
        ).all()

        top_dispatchers = (
            await session.execute(
                select(
                    ParserIngestEvent.phone,
                    func.count().label("cnt"),
                )
                .where(
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.phone.isnot(None),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .group_by(ParserIngestEvent.phone)
                .order_by(func.count().desc())
                .limit(5)
            )
        ).all()

        hot_deals = await session.scalar(
            select(func.count())
            .select_from(ParserIngestEvent)
            .where(
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.is_hot_deal.is_(True),
                ParserIngestEvent.created_at >= cutoff,
            )
        )

    now = datetime.utcnow()
    week_start = (now - timedelta(days=7)).strftime("%d.%m")
    week_end = now.strftime("%d.%m.%Y")

    text = "📊 <b>Анализ рынка грузоперевозок</b>\n"
    text += f"📅 {week_start} — {week_end}\n\n"
    text += f"📦 Всего грузов: <b>{total or 0}</b>\n"
    text += f"🔥 Выгодных сделок: <b>{hot_deals or 0}</b>\n\n"

    if top_routes:
        text += "<b>🏆 Топ-5 маршрутов:</b>\n"
        for i, r in enumerate(top_routes, 1):
            avg = int(r.avg_rate or 0)
            dist = None
            fc = city_coords(r.from_city) if r.from_city else None
            tc = city_coords(r.to_city) if r.to_city else None
            if fc and tc:
                dist = int(haversine_km(fc[0], fc[1], tc[0], tc[1]))
            rpk = f" ({avg // dist} ₽/км)" if dist and avg else ""
            text += f"  {i}. {r.from_city} → {r.to_city}: {r.cnt} грузов, avg {avg:,} ₽{rpk}\n"
        text += "\n"

    if top_dispatchers:
        text += "<b>📞 Самые активные диспетчеры:</b>\n"
        for i, d in enumerate(top_dispatchers, 1):
            masked = d.phone[:7] + "****" if d.phone and len(d.phone) > 7 else d.phone
            text += f"  {i}. {masked}: {d.cnt} грузов\n"
        text += "\n"

    text += "<i>Данные от ГрузПоток — умная биржа грузоперевозок</i>"
    return text


async def send_weekly_report() -> None:
    """Generate and send the weekly report to the admin."""
    from src.core.config import settings

    if not settings.admin_id:
        return

    try:
        from src.bot.bot import bot
        text = await generate_weekly_report_text()
        await bot.send_message(settings.admin_id, text, parse_mode="HTML")
        logger.info("Weekly market report sent to admin")
    except Exception as exc:
        logger.error("Failed to send weekly report: %s", exc)
