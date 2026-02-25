"""Tests for Company Profile & Smart Trust Score."""

from src.core.services.company_profile import (
    CompanyProfile,
    calculate_trust_score,
)


class TestTrustScore:
    def _profile(self, **kw) -> CompanyProfile:
        defaults = dict(inn="7707083893", name="ООО Тест", age_years=5.0, capital=500_000, source="test")
        defaults.update(kw)
        return CompanyProfile(**defaults)

    def test_veteran_high_score(self):
        result = calculate_trust_score(self._profile(age_years=6.0), telegram_activity=15, verified_vehicles=3)
        assert result.total >= 70
        assert result.verdict == "green"
        assert result.age_label == "Ветеран (5+ лет)"

    def test_new_company_lower_score(self):
        result = calculate_trust_score(self._profile(age_years=0.3), telegram_activity=0)
        assert result.total < 40
        assert "very_new" in result.flags

    def test_liquidating_drops_finance(self):
        result = calculate_trust_score(self._profile(is_liquidating=True))
        assert result.finance_score == 0
        assert "liquidating" in result.flags

    def test_lawsuits_penalty(self):
        normal = calculate_trust_score(self._profile())
        lawsuit = calculate_trust_score(self._profile(), has_active_lawsuits=True)
        assert lawsuit.total < normal.total
        assert "active_lawsuits" in lawsuit.flags

    def test_telegram_activity_boosts_score(self):
        inactive = calculate_trust_score(self._profile(), telegram_activity=0)
        active = calculate_trust_score(self._profile(), telegram_activity=10)
        assert active.total > inactive.total
        assert active.activity_score == 20
        assert "active_in_chats" in active.flags

    def test_fleet_boosts_score(self):
        no_fleet = calculate_trust_score(self._profile(), verified_vehicles=0)
        with_fleet = calculate_trust_score(self._profile(), verified_vehicles=4)
        assert with_fleet.total > no_fleet.total
        assert with_fleet.fleet_score == 20

    def test_score_capped_at_100(self):
        result = calculate_trust_score(
            self._profile(age_years=10.0, capital=10_000_000),
            telegram_activity=50, verified_vehicles=10,
        )
        assert result.total <= 100

    def test_score_min_zero(self):
        result = calculate_trust_score(
            self._profile(age_years=0.1, capital=1000, is_liquidating=True),
            has_active_lawsuits=True,
        )
        assert result.total >= 0

    def test_verdict_yellow_range(self):
        result = calculate_trust_score(self._profile(age_years=1.5), telegram_activity=2)
        assert result.verdict in ("yellow", "green")

    def test_components_sum(self):
        result = calculate_trust_score(self._profile(), telegram_activity=5, verified_vehicles=2)
        component_sum = result.age_score + result.activity_score + result.finance_score + result.fleet_score
        assert result.total == min(100, max(0, component_sum))
