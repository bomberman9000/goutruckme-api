from src.parser_bot.extractor import (
    build_dedupe_key,
    contains_invalid_geo_token,
    parse_cargo_message,
)


def test_parse_cargo_message_success():
    text = "Нужен тент Мск - Казань, 20т, 120к НДС, тел +7 (999) 111-22-33, ИНН 7701234567"
    parsed = parse_cargo_message(
        text,
        keywords=["тент", "ндс", "погрузка"],
    )

    assert parsed is not None
    assert parsed.from_city == "Москва"
    assert parsed.to_city == "Казань"
    assert parsed.body_type == "тент"
    assert parsed.weight_t == 20.0
    assert parsed.rate_rub == 120000
    assert parsed.phone == "+79991112233"
    assert parsed.inn == "7701234567"


def test_parse_route_with_city_hyphen_name():
    text = "Нужен реф Санкт-Петербург - Навои, 22т, 500000, тел +79990001122"
    parsed = parse_cargo_message(text, keywords=["реф"])
    assert parsed is not None
    assert parsed.from_city == "Санкт-Петербург"
    assert parsed.to_city == "Навои"


def test_parse_compact_route_without_spaces():
    text = "Тент Москва-Ташкент 20т 120к +79991112233"
    parsed = parse_cargo_message(text, keywords=["тент"])
    assert parsed is not None
    assert parsed.from_city == "Москва"
    assert parsed.to_city == "Ташкент"


def test_parse_cargo_message_requires_route_and_keyword():
    assert parse_cargo_message("Просто привет, без маршрута", keywords=["тент"]) is None
    assert parse_cargo_message("Мск - Казань без ключевых слов", keywords=["реф"]) is None


def test_parse_cargo_message_uses_cargo_signals_without_keyword_match():
    text = "Москва - Ташкент 20т 120к тел +7 999 000 11 22"
    parsed = parse_cargo_message(text, keywords=["реф"])

    assert parsed is not None
    assert parsed.from_city == "Москва"
    assert parsed.to_city == "Ташкент"
    assert parsed.rate_rub == 120000
    assert parsed.weight_t == 20.0
    assert parsed.matched_keywords == ["auto"]


def test_parse_cargo_message_normalizes_common_city_typos():
    text = "Тент Екатеринбург - Ташкен, 20т, 100к"
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Екатеринбург"
    assert parsed.to_city == "Ташкент"

    typo_text = "Тент Нижний Новогород - Ташкент, 20т, 100к"
    typo_parsed = parse_cargo_message(typo_text, keywords=["тент"])
    assert typo_parsed is not None
    assert typo_parsed.from_city == "Нижний Новгород"


def test_parse_cargo_message_normalizes_cis_city_variants():
    text = "Тент Бухоро - Самарқанд, 20т, 100к"
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Бухара"
    assert parsed.to_city == "Самарканд"


def test_parse_cargo_message_skips_non_city_stopword_route():
    text = "Растаможка - Ташкент, 20т, 310000"
    parsed = parse_cargo_message(text, keywords=["тент"])
    assert parsed is None


def test_parse_cargo_message_skips_invalid_geo_blacklist_route():
    assert parse_cargo_message("Оплата - Ташкент, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Верхняя - Ташкент, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Без нала - Ташкент, 20т, 100к", keywords=["тент"]) is None


def test_contains_invalid_geo_token_detects_payment_noise():
    assert contains_invalid_geo_token("Оплата наличными, Москва - Ташкент") is True
    assert contains_invalid_geo_token("Без нала, Москва - Ташкент") is True
    assert contains_invalid_geo_token("Москва - Ташкент, 20т, 120к") is False


def test_parse_cargo_message_does_not_treat_single_hyphenated_city_as_route():
    text = "Йошкар-Ола 20т 120к +79990001122"
    parsed = parse_cargo_message(text, keywords=["тент"])
    assert parsed is None


def test_parse_price_from_keyword_with_thousand_separators():
    text = "Тент Москва - Самара, фрахт 6.500.000, тел +7 999 1112233"
    parsed = parse_cargo_message(text, keywords=["тент", "фрахт"])
    assert parsed is not None
    assert parsed.rate_rub == 6500000


def test_parse_price_without_suffix_with_nds():
    text = "Реф Казань - Уфа, ставка 135000 с НДС, тел 8 999 000 11 22"
    parsed = parse_cargo_message(text, keywords=["реф", "ставка"])
    assert parsed is not None
    assert parsed.rate_rub == 135000


def test_parse_price_in_usd_suffix():
    text = "Тент Самара - Ташкент, 20т, 1800$, тел +79991112233"
    parsed = parse_cargo_message(text, keywords=["тент"])
    assert parsed is not None
    assert parsed.rate_rub == 180000


def test_parse_route_with_apostrophe_city_names():
    text = "Toshkent-Farg'ona 20t 120k +79991112233"
    parsed = parse_cargo_message(text, keywords=["реф"])
    assert parsed is not None
    assert parsed.from_city == "Toshkent"
    assert parsed.to_city == "Farg'ona"
    assert parsed.matched_keywords == ["auto"]


def test_build_dedupe_key_is_stable_for_same_route_and_phone():
    parsed = parse_cargo_message(
        "Тент СПБ - МСК, 20т, 80к, 8 999 000 11 22",
        keywords=["тент"],
    )
    assert parsed is not None

    key_a = build_dedupe_key(parsed, chat_id=-100123, fallback_id="msg-1")
    key_b = build_dedupe_key(parsed, chat_id=-100123, fallback_id="msg-2")
    key_c = build_dedupe_key(parsed, chat_id=-100777, fallback_id="msg-2")

    assert key_a == key_b
    assert key_a != key_c
