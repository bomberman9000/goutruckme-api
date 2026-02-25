from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

import pytest

from src.parser_bot.extractor import (
    _build_llm_system_prompt,
    _extract_json,
    _llm_result_to_parsed,
    _WEEKDAY_NAMES_RU,
    parse_cargo_message,
    parse_cargo_message_llm,
)

KEYWORDS = ["груз", "тент", "реф", "ндс", "ставка", "погрузка", "трал"]


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------
class TestExtractJson:
    def test_simple_json(self):
        result = _extract_json('{"from_city": "Москва", "to_city": "Казань"}')
        assert result == {"from_city": "Москва", "to_city": "Казань"}

    def test_json_in_markdown(self):
        text = '```json\n{"from_city": "Самара", "to_city": "Москва"}\n```'
        result = _extract_json(text)
        assert result == {"from_city": "Самара", "to_city": "Москва"}

    def test_json_with_surrounding_text(self):
        text = 'Вот результат: {"from_city": "Уфа", "to_city": "Пермь"} — готово.'
        result = _extract_json(text)
        assert result == {"from_city": "Уфа", "to_city": "Пермь"}

    def test_invalid_json_returns_none(self):
        assert _extract_json("не json вообще") is None
        assert _extract_json("{broken") is None
        assert _extract_json("") is None


# ---------------------------------------------------------------------------
# _llm_result_to_parsed  (unit tests for the converter)
# ---------------------------------------------------------------------------
class TestLlmResultToParsed:
    def test_full_result(self):
        data = {
            "from_city": "Самара",
            "to_city": "Москва",
            "body_type": "трал",
            "weight": 20,
            "rate": 120000,
            "load_date": "2026-02-26",
            "load_time": "12:00",
            "cargo_description": "трубы металлические",
            "payment_terms": "без НДС, нал",
            "is_direct_customer": True,
            "dimensions": "6x2.4x2.5",
        }
        raw = "трал 20 т самара- москва завтра в 12, ставка 120к, +79991112233"
        parsed = _llm_result_to_parsed(data, raw, keywords=KEYWORDS)

        assert parsed is not None
        assert parsed.from_city == "Самара"
        assert parsed.to_city == "Москва"
        assert parsed.body_type == "трал"
        assert parsed.weight_t == 20.0
        assert parsed.rate_rub == 120000
        assert parsed.load_date == "2026-02-26"
        assert parsed.load_time == "12:00"
        assert parsed.phone == "+79991112233"
        assert parsed.cargo_description == "трубы металлические"
        assert parsed.payment_terms == "без НДС, нал"
        assert parsed.is_direct_customer is True
        assert parsed.dimensions == "6x2.4x2.5"

    def test_missing_optional_fields(self):
        data = {"from_city": "Казань", "to_city": "Москва"}
        raw = "груз казань москва"
        parsed = _llm_result_to_parsed(data, raw, keywords=KEYWORDS)

        assert parsed is not None
        assert parsed.from_city == "Казань"
        assert parsed.to_city == "Москва"
        assert parsed.body_type is None
        assert parsed.weight_t is None
        assert parsed.rate_rub is None
        assert parsed.load_date is None
        assert parsed.load_time is None
        assert parsed.cargo_description is None

    def test_missing_route_returns_none(self):
        assert _llm_result_to_parsed({"from_city": "Москва"}, "test", keywords=KEYWORDS) is None
        assert _llm_result_to_parsed({}, "test", keywords=KEYWORDS) is None

    def test_body_type_aliases(self):
        data = {"from_city": "A", "to_city": "B", "body_type": "площадка"}
        parsed = _llm_result_to_parsed(data, "груз A-B площадка", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.body_type == "трал"

    def test_body_type_fura(self):
        data = {"from_city": "A", "to_city": "B", "body_type": "фура"}
        parsed = _llm_result_to_parsed(data, "груз A-B фура", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.body_type == "тент"

    def test_inn_extraction(self):
        data = {"from_city": "A", "to_city": "B"}
        parsed = _llm_result_to_parsed(
            data, "груз A-B ИНН 7701234567", keywords=KEYWORDS
        )
        assert parsed is not None
        assert parsed.inn == "7701234567"

    def test_weight_as_string(self):
        data = {"from_city": "A", "to_city": "B", "weight": "15,5"}
        parsed = _llm_result_to_parsed(data, "груз A-B", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.weight_t == 15.5

    def test_phone_from_llm_output(self):
        data = {
            "from_city": "Москва",
            "to_city": "Казань",
            "phone": "+79001234567",
        }
        parsed = _llm_result_to_parsed(data, "груз мск казань", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.phone == "+79001234567"

    def test_phone_from_llm_overrides_regex(self):
        data = {
            "from_city": "Москва",
            "to_city": "Казань",
            "phone": "+79001234567",
        }
        raw = "груз мск казань тел 89998887766"
        parsed = _llm_result_to_parsed(data, raw, keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.phone == "+79001234567"

    def test_phone_fallback_to_regex_when_llm_empty(self):
        data = {"from_city": "Москва", "to_city": "Казань"}
        raw = "груз мск казань тел 89998887766"
        parsed = _llm_result_to_parsed(data, raw, keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.phone == "+79998887766"

    def test_cargo_description_empty_string_becomes_none(self):
        data = {"from_city": "A", "to_city": "B", "cargo_description": "  "}
        parsed = _llm_result_to_parsed(data, "груз A-B", keywords=KEYWORDS)
        assert parsed is not None
        assert parsed.cargo_description is None


# ---------------------------------------------------------------------------
# _build_llm_system_prompt
# ---------------------------------------------------------------------------
class TestBuildLlmSystemPrompt:
    def test_contains_today_date(self):
        prompt = _build_llm_system_prompt()
        today_str = datetime.now().strftime("%Y-%m-%d")
        assert today_str in prompt

    def test_contains_tomorrow(self):
        prompt = _build_llm_system_prompt()
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        assert tomorrow in prompt

    def test_contains_weekday_name(self):
        prompt = _build_llm_system_prompt()
        weekday = _WEEKDAY_NAMES_RU[datetime.now().weekday()]
        assert weekday in prompt

    def test_contains_phone_extraction_instructions(self):
        prompt = _build_llm_system_prompt()
        assert "телефон" in prompt.lower()
        assert "+7XXXXXXXXXX" in prompt

    def test_contains_cargo_description_field(self):
        prompt = _build_llm_system_prompt()
        assert "cargo_description" in prompt

    def test_contains_weekday_conversion_hint(self):
        prompt = _build_llm_system_prompt()
        assert "понедельник" in prompt


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------
class TestRegexFallbackStillWorks:
    def test_regex_parse_success(self):
        text = "Нужен тент Мск - Казань, 20т, 120к НДС, тел +7 (999) 111-22-33"
        parsed = parse_cargo_message(text, keywords=["тент", "ндс"])
        assert parsed is not None
        assert parsed.from_city == "Москва"
        assert parsed.to_city == "Казань"
        assert parsed.body_type == "тент"
        assert parsed.weight_t == 20.0
        assert parsed.rate_rub == 120000
        assert parsed.load_date is None
        assert parsed.load_time is None
        assert parsed.cargo_description is None

    def test_new_body_type_aliases_in_regex(self):
        text = "Нужна площадка Мск - Казань, груз 10т"
        parsed = parse_cargo_message(text, keywords=["груз"])
        assert parsed is not None
        assert parsed.body_type == "трал"


# ---------------------------------------------------------------------------
# parse_cargo_message_llm — async integration with mocked providers
# ---------------------------------------------------------------------------
def _make_mock_response(content: str):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    return mock_response


@pytest.mark.asyncio
async def test_llm_parser_falls_back_without_any_key():
    with patch("src.core.config.settings") as mock_settings:
        mock_settings.groq_api_key = None
        mock_settings.openai_api_key = None
        text = "тент СПБ - МСК, 20т, 80к"
        parsed = await parse_cargo_message_llm(text, keywords=["тент"])
        assert parsed is not None
        assert parsed.from_city == "Санкт-Петербург"
        assert parsed.to_city == "Москва"


@pytest.mark.asyncio
async def test_llm_parser_groq_full():
    llm_json = (
        '{"from_city": "Самара", "to_city": "Москва", "body_type": "трал",'
        ' "weight": 20, "rate": 150000, "load_date": "2026-02-26",'
        ' "load_time": "12:00", "phone": "+79991112233",'
        ' "cargo_description": "трубы"}'
    )
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_mock_response(llm_json)
    )

    with patch("src.core.config.settings") as s:
        s.groq_api_key = "test-key"
        s.openai_api_key = None
        with patch("groq.AsyncGroq", return_value=mock_client):
            text = "трал 20т самара-москва завтра в 12, ставка 150к, трубы"
            parsed = await parse_cargo_message_llm(text, keywords=KEYWORDS)

    assert parsed is not None
    assert parsed.from_city == "Самара"
    assert parsed.to_city == "Москва"
    assert parsed.body_type == "трал"
    assert parsed.weight_t == 20.0
    assert parsed.rate_rub == 150000
    assert parsed.load_date == "2026-02-26"
    assert parsed.load_time == "12:00"
    assert parsed.phone == "+79991112233"
    assert parsed.cargo_description == "трубы"


@pytest.mark.asyncio
async def test_llm_parser_openai_preferred_over_groq():
    """When both keys are set, OpenAI should be preferred."""
    llm_json = '{"from_city": "Воронеж", "to_city": "Краснодар", "body_type": "тент"}'

    openai_response_json = {
        "choices": [{"message": {"content": llm_json}}]
    }

    with patch("src.core.config.settings") as s:
        s.groq_api_key = "groq-key"
        s.openai_api_key = "openai-key"
        s.openai_model = "gpt-4o-mini"
        with patch("httpx.AsyncClient") as mock_http_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = MagicMock(return_value=openai_response_json)

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_http_cls.return_value = mock_client

            text = "тент воронеж краснодар, груз"
            parsed = await parse_cargo_message_llm(text, keywords=KEYWORDS)

    assert parsed is not None
    assert parsed.from_city == "Воронеж"
    assert parsed.to_city == "Краснодар"
    assert parsed.body_type == "тент"


@pytest.mark.asyncio
async def test_llm_parser_falls_back_on_exception():
    with patch("src.core.config.settings") as s:
        s.groq_api_key = "test-key"
        s.openai_api_key = None
        with patch(
            "groq.AsyncGroq",
            side_effect=Exception("connection error"),
        ):
            text = "тент Мск - Казань, 20т, ставка 120к НДС"
            parsed = await parse_cargo_message_llm(text, keywords=["тент", "ндс"])

    assert parsed is not None
    assert parsed.from_city == "Москва"
    assert parsed.to_city == "Казань"


@pytest.mark.asyncio
async def test_llm_parser_falls_back_on_bad_json():
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        return_value=_make_mock_response("Извините, я не смог распознать")
    )

    with patch("src.core.config.settings") as s:
        s.groq_api_key = "test-key"
        s.openai_api_key = None
        with patch("groq.AsyncGroq", return_value=mock_client):
            text = "тент Мск - Казань, 20т, ставка 120к НДС"
            parsed = await parse_cargo_message_llm(text, keywords=["тент", "ндс"])

    assert parsed is not None
    assert parsed.from_city == "Москва"
    assert parsed.to_city == "Казань"


@pytest.mark.asyncio
async def test_llm_parser_empty_text_returns_none():
    result = await parse_cargo_message_llm("", keywords=KEYWORDS)
    assert result is None

    result2 = await parse_cargo_message_llm("   ", keywords=KEYWORDS)
    assert result2 is None
