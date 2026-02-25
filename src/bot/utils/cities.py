from __future__ import annotations

import re
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Iterable

try:
    from rapidfuzz import process as rf_process
except Exception:  # pragma: no cover - fallback when rapidfuzz not installed
    rf_process = None

ALIASES = {
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "петербург": "Санкт-Петербург",
    "мск": "Москва",
    "н новгород": "Нижний Новгород",
    "екб": "Екатеринбург",
    "ростов на дону": "Ростов-на-Дону",
}

FALLBACK_CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Нижний Новгород",
    "Казань",
    "Самара",
    "Омск",
    "Ростов-на-Дону",
    "Уфа",
    "Красноярск",
    "Пермь",
    "Воронеж",
    "Волгоград",
]

_CITIES_FILE = Path(__file__).resolve().parents[2] / "core" / "russia_cities.txt"


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
def _city_index() -> tuple[list[str], dict[str, str], list[str]]:
    cities: list[str] = []
    index: dict[str, str] = {}
    try:
        raw = _CITIES_FILE.read_text(encoding="utf-8").splitlines()
        for line in raw:
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            cities.append(name)
    except FileNotFoundError:
        cities = list(FALLBACK_CITIES)

    for name in cities:
        norm = _normalize(name)
        if norm and norm not in index:
            index[norm] = name

    keys = list(index.keys())
    return cities, index, keys


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def city_suggest(query: str, limit: int = 8) -> list[str]:
    norm = _normalize(query)
    if not norm:
        return []

    cities, index, keys = _city_index()
    results: list[str] = []

    alias = ALIASES.get(norm)
    if alias:
        results.append(alias)

    exact = index.get(norm)
    if exact:
        results.append(exact)

    for city in cities:
        if norm in _normalize(city):
            results.append(city)

    if rf_process:
        matches = rf_process.extract(norm, keys, limit=limit, score_cutoff=70)
        for key, _score, _ in matches:
            results.append(index[key])
    else:
        close = get_close_matches(norm, keys, n=limit, cutoff=0.8)
        for key in close:
            results.append(index[key])

    return _dedupe(results)[:limit]
