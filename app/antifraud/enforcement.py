from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.antifraud.ml import train_model
from app.core.config import settings
from app.models.models import EnforcementDecision, FraudSignal


_SERIOUS_CODES = {
    "high_prepay",
    "has_complaints",
    "price_too_low",
    "price_statistically_low",
    "low_trust_score",
    "blacklist_match",
    "repeat_high_risk_pattern",
}

_ALLOWED_DECISIONS = {"allow", "soft_block", "hard_block", "manual_review"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_any(values: set[str], candidates: list[str]) -> bool:
    return any(str(item) in values for item in candidates)


def _signal_severity_for_decision(decision: str) -> int:
    value = _normalize_decision(decision)
    if value == "hard_block":
        return 5
    if value == "manual_review":
        return 4
    if value == "soft_block":
        return 3
    return 1


def _normalize_decision(value: Any, default: str = "allow") -> str:
    decision = str(value or "").strip().lower()
    if decision in _ALLOWED_DECISIONS:
        return decision
    return default


async def decide_enforcement(
    *,
    deal_id: int,
    risk_level: str,
    reason_codes: list[str],
    flags: dict[str, Any],
    network_component_risk: int,
    ml_probability: float,
    whitelist_match: bool,
    blacklist_match: bool,
) -> dict[str, Any]:
    reasons = [str(code) for code in _as_list(reason_codes)]
    serious = _has_any(_SERIOUS_CODES, reasons) or bool(flags.get("blacklist_match"))

    high_prepay = bool(flags.get("high_prepay")) or "high_prepay" in reasons
    price_stat_low = bool(flags.get("price_statistically_low")) or "price_statistically_low" in reasons
    repeat_pattern = bool(flags.get("repeat_high_risk_pattern")) or "repeat_high_risk_pattern" in reasons

    decision = "allow"
    confidence = 60
    expires_at: datetime | None = None

    if whitelist_match and not serious:
        decision = "allow"
        confidence = 75
    elif blacklist_match:
        if settings.ANTIFRAUD_STRICT_MODE:
            decision = "hard_block"
            confidence = 95
        else:
            decision = "manual_review"
            confidence = 90
    elif network_component_risk >= 85 and (high_prepay or price_stat_low):
        decision = "hard_block"
        confidence = 90
    elif ml_probability >= 0.95 and (network_component_risk >= 80 or blacklist_match):
        decision = "hard_block"
        confidence = 92
    elif ml_probability >= 0.85 and serious:
        decision = "soft_block"
        confidence = 80
        expires_at = datetime.utcnow() + timedelta(hours=48)
    elif repeat_pattern:
        decision = "manual_review"
        confidence = 75
    else:
        risk = str(risk_level or "low").strip().lower()
        if risk == "low":
            decision = "allow"
            confidence = 60
        elif risk == "medium":
            decision = "soft_block"
            confidence = 70
            expires_at = datetime.utcnow() + timedelta(hours=48)
        else:
            if network_component_risk >= 70:
                decision = "manual_review"
                confidence = 78
            else:
                decision = "soft_block"
                confidence = 74
                expires_at = datetime.utcnow() + timedelta(hours=48)

    return {
        "scope": "deal",
        "scope_id": str(int(deal_id)),
        "decision": decision,
        "reason_codes": reasons,
        "confidence": int(max(0, min(confidence, 100))),
        "expires_at": expires_at,
    }


async def upsert_enforcement_decision(
    db: Session,
    *,
    scope: str,
    scope_id: str,
    decision: str,
    reason_codes: list[str],
    confidence: int,
    created_by: str = "system",
    expires_at: datetime | None = None,
) -> EnforcementDecision:
    scope_norm = str(scope or "deal")
    scope_id_norm = str(scope_id or "").strip() or "0"
    decision_norm = _normalize_decision(decision)

    row = (
        db.query(EnforcementDecision)
        .filter(
            EnforcementDecision.scope == scope_norm,
            EnforcementDecision.scope_id == scope_id_norm,
        )
        .first()
    )

    if not row:
        row = EnforcementDecision(
            scope=scope_norm,
            scope_id=scope_id_norm,
            decision=decision_norm,
            reason_codes=[],
            confidence=0,
            created_by=str(created_by or "system"),
        )
        db.add(row)

    row.scope = scope_norm
    row.scope_id = scope_id_norm
    row.decision = decision_norm
    row.reason_codes = [str(code) for code in _as_list(reason_codes)]
    row.confidence = int(max(0, min(_to_int(confidence, 0), 100)))
    row.created_by = str(created_by or "system")
    row.expires_at = expires_at
    row.updated_at = datetime.utcnow()

    try:
        deal_id = int(scope_id_norm) if scope_norm == "deal" else None
    except Exception:
        deal_id = None

    db.add(
        FraudSignal(
            signal_type="enforcement",
            entity_id=None,
            deal_id=deal_id,
            severity=_signal_severity_for_decision(decision_norm),
            payload={
                "scope": scope_norm,
                "scope_id": scope_id_norm,
                "decision": decision_norm,
                "confidence": int(max(0, min(_to_int(confidence, 0), 100))),
                "created_by": str(created_by or "system"),
            },
            created_at=datetime.utcnow(),
        )
    )

    db.commit()
    db.refresh(row)
    return row


async def get_enforcement_for_deal(db: Session, deal_id: int) -> EnforcementDecision | None:
    return (
        db.query(EnforcementDecision)
        .filter(
            EnforcementDecision.scope == "deal",
            EnforcementDecision.scope_id == str(int(deal_id)),
        )
        .first()
    )


async def override_enforcement_for_deal(
    db: Session,
    *,
    deal_id: int,
    decision: str,
    note: str,
    created_by: str,
    expires_at: datetime | None = None,
) -> EnforcementDecision:
    reason_codes = ["admin_override", f"note:{str(note)[:200]}"]
    return await upsert_enforcement_decision(
        db,
        scope="deal",
        scope_id=str(int(deal_id)),
        decision=str(decision),
        reason_codes=reason_codes,
        confidence=99,
        created_by=created_by,
        expires_at=expires_at,
    )


async def resolve_enforcement_for_deal(
    db: Session,
    *,
    deal_id: int,
    fraud_confirmed: bool,
    note: str,
    created_by: str,
) -> dict[str, Any]:
    payload_note = str(note or "")[:500]

    if fraud_confirmed:
        db.add(
            FraudSignal(
                signal_type="fraud_confirmed",
                entity_id=None,
                deal_id=int(deal_id),
                severity=5,
                payload={"note": payload_note, "created_by": created_by},
                created_at=datetime.utcnow(),
            )
        )
    else:
        db.add(
            FraudSignal(
                signal_type="resolved_not_fraud",
                entity_id=None,
                deal_id=int(deal_id),
                severity=1,
                payload={"note": payload_note, "created_by": created_by},
                created_at=datetime.utcnow(),
            )
        )

    db.commit()

    train_result = await train_model(db)
    return {
        "deal_id": int(deal_id),
        "fraud_confirmed": bool(fraud_confirmed),
        "train_result": train_result,
    }
