from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.antifraud.engine import review_deal_rules_v3
from app.antifraud.learning import record_closed_deal, recompute_route_stats
from app.antifraud.lists import add_to_list
from app.antifraud.service import run_deal_review_and_save
from app.db.database import Base
from app.models.models import ClosedDealStat, CounterpartyRiskHistory, DealDocRequest, RouteRateStats


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _base_deal() -> dict:
    return {
        "id": 51001,
        "route": {"from_city": "Москва", "to_city": "Казань", "distance_km": 800},
        "cargo": {"name": "Продукты", "weight_t": 20, "volume_m3": 82},
        "price": {"total_rub": 96000, "rate_per_km": 120},
        "payment": {"type": "bank", "prepay_percent": 0},
        "dates": {"pickup": "2026-02-20", "delivery": "2026-02-21"},
        "counterparty": {
            "name": "ООО Тест",
            "inn": "7701234567",
            "phone": "+79990001122",
            "email": "ops@test.ru",
            "is_new": False,
            "complaints_count": 0,
            "trust_score": 72,
        },
        "notes": "Обычная заявка",
    }


def _profile_with_stats(*, sample_size: int, mean: float, std_dev: float, min_rate: int = 35, max_rate: int = 200) -> dict:
    return {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": min_rate,
        "max_rate_per_km": max_rate,
        "source": "db",
        "cache": "miss",
        "stats": {
            "mean_rate": mean,
            "median_rate": mean,
            "std_dev": std_dev,
            "p25": mean * 0.9,
            "p75": mean * 1.1,
            "sample_size": sample_size,
            "updated_at": datetime.utcnow().isoformat(),
        },
    }


@pytest.mark.asyncio
async def test_statistical_low_price_triggers_z_rule(db_session):
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 40
    profile = _profile_with_stats(sample_size=20, mean=100.0, std_dev=20.0)
    list_check = {"whitelist_match": False, "blacklist_match": False, "matched_fields": [], "entries": []}

    result = review_deal_rules_v3(deal, profile, list_check, history_summary={})

    assert "price_statistically_low" in (result["reason_codes"] or [])
    assert result["flags"].get("price_statistically_low") is True


@pytest.mark.asyncio
async def test_repeat_pattern_escalates_risk(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AI_ANTIFRAUD_ENABLE_LLM", False)

    deal = _base_deal()
    deal["id"] = 52002

    now = datetime.utcnow()
    for idx, risk in enumerate(["high", "high", "medium", "high", "low"]):
        db_session.add(
            CounterpartyRiskHistory(
                counterparty_inn="7701234567",
                deal_id=4000 + idx,
                risk_level=risk,
                score_total=8 if risk == "high" else 5,
                reason_codes=["legacy"],
                created_at=now - timedelta(minutes=idx),
            )
        )
    db_session.commit()

    result = await run_deal_review_and_save(db_session, deal)

    assert result["risk_level"] == "high"
    assert result["flags"].get("repeat_high_risk_pattern") is True
    assert result["escalation_triggered"] is True


@pytest.mark.asyncio
async def test_escalation_overrides_medium_to_high(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AI_ANTIFRAUD_ENABLE_LLM", False)
    monkeypatch.setattr("app.core.config.settings.ANTIFRAUD_STRICT_MODE", False)

    await add_to_list(db_session, list_type="black", inn="7701234567", note="fraud")

    deal = _base_deal()
    deal["id"] = 53003

    result = await run_deal_review_and_save(db_session, deal)

    assert result["risk_level"] == "high"
    assert result["escalation_triggered"] is True
    required_docs = result["doc_request"].get("required_docs") or []
    for code in ["contract_or_order", "company_details", "driver_docs", "payment_confirmation"]:
        assert code in required_docs


@pytest.mark.asyncio
async def test_record_closed_deal_inserts_row(db_session):
    deal = _base_deal()
    deal["status"] = "closed"

    row = await record_closed_deal(db_session, deal)

    assert row is not None
    total = db_session.query(ClosedDealStat).count()
    assert total == 1


@pytest.mark.asyncio
async def test_recompute_route_stats_updates_mean_std(db_session):
    for idx, rate in enumerate([90, 100, 110, 95, 105, 115, 120, 85, 98, 102]):
        deal = _base_deal()
        deal["id"] = 60000 + idx
        deal["status"] = "closed"
        deal["price"]["rate_per_km"] = rate
        deal["price"]["total_rub"] = rate * deal["route"]["distance_km"]
        await record_closed_deal(db_session, deal)

    summary = await recompute_route_stats(db_session)
    stats = (
        db_session.query(RouteRateStats)
        .filter(RouteRateStats.from_city_norm == "москва", RouteRateStats.to_city_norm == "казань")
        .first()
    )

    assert summary["routes_updated"] >= 1
    assert stats is not None
    assert int(stats.sample_size or 0) == 10
    assert float(stats.mean_rate or 0.0) > 0
    assert float(stats.std_dev or 0.0) > 0


def test_fallback_when_sample_size_less_than_10():
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 20

    profile = _profile_with_stats(sample_size=5, mean=120.0, std_dev=25.0, min_rate=35, max_rate=200)
    list_check = {"whitelist_match": False, "blacklist_match": False, "matched_fields": [], "entries": []}

    result = review_deal_rules_v3(deal, profile, list_check, history_summary={})

    assert "price_statistically_low" not in (result["reason_codes"] or [])
    assert result["flags"].get("price_too_low") is True
