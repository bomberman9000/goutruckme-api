"""Finance service: transaction ledger, payment tracking, auto-penalty."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from src.core.database import async_session
from src.core.models import Transaction, ParserIngestEvent, CounterpartyList

logger = logging.getLogger(__name__)

DEFAULT_PAYMENT_DAYS = 5
PENALTY_GRACE_HOURS = 24


async def create_transaction(
    *,
    feed_id: int,
    carrier_user_id: int,
    amount_rub: int,
    payment_terms: str | None = None,
    payment_days: int = DEFAULT_PAYMENT_DAYS,
) -> dict:
    """Create a transaction when cargo is marked as delivered."""
    deadline = datetime.utcnow() + timedelta(days=payment_days)

    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        txn = Transaction(
            feed_id=feed_id,
            carrier_user_id=carrier_user_id,
            dispatcher_phone=event.phone if event else None,
            dispatcher_inn=event.inn if event else None,
            amount_rub=amount_rub,
            payment_terms=payment_terms,
            payment_deadline=deadline,
            status="delivered",
        )
        session.add(txn)
        await session.commit()
        await session.refresh(txn)

    return {"ok": True, "transaction_id": txn.id, "deadline": deadline.isoformat()}


async def update_transaction_status(txn_id: int, status: str) -> dict:
    """Update transaction status: delivered → docs_sent → awaiting_payment → paid / disputed."""
    valid = {"delivered", "docs_sent", "awaiting_payment", "paid", "disputed"}
    if status not in valid:
        return {"ok": False, "error": f"invalid status, must be one of {valid}"}

    async with async_session() as session:
        txn = await session.get(Transaction, txn_id)
        if not txn:
            return {"ok": False, "error": "not_found"}
        txn.status = status
        txn.updated_at = datetime.utcnow()
        await session.commit()

    return {"ok": True, "status": status}


async def check_overdue_payments() -> int:
    """Check for overdue payments and apply penalties.

    Called by scheduler. If payment_deadline passed and status is not
    'paid', sends warning. After grace period, auto-blacklists.
    """
    now = datetime.utcnow()
    penalized = 0

    async with async_session() as session:
        overdue = (
            await session.execute(
                select(Transaction).where(
                    Transaction.status.in_(["delivered", "docs_sent", "awaiting_payment"]),
                    Transaction.payment_deadline < now,
                    Transaction.penalty_applied.is_(False),
                )
            )
        ).scalars().all()

        for txn in overdue:
            hours_overdue = (now - txn.payment_deadline).total_seconds() / 3600

            if hours_overdue >= PENALTY_GRACE_HOURS and txn.dispatcher_phone:
                existing = await session.scalar(
                    select(CounterpartyList).where(
                        CounterpartyList.list_type == "black",
                        CounterpartyList.phone == txn.dispatcher_phone,
                    )
                )
                if not existing:
                    bl = CounterpartyList(
                        list_type="black",
                        phone=txn.dispatcher_phone,
                        inn=txn.dispatcher_inn,
                        note=f"Auto-penalty: overdue payment on txn #{txn.id}, {hours_overdue:.0f}h overdue",
                    )
                    session.add(bl)

                txn.penalty_applied = True
                penalized += 1
                logger.warning(
                    "finance: penalty applied txn=%d phone=%s overdue=%.0fh",
                    txn.id, txn.dispatcher_phone, hours_overdue,
                )

            try:
                from src.bot.bot import bot
                text = (
                    f"⚠️ <b>Просрочка оплаты</b>\n\n"
                    f"Заявка #{txn.feed_id} • {txn.amount_rub:,} ₽\n"
                    f"Срок истёк: {txn.payment_deadline.strftime('%d.%m.%Y')}\n"
                )
                if hours_overdue >= PENALTY_GRACE_HOURS:
                    text += "🔴 Рейтинг диспетчера снижен, телефон заблокирован."
                else:
                    remaining = int(PENALTY_GRACE_HOURS - hours_overdue)
                    text += f"⏳ Осталось {remaining}ч до автоматического снижения рейтинга."
                await bot.send_message(txn.carrier_user_id, text, parse_mode="HTML")
            except Exception:
                pass

        if overdue:
            await session.commit()

    if penalized:
        logger.info("finance: penalized %d overdue transactions", penalized)
    return penalized


async def get_carrier_ledger(user_id: int) -> dict:
    """Get carrier's transaction history + summary."""
    async with async_session() as session:
        txns = (
            await session.execute(
                select(Transaction)
                .where(Transaction.carrier_user_id == user_id)
                .order_by(Transaction.id.desc())
                .limit(50)
            )
        ).scalars().all()

        total = await session.scalar(
            select(func.sum(Transaction.amount_rub))
            .where(Transaction.carrier_user_id == user_id, Transaction.status == "paid")
        ) or 0

        pending = await session.scalar(
            select(func.sum(Transaction.amount_rub))
            .where(
                Transaction.carrier_user_id == user_id,
                Transaction.status.in_(["delivered", "docs_sent", "awaiting_payment"]),
            )
        ) or 0

    return {
        "total_earned": total,
        "pending_payment": pending,
        "transaction_count": len(txns),
        "transactions": [
            {
                "id": t.id,
                "feed_id": t.feed_id,
                "amount_rub": t.amount_rub,
                "status": t.status,
                "deadline": t.payment_deadline.isoformat() if t.payment_deadline else None,
                "penalty": t.penalty_applied,
                "created_at": t.created_at.isoformat(),
            }
            for t in txns
        ],
    }
