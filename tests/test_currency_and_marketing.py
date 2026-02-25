"""Tests for currency converter and marketing engine."""

from src.core.services.currency import SUPPORTED


class TestCurrency:
    def test_supported_currencies(self):
        assert "RUB" in SUPPORTED
        assert "USD" in SUPPORTED
        assert "KZT" in SUPPORTED
        assert "BYN" in SUPPORTED
        assert "EUR" in SUPPORTED

    def test_symbols(self):
        assert SUPPORTED["RUB"]["symbol"] == "₽"
        assert SUPPORTED["USD"]["symbol"] == "$"
        assert SUPPORTED["KZT"]["symbol"] == "₸"

    def test_all_have_names(self):
        for code, info in SUPPORTED.items():
            assert "name" in info, f"{code} missing name"
            assert "symbol" in info, f"{code} missing symbol"
