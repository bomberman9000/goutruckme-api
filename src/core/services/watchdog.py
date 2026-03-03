"""
Watchdog агент: мониторинг здоровья бота, автоперезапуск, уведомления
"""

import asyncio
from datetime import datetime

import httpx

from src.core.ai_diag import explain_health
from src.core.config import settings
from src.core.logger import logger


class BotWatchdog:
    def __init__(self):
        self.last_activity = datetime.utcnow()
        self.error_count = 0
        self.restart_count = 0
        self.is_healthy = True
        self.checks: list[dict] = []

    def heartbeat(self):
        """Обновить время последней активности"""
        self.last_activity = datetime.utcnow()
        self.error_count = 0
        self.is_healthy = True

    def record_error(self, error: str):
        """Записать ошибку"""
        self.error_count += 1
        self.checks.append({
            "time": datetime.utcnow().isoformat(),
            "type": "error",
            "message": error[:200],
        })
        self.checks = self.checks[-50:]

        if self.error_count >= 5:
            self.is_healthy = False

    async def collect_parser_metrics(self) -> dict:
        """Collect parser queue + moderation metrics for health/admin views."""
        metrics = {
            "stream": settings.parser_stream_name,
            "group": settings.parser_stream_group,
            "queue_depth": None,
            "pending": None,
            "lag": None,
            "consumers": None,
            "manual_review": None,
            "synced_24h": None,
            "ignored_24h": None,
            "last_event_age_min": None,
            "heartbeat_key": (settings.parser_heartbeat_key or "").strip() or "parser:heartbeat",
            "heartbeat_age_sec": None,
        }

        try:
            from src.core.redis import get_redis

            redis = await get_redis()
            metrics["queue_depth"] = await redis.xlen(settings.parser_stream_name)
            heartbeat_raw = await redis.get(metrics["heartbeat_key"])
            if heartbeat_raw:
                try:
                    heartbeat_ts = int(float(heartbeat_raw))
                    metrics["heartbeat_age_sec"] = max(
                        0,
                        int(datetime.utcnow().timestamp() - heartbeat_ts),
                    )
                except (TypeError, ValueError):
                    pass
            try:
                groups = await redis.xinfo_groups(settings.parser_stream_name)
                group = next(
                    (item for item in groups if item.get("name") == settings.parser_stream_group),
                    None,
                )
                if group:
                    metrics["pending"] = group.get("pending")
                    metrics["lag"] = group.get("lag")
                    metrics["consumers"] = group.get("consumers")
            except Exception:
                # Stream may exist without a group, or xinfo may not be available yet.
                pass
        except Exception:
            pass

        try:
            from datetime import timedelta
            from sqlalchemy import select, func
            from src.core.database import async_session
            from src.core.models import ParserIngestEvent

            since = datetime.utcnow() - timedelta(hours=24)

            async with async_session() as session:
                latest = await session.scalar(
                    select(func.max(ParserIngestEvent.created_at))
                )
                metrics["manual_review"] = await session.scalar(
                    select(func.count())
                    .select_from(ParserIngestEvent)
                    .where(ParserIngestEvent.status == "manual_review")
                )
                metrics["synced_24h"] = await session.scalar(
                    select(func.count())
                    .select_from(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.status == "synced",
                        ParserIngestEvent.created_at >= since,
                    )
                )
                metrics["ignored_24h"] = await session.scalar(
                    select(func.count())
                    .select_from(ParserIngestEvent)
                    .where(
                        ParserIngestEvent.status.in_(["ignored", "spam_filtered"]),
                        ParserIngestEvent.created_at >= since,
                    )
                )

            if latest:
                metrics["last_event_age_min"] = round(
                    (datetime.utcnow() - latest).total_seconds() / 60
                )
        except Exception:
            pass

        return metrics

    async def check_health(self) -> dict:
        """Проверка здоровья системы"""
        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "bot_healthy": self.is_healthy,
            "last_activity": self.last_activity.isoformat(),
            "error_count": self.error_count,
            "restart_count": self.restart_count,
            "checks": {},
            "metrics": {},
        }

        # Redis
        try:
            from src.core.redis import get_redis
            redis = await get_redis()
            await redis.ping()
            results["checks"]["redis"] = "✅ OK"
        except Exception as e:
            results["checks"]["redis"] = f"❌ Error: {e}"

        # PostgreSQL
        try:
            from src.core.database import async_session
            from sqlalchemy import text
            async with async_session() as session:
                await session.execute(text("SELECT 1"))
            results["checks"]["postgres"] = "✅ OK"
        except Exception as e:
            results["checks"]["postgres"] = f"❌ Error: {e}"

        # Telegram API
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{settings.bot_token}/getMe"
                )
                if resp.status_code == 200:
                    results["checks"]["telegram"] = "✅ OK"
                else:
                    results["checks"]["telegram"] = (
                        f"⚠️ Status {resp.status_code}"
                    )
        except Exception as e:
            results["checks"]["telegram"] = f"❌ Error: {e}"

        # Memory
        try:
            import os
            import psutil
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / 1024 / 1024
            results["checks"]["memory"] = f"✅ {memory_mb:.1f} MB"
            if memory_mb > 500:
                results["checks"]["memory"] = (
                    f"⚠️ High: {memory_mb:.1f} MB"
                )
        except Exception:
            results["checks"]["memory"] = "⚠️ psutil not available"

        # Idle
        idle_seconds = (
            datetime.utcnow() - self.last_activity
        ).total_seconds()
        if idle_seconds > 300:
            results["checks"]["activity"] = f"⚠️ Idle {idle_seconds:.0f}s"
        else:
            results["checks"]["activity"] = (
                f"✅ Active ({idle_seconds:.0f}s ago)"
            )

        # Parser freshness
        try:
            parser_metrics = await self.collect_parser_metrics()
            results["metrics"]["parser"] = parser_metrics

            last_event_age_min = parser_metrics.get("last_event_age_min")
            if last_event_age_min is None:
                results["checks"]["parser"] = "⚠️ No events yet"
            elif last_event_age_min > 30:
                results["checks"]["parser"] = (
                    f"⚠️ No new events for {last_event_age_min:.0f}m"
                )
            else:
                results["checks"]["parser"] = (
                    f"✅ Last event {last_event_age_min:.0f}m ago"
                )

            lag = parser_metrics.get("lag")
            pending = parser_metrics.get("pending")
            queue_depth = parser_metrics.get("queue_depth")
            if lag is not None and pending is not None and queue_depth is not None:
                if lag > 0 or pending > 0:
                    results["checks"]["parser_queue"] = (
                        f"⚠️ depth={queue_depth} pending={pending} lag={lag}"
                    )
                else:
                    results["checks"]["parser_queue"] = f"✅ OK (depth={queue_depth})"

            if settings.parser_enabled:
                heartbeat_age_sec = parser_metrics.get("heartbeat_age_sec")
                if heartbeat_age_sec is None:
                    results["checks"]["parser_heartbeat"] = "⚠️ No heartbeat"
                elif heartbeat_age_sec > max(60, int(settings.parser_self_kill_after_sec)):
                    results["checks"]["parser_heartbeat"] = (
                        f"❌ Stale {heartbeat_age_sec}s"
                    )
                else:
                    results["checks"]["parser_heartbeat"] = (
                        f"✅ {heartbeat_age_sec}s ago"
                    )
            else:
                results["checks"]["parser_heartbeat"] = "ℹ️ Parser disabled"
        except Exception:
            results["checks"]["parser"] = "⚠️ Unable to check"

        return results

    def format_status(self, health: dict) -> str:
        """Форматирует статус для отправки"""
        status = (
            "🟢 Здоров" if health["bot_healthy"] else "🔴 Проблемы"
        )

        text = "🤖 <b>Статус бота</b>\n\n"
        text += f"📊 Состояние: {status}\n"
        text += f"⏱ Проверка: {health['timestamp'][:19]}\n"
        text += f"🔄 Перезапусков: {health['restart_count']}\n"
        text += f"❌ Ошибок: {health['error_count']}\n\n"
        text += "<b>Компоненты:</b>\n"
        for name, status_val in health["checks"].items():
            text += f"• {name}: {status_val}\n"
        return text


watchdog = BotWatchdog()

# Кулдаун алертов: не слать одно и то же чаще чем раз в 15 минут
ALERT_COOLDOWN_SEC = 900
_last_alert: dict[str, float] = {}


async def notify_admin(message: str, alert_key: str = "default"):
    """Отправить уведомление админу (с кулдауном по alert_key)."""
    if settings.admin_id is None:
        return
    now = datetime.utcnow().timestamp()
    if now - _last_alert.get(alert_key, 0) < ALERT_COOLDOWN_SEC:
        return
    _last_alert[alert_key] = now
    try:
        from src.bot.bot import bot
        await bot.send_message(
            settings.admin_id,
            f"🚨 <b>Watchdog Alert</b>\n\n{message}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)


async def watchdog_loop():
    """Фоновый цикл мониторинга."""
    while True:
        try:
            await asyncio.sleep(60)

            health = await watchdog.check_health()

            if not health["bot_healthy"]:
                await notify_admin(
                    "Обнаружены проблемы!\n\n"
                    f"Ошибок: {health['error_count']}\n"
                    f"Последняя активность: {health['last_activity']}",
                    alert_key="bot_unhealthy",
                )

            for name, status in health["checks"].items():
                if "❌" in str(status):
                    await notify_admin(
                        f"Компонент {name} недоступен:\n{status}\n\n{explain_health(health)}",
                        alert_key=f"check_{name}",
                    )

            parser_status = str(health["checks"].get("parser", ""))
            if "No new events for" in parser_status:
                await notify_admin(
                    f"⚠️ Парсер простаивает!\n\n{parser_status}\n\n"
                    f"{explain_health(health)}",
                    alert_key="parser_stale",
                )

            parser_heartbeat = str(health["checks"].get("parser_heartbeat", ""))
            if "No heartbeat" in parser_heartbeat or "Stale" in parser_heartbeat:
                await notify_admin(
                    f"⚠️ Heartbeat парсера проблемный:\n{parser_heartbeat}\n\n"
                    f"{explain_health(health)}",
                    alert_key="parser_heartbeat",
                )

            parser_metrics = health.get("metrics", {}).get("parser", {})
            manual_review_count = parser_metrics.get("manual_review")
            threshold = settings.parser_manual_review_alert_threshold
            if (
                isinstance(manual_review_count, int)
                and threshold > 0
                and manual_review_count >= threshold
            ):
                await notify_admin(
                    "⚠️ Очередь ручной проверки переполнена!\n\n"
                    f"Сейчас: {manual_review_count}\n"
                    f"Порог: {threshold}\n\n"
                    f"{explain_health(health)}",
                    alert_key="manual_review_backlog",
                )

        except Exception as e:
            logger.error("Watchdog loop error: %s", e)
            await asyncio.sleep(30)
