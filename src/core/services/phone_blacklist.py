"""Phone blacklist checker.

Queries the ``counterparty_lists`` table (list_type='black') to see if
a phone number belongs to a known bad actor.  Used by the worker to
flag potentially risky contacts in feed items.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from src.core.database import async_session
from src.core.models import CounterpartyList

logger = logging.getLogger(__name__)


async def is_phone_blacklisted(phone: str | None) -> bool:
    """Return True if the phone is in the blacklist."""
    if not phone:
        return False

    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 10:
        return False

    normalized = f"+{digits}" if not phone.startswith("+") else phone

    async with async_session() as session:
        result = await session.scalar(
            select(CounterpartyList.id)
            .where(
                CounterpartyList.list_type == "black",
                CounterpartyList.phone == normalized,
            )
            .limit(1)
        )
    if result is not None:
        logger.warning("phone_blacklist hit: %s", normalized)
    return result is not None
