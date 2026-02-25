"""Company Profile & Smart Trust Score.

Auto-enriches company data by INN (via DaData/bridge), calculates
a transparent 0-100 trust score from 4 weighted metrics, and
provides a «digital passport» for each company.

Score components (total 100):
  - Age:       30% — years since registration
  - Activity:  20% — Telegram presence (parser phone matches)
  - Finance:   30% — capital, no liquidation, no lawsuits
  - Fleet:     20% — verified vehicles (placeholder for future)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, func
from src.core.database import async_session
from src.core.models import ParserIngestEvent

logger = logging.getLogger(__name__)


@dataclass
class CompanyProfile:
    inn: str
    name: str | None = None
    director: str | None = None
    age_years: float | None = None
    capital: int | None = None
    is_liquidating: bool = False
    registration_date: str | None = None
    source: str = "unknown"


@dataclass
class TrustScoreResult:
    total: int
    age_score: int
    activity_score: int
    finance_score: int
    fleet_score: int
    age_label: str
    verdict: str
    flags: list[str]
    details: dict[str, Any]


async def fetch_company_profile(inn: str) -> CompanyProfile:
    """Fetch company data from DaData or gruzpotok-api bridge."""
    normalized = "".join(ch for ch in str(inn) if ch.isdigit())
    if len(normalized) not in (10, 12):
        return CompanyProfile(inn=normalized, source="invalid")

    try:
        from src.core.services.gruzpotok_bridge import verify_inn
        bridge_data = await verify_inn(normalized)
        if bridge_data and bridge_data.get("valid"):
            return CompanyProfile(
                inn=normalized,
                name=bridge_data.get("company_name"),
                source="bridge",
            )
    except Exception:
        pass

    try:
        from src.services.scoring import _fetch_company_profile
        profile, provider = await _fetch_company_profile(normalized)
        return CompanyProfile(
            inn=normalized,
            name=profile.get("name"),
            age_years=profile.get("age_years"),
            capital=profile.get("capital"),
            is_liquidating=bool(profile.get("is_liquidating")),
            source=provider,
        )
    except Exception as exc:
        logger.warning("company_profile fetch failed inn=%s: %s", normalized, exc)
        return CompanyProfile(inn=normalized, source="error")


async def count_telegram_activity(phone: str | None) -> int:
    """Count how many parser events match this phone (Telegram presence)."""
    if not phone:
        return 0
    try:
        async with async_session() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ParserIngestEvent)
                .where(
                    ParserIngestEvent.phone == phone,
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_spam.is_(False),
                )
            )
            return count or 0
    except Exception:
        return 0


def calculate_trust_score(
    profile: CompanyProfile,
    telegram_activity: int = 0,
    verified_vehicles: int = 0,
    has_active_lawsuits: bool = False,
) -> TrustScoreResult:
    """Calculate Smart Trust Score (0-100) from 4 transparent metrics."""
    flags: list[str] = []

    # ── A. Age (max 30) ──────────────────────────────
    age = profile.age_years
    if age is None:
        age_score = 5
        age_label = "Неизвестен"
        flags.append("age_unknown")
    elif age < 0.5:
        age_score = 2
        age_label = "Новичок (<6 мес)"
        flags.append("very_new")
    elif age < 1:
        age_score = 8
        age_label = "Молодая (<1 год)"
        flags.append("new_company")
    elif age < 3:
        age_score = 18
        age_label = "Развивающаяся"
    elif age < 5:
        age_score = 25
        age_label = "Стабильная"
    else:
        age_score = 30
        age_label = "Ветеран (5+ лет)"

    # ── B. Telegram activity (max 20) ────────────────
    activity_score = min(telegram_activity * 2, 20)
    if telegram_activity == 0:
        flags.append("no_telegram_activity")
    elif telegram_activity >= 10:
        flags.append("active_in_chats")

    # ── C. Finance (max 30) ──────────────────────────
    finance_score = 15
    if profile.is_liquidating:
        finance_score = 0
        flags.append("liquidating")
    if has_active_lawsuits:
        finance_score = max(0, finance_score - 15)
        flags.append("active_lawsuits")
    if profile.capital is not None:
        if profile.capital >= 1_000_000:
            finance_score = min(30, finance_score + 10)
        elif profile.capital <= 10_000:
            finance_score = max(0, finance_score - 5)
            flags.append("low_capital")

    # ── D. Fleet (max 20) ────────────────────────────
    fleet_score = min(verified_vehicles * 5, 20)
    if verified_vehicles == 0:
        fleet_score = 5
        flags.append("no_verified_fleet")

    total = max(0, min(100, age_score + activity_score + finance_score + fleet_score))

    if total >= 70:
        verdict = "green"
    elif total >= 40:
        verdict = "yellow"
    else:
        verdict = "red"

    return TrustScoreResult(
        total=total,
        age_score=age_score,
        activity_score=activity_score,
        finance_score=finance_score,
        fleet_score=fleet_score,
        age_label=age_label,
        verdict=verdict,
        flags=flags,
        details={
            "inn": profile.inn,
            "name": profile.name,
            "age_years": profile.age_years,
            "capital": profile.capital,
            "is_liquidating": profile.is_liquidating,
            "telegram_posts": telegram_activity,
            "verified_vehicles": verified_vehicles,
            "has_lawsuits": has_active_lawsuits,
        },
    )


async def build_company_passport(
    inn: str,
    phone: str | None = None,
) -> dict[str, Any]:
    """Build a complete company passport with trust score."""
    profile = await fetch_company_profile(inn)
    tg_activity = await count_telegram_activity(phone)
    trust = calculate_trust_score(profile, telegram_activity=tg_activity)

    return {
        "inn": profile.inn,
        "name": profile.name,
        "director": profile.director,
        "registration_date": profile.registration_date,
        "age_label": trust.age_label,
        "source": profile.source,
        "trust_score": trust.total,
        "verdict": trust.verdict,
        "components": {
            "age": {"score": trust.age_score, "max": 30, "label": trust.age_label},
            "activity": {"score": trust.activity_score, "max": 20, "telegram_posts": tg_activity},
            "finance": {"score": trust.finance_score, "max": 30},
            "fleet": {"score": trust.fleet_score, "max": 20},
        },
        "flags": trust.flags,
        "ati_link": f"https://ati.su/firms?inn={profile.inn}",
    }
