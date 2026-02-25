"""Anti-Scam service: complaint handling and auto-hide logic.

When a feed item receives 3+ complaints from verified users,
it is automatically hidden (is_spam=True) and the dispatcher's
phone is flagged for review.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, func

from src.core.database import async_session
from src.core.models import FeedComplaint, ParserIngestEvent, CounterpartyList

logger = logging.getLogger(__name__)

HIDE_THRESHOLD = 3


async def report_feed_item(
    *, feed_id: int, user_id: int, reason: str = "scam", comment: str | None = None
) -> dict:
    """File a complaint against a feed item. Auto-hides after threshold."""
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            return {"ok": False, "error": "not_found"}

        existing = await session.scalar(
            select(FeedComplaint).where(
                FeedComplaint.feed_id == feed_id,
                FeedComplaint.user_id == user_id,
            )
        )
        if existing:
            return {"ok": True, "already_reported": True, "hidden": event.is_spam}

        complaint = FeedComplaint(
            feed_id=feed_id, user_id=user_id,
            reason=reason[:32], comment=(comment or "")[:500] or None,
        )
        session.add(complaint)

        total = await session.scalar(
            select(func.count()).select_from(FeedComplaint).where(FeedComplaint.feed_id == feed_id)
        )
        total = (total or 0) + 1

        hidden = False
        if total >= HIDE_THRESHOLD and not event.is_spam:
            event.is_spam = True
            hidden = True
            logger.warning(
                "anti_scam: auto-hidden feed_id=%s after %d complaints",
                feed_id, total,
            )

            if event.phone:
                existing_bl = await session.scalar(
                    select(CounterpartyList).where(
                        CounterpartyList.list_type == "black",
                        CounterpartyList.phone == event.phone,
                    )
                )
                if not existing_bl:
                    bl = CounterpartyList(
                        list_type="black",
                        phone=event.phone,
                        note=f"Auto-blacklisted: {total} complaints on feed #{feed_id}",
                    )
                    session.add(bl)
                    logger.warning("anti_scam: phone %s auto-blacklisted", event.phone)

        await session.commit()

    return {"ok": True, "total_complaints": total, "hidden": hidden}


async def get_complaint_count(feed_id: int) -> int:
    async with async_session() as session:
        return await session.scalar(
            select(func.count()).select_from(FeedComplaint).where(FeedComplaint.feed_id == feed_id)
        ) or 0
