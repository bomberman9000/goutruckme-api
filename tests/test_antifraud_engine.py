import json

import pytest

from app.antifraud.engine import (
    apply_llm_risk_policy,
    merge_flags,
    review_deal_llm,
    review_deal_rules,
)


def _base_deal() -> dict:
    return {
        "id": 123,
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


def test_review_deal_rules_low_normal_case():
    deal = _base_deal()

    result = review_deal_rules(deal)

    assert result["risk_level"] == "low"
    assert result["flags"] == {}
    assert "риск" in result["comment"].lower()


def test_review_deal_rules_high_prepay_and_suspicious_words():
    deal = _base_deal()
    deal["payment"]["prepay_percent"] = 100
    deal["payment"]["type"] = "cash"
    deal["notes"] = "Срочно, 100% предоплата и только на карту"

    result = review_deal_rules(deal)

    assert result["risk_level"] == "high"
    assert "high_prepay" in result["flags"]
    assert "suspicious_words" in result["flags"]


def test_review_deal_rules_medium_new_counterparty_missing_dimensions():
    deal = _base_deal()
    deal["counterparty"]["is_new"] = True
    deal["cargo"]["weight_t"] = 0
    deal["payment"]["type"] = "cash"

    result = review_deal_rules(deal)

    assert result["risk_level"] == "medium"
    assert result["flags"].get("new_counterparty") is True
    assert result["flags"].get("missing_dimensions") is True


def test_review_deal_rules_invalid_dates_flag():
    deal = _base_deal()
    deal["dates"]["pickup"] = "2026-02-22"
    deal["dates"]["delivery"] = "2026-02-20"

    result = review_deal_rules(deal)

    assert result["flags"].get("invalid_dates") is True


@pytest.mark.asyncio
async def test_llm_merge_and_risk_policy(monkeypatch):
    def _ask_raise(**kwargs):
        text = json.dumps(
            {
                "risk_level": "high",
                "flags": {"llm_extra_flag": True, "suspicious_words": ["доп.термин"]},
                "comment": "LLM добавил риск",
                "recommended_action": "Усилить проверку",
            },
            ensure_ascii=False,
        )
        return {"text": text, "model": "llama3:8b", "source": "local"}

    monkeypatch.setattr("app.antifraud.engine.ai_service.ask", _ask_raise)

    deal_serious = _base_deal()
    deal_serious["price"]["rate_per_km"] = 20  # serious: price_too_low
    rules_serious = review_deal_rules(deal_serious)
    llm_serious = await review_deal_llm(deal_serious, rules_serious)
    merged_serious = merge_flags(rules_serious["flags"], llm_serious.get("flags") or {})
    risk_serious = apply_llm_risk_policy(
        rules_serious["risk_level"],
        llm_serious.get("risk_level") or "low",
        rules_serious["flags"],
    )

    assert merged_serious.get("llm_extra_flag") is True
    assert risk_serious == "high"

    deal_no_serious = _base_deal()
    rules_no_serious = review_deal_rules(deal_no_serious)
    llm_no_serious = await review_deal_llm(deal_no_serious, rules_no_serious)
    risk_no_serious = apply_llm_risk_policy(
        rules_no_serious["risk_level"],
        llm_no_serious.get("risk_level") or "low",
        rules_no_serious["flags"],
    )

    assert rules_no_serious["risk_level"] == "low"
    assert risk_no_serious == "low"

    def _ask_lower(**kwargs):
        text = json.dumps(
            {
                "risk_level": "low",
                "flags": {"llm_soft_flag": True},
                "comment": "LLM понижает риск",
                "recommended_action": "Оставить как есть",
            },
            ensure_ascii=False,
        )
        return {"text": text, "model": "llama3:8b", "source": "local"}

    monkeypatch.setattr("app.antifraud.engine.ai_service.ask", _ask_lower)

    deal_high_rules = _base_deal()
    deal_high_rules["payment"]["prepay_percent"] = 100
    deal_high_rules["payment"]["type"] = "cash"
    deal_high_rules["notes"] = "100% предоплата, срочно"

    rules_high = review_deal_rules(deal_high_rules)
    llm_high = await review_deal_llm(deal_high_rules, rules_high)
    risk_high = apply_llm_risk_policy(
        rules_high["risk_level"],
        llm_high.get("risk_level") or "low",
        rules_high["flags"],
    )

    assert rules_high["risk_level"] == "high"
    assert risk_high == "high"
