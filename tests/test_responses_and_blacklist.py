"""Tests for response generator and phone blacklist."""

from src.core.services.responses import (
    build_default_response,
    build_suggested_response,
)


class TestBuildDefaultResponse:
    def test_basic(self):
        resp = build_default_response(from_city="Самара", to_city="Москва")
        assert "Самара" in resp
        assert "Москва" in resp
        assert "готов ехать" in resp

    def test_with_body_and_weight(self):
        resp = build_default_response(
            from_city="А", to_city="Б", body_type="трал", weight_t=20.0
        )
        assert "трал" in resp
        assert "20.0" in resp

    def test_with_date(self):
        resp = build_default_response(
            from_city="А", to_city="Б", load_date="2026-03-01"
        )
        assert "2026-03-01" in resp


class TestBuildSuggestedResponse:
    def test_with_carrier_info(self):
        resp = build_suggested_response(
            from_city="Самара",
            to_city="Москва",
            body_type="трал",
            weight_t=20.0,
            carrier_name="Иванов И.",
            carrier_phone="+79991112233",
        )
        assert "Самара" in resp
        assert "Москва" in resp
        assert "+79991112233" in resp
        assert "Иванов И." in resp

    def test_without_carrier(self):
        resp = build_suggested_response(from_city="А", to_city="Б")
        assert "Перевозчик" in resp
