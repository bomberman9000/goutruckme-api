from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.antifraud.engine import review_deal_rules_v2
from app.antifraud.lists import add_to_list
from app.antifraud.rates import get_route_rate_profile, route_rate_cache
from app.antifraud.service import run_deal_review_and_save
from app.db.database import Base
from app.models.models import DealDocRequest


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
        "id": 1001,
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


@pytest.mark.asyncio
async def test_route_rate_fallback_tier_by_distance(db_session, monkeypatch):
    route_rate_cache.clear()
    monkeypatch.setattr(
        "app.core.config.settings.ROUTE_RATE_TIER_MAP_JSON",
        '{"short":{"max_km":300,"min":40,"max":220},"mid":{"max_km":1200,"min":35,"max":200},"long":{"max_km":100000,"min":30,"max":180}}',
    )

    profile = await get_route_rate_profile(db_session, "Москва", "Тверь", 250)

    assert profile["source"] == "tier_fallback"
    assert profile["min_rate_per_km"] == 40
    assert profile["max_rate_per_km"] == 220


@pytest.mark.asyncio
async def test_route_rate_cache_hit_miss(db_session):
    route_rate_cache.clear()

    first = await get_route_rate_profile(db_session, "Москва", "Казань", 700)
    second = await get_route_rate_profile(db_session, "Москва", "Казань", 700)

    assert first["cache"] == "miss"
    assert second["cache"] == "hit"


def test_blacklist_strict_forces_high(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ANTIFRAUD_STRICT_MODE", True)
    deal = _base_deal()
    profile = {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": 35,
        "max_rate_per_km": 200,
        "source": "tier_fallback",
        "cache": "miss",
    }
    list_check = {
        "whitelist_match": False,
        "blacklist_match": True,
        "matched_fields": ["inn"],
        "entries": [{"list_type": "black", "inn": "7701234567", "phone": None, "name": None, "note": "fraud"}],
    }

    result = review_deal_rules_v2(deal, profile, list_check)

    assert result["risk_level"] == "high"
    assert result["flags"].get("blacklist_match") is True


def test_whitelist_reduces_risk_by_one_unless_blacklist(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.ANTIFRAUD_STRICT_MODE", True)
    deal = _base_deal()
    deal["payment"]["type"] = "cash"  # +1
    deal["cargo"]["weight_t"] = 0  # +1
    deal["dates"]["delivery"] = ""  # +2 => base medium

    profile = {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": 35,
        "max_rate_per_km": 200,
        "source": "tier_fallback",
        "cache": "miss",
    }

    white_only = {
        "whitelist_match": True,
        "blacklist_match": False,
        "matched_fields": ["inn"],
        "entries": [{"list_type": "white", "inn": "7701234567", "phone": None, "name": None, "note": "trusted"}],
    }
    result_white = review_deal_rules_v2(deal, profile, white_only)
    assert result_white["risk_level"] == "low"

    both_lists = {
        "whitelist_match": True,
        "blacklist_match": True,
        "matched_fields": ["inn"],
        "entries": [{"list_type": "black", "inn": "7701234567", "phone": None, "name": None, "note": "fraud"}],
    }
    result_both = review_deal_rules_v2(deal, profile, both_lists)
    assert result_both["risk_level"] == "high"


@pytest.mark.asyncio
async def test_doc_request_created_for_medium_or_high(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AI_ANTIFRAUD_ENABLE_LLM", False)
    monkeypatch.setattr("app.core.config.settings.ANTIFRAUD_DOCS_ENABLE", True)
    monkeypatch.setattr("app.core.config.settings.ANTIFRAUD_STRICT_MODE", True)

    deal = _base_deal()
    deal["id"] = 2002
    deal["payment"]["prepay_percent"] = 100  # serious, medium risk in v2 scoring
    deal["payment"]["type"] = "bank"

    result = await run_deal_review_and_save(db_session, deal)
    row = db_session.query(DealDocRequest).filter(DealDocRequest.deal_id == 2002).first()

    assert result["risk_level"] in {"medium", "high"}
    assert row is not None
    assert row.status == "requested"
    assert "payment_confirmation" in (row.required_docs or [])


def test_price_anomaly_uses_route_profile_thresholds():
    deal = _base_deal()
    list_check = {"whitelist_match": False, "blacklist_match": False, "matched_fields": [], "entries": []}

    deal["price"]["rate_per_km"] = 50
    profile_low = {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": 60,
        "max_rate_per_km": 100,
        "source": "db",
        "cache": "miss",
    }
    res_low = review_deal_rules_v2(deal, profile_low, list_check)
    assert res_low["flags"].get("price_too_low") is True

    deal["price"]["rate_per_km"] = 130
    res_high = review_deal_rules_v2(deal, profile_low, list_check)
    assert res_high["flags"].get("price_too_high") is True


def test_invalid_dates_triggers_and_affects_score():
    deal = _base_deal()
    deal["dates"]["pickup"] = "2026-02-25"
    deal["dates"]["delivery"] = "2026-02-20"

    profile = {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": 35,
        "max_rate_per_km": 200,
        "source": "tier_fallback",
        "cache": "miss",
    }
    list_check = {"whitelist_match": False, "blacklist_match": False, "matched_fields": [], "entries": []}

    result = review_deal_rules_v2(deal, profile, list_check)

    assert result["flags"].get("invalid_dates") is True
    assert result["score_total"] >= 2
    assert "invalid_dates" in (result["reason_codes"] or [])
