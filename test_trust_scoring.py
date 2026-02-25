from types import SimpleNamespace

from app.matching.scorer import evaluate_trust
from app.trust.scoring import compute_profile_completeness, compute_trust


def test_cold_start_cap_blocks_high_score_and_stars_for_one_deal():
    result = compute_trust(
        company_id=1,
        ctx={
            "company_age_days": 365,
            "deals_total": 1,
            "deals_success": 1,
            "success_rate": 1.0,
            "disputes_total": 0,
            "disputes_confirmed": 0,
            "flags_total": 0,
            "flags_high": 0,
            "profile_completeness": 1.0,
            "response_time_avg_min": 5,
        },
    )

    assert result["trust_score"] <= 65
    assert result["stars"] <= 3
    assert "cold_start_cap" in result["flags"]


def test_profile_minimum_name_phone_city_is_not_empty_profile():
    company = SimpleNamespace(
        organization_name="ООО Тест",
        phone="+79991234567",
        city="Самара",
        role="carrier",
    )

    completeness = compute_profile_completeness(company)
    assert completeness >= 0.4

    result = compute_trust(
        company_id=2,
        ctx={
            "company_age_days": 30,
            "deals_total": 3,
            "deals_success": 2,
            "success_rate": 2 / 3,
            "disputes_total": 0,
            "disputes_confirmed": 0,
            "flags_total": 0,
            "flags_high": 0,
            "profile_completeness": completeness,
            "response_time_avg_min": 60,
        },
    )

    assert "profile_empty" not in result["flags"]


def test_history_component_bucket_for_four_deals():
    result = compute_trust(
        company_id=3,
        ctx={
            "company_age_days": 40,
            "deals_total": 4,
            "deals_success": 2,
            "success_rate": 0.5,
            "disputes_total": 0,
            "disputes_confirmed": 0,
            "flags_total": 0,
            "flags_high": 0,
            "profile_completeness": 0.5,
            "response_time_avg_min": 120,
        },
    )

    assert result["components"]["history"] >= 3
    assert result["signals"]["deals_total_bucket"] == "3-9"


def test_matching_returns_explainable_trust_influence_and_penalty():
    low = evaluate_trust(35, 30, base_risk="low")
    assert low["trust_influence"] < 0 or low["low_trust_penalty_applied"] is True
    assert "Снижение ранга" in low["trust_explain"]

    high = evaluate_trust(90, 85, base_risk="low")
    assert high["trust_influence"] > 0
    assert "Повышение ранга" in high["trust_explain"]
