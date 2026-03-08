from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.antifraud.lists import add_to_list
from src.antifraud.rates import get_route_rate_profile
from src.antifraud.service import run_deal_review_and_save
from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.config import settings
from src.core.database import async_session


async def _require_admin(user: TelegramTMAUser = Depends(get_required_tma_user)) -> TelegramTMAUser:
    if settings.admin_id and user.user_id != settings.admin_id:
        raise HTTPException(status_code=403, detail="Admin only")
    return user


router = APIRouter(tags=["antifraud"])


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
    history_summary: dict[str, Any]
    escalation_triggered: bool
    doc_request: dict[str, Any]
    review_duration_ms: int


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


@router.post("/antifraud/deal/review", response_model=DealReviewResponse)
async def antifraud_deal_review(body: DealReviewRequest, _: TelegramTMAUser = Depends(_require_admin)) -> DealReviewResponse:
    async with async_session() as session:
        try:
            result = await run_deal_review_and_save(session, body.deal)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Antifraud review failed: {str(exc)[:200]}") from exc
    return DealReviewResponse(**result)


@router.post("/antifraud/counterparty/whitelist", response_model=CounterpartyListResponse)
async def add_counterparty_whitelist(body: CounterpartyListRequest, _: TelegramTMAUser = Depends(_require_admin)) -> CounterpartyListResponse:
    async with async_session() as session:
        try:
            row = await add_to_list(
                session,
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
async def add_counterparty_blacklist(body: CounterpartyListRequest, _: TelegramTMAUser = Depends(_require_admin)) -> CounterpartyListResponse:
    async with async_session() as session:
        try:
            row = await add_to_list(
                session,
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
) -> dict[str, Any]:
    async with async_session() as session:
        return await get_route_rate_profile(
            session,
            from_city=from_city,
            to_city=to_city,
            distance_km=distance_km,
        )
