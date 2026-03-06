from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote

from src.core.cities import city_directory, resolve_city

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedTruck:
    truck_type: str | None
    capacity_tons: float | None
    volume_m3: float | None
    base_city: str | None
    base_region: str | None
    routes: str | None
    phone: str | None
    contact_name: str | None
    price_rub: int | None
    raw_text: str


PHONE_RE = re.compile(r"(?:\+7|7|8)\D{0,3}\d{3}\D{0,3}\d{3}\D{0,3}\d{2}\D{0,3}\d{2}")
CAPACITY_RE = re.compile(r"(?P<cap>\d{1,2}(?:[.,]\d+)?)\s*(?:т(?:онн|онны|онна)?|тн|tn|ton)\b", re.IGNORECASE)
VOLUME_RE = re.compile(r"(?P<vol>\d{1,3}(?:[.,]\d+)?)\s*(?:м3|м\^3|куб(?:ов|а|ик)?|m3)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:^|\n)\s*цена:\s*(?P<price>\d[\d\s]{0,10})\s*руб", re.IGNORECASE)
AVITO_URL_RE = re.compile(r"https?://(?:www\.)?avito\.ru/(?P<location>[^/?#]+)/", re.IGNORECASE)

TRUCK_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("кран борт", "манипулятор"),
    ("кран-борт", "манипулятор"),
    ("кран манипулятор", "манипулятор"),
    ("манипулятор", "манипулятор"),
    ("самогруз", "манипулятор"),
    ("воровайк", "манипулятор"),
    ("перевозка спецтехники", "трал"),
    ("доставка спецтехники", "трал"),
    ("транспортировка спецтехники", "трал"),
    ("низкорамник", "трал"),
    ("площадка", "трал"),
    ("трал", "трал"),
    ("рефрижератор", "рефрижератор"),
    ("реф", "рефрижератор"),
    ("изотерм", "изотерм"),
    ("бортовой", "борт"),
    ("борт", "борт"),
    ("контейнер", "контейнер"),
    ("самосвал", "самосвал"),
    ("цистерн", "цистерна"),
    ("зерновоз", "зерновоз"),
    ("автовоз", "автовоз"),
    ("фура", "тент"),
    ("тент", "тент"),
    ("gazelle", "газель"),
    ("газел", "газель"),
]

CAPACITY_WORD_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\bполуторк[аиу]\b", re.IGNORECASE), 1.5),
    (re.compile(r"\bтрехтон(?:ник|ка)?\b", re.IGNORECASE), 3.0),
    (re.compile(r"\bпятитон(?:ник|ка)?\b", re.IGNORECASE), 5.0),
    (re.compile(r"\bдесятитон(?:ник|ка)?\b", re.IGNORECASE), 10.0),
    (re.compile(r"\bдесятка\b", re.IGNORECASE), 10.0),
    (re.compile(r"\bдвадцатитон(?:ник|ка)?\b", re.IGNORECASE), 20.0),
]

ROUTE_HINT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bмеж(?:ду)?город\b", re.IGNORECASE), "межгород"),
    (re.compile(r"\b(?:рф|по россии|россия)\b", re.IGNORECASE), "РФ"),
    (re.compile(r"\b(?:рб|беларусь)\b", re.IGNORECASE), "РБ"),
    (re.compile(r"\b(?:снг|по снг)\b", re.IGNORECASE), "СНГ"),
    (re.compile(r"\bиз китая\b", re.IGNORECASE), "из Китая"),
    (re.compile(r"\bкитай\b", re.IGNORECASE), "Китай"),
]

_INVALID_CITY_VALUES = {"", "китай", "китая", "рф", "рб", "межгород", "междугород", "снг"}
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ь": "", "ъ": "",
}

TRUCK_OFFER_RE = re.compile(
    r"(?:свободн|готов\s+к\s+рейс|ищу\s+груз|без\s+груз|порожн|перевез[уё]|доставл[юя]"
    r"|выполн[яю]\s+(?:рейс|перевозк)|своя\s+машин|наша\s+машин|собственн\s+(?:авто|транспорт|машин)"
    r"|услуги\s+(?:газел|перевоз|транспорт|манипул|трал)|аренда\s+(?:газел|авто|манипул|трал)"
    r"|предлага[юе]м?\s+(?:перевоз|транспорт|услуг)|работа[юе]м?\s+по|открыт\s+к\s+сотрудн)",
    re.IGNORECASE,
)
CARGO_REQUEST_RE = re.compile(
    r"(?:нужн[аяо]\s+машин|нужн[аяо]\s+(?:газел|фур|авто)|ищу\s+(?:машин|перевозч|транспорт|газел)"
    r"|есть\s+груз|груз\s+готов|отправляем\s+груз|требует[ся]\s+(?:машин|перевозч))",
    re.IGNORECASE,
)


def is_truck_offer(text: str) -> bool:
    has_truck_signal = bool(TRUCK_OFFER_RE.search(text))
    has_cargo_signal = bool(CARGO_REQUEST_RE.search(text))
    if has_cargo_signal and not has_truck_signal:
        return False
    return True


def _parse_truck_type(text_lc: str) -> str | None:
    for keyword, normalized in TRUCK_TYPE_PATTERNS:
        if keyword in text_lc:
            return normalized
    return None


def _parse_capacity(text: str) -> float | None:
    match = CAPACITY_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group("cap").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_capacity_words(text: str) -> float | None:
    for pattern, value in CAPACITY_WORD_PATTERNS:
        if pattern.search(text):
            return value
    return None


def _parse_volume(text: str) -> float | None:
    match = VOLUME_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group("vol").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_price(text: str) -> int | None:
    match = PRICE_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group("price").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _parse_phone(text: str) -> str | None:
    match = PHONE_RE.search(text)
    if not match:
        return None
    digits = "".join(ch for ch in match.group(0) if ch.isdigit())
    if len(digits) == 11:
        return f"+7{digits[1:]}"
    if len(digits) == 10:
        return f"+7{digits}"
    return None


def _normalize_slug_text(value: str) -> str:
    text = unquote((value or "").strip().lower())
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"[^0-9a-zа-я\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _transliterate_city(name: str) -> str:
    out: list[str] = []
    for ch in (name or "").strip().lower():
        if ch in _CYR_TO_LAT:
            out.append(_CYR_TO_LAT[ch])
        elif ch.isascii() and (ch.isalnum() or ch == " "):
            out.append(ch)
        elif ch in {"-", "_"}:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


@lru_cache(maxsize=1)
def _latin_city_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for city in city_directory(limit=5000):
        key = _normalize_slug_text(_transliterate_city(city))
        if key:
            index.setdefault(key, city)
    return index


def _resolve_city_name(candidate: str) -> str | None:
    raw = (candidate or "").strip().strip(",.")
    if not raw:
        return None
    if raw.lower().replace("ё", "е") in _INVALID_CITY_VALUES:
        return None
    exact, _ = resolve_city(raw)
    if exact:
        return exact
    latin_key = _normalize_slug_text(raw)
    if not latin_key:
        return None
    return _latin_city_index().get(latin_key)


def _parse_base_city_from_url(text: str) -> str | None:
    match = AVITO_URL_RE.search(text)
    if not match:
        return None
    slug = _normalize_slug_text(match.group("location"))
    if not slug:
        return None
    words = slug.split()
    for start in range(len(words)):
        city = _resolve_city_name(" ".join(words[start:]))
        if city:
            return city
    for end in range(len(words), 0, -1):
        city = _resolve_city_name(" ".join(words[:end]))
        if city:
            return city
    return None


def _parse_base_city_from_text(text: str) -> str | None:
    header = re.split(r"\n|Ссылка:|Цена:", text, maxsplit=1)[0]
    patterns = [
        re.compile(r"[.:]\s*([A-Za-zА-Яа-яё-]{3,}(?:\s+[A-Za-zА-Яа-яё-]{3,})?)\s*,"),
        re.compile(r"^([A-Za-zА-Яа-яё-]{3,}(?:\s+[A-Za-zА-Яа-яё-]{3,})?)\s*,"),
    ]
    for pattern in patterns:
        match = pattern.search(header)
        if not match:
            continue
        city = _resolve_city_name(match.group(1))
        if city:
            return city
    return None


def _parse_routes(text: str) -> str | None:
    hints: list[str] = []
    for pattern, label in ROUTE_HINT_PATTERNS:
        if pattern.search(text):
            hints.append(label)
    if not hints:
        return None
    return ", ".join(dict.fromkeys(hints))


def parse_truck_regex(text: str) -> ParsedTruck:
    text_lc = text.lower().replace("ё", "е")
    capacity = _parse_capacity(text)
    if capacity is None:
        capacity = _parse_capacity_words(text_lc)
    return ParsedTruck(
        truck_type=_parse_truck_type(text_lc),
        capacity_tons=capacity,
        volume_m3=_parse_volume(text),
        base_city=_parse_base_city_from_url(text) or _parse_base_city_from_text(text),
        base_region=None,
        routes=_parse_routes(text_lc),
        phone=_parse_phone(text),
        contact_name=None,
        price_rub=_parse_price(text),
        raw_text=text,
    )


_TRUCK_SYSTEM_PROMPT = """\
Ты — парсер объявлений перевозчиков. Извлекай данные из текста объявления и возвращай ТОЛЬКО JSON.

Поля:
- truck_type: тип кузова (газель|тент|рефрижератор|изотерм|борт|трал|манипулятор|самосвал|контейнер|цистерна|зерновоз|автовоз) или null
- capacity_tons: грузоподъёмность числом (например 1.5, 5, 20) или null
- volume_m3: объём кузова м³ числом или null
- base_city: город базирования (откуда работает) или null
- base_region: регион (область, республика) или null
- routes: куда готов ехать, краткий текст или null
- phone: телефон в формате +7XXXXXXXXXX или null
- contact_name: имя или название компании или null
- price_rub: ставка/цена числом рублей или null

Правила:
- Если тип не ясен, но упомянута «газель» — truck_type = "газель"
- «Пятитонник» → capacity_tons = 5.0, «десятка» → 10.0
- Возвращай только JSON, без пояснений
"""


def _extract_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _dict_to_parsed(data: dict, raw_text: str) -> ParsedTruck:
    fallback = parse_truck_regex(raw_text)

    capacity = None
    raw_cap = data.get("capacity_tons")
    if raw_cap is not None:
        try:
            capacity = float(str(raw_cap).replace(",", "."))
        except (TypeError, ValueError):
            pass

    volume = None
    raw_vol = data.get("volume_m3")
    if raw_vol is not None:
        try:
            volume = float(str(raw_vol).replace(",", "."))
        except (TypeError, ValueError):
            pass

    price = None
    raw_price = data.get("price_rub")
    if raw_price is not None:
        try:
            price = int(float(str(raw_price).replace(" ", "").replace(",", ".")))
        except (TypeError, ValueError):
            pass

    phone = (data.get("phone") or "").strip() or None
    if not phone:
        phone = fallback.phone

    return ParsedTruck(
        truck_type=(data.get("truck_type") or "").strip().lower() or fallback.truck_type,
        capacity_tons=capacity if capacity is not None else fallback.capacity_tons,
        volume_m3=volume if volume is not None else fallback.volume_m3,
        base_city=(data.get("base_city") or "").strip() or fallback.base_city,
        base_region=(data.get("base_region") or "").strip() or fallback.base_region,
        routes=(data.get("routes") or "").strip() or fallback.routes,
        phone=phone,
        contact_name=(data.get("contact_name") or "").strip() or None,
        price_rub=price if price is not None else fallback.price_rub,
        raw_text=raw_text,
    )


async def _call_groq(system: str, user: str, api_key: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=300,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


async def _call_openai(system: str, user: str, api_key: str, model: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=20) as http:
        response = await http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": 300,
                "temperature": 0,
            },
        )
        response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


async def parse_truck_llm(text: str) -> ParsedTruck | None:
    from src.core.config import settings

    try:
        if settings.groq_api_key:
            raw = await _call_groq(_TRUCK_SYSTEM_PROMPT, text[:2000], settings.groq_api_key)
        elif settings.openai_api_key:
            raw = await _call_openai(_TRUCK_SYSTEM_PROMPT, text[:2000], settings.openai_api_key, settings.openai_model)
        else:
            return None

        data = _extract_json(raw)
        if not data:
            logger.debug("truck_extractor: LLM returned non-JSON: %.100s", raw)
            return None
        return _dict_to_parsed(data, text)
    except Exception as exc:
        logger.warning("truck_extractor LLM error: %s", str(exc)[:200])
        return None
