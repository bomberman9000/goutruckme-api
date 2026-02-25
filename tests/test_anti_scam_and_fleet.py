"""Tests for Anti-Scam complaints and Fleet/Vehicle logic."""

from src.core.services.anti_scam import HIDE_THRESHOLD


class TestAntiScamThreshold:
    def test_threshold_is_3(self):
        assert HIDE_THRESHOLD == 3

    def test_threshold_is_positive(self):
        assert HIDE_THRESHOLD > 0


class TestVehicleTypes:
    def test_standard_body_types(self):
        from src.parser_bot.extractor import BODY_TYPES
        assert "тент" in BODY_TYPES
        assert "трал" in BODY_TYPES
        assert "рефрижератор" in BODY_TYPES.values()

    def test_alias_mapping(self):
        from src.parser_bot.extractor import BODY_TYPES
        assert BODY_TYPES["площадка"] == "трал"
        assert BODY_TYPES["фура"] == "тент"


class TestGeoForMatching:
    def test_city_coords_for_matching(self):
        from src.core.geo import city_coords
        assert city_coords("Казань") is not None
        assert city_coords("Самара") is not None
        assert city_coords("Москва") is not None

    def test_haversine_reasonable(self):
        from src.core.geo import haversine_km, city_coords
        msk = city_coords("Москва")
        kzn = city_coords("Казань")
        assert msk and kzn
        dist = haversine_km(msk[0], msk[1], kzn[0], kzn[1])
        assert 700 < dist < 900


class TestTrustScoreEdgeCases:
    def test_unknown_age(self):
        from src.core.services.company_profile import CompanyProfile, calculate_trust_score
        p = CompanyProfile(inn="1234567890", age_years=None, source="test")
        result = calculate_trust_score(p)
        assert result.age_score == 5
        assert "age_unknown" in result.flags

    def test_all_penalties(self):
        from src.core.services.company_profile import CompanyProfile, calculate_trust_score
        p = CompanyProfile(inn="1234567890", age_years=0.2, capital=5000, is_liquidating=True, source="test")
        result = calculate_trust_score(p, has_active_lawsuits=True)
        assert result.total < 20
        assert result.verdict == "red"

    def test_perfect_score(self):
        from src.core.services.company_profile import CompanyProfile, calculate_trust_score
        p = CompanyProfile(inn="1234567890", age_years=10, capital=5_000_000, source="test")
        result = calculate_trust_score(p, telegram_activity=20, verified_vehicles=5)
        assert result.total >= 90
        assert result.verdict == "green"

    def test_medium_score(self):
        from src.core.services.company_profile import CompanyProfile, calculate_trust_score
        p = CompanyProfile(inn="1234567890", age_years=2.0, capital=100_000, source="test")
        result = calculate_trust_score(p, telegram_activity=3)
        assert 40 <= result.total <= 70


class TestPreFilter:
    def test_cargo_text_passes(self):
        from src.parser_bot.extractor import looks_like_cargo
        assert looks_like_cargo("трал 20т мск-казань 120к") is True

    def test_noise_blocked(self):
        from src.parser_bot.extractor import looks_like_cargo
        assert looks_like_cargo("Доброе утро, коллеги!") is False
