from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import Complaint, Deal, DealSync, ModerationReview, User
from app.trust.service import (
    _build_moderation_maps,
    _review_related_company_ids,
    get_company_trust_payload,
    get_company_trust_snapshot,
)

router = APIRouter()

CONFIRMED_COMPLAINT_STATUSES = {"reviewed", "resolved"}


def _normalize_role(role: Any) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "shipper":
        return "client"
    if value == "expeditor":
        return "forwarder"
    if value in {"carrier", "client", "forwarder", "admin"}:
        return value
    return "forwarder"


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _company_name(company: User) -> str:
    return (
        company.organization_name
        or company.company
        or company.fullname
        or f"Компания #{company.id}"
    )


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": int(user.id),
        "role": _normalize_role(user.role),
        "email": user.email,
        "phone": user.phone,
        "tg_user_id": _safe_int(user.telegram_id),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _company_payload_private(company: User) -> dict[str, Any]:
    return {
        "id": int(company.id),
        "name": _company_name(company),
        "inn": company.inn,
        "ogrn": company.ogrn,
        "city": company.city,
        "contacts": {
            "phone": company.phone,
            "email": company.email,
            "website": company.website,
            "contact_person": company.contact_person or company.fullname,
        },
        "phone": company.phone,
        "contact_person": company.contact_person or company.fullname,
        "website": company.website,
        "edo_enabled": bool(company.edo_enabled),
        "bank_details": {
            "bank_name": company.bank_name,
            "bank_account": company.bank_account,
            "bank_bik": company.bank_bik,
            "bank_ks": company.bank_ks,
        },
        "created_at": company.created_at.isoformat() if company.created_at else None,
        "verified": bool(company.verified),
    }


def _company_payload_public(company: User) -> dict[str, Any]:
    requisites_filled = all(
        [
            _clean_str(company.bank_name),
            _clean_str(company.bank_account),
            _clean_str(company.bank_bik),
            _clean_str(company.bank_ks),
        ]
    )
    return {
        "id": int(company.id),
        "name": _company_name(company),
        "inn": company.inn,
        "ogrn": company.ogrn,
        "city": company.city,
        "phone": company.phone,
        "contact_person": company.contact_person or company.fullname,
        "website": company.website,
        "edo_enabled": bool(company.edo_enabled),
        "verification": {
            "profile_verified": bool(company.verified),
            "requisites_present": requisites_filled,
            "documents_verified": bool(company.documents_verified or company.payment_confirmed),
        },
        "created_at": company.created_at.isoformat() if company.created_at else None,
    }


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


def _company_days_since_created(company: User) -> int:
    if not company.created_at:
        return 0
    return max((datetime.utcnow() - company.created_at).days, 0)


def _verification_payload(company: User) -> dict[str, Any]:
    checks = {
        "inn_ogrn": bool(_clean_str(company.inn) and _clean_str(company.ogrn)),
        "phone": bool(_clean_str(company.phone)),
        "edo": bool(company.edo_enabled),
        "requisites": bool(
            _clean_str(company.bank_name)
            and _clean_str(company.bank_account)
            and _clean_str(company.bank_bik)
            and _clean_str(company.bank_ks)
        ),
        "documents": bool(company.documents_verified or company.payment_confirmed or company.verified),
    }
    done = sum(1 for value in checks.values() if value)
    total = len(checks)

    if done == 0:
        status = "не подтверждено"
    elif done == total:
        status = "подтверждено"
    else:
        status = "частично"

    return {
        "status": status,
        "progress": {"done": done, "total": total},
        "checklist": checks,
    }


def _collect_company_reviews(db: Session, company_id: int, max_scan: int = 500) -> list[dict[str, Any]]:
    rows = (
        db.query(ModerationReview)
        .order_by(ModerationReview.updated_at.desc(), ModerationReview.created_at.desc())
        .limit(max_scan)
        .all()
    )
    if not rows:
        return []

    deal_payload_map, document_deal_map, cargo_owner_map = _build_moderation_maps(db)

    result: list[dict[str, Any]] = []
    for review in rows:
        company_ids = _review_related_company_ids(
            review,
            deal_payload_map,
            document_deal_map,
            cargo_owner_map,
        )
        if company_id not in company_ids:
            continue

        result.append(
            {
                "id": int(review.id),
                "entity_type": review.entity_type,
                "entity_id": int(review.entity_id),
                "risk_level": (review.risk_level or "low").lower(),
                "flags": review.flags if isinstance(review.flags, list) else [],
                "comment": review.comment,
                "created_at": review.created_at.isoformat() if review.created_at else None,
                "updated_at": review.updated_at.isoformat() if review.updated_at else None,
            }
        )
    return result


def _document_issue_stats(reviews: list[dict[str, Any]]) -> tuple[int, int]:
    duplicates = 0
    mismatches = 0
    for item in reviews:
        if item.get("entity_type") != "document":
            continue
        flags = item.get("flags") or []
        for flag in flags:
            normalized = str(flag).lower()
            if "duplicate" in normalized or "дублик" in normalized:
                duplicates += 1
            if "mismatch" in normalized or "несоответ" in normalized or "inconsisten" in normalized:
                mismatches += 1
    return duplicates, mismatches


def _risk_summary(reviews: list[dict[str, Any]], trust_payload: dict[str, Any]) -> dict[str, Any]:
    high_count = 0
    medium_count = 0
    low_count = 0
    last_high_risk_at = None

    for item in reviews:
        risk_level = str(item.get("risk_level") or "low").lower()
        if risk_level == "high":
            high_count += 1
            if last_high_risk_at is None:
                last_high_risk_at = item.get("updated_at") or item.get("created_at")
        elif risk_level == "medium":
            medium_count += 1
        else:
            low_count += 1

    high_flags_count = int((trust_payload.get("signals") or {}).get("flags_high") or 0)

    return {
        "high_reviews": high_count,
        "medium_reviews": medium_count,
        "low_reviews": low_count,
        "high_flags_count": high_flags_count,
        "last_high_risk_at": last_high_risk_at,
    }


def _recent_activity_payload(db: Session, company_id: int, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    latest_deal = (
        db.query(Deal)
        .filter(or_(Deal.shipper_id == company_id, Deal.carrier_id == company_id))
        .order_by(Deal.created_at.desc())
        .first()
    )

    latest_dispute = (
        db.query(Complaint)
        .filter(Complaint.defendant_id == company_id)
        .order_by(Complaint.created_at.desc())
        .first()
    )

    if latest_deal is None:
        deal_sync_rows = db.query(DealSync.updated_at, DealSync.payload).all()
        latest_sync = None
        for updated_at, payload in deal_sync_rows:
            if company_id not in _extract_user_ids_from_payload(payload):
                continue
            if updated_at is None:
                continue
            if latest_sync is None or updated_at > latest_sync:
                latest_sync = updated_at
        latest_deal_at = latest_sync.isoformat() if latest_sync else None
    else:
        latest_deal_at = latest_deal.created_at.isoformat() if latest_deal.created_at else None

    return {
        "latest_deal_at": latest_deal_at,
        "latest_dispute_at": latest_dispute.created_at.isoformat() if latest_dispute and latest_dispute.created_at else None,
        "timeline": reviews[:10],
    }


def _stats_payload(company: User, trust_payload: dict[str, Any], reviews: list[dict[str, Any]], db: Session) -> dict[str, Any]:
    signals = trust_payload.get("signals") or {}
    duplicates, mismatches = _document_issue_stats(reviews)

    confirmed_complaints = (
        db.query(Complaint)
        .filter(
            Complaint.defendant_id == company.id,
            Complaint.status.in_(list(CONFIRMED_COMPLAINT_STATUSES)),
        )
        .count()
    )

    return {
        "deals_total": int(signals.get("deals_total") or 0),
        "deals_success": int(signals.get("deals_success") or 0),
        "success_rate": float(signals.get("success_rate") or 0) if signals.get("success_rate") is not None else 0,
        "complaints_confirmed": int(confirmed_complaints),
        "response_time_avg_min": signals.get("response_time_avg_min"),
        "high_flags_count": int(signals.get("flags_high") or 0),
        "last_high_risk_at": _risk_summary(reviews, trust_payload).get("last_high_risk_at"),
        "documents": {
            "duplicates": int(duplicates),
            "mismatches": int(mismatches),
        },
        "days_since_created": _company_days_since_created(company),
    }


def _build_company_profile_payload(db: Session, company: User, include_private: bool) -> dict[str, Any]:
    try:
        trust_payload = get_company_trust_payload(db, int(company.id), force_recalc=False)
    except Exception:
        trust_payload = get_company_trust_snapshot(db, int(company.id))
    reviews = _collect_company_reviews(db, int(company.id))

    company_block = _company_payload_private(company) if include_private else _company_payload_public(company)
    stats = _stats_payload(company, trust_payload, reviews, db)
    risk_summary = _risk_summary(reviews, trust_payload)
    recent_activity = _recent_activity_payload(db, int(company.id), reviews)

    payload = {
        "company": company_block,
        "trust": trust_payload,
        "stats": stats,
        "risk_summary": risk_summary,
        "recent_activity": recent_activity,
    }

    if include_private:
        payload["verification"] = _verification_payload(company)

    return payload


def _build_me_payload(db: Session, user: User) -> dict[str, Any]:
    base = _build_company_profile_payload(db, user, include_private=True)
    return {
        "user": _user_payload(user),
        "company": base["company"],
        "trust": base["trust"],
        "stats": base["stats"],
        "risk_summary": base["risk_summary"],
        "recent_activity": base["recent_activity"],
        "verification": base.get("verification", {}),
    }


def build_me_payload(db: Session, current_user: User) -> dict[str, Any]:
    return _build_me_payload(db, current_user)


def clean_str(value: str | None) -> str | None:
    return _clean_str(value)


@router.get("/companies/{company_id}/profile")
def get_public_company_profile(company_id: int, db: Session = Depends(get_db)):
    company = db.query(User).filter(User.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена")

    return _build_company_profile_payload(db, company, include_private=False)
