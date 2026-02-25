from src.antifraud.docs import build_doc_request_plan
from src.antifraud.engine import review_deal_rules_v2


def _base_deal() -> dict:
    return {
        "id": 1001,
        "route": {"from_city": "Москва", "to_city": "Казань", "distance_km": 800},
        "cargo": {"name": "Одежда", "weight_t": 10, "volume_m3": 40},
        "price": {"total_rub": 88000, "rate_per_km": 110},
        "payment": {"type": "bank", "prepay_percent": 0},
        "dates": {"pickup": "2026-02-24", "delivery": "2026-02-25"},
        "counterparty": {
            "name": "ООО Логистика",
            "inn": "7701234567",
            "phone": "+79998887766",
            "is_new": False,
            "complaints_count": 0,
            "trust_score": 70,
        },
        "notes": "",
    }


def _route_profile() -> dict:
    return {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": 35,
        "max_rate_per_km": 200,
        "source": "tier_fallback",
        "cache": "miss",
    }


def test_antifraud_v2_low_risk():
    result = review_deal_rules_v2(_base_deal(), _route_profile(), {"whitelist_match": False, "blacklist_match": False})
    assert result["risk_level"] == "low"
    assert "price_too_low" not in result["flags"]
    assert "high_prepay" not in result["flags"]


def test_antifraud_v2_high_by_prepay_and_words():
    deal = _base_deal()
    deal["payment"] = {"type": "cash", "prepay_percent": 100}
    deal["notes"] = "Срочно, 100% предоплата, только на карту"
    deal["counterparty"]["complaints_count"] = 1

    result = review_deal_rules_v2(deal, _route_profile(), {"whitelist_match": False, "blacklist_match": False})
    assert result["risk_level"] == "high"
    assert "high_prepay" in result["flags"]
    assert "suspicious_words" in result["flags"]


def test_antifraud_v2_invalid_dates_flag():
    deal = _base_deal()
    deal["dates"] = {"pickup": "2026-02-26", "delivery": "2026-02-25"}
    result = review_deal_rules_v2(deal, _route_profile(), {"whitelist_match": False, "blacklist_match": False})
    assert "invalid_dates" in result["flags"]


def test_antifraud_v2_blacklist_strict_forces_high():
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 80
    result = review_deal_rules_v2(
        deal,
        _route_profile(),
        {"whitelist_match": False, "blacklist_match": True, "matched_fields": ["inn"]},
    )
    assert result["risk_level"] == "high"
    assert "blacklist_match" in result["reason_codes"]


def test_antifraud_v2_whitelist_reduces_one_level_and_doc_plan():
    deal = _base_deal()
    deal["cargo"]["weight_t"] = 0
    deal["cargo"]["volume_m3"] = 0
    deal["counterparty"]["is_new"] = True
    deal["dates"] = {"pickup": "2026-02-26", "delivery": "2026-02-25"}
    result = review_deal_rules_v2(
        deal,
        _route_profile(),
        {"whitelist_match": True, "blacklist_match": False, "matched_fields": ["inn"]},
    )
    # До whitelist было бы medium, после снижения становится low.
    assert result["risk_level"] == "low"
    plan = build_doc_request_plan(
        risk_level=result["risk_level"],
        reason_codes=result["reason_codes"],
        payment_type=deal["payment"]["type"],
        prepay_percent=deal["payment"]["prepay_percent"],
    )
    assert isinstance(plan["required_docs"], list)
