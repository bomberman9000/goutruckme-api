from src.parser_bot.extractor import build_dedupe_key, parse_cargo_message


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


def test_parse_cargo_message_requires_route_and_keyword():
    assert parse_cargo_message("Просто привет, без маршрута", keywords=["тент"]) is None
    assert parse_cargo_message("Мск - Казань без ключевых слов", keywords=["реф"]) is None


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
