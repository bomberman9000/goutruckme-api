from __future__ import annotations

from datetime import datetime

from src.core.database import async_session
from src.core.logger import logger
from src.core.models import Cargo
from src.core.services.notifications import (
    collect_matching_available_vehicle_user_ids,
    collect_matching_route_subscriber_ids,
    dispatch_cargo_notification,
)


async def notify_matching_carriers(cargo_id: int) -> int:
    """Notify carriers matched by route subscriptions and available fleet."""
    async with async_session() as session:
        cargo = await session.get(Cargo, cargo_id)
        if not cargo:
            logger.warning("Notification dispatcher: cargo #%s not found", cargo_id)
            return 0

        route_user_ids = await collect_matching_route_subscriber_ids(session, cargo)
        vehicle_user_ids = await collect_matching_available_vehicle_user_ids(session, cargo)
        target_ids = sorted(set(route_user_ids + vehicle_user_ids))

    if not target_ids:
        logger.info("Notification dispatcher: no matches for cargo #%s", cargo_id)
        return 0

    sent = await dispatch_cargo_notification(cargo, target_ids)

    if sent:
        async with async_session() as session:
            current = await session.get(Cargo, cargo_id)
            if current:
                current.notified_at = datetime.utcnow()
                await session.commit()

    logger.info(
        "Notification dispatcher sent %d messages for cargo #%d (targets=%d)",
        sent,
        cargo_id,
        len(target_ids),
    )
    return sent
