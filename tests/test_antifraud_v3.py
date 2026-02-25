from src.antifraud.engine import review_deal_rules_v3


def _base_deal() -> dict:
    return {
        "id": 2001,
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


def _profile_with_stats(*, sample_size: int, mean_rate: float, std_dev: float, min_rate: int, max_rate: int) -> dict:
    return {
        "from_city_norm": "москва",
        "to_city_norm": "казань",
        "min_rate_per_km": min_rate,
        "max_rate_per_km": max_rate,
        "source": "db",
        "cache": "miss",
        "stats": {
            "mean_rate": mean_rate,
            "median_rate": mean_rate,
            "std_dev": std_dev,
            "p25": mean_rate - std_dev,
            "p75": mean_rate + std_dev,
            "sample_size": sample_size,
        },
    }


def test_antifraud_v3_statistical_low_price_triggers_z_rule():
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 40
    profile = _profile_with_stats(sample_size=24, mean_rate=110.0, std_dev=20.0, min_rate=80, max_rate=140)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": False, "blacklist_match": False},
        history_summary={"recent_count": 0, "high_risk_last5": 0, "avg_score_total": 0},
    )

    assert result["risk_level"] == "medium"
    assert "price_statistically_low" in result["reason_codes"]
    assert "price_too_low" not in result["reason_codes"]


def test_antifraud_v3_repeat_pattern_escalates_to_high():
    deal = _base_deal()
    profile = _profile_with_stats(sample_size=0, mean_rate=0.0, std_dev=0.0, min_rate=35, max_rate=200)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": False, "blacklist_match": False},
        history_summary={"recent_count": 5, "high_risk_last5": 3, "avg_score_total": 4},
    )

    assert result["escalation_triggered"] is True
    assert result["risk_level"] == "high"
    assert "repeat_high_risk_pattern" in result["reason_codes"]


def test_antifraud_v3_escalation_for_low_price_plus_high_prepay():
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 40
    deal["payment"] = {"type": "bank", "prepay_percent": 100}
    profile = _profile_with_stats(sample_size=24, mean_rate=110.0, std_dev=20.0, min_rate=80, max_rate=140)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": False, "blacklist_match": False},
        history_summary={"recent_count": 0, "high_risk_last5": 0, "avg_score_total": 0},
    )

    assert result["escalation_triggered"] is True
    assert result["risk_level"] == "high"
    assert "price_statistically_low" in result["reason_codes"]
    assert "high_prepay" in result["reason_codes"]


def test_antifraud_v3_fallback_to_v2_price_rule_when_sample_small():
    deal = _base_deal()
    deal["price"]["rate_per_km"] = 70
    profile = _profile_with_stats(sample_size=9, mean_rate=110.0, std_dev=20.0, min_rate=80, max_rate=140)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": False, "blacklist_match": False},
        history_summary={},
    )

    assert "price_too_low" in result["reason_codes"]
    assert "price_statistically_low" not in result["reason_codes"]


def test_antifraud_v3_invalid_dates_flag():
    deal = _base_deal()
    deal["dates"] = {"pickup": "2026-02-26", "delivery": "2026-02-25"}
    profile = _profile_with_stats(sample_size=0, mean_rate=0.0, std_dev=0.0, min_rate=35, max_rate=200)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": False, "blacklist_match": False},
        history_summary={},
    )

    assert "invalid_dates" in result["reason_codes"]
    assert result["score_total"] >= 2


def test_antifraud_v3_whitelist_reduces_risk_one_level():
    deal = _base_deal()
    deal["cargo"]["weight_t"] = 0
    deal["cargo"]["volume_m3"] = 0
    deal["dates"] = {"pickup": "2026-02-26", "delivery": "2026-02-25"}
    profile = _profile_with_stats(sample_size=0, mean_rate=0.0, std_dev=0.0, min_rate=35, max_rate=200)

    result = review_deal_rules_v3(
        deal,
        route_rate_profile=profile,
        list_check={"whitelist_match": True, "blacklist_match": False, "matched_fields": ["inn"]},
        history_summary={},
    )

    assert result["risk_level"] == "low"
    assert "whitelist_match" in result["reason_codes"]
