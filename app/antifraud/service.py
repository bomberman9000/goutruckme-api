from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.antifraud.docs import apply_escalation_doc_requirements, build_doc_request_plan
from app.antifraud.enforcement import decide_enforcement, upsert_enforcement_decision
from app.antifraud.engine import (
    SERIOUS_REASON_CODES,
    apply_llm_risk_policy_v2,
    merge_doc_request_additive,
    merge_flags,
    merge_reason_codes,
    review_deal_llm_v2,
    review_deal_rules_v3,
)
from app.antifraud.entities import extract_entities_from_deal, link_entities_for_deal, upsert_entities
from app.antifraud.graph import rebuild_components_incremental
from app.antifraud.learning import record_closed_deal
from app.antifraud.lists import check_lists
from app.antifraud.ml import build_features, predict_fraud_probability
from app.antifraud.normalize import norm_inn
from app.antifraud.rates import get_route_rate_profile
from app.antifraud.reputation import get_counterparty_network_risk
from app.core.config import settings
from app.models.models import CounterpartyRiskHistory, DealDocRequest, ModerationReview


logger = logging.getLogger(__name__)

_REVIEW_DURATIONS_MS: deque[int] = deque(maxlen=500)
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_RISK_BY_RANK = {rank: level for level, rank in _RISK_ORDER.items()}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _record_duration(duration_ms: int) -> None:
    _REVIEW_DURATIONS_MS.append(max(int(duration_ms), 0))


def get_average_review_duration_ms() -> float:
    if not _REVIEW_DURATIONS_MS:
        return 0.0
    return float(sum(_REVIEW_DURATIONS_MS)) / float(len(_REVIEW_DURATIONS_MS))


def _raise_risk_one_level(risk_level: str) -> str:
    rank = _RISK_ORDER.get(str(risk_level or "low").lower(), 0)
    return _RISK_BY_RANK.get(min(rank + 1, _RISK_ORDER["high"]), "high")


def _get_or_create_review(db_session: Session, *, entity_type: str, entity_id: int) -> ModerationReview:
    row = (
        db_session.query(ModerationReview)
        .filter(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
        .first()
    )
    if row:
        return row

    row = ModerationReview(entity_type=entity_type, entity_id=entity_id)
    db_session.add(row)
    db_session.flush()
    return row


def _upsert_review(
    db_session: Session,
    *,
    entity_type: str,
    entity_id: int,
    status: str,
    risk_level: str | None,
    flags: dict[str, Any],
    comment: str,
    recommended_action: str,
    model_used: str,
) -> ModerationReview:
    row = _get_or_create_review(
        db_session,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    row.status = status
    row.risk_level = risk_level
    row.flags = flags
    row.comment = comment
    row.recommended_action = recommended_action
    row.model_used = model_used
    row.updated_at = datetime.utcnow()
    db_session.commit()
    db_session.refresh(row)
    return row


def _get_or_create_doc_request(db_session: Session, *, deal_id: int) -> DealDocRequest:
    row = db_session.query(DealDocRequest).filter(DealDocRequest.deal_id == deal_id).first()
    if row:
        return row

    row = DealDocRequest(
        deal_id=deal_id,
        status="requested",
        required_docs=[],
        reason_codes=[],
    )
    db_session.add(row)
    db_session.flush()
    return row


def _upsert_doc_request(
    db_session: Session,
    *,
    deal_id: int,
    status: str,
    required_docs: list[str],
    reason_codes: list[str],
) -> DealDocRequest:
    row = _get_or_create_doc_request(db_session, deal_id=deal_id)
    row.status = status
    row.required_docs = list(required_docs)
    row.reason_codes = list(reason_codes)
    row.updated_at = datetime.utcnow()
    db_session.commit()
    db_session.refresh(row)
    return row


def _build_model_used(llm_result: dict[str, Any]) -> str:
    llm_model = str(llm_result.get("_llm_model") or settings.AI_ANTIFRAUD_LLM_MODEL or "").strip()
    if llm_model:
        return f"rules-v4+{llm_model}"
    return "rules-v4"


def _load_history_summary(db_session: Session, counterparty_inn: str | None) -> dict[str, Any]:
    if not counterparty_inn:
        return {"recent_count": 0, "high_risk_last5": 0, "avg_score_total": 0.0}

    rows = (
        db_session.query(CounterpartyRiskHistory)
        .filter(CounterpartyRiskHistory.counterparty_inn == counterparty_inn)
        .order_by(CounterpartyRiskHistory.created_at.desc())
        .limit(10)
        .all()
    )

    high_risk_last5 = 0
    for row in rows[:5]:
        if str(row.risk_level or "").lower() == "high":
            high_risk_last5 += 1

    avg_score_total = 0.0
    if rows:
        avg_score_total = float(sum(int(row.score_total or 0) for row in rows)) / float(len(rows))

    return {
        "recent_count": len(rows),
        "high_risk_last5": high_risk_last5,
        "avg_score_total": avg_score_total,
    }


def _insert_risk_history(
    db_session: Session,
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
        risk_level=str(risk_level or "low"),
        score_total=int(score_total),
        reason_codes=[str(code) for code in reason_codes],
        created_at=datetime.utcnow(),
    )
    db_session.add(row)
    db_session.commit()


async def run_deal_review_and_save(db_session: Session, deal: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    payload = _as_dict(deal)
    entity_id = _safe_int(payload.get("id"))
    if entity_id <= 0:
        raise ValueError("deal.id is required and must be > 0")

    llm_source = "rules"
    llm_model = "rules-v4"

    try:
        route = _as_dict(payload.get("route"))
        counterparty = _as_dict(payload.get("counterparty"))
        payment = _as_dict(payload.get("payment"))

        counterparty_inn = norm_inn(counterparty.get("inn"))

        route_rate_profile = await get_route_rate_profile(
            db_session,
            from_city=str(route.get("from_city") or ""),
            to_city=str(route.get("to_city") or ""),
            distance_km=route.get("distance_km"),
        )

        list_check = await check_lists(
            db_session,
            inn=str(counterparty.get("inn") or ""),
            phone=str(counterparty.get("phone") or ""),
            name=str(counterparty.get("name") or ""),
        )

        history_summary = _load_history_summary(db_session, counterparty_inn)

        rules_result = review_deal_rules_v3(
            payload,
            route_rate_profile=route_rate_profile,
            list_check=list_check,
            history_summary=history_summary,
        )

        extracted = extract_entities_from_deal(payload)
        entity_rows = await upsert_entities(db_session, extracted)
        deal_node = await link_entities_for_deal(db_session, entity_id, entity_rows)
        await rebuild_components_incremental(
            db_session,
            starting_entity_ids=[int(deal_node.id)] + [int(item.id) for item in entity_rows],
        )

        network_summary = await get_counterparty_network_risk(
            db_session,
            inn=str(counterparty.get("inn") or ""),
            phone=str(counterparty.get("phone") or ""),
            email=str(counterparty.get("email") or ""),
            name=str(counterparty.get("name") or ""),
        )

        ml_features = build_features(payload, rules_result, network_summary)
        ml_prediction = await predict_fraud_probability(db_session, ml_features)
        ml_probability = _safe_float(ml_prediction.get("probability"), 0.0)

        llm_result: dict[str, Any] = {}
        if settings.AI_ANTIFRAUD_ENABLE_LLM:
            llm_result = await review_deal_llm_v2(payload, rules_result, route_rate_profile)
            llm_source = str(llm_result.get("_llm_source") or "none")
            llm_model = str(llm_result.get("_llm_model") or settings.AI_ANTIFRAUD_LLM_MODEL or "")

        merged_flags = merge_flags(
            _as_dict(rules_result.get("flags")),
            _as_dict(llm_result.get("flags")),
        )

        reason_codes = merge_reason_codes(
            [str(code) for code in rules_result.get("reason_codes") or []],
            [str(code) for code in llm_result.get("reason_codes") or []],
            max_extra=2,
        )

        serious = bool(rules_result.get("serious")) or any(code in SERIOUS_REASON_CODES for code in reason_codes)

        rules_risk = str(rules_result.get("risk_level") or "low")
        llm_risk = str(llm_result.get("risk_level") or rules_risk)
        final_risk = apply_llm_risk_policy_v2(rules_risk, llm_risk, serious)

        if ml_probability >= 0.85 and serious:
            final_risk = _raise_risk_one_level(final_risk)
            if "ml_high_probability" not in reason_codes:
                reason_codes.append("ml_high_probability")
            merged_flags["ml_high_probability"] = round(ml_probability, 6)

        if ml_probability >= 0.95 and (
            _safe_int(network_summary.get("component_risk"), 0) >= 80 or bool(merged_flags.get("blacklist_match"))
        ):
            merged_flags["ml_extreme_probability"] = round(ml_probability, 6)

        escalation_triggered = bool(rules_result.get("escalation_triggered"))
        if bool(merged_flags.get("blacklist_match")):
            escalation_triggered = True
        if bool(merged_flags.get("repeat_high_risk_pattern")):
            escalation_triggered = True
        if (
            ("price_statistically_low" in reason_codes or bool(merged_flags.get("price_statistically_low")))
            and ("high_prepay" in reason_codes or bool(merged_flags.get("high_prepay")))
        ):
            escalation_triggered = True

        if escalation_triggered:
            final_risk = "high"

        final_comment = str(llm_result.get("comment") or rules_result.get("comment") or "").strip()
        final_recommended_action = str(
            llm_result.get("recommended_action") or rules_result.get("recommended_action") or ""
        ).strip()

        model_used = "rules-v4"
        if llm_result:
            model_used = _build_model_used(llm_result)

        _upsert_review(
            db_session,
            entity_type="deal",
            entity_id=entity_id,
            status="done",
            risk_level=final_risk,
            flags=merged_flags,
            comment=final_comment,
            recommended_action=final_recommended_action,
            model_used=model_used,
        )

        base_doc_request = build_doc_request_plan(
            risk_level=final_risk,
            reason_codes=reason_codes,
            payment_type=payment.get("type"),
            prepay_percent=payment.get("prepay_percent"),
        )
        base_doc_request["reason_codes"] = reason_codes

        if escalation_triggered:
            base_doc_request = apply_escalation_doc_requirements(base_doc_request)

        llm_doc_request = _as_dict(llm_result.get("doc_request"))
        doc_request_payload = merge_doc_request_additive(base_doc_request, llm_doc_request)
        doc_request_payload["reason_codes"] = reason_codes

        if escalation_triggered:
            doc_request_payload = apply_escalation_doc_requirements(doc_request_payload)

        doc_request_row: DealDocRequest | None = None
        if settings.ANTIFRAUD_DOCS_ENABLE:
            if final_risk in {"medium", "high"}:
                doc_request_row = _upsert_doc_request(
                    db_session,
                    deal_id=entity_id,
                    status="requested",
                    required_docs=[str(item) for item in doc_request_payload.get("required_docs") or []],
                    reason_codes=reason_codes,
                )
            else:
                doc_request_row = _upsert_doc_request(
                    db_session,
                    deal_id=entity_id,
                    status="skipped",
                    required_docs=[],
                    reason_codes=reason_codes,
                )

        if doc_request_row:
            doc_request_payload = {
                "status": doc_request_row.status,
                "required_docs": doc_request_row.required_docs or [],
                "reason_codes": doc_request_row.reason_codes or [],
                "message_template": str(base_doc_request.get("message_template") or "").strip(),
            }
        else:
            doc_request_payload = {
                "status": "disabled",
                "required_docs": doc_request_payload.get("required_docs") or [],
                "reason_codes": reason_codes,
                "message_template": str(base_doc_request.get("message_template") or "").strip(),
            }

        enforcement_input = await decide_enforcement(
            deal_id=entity_id,
            risk_level=final_risk,
            reason_codes=reason_codes,
            flags=merged_flags,
            network_component_risk=_safe_int(network_summary.get("component_risk"), 0),
            ml_probability=ml_probability,
            whitelist_match=bool(list_check.get("whitelist_match")),
            blacklist_match=bool(list_check.get("blacklist_match")),
        )

        enforcement_row = await upsert_enforcement_decision(
            db_session,
            scope="deal",
            scope_id=str(entity_id),
            decision=str(enforcement_input.get("decision") or "allow"),
            reason_codes=[str(code) for code in reason_codes],
            confidence=_safe_int(enforcement_input.get("confidence"), 0),
            created_by="system",
            expires_at=enforcement_input.get("expires_at"),
        )

        _insert_risk_history(
            db_session,
            counterparty_inn=counterparty_inn,
            deal_id=entity_id,
            risk_level=final_risk,
            score_total=int(rules_result.get("score_total") or 0),
            reason_codes=reason_codes,
        )

        if str(payload.get("status") or "").strip().lower() == "closed":
            await record_closed_deal(db_session, payload)

        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_duration(duration_ms)
        logger.info(
            "antifraud.review.v4.done source=%s model=%s duration_ms=%s entity_type=deal entity_id=%s risk=%s",
            llm_source,
            llm_model or model_used,
            duration_ms,
            entity_id,
            final_risk,
        )

        return {
            "entity_type": "deal",
            "entity_id": entity_id,
            "status": "done",
            "risk_level": final_risk,
            "flags": merged_flags,
            "comment": final_comment,
            "recommended_action": final_recommended_action,
            "model_used": model_used,
            "score_total": int(rules_result.get("score_total") or 0),
            "score_breakdown": rules_result.get("score_breakdown") or [],
            "reason_codes": reason_codes,
            "route_rate_profile": rules_result.get("route_rate_profile") or route_rate_profile,
            "list_check": rules_result.get("list_check") or list_check,
            "history_summary": rules_result.get("history_summary") or history_summary,
            "escalation_triggered": escalation_triggered,
            "doc_request": doc_request_payload,
            "review_duration_ms": duration_ms,
            "network": {
                "component_key": network_summary.get("component_key"),
                "component_risk": _safe_int(network_summary.get("component_risk"), 0),
                "entity_risks": (network_summary.get("entity_risks") or [])[:5],
                "connected_blacklist": bool(network_summary.get("connected_blacklist")),
                "top_signals": network_summary.get("top_signals") or [],
            },
            "ml": {
                "probability": round(ml_probability, 6),
                "model_version": _safe_int(ml_prediction.get("model_version"), 0),
            },
            "enforcement": {
                "decision": enforcement_row.decision,
                "confidence": _safe_int(enforcement_row.confidence, 0),
                "expires_at": enforcement_row.expires_at.isoformat() if enforcement_row.expires_at else None,
            },
        }

    except Exception as exc:
        db_session.rollback()
        error_comment = f"antifraud error: {str(exc)[:200]}"
        try:
            _upsert_review(
                db_session,
                entity_type="deal",
                entity_id=entity_id,
                status="error",
                risk_level="high",
                flags={"error": True},
                comment=error_comment,
                recommended_action="Проверить входные данные и повторить модерацию",
                model_used="rules-v4",
            )
        except Exception:
            db_session.rollback()
            logger.exception("antifraud.review.v4.error.upsert_failed entity_id=%s", entity_id)

        duration_ms = int((time.perf_counter() - started) * 1000)
        _record_duration(duration_ms)
        logger.exception(
            "antifraud.review.v4.error source=%s model=%s duration_ms=%s entity_type=deal entity_id=%s",
            llm_source,
            llm_model,
            duration_ms,
            entity_id,
        )
        raise
