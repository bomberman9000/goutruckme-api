import src.core.geo as geo
from src.core.config import settings

from src.parser_bot.extractor import ParsedCargo
from src.parser_bot.worker import (
    _has_min_signal,
    _is_unrealistic_rate,
    _rate_review_reason,
    _should_drop_ambiguous_million_rate,
)


def make_parsed(from_city: str, to_city: str, rate_rub: int) -> ParsedCargo:
    return ParsedCargo(
        from_city=from_city,
        to_city=to_city,
        body_type="тент",
        rate_rub=rate_rub,
        weight_t=20.0,
        phone=None,
        inn=None,
        matched_keywords=["auto"],
        raw_text="stub",
    )


def test_is_unrealistic_rate_rejects_tiny_absolute_values():
    parsed = make_parsed("Москва", "Казань", 3203)
    assert _is_unrealistic_rate(parsed) is True


def test_is_unrealistic_rate_rejects_too_low_rate_per_km(monkeypatch):
    monkeypatch.setattr(geo, "city_coords", lambda city: (0.0, 0.0) if city == "Москва" else (10.0, 10.0))
    monkeypatch.setattr(geo, "haversine_km", lambda *_args: 1200.0)

    parsed = make_parsed("Москва", "Екатеринбург", 8000)
    assert _is_unrealistic_rate(parsed) is True


def test_is_unrealistic_rate_allows_reasonable_rate():
    parsed = make_parsed("Москва", "Казань", 120000)
    assert _is_unrealistic_rate(parsed) is False


def test_rate_review_reason_uses_upper_cap():
    parsed = make_parsed("Москва", "Казань", settings.parser_max_rate_rub + 1)
    assert _rate_review_reason(parsed) == "rate_above_cap"


def test_rate_review_reason_uses_rate_per_km_cap(monkeypatch):
    monkeypatch.setattr(geo, "city_coords", lambda city: (0.0, 0.0) if city == "Москва" else (10.0, 10.0))
    monkeypatch.setattr(geo, "haversine_km", lambda *_args: 100.0)

    parsed = make_parsed("Москва", "Екатеринбург", 60000)
    assert _rate_review_reason(parsed) == "rate_per_km_above_cap"


def test_rate_review_reason_allows_reasonable_caps(monkeypatch):
    monkeypatch.setattr(geo, "city_coords", lambda city: (0.0, 0.0) if city == "Москва" else (10.0, 10.0))
    monkeypatch.setattr(geo, "haversine_km", lambda *_args: 300.0)

    parsed = make_parsed("Москва", "Екатеринбург", 120000)
    assert _rate_review_reason(parsed) is None


def test_has_min_signal_accepts_body_only_route_offer():
    parsed = ParsedCargo(
        from_city="Уфа",
        to_city="Ташкент",
        body_type="рефрижератор",
        rate_rub=None,
        weight_t=None,
        phone=None,
        inn=None,
        matched_keywords=["auto"],
        raw_text="stub",
    )

    assert _has_min_signal(parsed) is True


def test_has_min_signal_accepts_cargo_intent_phrase_without_numeric_fields():
    parsed = ParsedCargo(
        from_city="Поти",
        to_city="Астана",
        body_type=None,
        rate_rub=None,
        weight_t=None,
        phone=None,
        inn=None,
        matched_keywords=["auto"],
        raw_text="Поти - Астана Казахстан Груз готов",
    )

    assert _has_min_signal(parsed) is True


def test_has_min_signal_accepts_customs_or_ferry_intent_without_numeric_fields():
    customs = ParsedCargo(
        from_city="Балыкесир",
        to_city="Ташкент",
        body_type=None,
        rate_rub=None,
        weight_t=None,
        phone=None,
        inn=None,
        matched_keywords=["auto"],
        raw_text="Балыкесир - Ташкент Растаможка: Бурса",
    )
    ferry = ParsedCargo(
        from_city="Поти",
        to_city="Астана",
        body_type=None,
        rate_rub=None,
        weight_t=None,
        phone=None,
        inn=None,
        matched_keywords=["auto"],
        raw_text="Поти - Астана Казахстан Груз готов Через паром",
    )

    assert _has_min_signal(customs) is True
    assert _has_min_signal(ferry) is True


def test_should_drop_ambiguous_million_rate_for_central_asia_route():
    parsed = make_parsed("Липецк", "Ташкент", 40_000_000)
    assert _should_drop_ambiguous_million_rate("Липецк - Ташкент Ставка 40 млн", parsed) is True


def test_should_not_drop_million_rate_with_explicit_rubles_or_usd():
    parsed = make_parsed("Липецк", "Ташкент", 40_000_000)
    assert _should_drop_ambiguous_million_rate("Липецк - Ташкент Ставка 40 млн руб", parsed) is False
    assert _should_drop_ambiguous_million_rate("Мерсин - Астана Аванс 35000 $", make_parsed("Мерсин", "Астана", 3_500_000)) is False
