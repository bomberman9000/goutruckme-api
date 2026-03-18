from __future__ import annotations

import re

from src.core.ai import CITY_ALIASES, parse_cargo_search

_TRUCK_SEARCH_HINTS = (
    "ищу машин",
    "ищу маш",
    "ищу тонник",
    "ищу грузовик",
    "нужна машин",
    "нужен транспорт",
    "ищу транспорт",
    "подбери машин",
    "подберите машин",
    "нужен камаз",
    "нужна газель",
    "нужен манипулятор",
    "нужен трал",
)
_TRUCK_OFFER_HINTS = (
    "свободен",
    "свободная",
    "свободный",
    "ищу груз",
    "без груза",
    "готов к рейсу",
    "машина свободна",
    "есть машина",
)
_TRUCK_WORDS = (
    "машин",
    "тонник",
    "тонн",
    "грузовик",
    "камаз",
    "газел",
    "манипулятор",
    "трал",
    "реф",
    "тент",
    "самосвал",
    "борт",
    "фура",
)

_TRUCK_TYPE_MAP = {
    "тент": "тент",
    "tent": "тент",
    "фура": "тент",
    "реф": "рефрижератор",
    "рефрижератор": "рефрижератор",
    "борт": "борт",
    "бортовой": "борт",
    "газель": "газель",
    "трал": "трал",
    "манипулятор": "манипулятор",
    "самосвал": "самосвал",
    "изотерм": "изотерм",
    "контейнер": "контейнер",
}

_ROUTE_STOP_TOKENS = (
    "тонн",
    "тонны",
    "тонна",
    "тн",
    "кг",
    "манипулятор",
    "трал",
    "реф",
    "рефрижератор",
    "тент",
    "газель",
    "камаз",
    "борт",
    "самосвал",
    "завтра",
    "послезавтра",
    "сегодня",
)
_EXPLICIT_ROUTE_RE = re.compile(
    r"(?:^|\b)(?:из|от)\s+([а-яa-zё\-\s]+?)\s+(?:в|до|на)\s+([а-яa-zё\-\s]+?)(?=$|[\d,]|\s+(?:"
    + "|".join(re.escape(token.strip()) for token in _ROUTE_STOP_TOKENS if token.strip())
    + r"))",
    re.I,
)


def normalize_truck_text(text: str | None) -> str:
    return (text or "").strip().lower().replace("ё", "е")


_CITY_LOOKUP = {
    **{normalize_truck_text(alias): city for alias, city in CITY_ALIASES.items()},
    **{normalize_truck_text(city): city for city in set(CITY_ALIASES.values())},
}


def looks_like_truck_search_text(text: str | None) -> bool:
    t = normalize_truck_text(text)
    if not t or t.startswith("/"):
        return False
    has_search_verb = any(token in t for token in ("ищу", "нуж", "подбер"))
    has_truck_word = any(word in t for word in _TRUCK_WORDS)
    if any(hint in t for hint in _TRUCK_SEARCH_HINTS):
        return True
    if "ищу" in t and has_truck_word:
        return True
    if ("нуж" in t or "подбер" in t) and has_truck_word:
        return True
    city_hits = _extract_city_hits(t)
    if (
        len(city_hits) >= 2
        and (parse_tonnage_hint(t) is not None or parse_truck_type(t) is not None)
        and (has_search_verb or has_truck_word)
    ):
        return True
    return False


def looks_like_truck_offer_text(text: str | None) -> bool:
    t = normalize_truck_text(text)
    if not t or t.startswith("/"):
        return False
    if looks_like_truck_search_text(t):
        return False
    if not any(hint in t for hint in _TRUCK_OFFER_HINTS):
        return False
    if any(word in t for word in _TRUCK_WORDS):
        return True
    has_weight = parse_tonnage_hint(t) is not None
    has_city = len(_extract_city_hits(t)) >= 1
    return has_weight and has_city


def parse_truck_type(text: str | None) -> str | None:
    t = normalize_truck_text(text)
    if not t:
        return None
    if t in _TRUCK_TYPE_MAP:
        return _TRUCK_TYPE_MAP[t]
    for key, value in _TRUCK_TYPE_MAP.items():
        if key in t:
            return value
    return None


def _infer_truck_type_from_weight(weight: float | None) -> str | None:
    if weight is None or weight <= 0:
        return None
    if weight <= 2.5:
        return "газель"
    if weight <= 7:
        return "борт"
    return "тент"


def parse_tonnage_hint(text: str | None) -> float | None:
    normalized = normalize_truck_text(text).replace(",", ".")
    ton_match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:т\b|тн\b|тонн(?:ик)?|тонны|тонна|ти\s+тонник|ти\b)",
        normalized,
    )
    if ton_match:
        try:
            return float(ton_match.group(1))
        except ValueError:
            return None
    kg_match = re.search(r"(\d+(?:\.\d+)?)\s*кг\b", normalized)
    if kg_match:
        try:
            return round(float(kg_match.group(1)) / 1000, 3)
        except ValueError:
            return None
    return None


def _extract_city_hits(text: str | None) -> list[tuple[int, str]]:
    normalized = normalize_truck_text(text)
    if not normalized:
        return []
    hits: list[tuple[int, str, int]] = []
    for alias, city in _CITY_LOOKUP.items():
        if len(alias) < 3:
            continue
        for match in re.finditer(rf"(?<![а-яa-z]){re.escape(alias)}(?![а-яa-z])", normalized):
            hits.append((match.start(), city, len(alias)))
    hits.sort(key=lambda item: (item[0], -item[2]))
    ordered: list[tuple[int, str]] = []
    seen: set[str] = set()
    for idx, city, _size in hits:
        if city in seen:
            continue
        seen.add(city)
        ordered.append((idx, city))
    return ordered


def _normalize_city_fragment(fragment: str | None) -> str | None:
    text = normalize_truck_text(fragment)
    if not text:
        return None
    cleaned = re.sub(r"[^a-zа-я\\-\\s]", " ", text)
    cleaned = re.sub(r"\\s+", " ", cleaned).strip(" -")
    if not cleaned:
        return None

    words = cleaned.split()
    variants = [cleaned]
    if words:
        variants.append(words[0])
    if len(words) >= 2:
        variants.append(" ".join(words[:2]))

    for variant in variants:
        if variant in _CITY_LOOKUP:
            return _CITY_LOOKUP[variant]

    fuzzy_variants = set(variants)
    for variant in list(variants):
        if len(variant) > 4 and variant.endswith("и"):
            fuzzy_variants.add(variant[:-1] + "ь")
            fuzzy_variants.add(variant[:-1] + "а")
        if len(variant) > 4 and variant.endswith("у"):
            fuzzy_variants.add(variant[:-1] + "а")
        if len(variant) > 4 and variant.endswith("ю"):
            fuzzy_variants.add(variant[:-1] + "я")
        if len(variant) > 4 and variant.endswith("е"):
            fuzzy_variants.add(variant[:-1] + "а")

    for variant in fuzzy_variants:
        if variant in _CITY_LOOKUP:
            return _CITY_LOOKUP[variant]

    matches: list[tuple[int, int, str]] = []
    for alias, city in _CITY_LOOKUP.items():
        if len(alias) < 3:
            continue
        if re.search(rf"(?<![а-яa-z]){re.escape(alias)}(?![а-яa-z])", cleaned):
            matches.append((3, len(alias), city))
            continue
        prefix_len = 0
        for left, right in zip(alias, cleaned):
            if left != right:
                break
            prefix_len += 1
        if prefix_len >= 4:
            matches.append((2, prefix_len, city))
            continue
        if alias.startswith(cleaned[:4]) or cleaned.startswith(alias[:4]):
            matches.append((1, min(len(alias), len(cleaned)), city))
    if matches:
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return matches[0][2]
    return None


def _extract_explicit_route(text: str | None) -> tuple[str | None, str | None]:
    normalized = normalize_truck_text(text)
    if not normalized:
        return None, None
    match = _EXPLICIT_ROUTE_RE.search(normalized)
    if not match:
        return None, None
    from_city = _normalize_city_fragment(match.group(1))
    to_city = _normalize_city_fragment(match.group(2))
    return from_city, to_city


async def extract_truck_search_params(text: str) -> dict | None:
    parsed = await parse_cargo_search(text)
    explicit_from, explicit_to = _extract_explicit_route(text)
    ordered_hits = _extract_city_hits(text)
    fallback_from = ordered_hits[0][1] if len(ordered_hits) >= 1 else None
    fallback_to = ordered_hits[1][1] if len(ordered_hits) >= 2 else None
    if not parsed and not any([explicit_from, explicit_to]):
        if not any([fallback_from, fallback_to]):
            return None
    truck_type = parse_truck_type(text)
    weight = parse_tonnage_hint(text)
    if weight is None:
        weight = (parsed or {}).get("max_weight") or (parsed or {}).get("min_weight")
        if isinstance(weight, (int, float)) and weight > 100:
            weight = round(float(weight) / 1000, 3)
    if truck_type is None:
        truck_type = _infer_truck_type_from_weight(weight)
    from_city = explicit_from or (parsed or {}).get("from_city") or fallback_from
    to_city = explicit_to or (parsed or {}).get("to_city")
    if fallback_to and fallback_to != from_city:
        if not to_city or (to_city == from_city and fallback_to != from_city):
            to_city = fallback_to
    return {
        "from_city": from_city,
        "to_city": to_city,
        "weight": weight,
        "truck_type": truck_type,
    }
