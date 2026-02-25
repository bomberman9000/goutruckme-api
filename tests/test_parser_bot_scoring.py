import pytest

from src.services.scoring import get_score


@pytest.mark.asyncio
async def test_get_score_invalid_inn_returns_red():
    result = await get_score("invalid")
    assert result.inn is None
    assert result.score == 35
    assert result.verdict == "red"
    assert result.provider == "none"


@pytest.mark.asyncio
async def test_get_score_stub_provider_for_valid_inn():
    result = await get_score("7701234567")
    assert result.inn == "7701234567"
    assert result.provider in {"stub", "dadata"}
    assert 0 <= result.score <= 100
    assert result.verdict in {"green", "yellow", "red"}
