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
    "краснадар": "Краснодар",
    "городно": "Гродно",
    "барисов": "Борисов",
    "иванцаевич": "Ивацевичи",
    "ивацевичи": "Ивацевичи",
    "новогрудок": "Новогрудок",
    "сморгонь": "Сморгонь",
    "samarqand": "Самарканд",
    "samarqan": "Самарканд",
    "buxoro": "Бухара",
    "toshkent": "Ташкент",
    "andijon": "Андижан",
    "andijan": "Андижан",
    "андижон": "Андижан",
    "namangan": "Наманган",
    "fargona": "Фергана",
    "farg ona": "Фергана",
    "fergana": "Фергана",
    "фаргона": "Фергана",
    "фаргона шахар": "Фергана",
    "jizzax": "Джизак",
    "жиззах": "Джизак",
    "qoqon": "Коканд",
    "qo qon": "Коканд",
    "kokan": "Коканд",
    "kokand": "Коканд",
    "navoi": "Навои",
    "navoyi": "Навои",
    "navoiy": "Навои",
    "urgench": "Ургенч",
    "urganch": "Ургенч",
    "хорезм": "Ургенч",
    "хоразм": "Ургенч",
    "xorazm": "Ургенч",
    "бухоро": "Бухара",
    "зерафшан": "Зарафшан",
    "навоий": "Навои",
    "термезский район": "Термез",
    "термез туман": "Термез",
    "шахрисабз туман": "Шахрисабз",
    "табольск": "Тобольск",
    "газган": "Газган",
    "алашань": "Алашань",
    "алашанькоу": "Алашанькоу",
    "хоргос": "Хоргос",
}

_PREFIX_TOKENS = {
    "г",
    "го",
    "гор",
    "город",
    "city",
    "shahar",
    "shaxar",
    "шахар",
}

_PREFIX_PHRASES = (
    ("г", "о"),
    ("г", "округ"),
    ("г", "п"),
    ("городской", "округ"),
    ("городского", "округа"),
    ("городское", "поселение"),
    ("городского", "поселения"),
    ("городской", "поселок"),
    ("городской", "посёлок"),
)

_SUFFIX_TOKENS = {
    "область",
    "обл",
    "район",
    "рн",
    "р н",
    "р-н",
    "district",
    "region",
    "oblast",
    "tuman",
    "tumani",
    "viloyat",
    "viloyati",
    "shahri",
    "шахри",
    "шахар",
    "city",
    "россия",
    "рф",
    "беларусь",
    "белоруссия",
    "казахстан",
    "кыргызстан",
    "киргизия",
    "узбекистан",
    "ru",
    "by",
    "uz",
    "kz",
    "kg",
    "cn",
}


def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = t.split(",", 1)[0]
    t = re.sub(r"^г\s*\.\s*о\s*\.?\s+", "", t)
    t = re.sub(r"^г\s*\.\s*п\s*\.?\s+", "", t)
    t = re.sub(r"^город(?:ской|ского)?\s+округ(?:а)?\s+", "", t)
    t = re.sub(r"^г\.?\s+", "", t)
    t = t.replace("ё", "е")
    t = re.sub(r"[^0-9a-zа-яқғўҳүұ\s-]", " ", t)
    t = t.replace("-", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _trim_noise_tokens(norm: str) -> str:
    words = [word for word in norm.split() if word]
    if not words:
        return ""

    changed = True
    while changed and words:
        changed = False
        for phrase in _PREFIX_PHRASES:
            if len(words) >= len(phrase) and tuple(words[: len(phrase)]) == phrase:
                words = words[len(phrase) :]
                changed = True
                break

    while words and words[0] in _PREFIX_TOKENS:
        words.pop(0)
    while words and words[-1] in _SUFFIX_TOKENS:
        words.pop()

    return " ".join(words).strip()


def _resolve_direct(norm: str, index: dict[str, str]) -> str | None:
    if not norm:
        return None
    alias = _ALIASES.get(norm)
    if alias:
        return alias
    return index.get(norm)


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

    _, index, keys = _city_index()
    if not index:
        return None, []

    direct = _resolve_direct(norm, index)
    if direct:
        return direct, []

    trimmed = _trim_noise_tokens(norm)
    if trimmed and trimmed != norm:
        direct = _resolve_direct(trimmed, index)
        if direct:
            return direct, []

    words = trimmed.split() if trimmed else norm.split()
    if len(words) >= 2:
        for size in (2, 1):
            for start in range(0, len(words) - size + 1):
                candidate = " ".join(words[start : start + size])
                direct = _resolve_direct(candidate, index)
                if direct:
                    return direct, []

    suggestions_norm = get_close_matches(trimmed or norm, keys, n=5, cutoff=0.8)
    suggestions = [index[s] for s in suggestions_norm]
    return None, _dedupe(suggestions)


def city_suggest(query: str, limit: int = 8) -> list[str]:
    norm = _normalize(query)
    if not norm:
        return []
    trimmed = _trim_noise_tokens(norm)

    cities, index, keys = _city_index()
    results: list[str] = []

    alias = _ALIASES.get(norm) or _ALIASES.get(trimmed)
    if alias:
        results.append(alias)

    exact = index.get(norm) or index.get(trimmed)
    if exact:
        results.append(exact)

    for city in cities:
        city_norm = _normalize(city)
        if norm in city_norm or (trimmed and trimmed in city_norm):
            results.append(city)
            if len(results) >= limit * 2:
                break

    close = get_close_matches(trimmed or norm, keys, n=limit, cutoff=0.78)
    for key in close:
        results.append(index[key])

    return _dedupe(results)[:limit]


def city_directory(query: str | None = None, limit: int = 50) -> list[str]:
    cities, _, _ = _city_index()
    if limit <= 0:
        return []

    q = (query or "").strip()
    if not q:
        return cities[:limit]

    return city_suggest(q, limit=limit)
