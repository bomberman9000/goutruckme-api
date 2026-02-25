from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.antifraud.docs import apply_escalation_doc_requirements, build_doc_request_plan
from src.antifraud.engine import review_deal_rules_v3
from src.antifraud.learning import record_closed_deal
from src.antifraud.lists import check_lists
from src.antifraud.normalize import norm_inn
from src.antifraud.rates import get_route_rate_profile
from src.core.config import settings
from src.core.models import CounterpartyRiskHistory, DealDocRequest, ModerationReview


logger = logging.getLogger(__name__)

_REVIEW_DURATIONS_MS: deque[int] = deque(maxlen=500)


def _record_duration(duration_ms: int) -> None:
    _REVIEW_DURATIONS_MS.append(max(int(duration_ms), 0))


def get_average_review_duration_ms() -> float:
    if not _REVIEW_DURATIONS_MS:
        return 0.0
    return float(sum(_REVIEW_DURATIONS_MS)) / float(len(_REVIEW_DURATIONS_MS))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


async def _get_or_create_review(db: AsyncSession, *, entity_type: str, entity_id: int) -> ModerationReview:
    result = await db.execute(
        select(ModerationReview).where(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = ModerationReview(entity_type=entity_type, entity_id=entity_id)
    db.add(row)
    await db.flush()
    return row


async def _get_or_create_doc_request(db: AsyncSession, *, deal_id: int) -> DealDocRequest:
    result = await db.execute(select(DealDocRequest).where(DealDocRequest.deal_id == deal_id))
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = DealDocRequest(deal_id=deal_id)
    db.add(row)
    await db.flush()
    return row


async def _load_history_summary(db: AsyncSession, counterparty_inn: str | None) -> dict[str, Any]:
    if not counterparty_inn:
        return {"recent_count": 0, "high_risk_last5": 0, "avg_score_total": 0.0}

    result = await db.execute(
        select(CounterpartyRiskHistory)
        .where(CounterpartyRiskHistory.counterparty_inn == counterparty_inn)
        .order_by(CounterpartyRiskHistory.created_at.desc())
        .limit(10)
    )
    rows = list(result.scalars().all())

    high_risk_last5 = sum(1 for row in rows[:5] if str(row.risk_level or "").lower() == "high")
    avg_score_total = 0.0
    if rows:
        avg_score_total = float(sum(int(row.score_total or 0) for row in rows)) / float(len(rows))

    return {
        "recent_count": len(rows),
        "high_risk_last5": high_risk_last5,
        "avg_score_total": avg_score_total,
    }


async def _insert_risk_history(
    db: AsyncSession,
    *,
    counterparty_inn: str | None,
    deal_id: int,
    risk_level: str,
    score_total: int,
    reason_codes: list[str],
) -> None:
    if not counterparty_inn:
        return
    row = CounterpartyRiskHistory(
        counterparty_inn=counterparty_inn,
        deal_id=deal_id,
        risk_level=risk_level,
        score_total=score_total,
        reason_codes_json=_dump_json(reason_codes),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    await db.flush()


async def run_deal_review_and_save(db: AsyncSession, deal: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    payload = deal if isinstance(deal, dict) else {}
    deal_id = _to_int(payload.get("id"), 0)
    if deal_id <= 0:
        raise ValueError("deal.id is required")

    try:
        route = payload.get("route") if isinstance(payload.get("route"), dict) else {}
        payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
        counterparty = payload.get("counterparty") if isinstance(payload.get("counterparty"), dict) else {}
        counterparty_inn = norm_inn(counterparty.get("inn"))

        route_rate_profile = await get_route_rate_profile(
            db,
            from_city=route.get("from_city"),
            to_city=route.get("to_city"),
            distance_km=route.get("distance_km"),
        )
        list_check = await check_lists(
            db,
            inn=counterparty.get("inn"),
            phone=counterparty.get("phone"),
            name=counterparty.get("name"),
        )
        history_summary = await _load_history_summary(db, counterparty_inn)

        rules = review_deal_rules_v3(
            payload,
            route_rate_profile=route_rate_profile,
            list_check=list_check,
            history_summary=history_summary,
        )

        risk_level = str(rules.get("risk_level") or "low")
        reason_codes = [str(code) for code in (rules.get("reason_codes") or [])]
        escalation_triggered = bool(rules.get("escalation_triggered"))
        if escalation_triggered:
            risk_level = "high"

        row = await _get_or_create_review(db, entity_type="deal", entity_id=deal_id)
        row.status = "done"
        row.risk_level = risk_level
        row.flags_json = _dump_json(rules.get("flags") or {})
        row.comment = str(rules.get("comment") or "")
        row.recommended_action = str(rules.get("recommended_action") or "")
        row.model_used = "rules-v3"

        doc_plan = build_doc_request_plan(
            risk_level=risk_level,
            reason_codes=reason_codes,
            payment_type=str(payment.get("type") or "unknown"),
            prepay_percent=payment.get("prepay_percent"),
        )
        if escalation_triggered:
            doc_plan = apply_escalation_doc_requirements(doc_plan)

        doc_status = "disabled"
        if settings.antifraud_docs_enable:
            doc_row = await _get_or_create_doc_request(db, deal_id=deal_id)
            doc_status = "requested" if risk_level in {"medium", "high"} else "skipped"
            doc_row.status = doc_status
            doc_row.required_docs_json = _dump_json(doc_plan.get("required_docs") or [])
            doc_row.reason_codes_json = _dump_json(reason_codes)

        await _insert_risk_history(
            db,
            counterparty_inn=counterparty_inn,
            deal_id=deal_id,
            risk_level=risk_level,
            score_total=_to_int(rules.get("score_total"), 0),
            reason_codes=reason_codes,
        )

        if str(payload.get("status") or "").strip().lower() == "closed":
            await record_closed_deal(db, payload)

        await db.commit()

        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_duration(duration_ms)
        logger.info(
            "antifraud.review.v3.done duration_ms=%s entity_type=deal entity_id=%s risk=%s",
            duration_ms,
            deal_id,
            risk_level,
        )

        return {
            "entity_type": "deal",
            "entity_id": deal_id,
            "status": row.status,
            "risk_level": risk_level,
            "flags": _load_json(row.flags_json, {}),
            "comment": row.comment,
            "recommended_action": row.recommended_action,
            "model_used": row.model_used,
            "score_total": _to_int(rules.get("score_total"), 0),
            "score_breakdown": list(rules.get("score_breakdown") or []),
            "reason_codes": reason_codes,
            "route_rate_profile": dict(rules.get("route_rate_profile") or {}),
            "list_check": dict(rules.get("list_check") or {}),
            "history_summary": dict(rules.get("history_summary") or history_summary),
            "escalation_triggered": escalation_triggered,
            "doc_request": {
                "status": doc_status,
                "required_docs": list(doc_plan.get("required_docs") or []),
                "reason_codes": reason_codes,
                "message_template": str(doc_plan.get("message_template") or ""),
            },
            "review_duration_ms": duration_ms,
        }
    except Exception:
        await db.rollback()
        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_duration(duration_ms)
        logger.exception("antifraud.review.v3.error entity_type=deal entity_id=%s", deal_id)
        raise
