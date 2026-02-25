from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.antifraud.enforcement import (
    get_enforcement_for_deal,
    override_enforcement_for_deal,
    resolve_enforcement_for_deal,
)
from app.antifraud.lists import add_to_list
from app.antifraud.rates import get_route_rate_profile
from app.antifraud.service import run_deal_review_and_save
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import EnforcementDecision, User


router = APIRouter()


def _normalize_role(role: Any) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "expeditor":
        return "forwarder"
    if value == "shipper":
        return "client"
    return value


def require_antifraud_admin(current_user: User = Depends(get_current_user)) -> User:
    role = _normalize_role(current_user.role)
    if role not in {"admin", "forwarder"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return current_user


class DealReviewRequest(BaseModel):
    deal: dict[str, Any] = Field(default_factory=dict)


class DealReviewResponse(BaseModel):
    entity_type: str
    entity_id: int
    status: str
    risk_level: str | None
    flags: dict[str, Any]
    comment: str | None
    recommended_action: str | None
    model_used: str | None
    score_total: int
    score_breakdown: list[dict[str, Any]]
    reason_codes: list[str]
    route_rate_profile: dict[str, Any]
    list_check: dict[str, Any]
    history_summary: dict[str, Any] = Field(default_factory=dict)
    escalation_triggered: bool = False
    doc_request: dict[str, Any]
    review_duration_ms: int | None = None
    network: dict[str, Any] = Field(default_factory=dict)
    ml: dict[str, Any] = Field(default_factory=dict)
    enforcement: dict[str, Any] = Field(default_factory=dict)


class CounterpartyListRequest(BaseModel):
    inn: str | None = None
    phone: str | None = None
    name: str | None = None
    note: str | None = None


class CounterpartyListResponse(BaseModel):
    id: int
    list_type: str
    inn: str | None
    phone: str | None
    name: str | None
    note: str | None
    created_at: datetime | None


class EnforcementOverrideRequest(BaseModel):
    decision: str
    note: str = Field(default="")
    expires_at: datetime | None = None


class EnforcementResolveRequest(BaseModel):
    fraud_confirmed: bool
    note: str = Field(default="")


def _serialize_enforcement(row: EnforcementDecision | None) -> dict[str, Any]:
    if not row:
        return {
            "scope": "deal",
            "scope_id": None,
            "decision": "allow",
            "reason_codes": [],
            "confidence": 0,
            "created_by": None,
            "expires_at": None,
            "updated_at": None,
        }

    return {
        "scope": row.scope,
        "scope_id": row.scope_id,
        "decision": row.decision,
        "reason_codes": row.reason_codes or [],
        "confidence": int(row.confidence or 0),
        "created_by": row.created_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/antifraud/deal/review", response_model=DealReviewResponse)
async def antifraud_deal_review(
    body: DealReviewRequest,
    db: Session = Depends(get_db),
) -> DealReviewResponse:
    deal_payload = body.deal if isinstance(body.deal, dict) else {}

    try:
        result = await run_deal_review_and_save(db, deal_payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Antifraud review failed: {str(exc)[:200]}") from exc

    return DealReviewResponse(
        entity_type=str(result.get("entity_type") or "deal"),
        entity_id=int(result.get("entity_id") or 0),
        status=str(result.get("status") or "done"),
        risk_level=result.get("risk_level"),
        flags=result.get("flags") or {},
        comment=result.get("comment"),
        recommended_action=result.get("recommended_action"),
        model_used=result.get("model_used"),
        score_total=int(result.get("score_total") or 0),
        score_breakdown=result.get("score_breakdown") or [],
        reason_codes=[str(code) for code in result.get("reason_codes") or []],
        route_rate_profile=result.get("route_rate_profile") or {},
        list_check=result.get("list_check") or {},
        history_summary=result.get("history_summary") or {},
        escalation_triggered=bool(result.get("escalation_triggered")),
        doc_request=result.get("doc_request") or {},
        review_duration_ms=int(result.get("review_duration_ms") or 0),
        network=result.get("network") or {},
        ml=result.get("ml") or {},
        enforcement=result.get("enforcement") or {},
    )


@router.post("/antifraud/counterparty/whitelist", response_model=CounterpartyListResponse)
async def add_counterparty_whitelist(
    body: CounterpartyListRequest,
    db: Session = Depends(get_db),
) -> CounterpartyListResponse:
    try:
        row = await add_to_list(
            db,
            list_type="white",
            inn=body.inn,
            phone=body.phone,
            name=body.name,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return CounterpartyListResponse(
        id=row.id,
        list_type=row.list_type,
        inn=row.inn,
        phone=row.phone,
        name=row.name,
        note=row.note,
        created_at=row.created_at,
    )


@router.post("/antifraud/counterparty/blacklist", response_model=CounterpartyListResponse)
async def add_counterparty_blacklist(
    body: CounterpartyListRequest,
    db: Session = Depends(get_db),
) -> CounterpartyListResponse:
    try:
        row = await add_to_list(
            db,
            list_type="black",
            inn=body.inn,
            phone=body.phone,
            name=body.name,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return CounterpartyListResponse(
        id=row.id,
        list_type=row.list_type,
        inn=row.inn,
        phone=row.phone,
        name=row.name,
        note=row.note,
        created_at=row.created_at,
    )


@router.get("/antifraud/rates/preview")
async def antifraud_rates_preview(
    from_city: str = Query(..., alias="from"),
    to_city: str = Query(..., alias="to"),
    distance_km: float = Query(0.0, ge=0.0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return await get_route_rate_profile(
        db,
        from_city=from_city,
        to_city=to_city,
        distance_km=distance_km,
    )


@router.get("/antifraud/enforcement/deal/{deal_id}")
async def antifraud_enforcement_get(
    deal_id: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = await get_enforcement_for_deal(db, int(deal_id))
    return _serialize_enforcement(row)


@router.post("/antifraud/enforcement/deal/{deal_id}/override")
async def antifraud_enforcement_override(
    deal_id: int,
    body: EnforcementOverrideRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_antifraud_admin),
) -> dict[str, Any]:
    row = await override_enforcement_for_deal(
        db,
        deal_id=int(deal_id),
        decision=str(body.decision),
        note=str(body.note),
        expires_at=body.expires_at,
        created_by=f"admin:{int(current_user.id)}",
    )
    return _serialize_enforcement(row)


@router.post("/antifraud/enforcement/deal/{deal_id}/resolve")
async def antifraud_enforcement_resolve(
    deal_id: int,
    body: EnforcementResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_antifraud_admin),
) -> dict[str, Any]:
    return await resolve_enforcement_for_deal(
        db,
        deal_id=int(deal_id),
        fraud_confirmed=bool(body.fraud_confirmed),
        note=str(body.note),
        created_by=f"admin:{int(current_user.id)}",
    )
