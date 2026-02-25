import re
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Tuple

from src.core.logger import logger

_CITIES_FILE = Path(__file__).with_name("russia_cities.txt")

_ALIASES = {
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "мск": "Москва",
    "н новгород": "Нижний Новгород",
    "екб": "Екатеринбург",
    "ростов на дону": "Ростов-на-Дону",
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = t.split(",", 1)[0]
    t = re.sub(r"^г\\.?\\s+", "", t)
    t = t.replace("ё", "е")
    t = re.sub(r"[^0-9a-zа-я\\s-]", " ", t)
    t = t.replace("-", " ")
    t = re.sub(r"\\s+", " ", t).strip()
    return t


@lru_cache(maxsize=1)
def _city_index() -> Tuple[list[str], dict[str, str], list[str]]:
    try:
        raw = _CITIES_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logger.error("Cities file not found: %s", _CITIES_FILE)
        return [], {}, []

    cities: list[str] = []
    index: dict[str, str] = {}
    for line in raw:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        cities.append(name)
        norm = _normalize(name)
        if norm and norm not in index:
            index[norm] = name
    keys = list(index.keys())
    return cities, index, keys


def resolve_city(raw: str) -> Tuple[str | None, list[str]]:
    norm = _normalize(raw)
    if not norm:
        return None, []

    alias = _ALIASES.get(norm)
    if alias:
        return alias, []

    _, index, keys = _city_index()
    if not index:
        return None, []

    if norm in index:
        return index[norm], []

    suggestions_norm = get_close_matches(norm, keys, n=3, cutoff=0.8)
    suggestions = [index[s] for s in suggestions_norm]
    return None, suggestions
