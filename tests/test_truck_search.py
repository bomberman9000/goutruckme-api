from __future__ import annotations

import asyncio

from src.core.truck_search import (
    extract_truck_search_params,
    looks_like_truck_offer_text,
    looks_like_truck_search_text,
    parse_tonnage_hint,
    parse_truck_type,
)


def test_parse_tonnage_hint_handles_tons_and_kilograms():
    assert parse_tonnage_hint("4 тонны") == 4.0
    assert parse_tonnage_hint("3000 кг") == 3.0


def test_parse_truck_type_matches_substrings():
    assert parse_truck_type("нужен манипулятор из Тюмени") == "манипулятор"
    assert parse_truck_type("ищу реф на завтра") == "рефрижератор"


def test_truck_text_intent_heuristics():
    assert looks_like_truck_search_text("ищу машину из Москвы в Самару 4 тонны завтра")
    assert not looks_like_truck_offer_text("ищу машину из Москвы в Самару 4 тонны завтра")
    assert looks_like_truck_offer_text("камаз 10т свободен, межгород")
    assert looks_like_truck_offer_text("10т свободен москва")


def test_extract_truck_search_params_normalizes_weight():
    parsed = asyncio.run(extract_truck_search_params("ищу машину из Москвы в Самару 4 тонны завтра"))
    assert parsed is not None
    assert (parsed["from_city"] or "").upper() == "МОСКВА"
    assert (parsed["to_city"] or "").upper() == "САМАРА"
    assert parsed["weight"] == 4.0


def test_extract_truck_search_params_prefers_explicit_route_over_generic_parser():
    parsed = asyncio.run(extract_truck_search_params("ищу машину из Тюмени в Екатеринбург 3 тонны манипулятор"))
    assert parsed is not None
    assert parsed["from_city"] == "Тюмень"
    assert parsed["to_city"] == "Екатеринбург"
    assert parsed["weight"] == 3.0
    assert parsed["truck_type"] == "манипулятор"


def test_extract_truck_search_params_handles_tonnik_and_bare_city_pair():
    text = "ищу 10 ти тонник Москва самара"
    assert looks_like_truck_search_text(text)
    parsed = asyncio.run(extract_truck_search_params(text))
    assert parsed is not None
    assert parsed["from_city"] == "Москва"
    assert parsed["to_city"] == "Самара"
    assert parsed["weight"] == 10.0
    assert parsed["truck_type"] == "тент"


def test_extract_truck_search_params_infers_gazel_for_small_weight_without_type():
    parsed = asyncio.run(extract_truck_search_params("ищу 2т москва самара"))
    assert parsed is not None
    assert parsed["truck_type"] == "газель"
