from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.antifraud.enforcement import decide_enforcement
from app.antifraud.ml import build_features
from app.antifraud.service import run_deal_review_and_save
from app.db.database import Base


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


def _deal() -> dict:
    return {
        "id": 910001,
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


def test_feature_builder_output_shape():
    deal = _deal()
    rules = {
        "flags": {"suspicious_words": ["срочно"], "missing_dimensions": True, "invalid_dates": False},
        "reason_codes": ["repeat_high_risk_pattern"],
    }
    network = {"component_risk": 67}

    features = build_features(deal, rules, network)

    assert set(features.keys())
    assert features["distance_km"] == 800.0
    assert features["payment_bank"] == 1.0
    assert features["repeat_pattern_flags"] == 1.0
    assert features["network_component_risk"] == 67.0


@pytest.mark.asyncio
async def test_prediction_raises_risk_only_never_lowers(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.AI_ANTIFRAUD_ENABLE_LLM", False)

    async def _high_prob(*args, **kwargs):
        return {"probability": 0.99, "model_version": 1}

    monkeypatch.setattr("app.antifraud.service.predict_fraud_probability", _high_prob)

    low_deal = _deal()
    low_deal["id"] = 910002
    low_result = await run_deal_review_and_save(db_session, low_deal)
    assert low_result["risk_level"] == "low"  # no serious flags, ML must not force raise

    serious_deal = _deal()
    serious_deal["id"] = 910003
    serious_deal["payment"]["prepay_percent"] = 100
    serious_result = await run_deal_review_and_save(db_session, serious_deal)
    assert serious_result["risk_level"] == "high"  # medium + ML raise by 1


@pytest.mark.asyncio
async def test_hard_block_requires_combined_conditions():
    base = await decide_enforcement(
        deal_id=9901,
        risk_level="medium",
        reason_codes=["high_prepay"],
        flags={"high_prepay": 100},
        network_component_risk=70,
        ml_probability=0.95,
        whitelist_match=False,
        blacklist_match=False,
    )
    assert base["decision"] != "hard_block"

    combined = await decide_enforcement(
        deal_id=9902,
        risk_level="medium",
        reason_codes=["high_prepay"],
        flags={"high_prepay": 100},
        network_component_risk=85,
        ml_probability=0.95,
        whitelist_match=False,
        blacklist_match=False,
    )
    assert combined["decision"] == "hard_block"
