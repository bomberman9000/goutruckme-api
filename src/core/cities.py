from __future__ import annotations

import re
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.core.logger import logger

_CITY_FILES = (
    Path(__file__).with_name("russia_cities.txt"),
    Path(__file__).with_name("cis_cities.txt"),
)

_ALIASES = {
    "спб": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "мск": "Москва",
    "н новгород": "Нижний Новгород",
    "екб": "Екатеринбург",
    "ростов на дону": "Ростов-на-Дону",
    "минск беларусь": "Минск",
    "город бишкек": "Бишкек",
    "астана": "Астана",
    "нур султан": "Астана",
    "шимкент": "Шымкент",
    "чимкент": "Шымкент",
    "тошкент": "Ташкент",
    "ташкен": "Ташкент",
    "ташкенд": "Ташкент",
    "samarqand": "Самарканд",
    "buxoro": "Бухара",
    "toshkent": "Ташкент",
    "andijon": "Андижан",
    "namangan": "Наманган",
    "fargona": "Фергана",
    "fergana": "Фергана",
    "jizzax": "Джизак",
    "qoqon": "Коканд",
    "urgench": "Ургенч",
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = t.split(",", 1)[0]
    t = re.sub(r"^г\.?\s+", "", t)
    t = t.replace("ё", "е")
    t = re.sub(r"[^0-9a-zа-яқғўҳүұ\s-]", " ", t)
    t = t.replace("-", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


@lru_cache(maxsize=1)
def _city_index() -> tuple[list[str], dict[str, str], list[str]]:
    cities: list[str] = []
    index: dict[str, str] = {}

    for file_path in _CITY_FILES:
        try:
            raw = file_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            logger.warning("Cities file not found: %s", file_path)
            continue

        for line in raw:
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            cities.append(name)

    deduped: list[str] = []
    seen_names: set[str] = set()
    for city in cities:
        if city in seen_names:
            continue
        seen_names.add(city)
        deduped.append(city)

    for name in deduped:
        norm = _normalize(name)
        if norm and norm not in index:
            index[norm] = name

    keys = list(index.keys())
    return deduped, index, keys


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def resolve_city(raw: str) -> tuple[str | None, list[str]]:
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

    suggestions_norm = get_close_matches(norm, keys, n=5, cutoff=0.8)
    suggestions = [index[s] for s in suggestions_norm]
    return None, _dedupe(suggestions)


def city_suggest(query: str, limit: int = 8) -> list[str]:
    norm = _normalize(query)
    if not norm:
        return []

    cities, index, keys = _city_index()
    results: list[str] = []

    alias = _ALIASES.get(norm)
    if alias:
        results.append(alias)

    exact = index.get(norm)
    if exact:
        results.append(exact)

    for city in cities:
        if norm in _normalize(city):
            results.append(city)
            if len(results) >= limit * 2:
                break

    close = get_close_matches(norm, keys, n=limit, cutoff=0.78)
    for key in close:
        results.append(index[key])

    return _dedupe(results)[:limit]
