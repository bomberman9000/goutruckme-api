"""Tests for v2 features: content dedupe, geo, feed filters, hot deals."""

from __future__ import annotations

from src.parser_bot.extractor import (
    ParsedCargo,
    build_content_dedupe_key,
    build_dedupe_key,
    evaluate_hot_deal,
    looks_like_cargo,
    _llm_result_to_parsed,
)
from src.core.geo import city_coords, haversine_km, resolve_region, region_center

KEYWORDS = ["груз", "тент", "реф", "ндс", "ставка", "погрузка", "трал"]


# ---------------------------------------------------------------------------
# Content-based deduplication
# ---------------------------------------------------------------------------
class TestContentDedupe:
    def _cargo(self, **overrides) -> ParsedCargo:
        defaults = dict(
            from_city="Самара",
            to_city="Москва",
            body_type="трал",
            rate_rub=120000,
            weight_t=20.0,
            phone="+79991112233",
            inn=None,
            matched_keywords=["трал"],
            raw_text="test",
            load_date="2026-02-26",
            load_time="12:00",
        )
        defaults.update(overrides)
        return ParsedCargo(**defaults)

    def test_same_content_same_key(self):
        a = self._cargo()
        b = self._cargo(phone="+79998887766", rate_rub=999)
        assert build_content_dedupe_key(a) == build_content_dedupe_key(b)

    def test_different_route_different_key(self):
        a = self._cargo(from_city="Самара")
        b = self._cargo(from_city="Казань")
        assert build_content_dedupe_key(a) != build_content_dedupe_key(b)

    def test_different_weight_different_key(self):
        a = self._cargo(weight_t=20.0)
        b = self._cargo(weight_t=15.0)
        assert build_content_dedupe_key(a) != build_content_dedupe_key(b)

    def test_different_date_different_key(self):
        a = self._cargo(load_date="2026-02-26")
        b = self._cargo(load_date="2026-02-27")
        assert build_content_dedupe_key(a) != build_content_dedupe_key(b)

    def test_original_dedupe_still_works(self):
        a = self._cargo()
        key = build_dedupe_key(a, chat_id=-100123, fallback_id="msg-1")
        assert key.startswith("parser-dedupe:")


# ---------------------------------------------------------------------------
# Geo module
# ---------------------------------------------------------------------------
class TestGeo:
    def test_known_city(self):
        coords = city_coords("Москва")
        assert coords is not None
        assert abs(coords[0] - 55.7558) < 0.01

    def test_unknown_city(self):
        assert city_coords("НесуществующийГород") is None

    def test_case_insensitive(self):
        assert city_coords("москва") == city_coords("МОСКВА")

    def test_haversine_moscow_spb(self):
        msk = city_coords("Москва")
        spb = city_coords("Санкт-Петербург")
        assert msk and spb
        dist = haversine_km(msk[0], msk[1], spb[0], spb[1])
        assert 600 < dist < 750

    def test_haversine_same_point(self):
        assert haversine_km(55.0, 37.0, 55.0, 37.0) == 0.0

    def test_city_with_dash(self):
        coords = city_coords("Ростов-на-Дону")
        assert coords is not None

    def test_many_cities_available(self):
        cities = [
            "Казань", "Самара", "Екатеринбург", "Краснодар",
            "Воронеж", "Челябинск", "Омск", "Красноярск",
            "Пермь", "Волгоград", "Уфа", "Тюмень",
        ]
        for city in cities:
            assert city_coords(city) is not None, f"{city} not found"


# ---------------------------------------------------------------------------
# Hot Deals evaluation
# ---------------------------------------------------------------------------
class TestHotDeal:
    def _cargo(self, **kw) -> ParsedCargo:
        defaults = dict(
            from_city="Москва", to_city="Краснодар",
            body_type="тент", rate_rub=100000, weight_t=20.0,
            phone=None, inn=None, matched_keywords=["тент"],
            raw_text="test",
        )
        defaults.update(kw)
        return ParsedCargo(**defaults)

    def test_high_rate_is_hot(self):
        cargo = self._cargo(rate_rub=200000)
        assert evaluate_hot_deal(cargo) is True

    def test_low_rate_not_hot(self):
        cargo = self._cargo(rate_rub=10000)
        assert evaluate_hot_deal(cargo) is False

    def test_no_rate_not_hot(self):
        cargo = self._cargo(rate_rub=None)
        assert evaluate_hot_deal(cargo) is False

    def test_unknown_city_not_hot(self):
        cargo = self._cargo(from_city="НеизвестныйГород", rate_rub=999999)
        assert evaluate_hot_deal(cargo) is False


# ---------------------------------------------------------------------------
# New LLM fields: payment_terms, is_direct_customer, dimensions
# ---------------------------------------------------------------------------
class TestNewLlmFields:
    def test_payment_terms_extracted(self):
        data = {
            "from_city": "А", "to_city": "Б",
            "payment_terms": "без НДС, нал",
        }
        parsed = _llm_result_to_parsed(data, "груз А-Б", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.payment_terms == "без НДС, нал"

    def test_is_direct_customer_bool(self):
        data = {"from_city": "А", "to_city": "Б", "is_direct_customer": True}
        parsed = _llm_result_to_parsed(data, "груз А-Б", keywords=KEYWORDS)
        assert parsed.is_direct_customer is True

    def test_is_direct_customer_string(self):
        data = {"from_city": "А", "to_city": "Б", "is_direct_customer": "true"}
        parsed = _llm_result_to_parsed(data, "груз А-Б", keywords=KEYWORDS)
        assert parsed.is_direct_customer is True

    def test_dimensions_extracted(self):
        data = {"from_city": "А", "to_city": "Б", "dimensions": "12x2.4x2.7"}
        parsed = _llm_result_to_parsed(data, "груз А-Б", keywords=KEYWORDS)
        assert parsed.dimensions == "12x2.4x2.7"

    def test_missing_new_fields_are_none(self):
        data = {"from_city": "А", "to_city": "Б"}
        parsed = _llm_result_to_parsed(data, "груз А-Б", keywords=KEYWORDS)
        assert parsed.payment_terms is None
        assert parsed.is_direct_customer is None
        assert parsed.dimensions is None
        assert parsed.is_hot_deal is False


# ---------------------------------------------------------------------------
# Geo regions
# ---------------------------------------------------------------------------
class TestGeoRegions:
    def test_resolve_siberia(self):
        cities = resolve_region("Сибирь")
        assert cities is not None
        assert "новосибирск" in cities
        assert "омск" in cities

    def test_resolve_alias(self):
        assert resolve_region("Кубань") is not None
        assert resolve_region("ЦФО") is not None
        assert resolve_region("ДВФО") is not None

    def test_resolve_unknown_returns_none(self):
        assert resolve_region("Марс") is None

    def test_region_center(self):
        center = region_center("Урал")
        assert center is not None
        assert 50 < center[0] < 60

    def test_region_center_unknown(self):
        assert region_center("Нигде") is None


# ---------------------------------------------------------------------------
# LLM pre-filter (anti-flood)
# ---------------------------------------------------------------------------
class TestLooksLikeCargo:
    def test_cargo_message(self):
        assert looks_like_cargo("тент 20т Мск-Казань 120к") is True

    def test_weight_signal(self):
        assert looks_like_cargo("нужна машина 15 тонн завтра") is True

    def test_price_signal(self):
        assert looks_like_cargo("ставка 80к с ндс самара") is True

    def test_vehicle_signal(self):
        assert looks_like_cargo("нужна фура на загрузку в среду") is True

    def test_chat_noise_skipped(self):
        assert looks_like_cargo("Привет всем!") is False
        assert looks_like_cargo("Обедаем") is False
        assert looks_like_cargo("Доброе утро, коллеги") is False
        assert looks_like_cargo("Когда будет груз?") is False

    def test_short_text_skipped(self):
        assert looks_like_cargo("ок") is False
        assert looks_like_cargo("да") is False

    def test_route_arrow(self):
        assert looks_like_cargo("Самара → Москва нужна машина") is True

    def test_payment_terms(self):
        assert looks_like_cargo("груз с предоплатой безнал") is True
