from src.bot.utils.cities import city_suggest as bot_city_suggest
from src.core.cities import city_suggest, resolve_city


def test_city_directory_resolves_common_cis_aliases():
    resolved, suggestions = resolve_city("город Бишкек")

    assert resolved == "Бишкек"
    assert suggestions == []

    resolved, suggestions = resolve_city("toshkent")
    assert resolved == "Ташкент"
    assert suggestions == []


def test_city_directory_suggests_cis_cities():
    suggestions = city_suggest("Ташк", limit=5)

    assert "Ташкент" in suggestions

    bot_suggestions = bot_city_suggest("Биш", limit=5)
    assert "Бишкек" in bot_suggestions
