from __future__ import annotations

import asyncio

from src.core import ai


def test_parse_cargo_nlp_extracts_volume_and_cargo_profile(monkeypatch):
    monkeypatch.setattr(ai, "client", None)

    parsed = asyncio.run(ai.parse_cargo_nlp("самар казан 20т 82м3 досок 145к завтра"))

    assert parsed is not None
    assert parsed["from_city"] == "Самара"
    assert parsed["to_city"] == "Казань"
    assert parsed["weight"] == 20.0
    assert parsed["volume_m3"] == 82.0
    assert parsed["cargo_type"] == "Пиломатериалы"
    assert parsed["body_type"] == "борт"
    assert parsed["price"] == 145000


def test_parse_cargo_nlp_handles_partial_volume_fragment(monkeypatch):
    monkeypatch.setattr(ai, "client", None)

    parsed = asyncio.run(ai.parse_cargo_nlp("82м3 досок"))

    assert parsed is not None
    assert parsed["volume_m3"] == 82.0
    assert parsed["cargo_type"] == "Пиломатериалы"
    assert parsed["body_type"] == "борт"
    assert "weight" not in parsed


def test_calculate_market_rate_uses_benchmark_for_exact_route():
    rate = ai.calculate_market_rate(
        from_city="Москва",
        to_city="Кемерово",
        distance_km=3560,
        weight=20,
        cargo_type="ТНП",
        body_type="тент",
    )

    assert rate["rate_per_km"] == 85
    assert rate["price"] == 301682
    assert rate["is_international"] is False
    assert "benchmark" in rate["source"]


def test_calculate_market_rate_boosts_short_haul():
    rate = ai.calculate_market_rate(
        from_city="Смоленск",
        to_city="Москва",
        distance_km=400,
        weight=20,
        cargo_type="ТНП",
        body_type="тент",
    )

    assert rate["rate_per_km"] >= 120
    assert rate["price"] >= 48000
    assert "короткое плечо" in rate["factors"]


def test_calculate_market_rate_boosts_international_reefer_long_haul():
    rate = ai.calculate_market_rate(
        from_city="Москва",
        to_city="Ташкент",
        distance_km=3000,
        weight=20,
        cargo_type="Продукты",
        body_type="рефрижератор",
    )

    assert rate["is_international"] is True
    assert rate["rate_per_km"] >= 160
    assert rate["price"] > 450000
    assert "международное направление" in rate["factors"]
    assert "рефрижератор" in rate["factors"]
