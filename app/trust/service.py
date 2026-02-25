from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.models import (
    CompanyTrustStats,
    Complaint,
    Deal,
    DealSync,
    DocumentSync,
    Load,
    ModerationReview,
    User,
)
from app.trust.scoring import compute_profile_completeness, compute_trust


SUCCESS_DEAL_STATUSES = {"confirmed", "contracted", "completed", "closed", "success"}
CONFIRMED_DISPUTE_STATUSES = {"resolved", "reviewed"}


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_flags(flags: Any) -> list[str]:
    if flags is None:
        return []
    if isinstance(flags, dict):
        return [str(key) for key, value in flags.items() if bool(value)]
    if isinstance(flags, (list, tuple, set)):
        return [str(item) for item in flags if item not in (None, "")]
    if isinstance(flags, str) and flags.strip():
        return [flags.strip()]
    return []


def _extract_user_ids_from_payload(payload: Any) -> set[int]:
    if not isinstance(payload, dict):
        return set()

    candidates: set[int] = set()
    direct_keys = {
        "user_id",
        "userId",
        "shipper_id",
        "shipperId",
        "client_id",
        "clientId",
        "carrier_id",
        "carrierId",
        "counterparty_id",
        "counterpartyId",
        "owner_id",
        "ownerId",
    }

    for key in direct_keys:
        maybe_id = _safe_int(payload.get(key))
        if maybe_id is not None:
            candidates.add(maybe_id)

    nested_keys = {"shipper", "client", "carrier", "counterparty", "owner", "user"}
    for key in nested_keys:
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_key in ("id", "user_id", "userId"):
                maybe_id = _safe_int(nested.get(nested_key))
                if maybe_id is not None:
                    candidates.add(maybe_id)

    return candidates


def _extract_cargo_id_from_payload(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None

    for key in ("cargo_id", "cargoId", "load_id", "loadId"):
        maybe_id = _safe_int(payload.get(key))
        if maybe_id is not None:
            return maybe_id

    cargo_snapshot = payload.get("cargoSnapshot")
    if isinstance(cargo_snapshot, dict):
        maybe_id = _safe_int(cargo_snapshot.get("id"))
        if maybe_id is not None:
            return maybe_id

    return None


def _build_moderation_maps(db: Session) -> tuple[dict[int, dict[str, Any]], dict[int, int], dict[int, int]]:
    deal_rows = db.query(DealSync.id, DealSync.payload).all()
    deal_payload_map: dict[int, dict[str, Any]] = {
        int(row_id): payload for row_id, payload in deal_rows if isinstance(payload, dict)
    }

    doc_rows = db.query(DocumentSync.id, DocumentSync.deal_server_id).all()
    document_deal_map: dict[int, int] = {}
    for doc_id, deal_server_id in doc_rows:
        if doc_id is None or deal_server_id is None:
            continue
        document_deal_map[int(doc_id)] = int(deal_server_id)

    cargo_ids: set[int] = set()
    for payload in deal_payload_map.values():
        cargo_id = _extract_cargo_id_from_payload(payload)
        if cargo_id is not None:
            cargo_ids.add(cargo_id)

    cargo_owner_map: dict[int, int] = {}
    if cargo_ids:
        for load_id, owner_id in db.query(Load.id, Load.user_id).filter(Load.id.in_(list(cargo_ids))).all():
            if load_id is None or owner_id is None:
                continue
            cargo_owner_map[int(load_id)] = int(owner_id)

    return deal_payload_map, document_deal_map, cargo_owner_map


def _review_related_company_ids(
    review: ModerationReview,
    deal_payload_map: dict[int, dict[str, Any]],
    document_deal_map: dict[int, int],
    cargo_owner_map: dict[int, int],
) -> set[int]:
    payload: dict[str, Any] | None = None

    if review.entity_type == "deal":
        payload = deal_payload_map.get(int(review.entity_id))
    elif review.entity_type == "document":
        deal_server_id = document_deal_map.get(int(review.entity_id))
        if deal_server_id is not None:
            payload = deal_payload_map.get(int(deal_server_id))

    if not isinstance(payload, dict):
        return set()

    user_ids = _extract_user_ids_from_payload(payload)
    cargo_id = _extract_cargo_id_from_payload(payload)
    if cargo_id is not None and cargo_id in cargo_owner_map:
        user_ids.add(int(cargo_owner_map[cargo_id]))

    return user_ids


def get_related_company_ids_for_review(db: Session, entity_type: str, entity_id: int) -> set[int]:
    review = (
        db.query(ModerationReview)
        .filter(ModerationReview.entity_type == entity_type, ModerationReview.entity_id == entity_id)
        .first()
    )
    if not review:
        return set()

    deal_payload_map, document_deal_map, cargo_owner_map = _build_moderation_maps(db)
    return _review_related_company_ids(review, deal_payload_map, document_deal_map, cargo_owner_map)


def _collect_deal_signals(db: Session, company_id: int) -> tuple[int, int, float | None]:
    deals_query = db.query(Deal).filter(or_(Deal.shipper_id == company_id, Deal.carrier_id == company_id))
    deals_total = deals_query.count()

    deals_success = (
        deals_query.filter(func.lower(func.coalesce(Deal.status, "")).in_(SUCCESS_DEAL_STATUSES)).count()
        if deals_total > 0
        else 0
    )

    success_rate = round(deals_success / deals_total, 4) if deals_total > 0 else None
    if deals_total > 0:
        return deals_total, deals_success, success_rate

    # Fallback для сценариев, где сделки живут в DealSync (frontend/localStorage).
    deal_rows = db.query(DealSync.payload).all()
    if not deal_rows:
        return deals_total, deals_success, success_rate

    cargo_ids: set[int] = set()
    payloads: list[dict[str, Any]] = []
    for (payload,) in deal_rows:
        if not isinstance(payload, dict):
            continue
        payloads.append(payload)
        cargo_id = _extract_cargo_id_from_payload(payload)
        if cargo_id is not None:
            cargo_ids.add(cargo_id)

    cargo_owner_map: dict[int, int] = {}
    if cargo_ids:
        for load_id, owner_id in db.query(Load.id, Load.user_id).filter(Load.id.in_(list(cargo_ids))).all():
            if load_id is None or owner_id is None:
                continue
            cargo_owner_map[int(load_id)] = int(owner_id)

    ds_total = 0
    ds_success = 0
    for payload in payloads:
        user_ids = _extract_user_ids_from_payload(payload)
        cargo_id = _extract_cargo_id_from_payload(payload)
        if cargo_id is not None and cargo_id in cargo_owner_map:
            user_ids.add(cargo_owner_map[cargo_id])

        if company_id not in user_ids:
            continue

        ds_total += 1
        status = str(payload.get("status") or "").strip().lower()
        if status in SUCCESS_DEAL_STATUSES:
            ds_success += 1

    if ds_total == 0:
        return 0, 0, None
    return ds_total, ds_success, round(ds_success / ds_total, 4)


def _collect_dispute_signals(db: Session, company_id: int) -> tuple[int, int]:
    disputes_query = db.query(Complaint).filter(Complaint.defendant_id == company_id)
    disputes_total = disputes_query.count()

    disputes_confirmed = (
        disputes_query.filter(func.lower(func.coalesce(Complaint.status, "")).in_(CONFIRMED_DISPUTE_STATUSES)).count()
        if disputes_total > 0
        else 0
    )
    return disputes_total, disputes_confirmed


def _collect_moderation_signals(db: Session, company_id: int) -> tuple[int, int]:
    reviews = db.query(ModerationReview).all()
    if not reviews:
        return 0, 0

    deal_payload_map, document_deal_map, cargo_owner_map = _build_moderation_maps(db)

    flags_total = 0
    flags_high = 0

    for review in reviews:
        related_company_ids = _review_related_company_ids(
            review,
            deal_payload_map,
            document_deal_map,
            cargo_owner_map,
        )
        if company_id not in related_company_ids:
            continue

        extracted_flags = _extract_flags(review.flags)
        flags_total += len(extracted_flags)

        if str(review.risk_level or "").lower() == "high":
            flags_high += max(1, len(extracted_flags))

    return flags_total, flags_high


def _collect_response_time_avg(db: Session, company_id: int) -> float | None:
    rows = (
        db.query(Deal.created_at, Load.created_at)
        .join(Load, Deal.cargo_id == Load.id)
        .filter(
            Deal.carrier_id == company_id,
            Deal.created_at.isnot(None),
            Load.created_at.isnot(None),
        )
        .all()
    )

    if not rows:
        return None

    deltas: list[float] = []
    for response_at, load_created_at in rows:
        if not response_at or not load_created_at:
            continue
        delta_min = (response_at - load_created_at).total_seconds() / 60.0
        if delta_min >= 0:
            deltas.append(delta_min)

    if not deltas:
        return None
    return round(mean(deltas), 2)


def _company_age_days(company: User) -> int:
    if not company.created_at:
        return 0
    delta = datetime.utcnow() - company.created_at
    return max(int(delta.days), 0)


def _build_trust_context(db: Session, company: User) -> dict[str, Any]:
    deals_total, deals_success, success_rate = _collect_deal_signals(db, company.id)
    disputes_total, disputes_confirmed = _collect_dispute_signals(db, company.id)
    flags_total, flags_high = _collect_moderation_signals(db, company.id)

    return {
        "company_age_days": _company_age_days(company),
        "deals_total": deals_total,
        "deals_success": deals_success,
        "success_rate": success_rate,
        "disputes_total": disputes_total,
        "disputes_confirmed": disputes_confirmed,
        "flags_total": flags_total,
        "flags_high": flags_high,
        "profile_completeness": compute_profile_completeness(company),
        "response_time_avg_min": _collect_response_time_avg(db, company.id),
    }


def _default_trust_payload(company_id: int) -> dict[str, Any]:
    return {
        "company_id": int(company_id),
        "trust_score": 50,
        "stars": 3,
        "components": {
            "history": 0,
            "success": 0,
            "disputes": 0,
            "risk": 0,
            "profile": 0,
            "speed": 0,
        },
        "signals": {
            "company_age_days": 0,
            "deals_total": 0,
            "deals_total_bucket": "0-2",
            "deals_success": 0,
            "success_rate": None,
            "disputes_total": 0,
            "disputes_confirmed": 0,
            "flags_total": 0,
            "flags_high": 0,
            "profile_completeness": 0.0,
            "response_time_avg_min": None,
        },
        "flags": ["insufficient_data"],
        "updated_at": None,
    }


def recalc_company_trust(db: Session, company_id: int) -> CompanyTrustStats:
    company = db.query(User).filter(User.id == company_id).first()
    if not company:
        raise ValueError("Company not found")

    context = _build_trust_context(db, company)
    trust_result = compute_trust(company.id, context)

    row = db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == company.id).first()
    if not row:
        row = CompanyTrustStats(company_id=company.id)
        db.add(row)

    signals = trust_result["signals"]
    row.trust_score = int(trust_result["trust_score"])
    row.stars = int(trust_result["stars"])
    row.success_rate = signals.get("success_rate")
    row.deals_total = int(signals.get("deals_total") or 0)
    row.deals_success = int(signals.get("deals_success") or 0)
    row.disputes_total = int(signals.get("disputes_total") or 0)
    row.disputes_confirmed = int(signals.get("disputes_confirmed") or 0)
    row.flags_total = int(signals.get("flags_total") or 0)
    row.flags_high = int(signals.get("flags_high") or 0)
    row.profile_completeness = float(signals.get("profile_completeness") or 0.0)
    row.response_time_avg_min = signals.get("response_time_avg_min")
    row.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(row)
    return row


def get_company_trust_payload(db: Session, company_id: int, force_recalc: bool = False) -> dict[str, Any]:
    company = db.query(User).filter(User.id == company_id).first()
    if not company:
        raise ValueError("Company not found")

    row = db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == company.id).first()
    if force_recalc or row is None:
        row = recalc_company_trust(db, company.id)

    if not row:
        return _default_trust_payload(company.id)

    # Формируем explainability из сохранённых агрегатов.
    context = {
        "company_age_days": _company_age_days(company),
        "deals_total": row.deals_total,
        "deals_success": row.deals_success,
        "success_rate": row.success_rate,
        "disputes_total": row.disputes_total,
        "disputes_confirmed": row.disputes_confirmed,
        "flags_total": row.flags_total,
        "flags_high": row.flags_high,
        "profile_completeness": row.profile_completeness,
        "response_time_avg_min": row.response_time_avg_min,
    }
    trust_result = compute_trust(company.id, context)

    return {
        "company_id": company.id,
        "trust_score": int(row.trust_score if row.trust_score is not None else trust_result["trust_score"]),
        "stars": int(row.stars if row.stars is not None else trust_result["stars"]),
        "components": trust_result["components"],
        "signals": trust_result["signals"],
        "flags": trust_result["flags"],
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_company_trust_snapshot(db: Session, company_id: int | None) -> dict[str, Any]:
    if company_id is None:
        return _default_trust_payload(0)

    row = db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == company_id).first()
    if not row:
        payload = _default_trust_payload(company_id)
        payload["company_id"] = company_id
        return payload

    company = db.query(User).filter(User.id == company_id).first()
    context = {
        "company_age_days": _company_age_days(company) if company else 0,
        "deals_total": row.deals_total,
        "deals_success": row.deals_success,
        "success_rate": row.success_rate,
        "disputes_total": row.disputes_total,
        "disputes_confirmed": row.disputes_confirmed,
        "flags_total": row.flags_total,
        "flags_high": row.flags_high,
        "profile_completeness": row.profile_completeness,
        "response_time_avg_min": row.response_time_avg_min,
    }
    trust_result = compute_trust(company_id, context)

    return {
        "company_id": company_id,
        "trust_score": int(row.trust_score if row.trust_score is not None else trust_result["trust_score"]),
        "stars": int(row.stars if row.stars is not None else trust_result["stars"]),
        "components": trust_result["components"],
        "signals": trust_result["signals"],
        "flags": trust_result["flags"],
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
