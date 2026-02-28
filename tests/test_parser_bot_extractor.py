from src.parser_bot.extractor import (
    build_dedupe_key,
    contains_invalid_geo_token,
    parse_cargo_message,
    split_cargo_message_blocks,
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


def test_parse_route_with_slash_or_pipe_separator():
    slash_parsed = parse_cargo_message("Тент Москва / Казань 20т 120к", keywords=["тент"])
    assert slash_parsed is not None
    assert slash_parsed.from_city == "Москва"
    assert slash_parsed.to_city == "Казань"

    pipe_parsed = parse_cargo_message("Реф Самара | Уфа 10т 50000", keywords=["реф"])
    assert pipe_parsed is not None
    assert pipe_parsed.from_city == "Самара"
    assert pipe_parsed.to_city == "Уфа"


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


def test_parse_cargo_message_prefers_first_valid_route_over_invalid_later_line():
    text = (
        "Калужская область (Ворсино) ➞ Ташкент\n"
        "Растаможка - Ташкент\n"
        "23 тонна\n"
        "Тўлов - нақд 3100 $\n"
    )
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Ворсино"
    assert parsed.to_city == "Ташкент"


def test_parse_cargo_message_ignores_flag_emoji_and_payment_route_noise():
    text = (
        "🇺🇿ТАШКЕНТ-🇰🇬БИШКЕК\n"
        "ОПТАЛА-ПЕРЕЧИС/НАЛ\n"
        "22 ТОННЫ\n"
        "📞 +998920104057\n"
    )
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Ташкент"
    assert parsed.to_city == "Бишкек"


def test_parse_cargo_message_parses_translit_compact_route():
    text = "Toshkent-SAMARQAND Tent fura yoki ref +998870200292"
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Ташкент"
    assert parsed.to_city in {"Самарканд", "Коканд"}
    assert parsed.body_type == "тент"
    assert parsed.phone == "+998870200292"
    assert parsed.inn is None


def test_parse_cargo_message_parses_uzbek_suffix_route():
    text = "Jizzaxdan Qashqadaryoga yuk bor 25tona Fura tent"
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Джизак"
    assert parsed.to_city == "Кашкадарья"
    assert parsed.weight_t == 25.0


def test_parse_cargo_message_parses_local_uzbek_phone_without_country_code():
    text = "Buxoro - Qashqadaryo tent kerak 90-478-28-11"
    parsed = parse_cargo_message(text, keywords=["тент"])

    assert parsed is not None
    assert parsed.from_city == "Бухара"
    assert parsed.to_city == "Кашкадарья"
    assert parsed.phone == "+998904782811"


def test_split_cargo_message_blocks_splits_multiload_posts():
    text = (
        "Москва - Ташкент\n"
        "20т\n"
        "\n"
        "Пермь - Ташкент\n"
        "22т\n"
    )
    blocks = split_cargo_message_blocks(text)

    assert len(blocks) == 2
    assert "Москва - Ташкент" in blocks[0]
    assert "Пермь - Ташкент" in blocks[1]


def test_split_cargo_message_blocks_keeps_single_route_with_details():
    text = (
        "Москва - Ташкент\n"
        "20т\n"
        "Тент\n"
        "Оплата нал\n"
    )
    blocks = split_cargo_message_blocks(text)

    assert blocks == [text.strip()]


def test_split_cargo_message_blocks_expands_stacked_city_lists():
    text = (
        "🇷🇺 МОСКВА ЕГОРЬЕВСК\n"
        "🇺🇿 ТОШКЕНТ\n"
        "🇺🇿 ВОДИЙ\n"
        "🚛 Тент Фура\n"
        "📞 +998917431571\n"
    )
    blocks = split_cargo_message_blocks(text)

    assert len(blocks) == 2
    assert blocks[0].startswith("Егорьевск - Ташкент")
    assert blocks[1].startswith("Егорьевск - Водий")
    assert "Тент Фура" in blocks[0]


def test_parse_cargo_message_skips_invalid_geo_blacklist_route():
    assert parse_cargo_message("Оплата - Ташкент, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Оптала - Ташкент, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Ташкент - Перечисление, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Верхняя - Ташкент, 20т, 100к", keywords=["тент"]) is None
    assert parse_cargo_message("Без нала - Ташкент, 20т, 100к", keywords=["тент"]) is None


def test_contains_invalid_geo_token_detects_payment_noise():
    assert contains_invalid_geo_token("Оплата наличными, Москва - Ташкент") is True
    assert contains_invalid_geo_token("ОПТАЛА-ПЕРЕЧИС/НАЛ") is True
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
    assert parsed.from_city == "Ташкент"
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
