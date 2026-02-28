from __future__ import annotations

from datetime import datetime

from src.core.audit import log_audit_event
from src.core.config import settings
from src.core.database import async_session
from src.core.logger import logger
from src.core.models import Cargo
from src.core.redis import get_redis
from src.core.services.notifications import (
    collect_matching_available_vehicle_user_ids,
    collect_matching_route_subscriber_ids,
    dispatch_cargo_notification,
)


def _throttle_key(cargo: Cargo) -> str:
    from_city = (cargo.from_city or "").strip().lower()
    to_city = (cargo.to_city or "").strip().lower()
    body = (cargo.cargo_type or "").strip().lower()
    weight = int(round(float(cargo.weight or 0)))
    return (
        f"notify:manual:{int(cargo.owner_id)}:"
        f"{from_city}:{to_city}:{body}:{weight}"
    )


def _mute_key(cargo_id: int) -> str:
    return f"notify:mute:cargo:{int(cargo_id)}"


async def _is_throttled(cargo: Cargo) -> bool:
    try:
        redis = await get_redis()
        created = await redis.set(
            _throttle_key(cargo),
            "1",
            ex=settings.manual_cargo_notify_dedupe_sec,
            nx=True,
        )
        return not bool(created)
    except Exception as exc:
        logger.debug("Notification dispatcher throttle check failed: %s", exc)
        return False


async def is_dispatch_muted(cargo_id: int) -> bool:
    try:
        redis = await get_redis()
        return bool(await redis.exists(_mute_key(cargo_id)))
    except Exception as exc:
        logger.debug("Notification dispatcher mute check failed: %s", exc)
        return False


async def mute_dispatch(cargo_id: int, *, ttl_sec: int | None = None) -> None:
    try:
        redis = await get_redis()
        await redis.set(
            _mute_key(cargo_id),
            "1",
            ex=max(60, int(ttl_sec or settings.admin_notification_mute_sec)),
        )
    except Exception as exc:
        logger.debug("Notification dispatcher mute set failed: %s", exc)


async def notify_matching_carriers(cargo_id: int, *, force: bool = False) -> int:
    """Notify carriers matched by route subscriptions and available fleet."""
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            logger.warning("Notification dispatcher: cargo #%s not found", cargo_id)
            return 0

        if not force and await is_dispatch_muted(cargo_id):
            log_audit_event(
                session,
                entity_type="cargo",
                entity_id=cargo_id,
                action="notification_dispatch_skipped",
                actor_user_id=int(cargo.owner_id),
                actor_role="customer",
                meta={"reason": "muted", "target_count": 0},
            )
            await session.commit()
            logger.info("Notification dispatcher muted cargo #%s", cargo_id)
            return 0

        route_user_ids = await collect_matching_route_subscriber_ids(session, cargo)
        vehicle_user_ids = await collect_matching_available_vehicle_user_ids(session, cargo)
        target_ids = sorted(set(route_user_ids + vehicle_user_ids))

        if not target_ids:
            log_audit_event(
                session,
                entity_type="cargo",
                entity_id=cargo_id,
                action="notification_dispatch_skipped",
                actor_user_id=int(cargo.owner_id),
                actor_role="customer",
                meta={"reason": "no_matches", "target_count": 0},
            )
            await session.commit()

    if not target_ids:
        logger.info("Notification dispatcher: no matches for cargo #%s", cargo_id)
        return 0

    if not force and await _is_throttled(cargo):
        async with async_session() as session:
            log_audit_event(
                session,
                entity_type="cargo",
                entity_id=cargo_id,
                action="notification_dispatch_throttled",
                actor_user_id=int(cargo.owner_id),
                actor_role="customer",
                meta={
                    "target_count": len(target_ids),
                    "dedupe_sec": settings.manual_cargo_notify_dedupe_sec,
                },
            )
            await session.commit()
        logger.info("Notification dispatcher throttled cargo #%s", cargo_id)
        return 0

    sent = await dispatch_cargo_notification(cargo, target_ids)

    async with async_session() as session:
        current = await session.get(Cargo, cargo_id)
        if current and sent:
            current.notified_at = datetime.utcnow()
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=cargo_id,
            action="notification_dispatch",
            actor_user_id=int(cargo.owner_id),
            actor_role="customer",
            meta={
                "target_count": len(target_ids),
                "sent_count": sent,
                "user_ids": target_ids[:50],
            },
        )
        await session.commit()

    logger.info(
        "Notification dispatcher sent %d messages for cargo #%d (targets=%d)",
        sent,
        cargo_id,
        len(target_ids),
    )
    return sent
