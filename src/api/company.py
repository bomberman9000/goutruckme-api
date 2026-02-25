"""Company passport API — public trust profile for any INN."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.core.services.company_profile import build_company_passport

router = APIRouter(tags=["company"])


class TrustComponent(BaseModel):
    score: int
    max: int
    label: str | None = None
    telegram_posts: int | None = None


class CompanyPassportResponse(BaseModel):
    inn: str
    name: str | None = None
    director: str | None = None
    registration_date: str | None = None
    age_label: str | None = None
    source: str | None = None
    trust_score: int
    verdict: str
    components: dict[str, TrustComponent] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    ati_link: str | None = None


@router.get("/api/v1/company/passport", response_model=CompanyPassportResponse)
async def get_company_passport(
    inn: str = Query(..., min_length=10, max_length=12),
    phone: str | None = Query(default=None),
):
    """Get the full trust passport for a company by INN.

    Returns trust score (0-100) broken down into 4 transparent
    components: Age, Telegram Activity, Finance, Fleet.
    """
    passport = await build_company_passport(inn, phone)
    return CompanyPassportResponse(**passport)
