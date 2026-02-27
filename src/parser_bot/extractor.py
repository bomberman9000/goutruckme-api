from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?:\+7|7|8)\D{0,3}\d{3}\D{0,3}\d{3}\D{0,3}\d{2}\D{0,3}\d{2}")
INN_RE = re.compile(r"\b\d{10}(?:\d{2})?\b")
ROUTE_RE = re.compile(
    r"(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ.\- '’ʻ`]{2,40}?)\s*(?:->|→|—|–|\s-\s)\s*(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ.\- '’ʻ`]{2,40})",
    re.IGNORECASE,
)
ROUTE_COMPACT_RE = re.compile(
    r"\b(?P<from>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,20})-(?P<to>[A-Za-zА-Яа-яЁёҚқҒғЎўҲҳҮүҰұ'’ʻ`]{3,20})\b",
    re.IGNORECASE,
)
WEIGHT_RE = re.compile(r"(?P<weight>\d{1,2}(?:[.,]\d+)?)\s*т(?:онн|онны|онна)?\b", re.IGNORECASE)
PRICE_RE = re.compile(
    r"(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{2,8}(?:[.,]\d+)?)\s*(?P<suffix>к|k|тыс|тыс\.|млн|мил|₽|р|руб(?:лей)?|\$|usd|дол(?:лар(?:ов|а)?)?)",
    re.IGNORECASE,
)
PRICE_BY_KEYWORD_RE = re.compile(
    r"(?:фрахт|ставка|цена|оплата)\s*[:=]?\s*(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{5,9}(?:[.,]\d+)?)",
    re.IGNORECASE,
)
PRICE_BY_NDS_RE = re.compile(
    r"(?P<price>\d{1,3}(?:[\s.,]\d{3})+|\d{5,9}(?:[.,]\d+)?)\s*(?:с|без)\s*ндс",
    re.IGNORECASE,
)

BODY_TYPES = {
    "тент": "тент",
    "реф": "рефрижератор",
    "рефрижератор": "рефрижератор",
    "изотерм": "изотерм",
    "борт": "борт",
    "контейнер": "контейнер",
    "шаланда": "шаланда",
    "трал": "трал",
    "площадка": "трал",
    "телега": "трал",
    "низкорамник": "трал",
    "фура": "тент",
    "газель": "борт",
}

CITY_STOP_WORDS = {
    "нужен",
    "нужна",
    "нужно",
    "груз",
    "машина",
    "срочно",
    "на",
    "в",
    "тент",
    "реф",
    "рефрижератор",
    "изотерм",
    "контейнер",
    "борт",
    "трал",
    "догруз",
    "растаможка",
    "растоможка",
    "таможня",
    "перецепка",
    "перегруз",
}

CITY_INVALID_EXACT = {
    "оплата",
    "нал",
    "растаможка",
    "растоможка",
    "верхняя",
    "боковая",
}

CITY_ALIASES = {
    "мск": "Москва",
    "москва": "Москва",
    "спб": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "санкт-петербург": "Санкт-Петербург",
    "питер": "Санкт-Петербург",
    "екб": "Екатеринбург",
    "нн": "Нижний Новгород",
    "нижний новогород": "Нижний Новгород",
    "йошкар ола": "Йошкар-Ола",
    "йошкар-ола": "Йошкар-Ола",
    "ростов на дону": "Ростов-на-Дону",
    "ростов-на-дону": "Ростов-на-Дону",
    "ташкен": "Ташкент",
    "ташкенд": "Ташкент",
    "сырдаря": "Сырдарья",
    "бухоро": "Бухара",
    "самарқанд": "Самарканд",
    "самарканд": "Самарканд",
    "фаргона": "Фергана",
    "фарғона": "Фергана",
    "фергана": "Фергана",
    "fargona": "Farg'ona",
    "farg'ona": "Farg'ona",
    "fargʻona": "Farg'ona",
    "farg’ona": "Farg'ona",
}

CITY_ALIAS_INDEX: dict[str, set[str]] = {}
for _alias, _city in CITY_ALIASES.items():
    _city_key = _city.lower()
    CITY_ALIAS_INDEX.setdefault(_city_key, set()).add(_alias.lower())
    CITY_ALIAS_INDEX[_city_key].add(_city_key)

_LLM_SYSTEM_PROMPT = """\
Ты — парсер сообщений о грузоперевозках. Работаешь как опытный диспетчер: \
понимаешь сленг, исправляешь опечатки, вычисляешь даты.

Из текста извлеки ВСЕ возможные данные и верни СТРОГО JSON (без пояснений, без markdown):
{{
  "from_city": "Город отправления (полное название с большой буквы)",
  "to_city": "Город назначения (полное название с большой буквы)",
  "body_type": "Тип кузова/транспорта",
  "weight": число в тоннах (float),
  "rate": число в рублях (int),
  "load_date": "YYYY-MM-DD",
  "load_time": "HH:MM",
  "phone": "телефон в формате +7XXXXXXXXXX",
  "cargo_description": "краткое описание груза, если указано",
  "payment_terms": "условия оплаты",
  "is_direct_customer": true/false,
  "dimensions": "ДxШxВ в метрах, если указаны"
}}

Правила парсинга:

ГОРОДА:
- Исправляй опечатки: «самар» → Самара, «масква» → Москва.
- Сокращения: мск=Москва, спб/питер=Санкт-Петербург, екб=Екатеринбург, \
нск=Новосибирск, рнд=Ростов-на-Дону, нн=Нижний Новгород, крд=Краснодар, \
крск=Красноярск, кзн=Казань, чел/челяба=Челябинск, врн=Воронеж, \
тмн=Тюмень, влг=Волгоград.
- Никогда не считай городами слова: «Оплата», «Нал», «Растаможка», \
«Растоможка», «Верхняя», «Боковая».
- Если указан регион вместо города (Сибирь, Урал, Поволжье, Юг, Центр, \
Северо-Запад, Дальний Восток, Кубань, Кавказ), верни крупнейший город \
региона: Сибирь→Новосибирск, Урал→Екатеринбург, Поволжье→Самара, \
Юг/Кубань/Кавказ→Краснодар, Центр→Москва, Северо-Запад→Санкт-Петербург, \
Дальний Восток→Хабаровск.

ТИП КУЗОВА:
- площадка / телега / низкорамник / негабарит → трал
- фура / еврофура / полуприцеп → тент
- газель / газелька / малотоннажник → борт
- термос → изотерм
- холодильник → рефрижератор
- Стандартные: тент, рефрижератор, изотерм, борт, контейнер, шаланда, трал.

ДАТЫ:
- Сегодня: {today}, день недели: {weekday}.
- «сегодня» = {today}, «завтра» = {tomorrow}, «послезавтра» = {day_after}.
- «в понедельник» / «в пн» / «в субботу» — вычисли ближайшую будущую дату.
- «20 числа» / «20-го» — текущий или следующий месяц (если 20-е уже прошло).
- «через 3 дня» — прибавь к {today}.

ЦЕНА:
- 120к / 120K = 120000; 50 тыс = 50000; 80к с ндс → rate=80000.
- Если указана ставка за км — умножь примерно: «35 руб/км» при ~1000 км → 35000.

ТЕЛЕФОН:
- Извлеки номер, даже если написан словами: «восемь девятьсот…» → +79...
- Форматы: +7(...), 8-..., 8 (...), без пробелов — приведи к +7XXXXXXXXXX.

ОПИСАНИЕ ГРУЗА:
- Если указан тип/характер груза (напр. «трубы», «стройматериалы», \
«продукты», «оборудование»), верни в cargo_description.

УСЛОВИЯ ОПЛАТЫ (payment_terms):
- «с НДС» / «без НДС» / «нал» / «безнал» / «предоплата» / «по факту» / \
«на карту» / «карта» — извлеки как строку, напр. "без НДС, нал".

ПРЯМОЙ ЗАКАЗЧИК (is_direct_customer):
- true если текст указывает на прямого заказчика/завод/производителя \
(«от завода», «напрямую», «без посредников», «собственный груз»).
- false если посредник/диспетчер/экспедитор («диспетчер», «экспедитор», \
«ищем машину для клиента»).
- Не включай поле, если невозможно определить.

ГАБАРИТЫ (dimensions):
- Если указаны размеры груза (длина, ширина, высота), верни строкой: \
«6x2.4x2.5» (м). Примеры: «длина 12м» → «12», «6*2.4*2.5» → «6x2.4x2.5».

ВАЖНО:
- Если поле не удалось извлечь — НЕ включай его в JSON.
- Верни ТОЛЬКО JSON-объект, ничего больше.\
"""

_WEEKDAY_NAMES_RU = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


@dataclass(slots=True)
class ParsedCargo:
    from_city: str
    to_city: str
    body_type: str | None
    rate_rub: int | None
    weight_t: float | None
    phone: str | None
    inn: str | None
    matched_keywords: list[str]
    raw_text: str
    load_date: str | None = None
    load_time: str | None = None
    cargo_description: str | None = None
    payment_terms: str | None = None
    is_direct_customer: bool | None = None
    dimensions: str | None = None
    is_hot_deal: bool = False
    suggested_response: str | None = None
    phone_blacklisted: bool = False


def _normalize_city(value: str) -> str:
    words = [w.strip(".,:;()[]{}") for w in value.replace("ё", "е").replace("Ё", "Е").split()]
    words = [w for w in words if w]
    while words and words[0].lower() in CITY_STOP_WORDS:
        words.pop(0)
    if len(words) > 2:
        words = words[-2:]
    raw = " ".join(words).strip()
    if not raw:
        return ""
    lookup = raw.lower().strip(".")
    lookup = lookup.replace("ʻ", "'").replace("’", "'").replace("`", "'")
    lookup_variants = {
        lookup,
        lookup.replace("-", " "),
        lookup.replace(" ", "-"),
    }
    for variant in lookup_variants:
        if variant in CITY_ALIASES:
            return CITY_ALIASES[variant]
    return raw.title()


def _city_key(value: str) -> str:
    key = (value or "").strip().lower()
    key = key.replace("ё", "е").replace("-", " ")
    key = re.sub(r"[^0-9a-zа-яқғўҳүұ\\s'’ʻ`]", " ", key)
    key = key.replace("ʻ", "'").replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", key).strip()


def _is_invalid_city_name(value: str) -> bool:
    return _city_key(value) in CITY_INVALID_EXACT


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 10:
        digits = f"7{digits}"
    elif len(digits) == 11 and digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if len(digits) != 11:
        return digits
    return f"+{digits}"


def _normalize_inn(value: str) -> str | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) not in (10, 12):
        return None
    return digits


def _parse_price(text: str) -> int | None:
    usd_to_rub = 100

    def _parse_amount(raw: str, *, suffix: str = "") -> int | None:
        token = (raw or "").strip().replace("\xa0", " ")
        if not token:
            return None

        multiplier = 1
        suffix_norm = (suffix or "").strip().lower()
        if suffix_norm in {"к", "k", "тыс", "тыс."}:
            multiplier = 1000
        elif suffix_norm in {"млн", "мил"}:
            multiplier = 1_000_000
        elif suffix_norm in {"$", "usd", "дол", "доллар", "доллара", "долларов"}:
            multiplier = usd_to_rub

        compact = token.replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", compact):
            digits = re.sub(r"\D", "", compact)
            return int(digits) * multiplier if digits else None

        numeric = re.sub(r"[^0-9.,]", "", compact)
        if not numeric:
            return None

        if numeric.count(".") + numeric.count(",") > 1:
            digits = re.sub(r"\D", "", numeric)
            return int(digits) * multiplier if digits else None

        value = float(numeric.replace(",", "."))
        return int(round(value * multiplier))

    match = PRICE_RE.search(text)
    if match:
        parsed = _parse_amount(match.group("price"), suffix=match.group("suffix") or "")
        if parsed:
            return parsed

    keyword_match = PRICE_BY_KEYWORD_RE.search(text)
    if keyword_match:
        parsed = _parse_amount(keyword_match.group("price"))
        if parsed:
            return parsed

    nds_match = PRICE_BY_NDS_RE.search(text)
    if nds_match:
        parsed = _parse_amount(nds_match.group("price"))
        if parsed:
            return parsed

    return None


def _parse_weight(text: str) -> float | None:
    match = WEIGHT_RE.search(text)
    if not match:
        return None
    return float(match.group("weight").replace(",", "."))


def _parse_body_type(text_lc: str) -> str | None:
    for token, norm in BODY_TYPES.items():
        if token in text_lc:
            return norm
    return None


def _parse_route(text: str) -> tuple[str, str] | tuple[None, None]:
    route = ROUTE_RE.search(text)
    if route:
        from_city = _normalize_city(route.group("from"))
        to_city = _normalize_city(route.group("to"))
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            return from_city, to_city

    compact = ROUTE_COMPACT_RE.search(text)
    if compact:
        compact_token = f"{compact.group('from')}-{compact.group('to')}"
        compact_lookup = compact_token.lower().replace("ʻ", "'").replace("’", "'").replace("`", "'")
        if compact_lookup in CITY_ALIASES or compact_lookup.replace("-", " ") in CITY_ALIASES:
            return None, None
        from_city = _normalize_city(compact.group("from"))
        to_city = _normalize_city(compact.group("to"))
        if from_city and to_city and not _is_invalid_city_name(from_city) and not _is_invalid_city_name(to_city):
            return from_city, to_city

    return None, None


def _extract_matched_keywords(text_lc: str, keywords: Iterable[str]) -> list[str]:
    found: list[str] = []
    for word in keywords:
        marker = (word or "").strip().lower()
        if marker and marker in text_lc:
            found.append(marker)
    return found


def parse_cargo_message(text: str, *, keywords: Iterable[str]) -> ParsedCargo | None:
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    from_city, to_city = _parse_route(clean_text)
    if not from_city or not to_city:
        return None

    text_lc = clean_text.lower()
    matched_keywords = _extract_matched_keywords(text_lc, keywords)
    if not matched_keywords:
        if not looks_like_cargo(clean_text):
            return None
        matched_keywords = ["auto"]

    phone_match = PHONE_RE.search(clean_text)
    phone = _normalize_phone(phone_match.group(0)) if phone_match else None
    inn_match = INN_RE.search(clean_text)
    inn = _normalize_inn(inn_match.group(0)) if inn_match else None

    return ParsedCargo(
        from_city=from_city,
        to_city=to_city,
        body_type=_parse_body_type(text_lc),
        rate_rub=_parse_price(clean_text),
        weight_t=_parse_weight(clean_text),
        phone=phone,
        inn=inn,
        matched_keywords=matched_keywords,
        raw_text=clean_text,
    )


def _build_llm_system_prompt() -> str:
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    day_after = today + timedelta(days=2)
    weekday = _WEEKDAY_NAMES_RU[today.weekday()]
    return _LLM_SYSTEM_PROMPT.format(
        today=today.strftime("%Y-%m-%d"),
        tomorrow=tomorrow.strftime("%Y-%m-%d"),
        day_after=day_after.strftime("%Y-%m-%d"),
        weekday=weekday,
    )


def _extract_json(text: str) -> dict | None:
    """Safely extract a JSON object from LLM output that may contain markdown."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _llm_result_to_parsed(
    data: dict, raw_text: str, *, keywords: Iterable[str]
) -> ParsedCargo | None:
    from_city = _normalize_city(str(data.get("from_city") or "").strip())
    to_city = _normalize_city(str(data.get("to_city") or "").strip())
    invalid_city_markers = {
        _city_key("нет данных"),
        _city_key("не указано"),
        _city_key("неизвестно"),
        _city_key("unknown"),
        _city_key("n/a"),
        _city_key("none"),
        _city_key("умная логистика"),
        _city_key("этрн"),
    }
    from_city_key = _city_key(from_city)
    to_city_key = _city_key(to_city)
    if (
        from_city_key in invalid_city_markers
        or to_city_key in invalid_city_markers
        or _is_invalid_city_name(from_city)
        or _is_invalid_city_name(to_city)
    ):
        return None
    if not from_city or not to_city:
        return None

    # Guardrail: if the message has no explicit route separator, verify both
    # cities are still present in the raw text (full name or known alias).
    if ROUTE_RE.search(raw_text) is None:
        text_lc = raw_text.lower().replace("ё", "е")
        from_markers = CITY_ALIAS_INDEX.get(from_city.lower(), {from_city.lower()})
        to_markers = CITY_ALIAS_INDEX.get(to_city.lower(), {to_city.lower()})
        if not any(marker in text_lc for marker in from_markers):
            return None
        if not any(marker in text_lc for marker in to_markers):
            return None

    body_type = (data.get("body_type") or "").strip().lower() or None
    if body_type:
        body_type = BODY_TYPES.get(body_type, body_type)

    weight: float | None = None
    if data.get("weight") is not None:
        try:
            weight = float(str(data["weight"]).replace(",", "."))
        except (ValueError, TypeError):
            pass

    rate: int | None = None
    if data.get("rate") is not None:
        try:
            raw_rate = str(data["rate"]).replace(" ", "").replace(",", ".")
            rate = int(float(raw_rate))
        except (ValueError, TypeError):
            pass

    load_date = (data.get("load_date") or "").strip() or None
    load_time = (data.get("load_time") or "").strip() or None

    cargo_description = (data.get("cargo_description") or "").strip() or None
    payment_terms = (data.get("payment_terms") or "").strip() or None
    dimensions = (data.get("dimensions") or "").strip() or None

    is_direct_customer: bool | None = None
    raw_direct = data.get("is_direct_customer")
    if isinstance(raw_direct, bool):
        is_direct_customer = raw_direct
    elif isinstance(raw_direct, str):
        is_direct_customer = raw_direct.strip().lower() in ("true", "1", "да")

    phone: str | None = None
    llm_phone = (data.get("phone") or "").strip()
    if llm_phone:
        digits = "".join(ch for ch in llm_phone if ch.isdigit())
        if len(digits) >= 10:
            phone = _normalize_phone(digits)
    if not phone:
        phone_match = PHONE_RE.search(raw_text)
        phone = _normalize_phone(phone_match.group(0)) if phone_match else None

    inn_match = INN_RE.search(raw_text)
    inn = _normalize_inn(inn_match.group(0)) if inn_match else None

    text_lc = raw_text.lower()
    matched_keywords = _extract_matched_keywords(text_lc, keywords) or ["auto"]

    if from_city == to_city and rate is None and weight is None:
        return None

    return ParsedCargo(
        from_city=from_city,
        to_city=to_city,
        body_type=body_type,
        rate_rub=rate,
        weight_t=weight,
        phone=phone,
        inn=inn,
        matched_keywords=matched_keywords,
        raw_text=raw_text,
        load_date=load_date,
        load_time=load_time,
        cargo_description=cargo_description,
        payment_terms=payment_terms,
        is_direct_customer=is_direct_customer,
        dimensions=dimensions,
    )


def evaluate_hot_deal(parsed: ParsedCargo) -> bool:
    """Check if the rate is above market average for the route.

    Uses the local distance-based price estimator from ``src.core.geo``
    to compare.  A deal is "hot" when the offered rate is >=15 % above
    the average calculated price.
    """
    if not parsed.rate_rub or not parsed.weight_t:
        return False
    try:
        from src.core.geo import city_coords, haversine_km

        fc = city_coords(parsed.from_city)
        tc = city_coords(parsed.to_city)
        if not fc or not tc:
            return False
        distance = haversine_km(fc[0], fc[1], tc[0], tc[1])
        if distance < 10:
            return False
        avg_rate_per_km = 35 + min(parsed.weight_t, 20) * 0.5
        avg_price = int(distance * avg_rate_per_km)
        return parsed.rate_rub >= avg_price * 1.15
    except Exception:
        return False


async def _call_groq(system_prompt: str, user_text: str, api_key: str) -> str:
    from groq import AsyncGroq

    client = AsyncGroq(api_key=api_key)
    response = await client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        max_tokens=400,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


async def _call_openai(
    system_prompt: str,
    user_text: str,
    api_key: str,
    model: str,
) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "max_tokens": 400,
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


_CARGO_SIGNAL_RE = re.compile(
    r"(?:"
    r"\d+\s*т(?:онн)?"                    # weight: 20т, 20 тонн
    r"|руб|₽|\d+\s*к\b|\d+\s*тыс"        # price: 120к, 50 тыс, руб
    r"|(?:->|→|—|–)\s*[А-Яа-я]"          # route arrow: -> Москва
    r"|тент|реф|трал|борт|фура|контейнер" # vehicle types
    r"|погруз|выгруз|догруз|фрахт"        # logistics terms
    r"|ндс|предоплат|безнал"              # payment terms
    r"|(?:\+7|^8\d{10})"                  # phone patterns
    r")",
    re.IGNORECASE,
)

_INVALID_GEO_TOKEN_RE = re.compile(
    r"\b(?:оплата|нал|растаможка|растоможка|верхняя|боковая)\b",
    re.IGNORECASE,
)

_MIN_CARGO_TEXT_LEN = 15


def looks_like_cargo(text: str) -> bool:
    """Lightweight pre-filter: True if the text likely contains cargo info.

    Checks for logistics signal words (weight, price, vehicle types,
    route arrows, phone patterns) without calling the LLM.  Messages
    like "Привет всем" or "Обедаем" are skipped, saving API costs.
    """
    if len(text) < _MIN_CARGO_TEXT_LEN:
        return False
    return bool(_CARGO_SIGNAL_RE.search(text))


def contains_invalid_geo_token(text: str) -> bool:
    return bool(_INVALID_GEO_TOKEN_RE.search(text or ""))


async def parse_cargo_message_llm(
    text: str, *, keywords: Iterable[str]
) -> ParsedCargo | None:
    """Parse a cargo message using LLM, falling back to regex.

    Includes a lightweight pre-filter: if the text has no logistics
    signal words at all, it is skipped before the LLM is called.
    This saves 3-5x on API costs by filtering chat noise.

    Supports two LLM providers:
    - **Groq** (preferred): set ``GROQ_API_KEY``.
    - **OpenAI** fallback: set ``OPENAI_API_KEY`` (uses ``OPENAI_MODEL``,
      default ``gpt-4o-mini``).

    Falls back to the regex-based ``parse_cargo_message`` when no API
    key is configured or the LLM call fails.
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return None

    if not looks_like_cargo(clean_text):
        logger.debug("pre-filter skip (no cargo signals): %.60s…", clean_text)
        return None

    from src.core.config import settings

    has_groq = bool(getattr(settings, "groq_api_key", None))
    has_openai = bool(getattr(settings, "openai_api_key", None))

    if not has_groq and not has_openai:
        return parse_cargo_message(clean_text, keywords=keywords)

    try:
        system_prompt = _build_llm_system_prompt()

        if has_groq:
            llm_output = await _call_groq(
                system_prompt, clean_text, settings.groq_api_key
            )
            provider = "groq/llama-3.1-8b-instant"
        else:
            openai_model = getattr(settings, "openai_model", None) or "gpt-4o-mini"
            llm_output = await _call_openai(
                system_prompt,
                clean_text,
                settings.openai_api_key,
                openai_model,
            )
            provider = f"openai/{openai_model}"

        logger.info("LLM extractor [%s] raw: %s", provider, llm_output)

        data = _extract_json(llm_output)
        if data is None:
            logger.warning("LLM returned non-JSON, falling back to regex")
            return parse_cargo_message(clean_text, keywords=keywords)

        parsed = _llm_result_to_parsed(data, clean_text, keywords=keywords)
        if parsed is None:
            logger.warning("LLM result missing route, falling back to regex")
            return parse_cargo_message(clean_text, keywords=keywords)

        parsed.is_hot_deal = evaluate_hot_deal(parsed)

        logger.info(
            "LLM parsed [%s]: %s → %s, body=%s, wt=%s, rate=%s, "
            "date=%s %s, desc=%s, pay=%s, direct=%s, hot=%s",
            provider,
            parsed.from_city,
            parsed.to_city,
            parsed.body_type,
            parsed.weight_t,
            parsed.rate_rub,
            parsed.load_date,
            parsed.load_time,
            parsed.cargo_description,
            parsed.payment_terms,
            parsed.is_direct_customer,
            parsed.is_hot_deal,
        )
        return parsed

    except Exception as exc:
        logger.warning("LLM extractor failed (%s), falling back to regex", exc)
        return parse_cargo_message(clean_text, keywords=keywords)


def build_dedupe_key(parsed: ParsedCargo, *, chat_id: int | str, fallback_id: str) -> str:
    phone_part = (parsed.phone or "").strip().lower()
    from_part = parsed.from_city.strip().lower()
    to_part = parsed.to_city.strip().lower()
    if phone_part:
        stable = f"{phone_part}|{from_part}|{to_part}"
    else:
        stable = f"{from_part}|{to_part}|{fallback_id}"
    digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return f"parser-dedupe:{chat_id}:{digest}"


def build_content_dedupe_key(parsed: ParsedCargo) -> str:
    """Build a cross-chat content-based dedupe key.

    Catches the same cargo posted by different dispatchers or across
    multiple chats.  Uses route + weight + body_type + date so that
    ``трал 20т Самара-Москва завтра`` is recognized as a duplicate
    regardless of who posted it or in which chat.
    """
    parts = [
        parsed.from_city.strip().lower(),
        parsed.to_city.strip().lower(),
        str(parsed.weight_t or ""),
        (parsed.body_type or "").strip().lower(),
        (parsed.load_date or "").strip(),
    ]
    stable = "|".join(parts)
    digest = hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return f"parser-content-dedupe:{digest}"
