from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime
from sqlalchemy import select
from src.core.logger import logger

scheduler = AsyncIOScheduler()

async def daily_stats_job():
    from src.bot.bot import bot
    from src.core.config import settings
    from src.core.redis import get_redis
    from src.core.database import async_session
    from src.core.models import User
    from sqlalchemy import func

    if not settings.admin_id:
        return

    redis = await get_redis()
    async with async_session() as session:
        users_count = await session.scalar(select(func.count()).select_from(User))

    messages = await redis.get("stats:messages") or 0

    await bot.send_message(
        settings.admin_id,
        f"📊 Ежедневный отчёт:\n\n👥 Пользователей: {users_count}\n💬 Сообщений: {messages}"
    )
    logger.info("Daily stats sent")

async def check_reminders_job():
    from src.bot.bot import bot
    from src.core.database import async_session
    from src.core.models import Reminder

    async with async_session() as session:
        result = await session.execute(
            select(Reminder)
            .where(Reminder.is_sent == False)
            .where(Reminder.remind_at <= datetime.utcnow())
        )
        reminders = result.scalars().all()

        for r in reminders:
            try:
                await bot.send_message(r.user_id, f"⏰ Напоминание:\n\n{r.text}")
                r.is_sent = True
                logger.info(f"Reminder sent to {r.user_id}")
            except Exception as e:
                logger.error(f"Failed to send reminder: {e}")

        await session.commit()

async def archive_old_cargos_job():
    from src.core.database import async_session
    from src.core.models import Cargo, CargoStatus

    cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.status == CargoStatus.NEW)
            .where(Cargo.load_date < cutoff)
        )
        cargos = result.scalars().all()

        for cargo in cargos:
            cargo.status = CargoStatus.ARCHIVED

        if cargos:
            await session.commit()
            logger.info("Archived cargos: %s", len(cargos))


async def push_notifications_job():
    """Find cargos created in the last 5 min that haven't been notified yet."""
    from datetime import timedelta
    from src.core.database import async_session
    from src.core.models import Cargo, CargoStatus
    from src.core.services.notifications import notify_subscribers

    cutoff = datetime.utcnow() - timedelta(minutes=6)
    async with async_session() as session:
        result = await session.execute(
            select(Cargo)
            .where(Cargo.status == CargoStatus.NEW)
            .where(Cargo.notified_at.is_(None))
            .where(Cargo.created_at >= cutoff)
        )
        cargos = result.scalars().all()

        for cargo in cargos:
            try:
                await notify_subscribers(cargo)
                cargo.notified_at = datetime.utcnow()
            except Exception as e:
                logger.error("Push notification error for cargo #%s: %s", cargo.id, e)

        if cargos:
            await session.commit()
            logger.info("Push-notified %d new cargos", len(cargos))


async def feed_notifications_job():
    """Send notifications for new parser feed items matching subscriptions."""
    try:
        from src.core.services.feed_notifications import notify_feed_subscribers
        await notify_feed_subscribers()
    except Exception as e:
        logger.error("Feed notification error: %s", e)


async def auto_reply_job():
    """Auto-reply to dispatchers on behalf of trusted carriers."""
    try:
        from src.core.services.auto_reply import process_auto_replies
        await process_auto_replies()
    except Exception as e:
        logger.error("Auto-reply error: %s", e)


async def reverse_matching_job():
    """Find cargos for available vehicles and notify carriers."""
    from src.core.database import async_session
    from src.core.models import UserVehicle, ParserIngestEvent
    from sqlalchemy import select, or_

    try:
        async with async_session() as session:
            vehicles = (
                await session.execute(
                    select(UserVehicle).where(
                        UserVehicle.is_available.is_(True),
                        UserVehicle.location_city.isnot(None),
                    )
                )
            ).scalars().all()

            if not vehicles:
                return

            for vehicle in vehicles:
                city = (vehicle.location_city or "").strip()
                if not city:
                    continue

                stmt = (
                    select(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.is_spam.is_(False),
                        ParserIngestEvent.status == "synced",
                        ParserIngestEvent.from_city.ilike(f"%{city}%"),
                        ParserIngestEvent.created_at >= datetime.utcnow() - __import__("datetime").timedelta(minutes=10),
                    )
                )
                if vehicle.body_type:
                    stmt = stmt.where(
                        or_(
                            ParserIngestEvent.body_type.ilike(f"%{vehicle.body_type}%"),
                            ParserIngestEvent.body_type.is_(None),
                        )
                    )

                matches = (
                    await session.execute(stmt.order_by(ParserIngestEvent.id.desc()).limit(3))
                ).scalars().all()

                if not matches:
                    continue

                try:
                    from src.bot.bot import bot
                    text = "🚛 <b>Подбор для вашей машины</b>\n"
                    text += f"📍 {city} | {vehicle.body_type} {vehicle.capacity_tons}т\n\n"
                    for m in matches:
                        hot = "🔥 " if m.is_hot_deal else ""
                        text += f"{hot}<b>{m.from_city} → {m.to_city}</b>\n"
                        text += f"  {m.body_type or '?'} {m.weight_t or 0}т • {m.rate_rub or 0:,}₽"
                        if m.load_date:
                            text += f" • 📅 {m.load_date}"
                        text += f"\n  /cargo_{m.id}\n\n"
                    await bot.send_message(vehicle.user_id, text, parse_mode="HTML")
                except Exception as exc:
                    logger.debug("reverse_matching notify failed user=%s: %s", vehicle.user_id, exc)

        logger.info("reverse_matching: checked %d vehicles", len(vehicles))
    except Exception as e:
        logger.error("Reverse matching error: %s", e)


async def retention_nudge_job():
    """Nudge carriers whose vehicles have been idle for 4+ hours."""
    from datetime import timedelta
    from src.core.database import async_session
    from src.core.models import UserVehicle, ParserIngestEvent
    from sqlalchemy import select, func as sa_func

    try:
        async with async_session() as session:
            vehicles = (
                await session.execute(
                    select(UserVehicle).where(
                        UserVehicle.is_available.is_(True),
                        UserVehicle.location_city.isnot(None),
                    )
                )
            ).scalars().all()

            for vehicle in vehicles:
                city = (vehicle.location_city or "").strip()
                if not city:
                    continue

                count = await session.scalar(
                    select(sa_func.count()).select_from(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.is_spam.is_(False),
                        ParserIngestEvent.status == "synced",
                        ParserIngestEvent.from_city.ilike(f"%{city}%"),
                        ParserIngestEvent.created_at >= datetime.utcnow() - timedelta(hours=4),
                    )
                )
                if not count:
                    continue

                try:
                    from src.bot.bot import bot
                    text = (
                        f"💤 Вы простаиваете в <b>{city}</b>.\n"
                        f"По вашему профилю ({vehicle.body_type} {vehicle.capacity_tons}т) "
                        f"есть <b>{count} грузов</b> за последние 4 часа.\n\n"
                        f"Посмотреть: /feed {city} {vehicle.body_type}"
                    )
                    await bot.send_message(vehicle.user_id, text, parse_mode="HTML")
                except Exception:
                    pass

        logger.info("retention_nudge: checked %d idle vehicles", len(vehicles))
    except Exception as e:
        logger.error("Retention nudge error: %s", e)


async def overdue_payment_check_job():
    """Check for overdue payments and apply penalties."""
    try:
        from src.core.services.finance import check_overdue_payments
        await check_overdue_payments()
    except Exception as e:
        logger.error("Overdue payment check error: %s", e)


async def auto_purge_job():
    """Delete parser_ingest_events older than 14 days to keep DB lean."""
    from datetime import timedelta
    from src.core.database import async_session
    from src.core.models import ParserIngestEvent
    from sqlalchemy import delete

    cutoff = datetime.utcnow() - timedelta(days=14)
    try:
        async with async_session() as session:
            result = await session.execute(
                delete(ParserIngestEvent).where(
                    ParserIngestEvent.created_at < cutoff
                )
            )
            deleted = result.rowcount
            await session.commit()
        if deleted:
            logger.info("Auto-purge: deleted %d old events", deleted)
    except Exception as e:
        logger.error("Auto-purge error: %s", e)


async def marketing_post_job():
    """Auto-post best cargos to Telegram channel (3x daily)."""
    try:
        from src.core.services.marketing import post_to_channel
        await post_to_channel()
    except Exception as e:
        logger.error("Marketing post error: %s", e)


async def weekly_report_job():
    """Send weekly market report to admin."""
    try:
        from src.core.services.market_report import send_weekly_report
        await send_weekly_report()
    except Exception as e:
        logger.error("Weekly report error: %s", e)


def setup_scheduler():
    scheduler.add_job(daily_stats_job, CronTrigger(hour=9, minute=0), id="daily_stats")
    scheduler.add_job(check_reminders_job, IntervalTrigger(seconds=30), id="check_reminders")
    scheduler.add_job(archive_old_cargos_job, CronTrigger(hour=0, minute=10), id="archive_cargos")
    scheduler.add_job(push_notifications_job, IntervalTrigger(minutes=5), id="push_notifications")
    scheduler.add_job(feed_notifications_job, IntervalTrigger(minutes=5), id="feed_notifications")
    scheduler.add_job(auto_reply_job, IntervalTrigger(minutes=5), id="auto_reply")
    scheduler.add_job(reverse_matching_job, IntervalTrigger(minutes=5), id="reverse_matching")
    scheduler.add_job(retention_nudge_job, IntervalTrigger(hours=4), id="retention_nudge")
    scheduler.add_job(overdue_payment_check_job, IntervalTrigger(hours=1), id="overdue_payments")
    scheduler.add_job(auto_purge_job, CronTrigger(hour=3, minute=0), id="auto_purge")
    scheduler.add_job(weekly_report_job, CronTrigger(day_of_week="mon", hour=9, minute=0), id="weekly_report")
    scheduler.add_job(marketing_post_job, CronTrigger(hour="9,15,21", minute=0), id="marketing_post")
    scheduler.start()
    logger.info("Scheduler started")
