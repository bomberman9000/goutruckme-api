from datetime import datetime, timedelta
from groq import Groq
from src.core.config import settings
from src.core.logger import logger
import json
import math
import re

client = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

# Паттерн времени ЧЧ:ММ или ЧЧ.ММ
_TIME_RE = re.compile(r"(?:в\s+)?(\d{1,2})[.:](\d{2})\s*$", re.I)
_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|т\b|t\b|тн\b|тонн(?:а|ы)?)", re.I)
_VOLUME_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:м3|м³|m3|куб(?:\.|а|ов)?(?:ик)?|кубовик|volume)\b",
    re.I,
)
_PRICE_SHORT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*к(?:\s|$|руб|р|₽)", re.I)
_PRICE_FULL_RE = re.compile(r"(\d{5,})\s*(?:руб|р|₽)", re.I)
_BODY_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "рефрижератор": ("реф", "рефриж", "замороз", "мороз", "охлажд", "темпер", "морож"),
    "борт": ("борт", "металл", "арматур", "труба", "лист", "швеллер", "пиломат", "доск", "досок", "брус"),
    "контейнер": ("контейнер", "container"),
    "трал": ("трал", "негабарит", "низкорам", "спецтех"),
    "изотерм": ("изотерм",),
    "тент": ("тент", "фура", "тнп", "паллет", "короб"),
}
_CARGO_TYPE_HINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Продукты", ("замороз", "мороз", "охлажд", "реф", "продукт", "молоч", "фрукты", "овощ", "рыба"), "рефрижератор"),
    ("Металл", ("металл", "арматур", "труба", "швеллер", "лист"), "борт"),
    ("Пиломатериалы", ("пиломат", "доски", "досок", "брус", "фанер"), "борт"),
    ("ТНП", ("тнп", "паллет", "паллеты", "короб", "бытхим", "товар"), "тент"),
    ("Стройматериалы", ("строймат", "кирпич", "цемент", "плитка"), "тент"),
)

CITY_ALIASES = {
    "мск": "Москва", "москва": "Москва",
    "спб": "Санкт-Петербург", "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург",
    "нск": "Новосибирск", "новосиб": "Новосибирск",
    "екб": "Екатеринбург", "ёбург": "Екатеринбург",
    "казань": "Казань", "казан": "Казань", "кзн": "Казань",
    "нн": "Нижний Новгород", "нижний": "Нижний Новгород",
    "самара": "Самара", "самар": "Самара",
    "ростов": "Ростов-на-Дону", "рнд": "Ростов-на-Дону",
    "уфа": "Уфа",
    "красноярск": "Красноярск", "крск": "Красноярск",
    "воронеж": "Воронеж", "врн": "Воронеж",
    "пермь": "Пермь",
    "волгоград": "Волгоград",
    "краснодар": "Краснодар", "крд": "Краснодар",
    "челябинск": "Челябинск", "челяба": "Челябинск",
    "омск": "Омск",
    "тюмень": "Тюмень",
    "челны": "Набережные Челны", "набережные": "Набережные Челны", "нч": "Набережные Челны",
}

async def parse_city(text: str) -> str | None:
    """Распознать город из текста"""
    text_lower = text.lower().strip()

    # Сначала проверяем алиасы
    if text_lower in CITY_ALIASES:
        return CITY_ALIASES[text_lower]

    # Проверяем частичное совпадение
    for alias, city in CITY_ALIASES.items():
        if alias in text_lower or text_lower in alias:
            return city

    # Если не нашли — спрашиваем AI
    if not client:
        return text.title()

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": "Ты помощник для распознавания городов России. Пользователь вводит название города, возможно с опечаткой или сокращением. Верни только название города с большой буквы. Если не можешь распознать — верни исходный текст."
            }, {
                "role": "user",
                "content": f"Распознай город: {text}"
            }],
            max_tokens=50,
            temperature=0
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"AI parsed city: {text} -> {result}")
        return result
    except Exception as e:
        logger.error(f"AI city parse error: {e}")
        return text.title()


def parse_load_datetime(text: str):
    """
    Парсит дату и опционально время из текста.
    Примеры: завтра, завтра в 10:00, послезавтра 14:00, 15.02.2026, 15.02 9:00.
    Возвращает (datetime, time_str | None) или None при ошибке.
    """
    if not text or not text.strip():
        return None
    raw = text.strip().lower()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    load_time_str = None

    # Отделяем время в конце: "завтра в 10:00", "завтра 14:00"
    time_match = _TIME_RE.search(raw)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        if h <= 23 and m <= 59:
            load_time_str = f"{h:02d}:{m:02d}"
        raw = raw[: time_match.start()].strip()

    if raw in ("сегодня", "today"):
        return (today, load_time_str)
    if raw in ("завтра", "tomorrow"):
        return (today + timedelta(days=1), load_time_str)
    if raw in ("послезавтра",):
        return (today + timedelta(days=2), load_time_str)

    # ДД.ММ.ГГГГ или ДД.ММ
    raw_date = raw
    try:
        parts = raw_date.split(".")
        if len(parts) == 2:
            raw_date = raw_date + f".{today.year}"
        load_date = datetime.strptime(raw_date, "%d.%m.%Y")
        return (load_date, load_time_str)
    except ValueError:
        pass

    # AI fallback: «завтра утром», «в понедельник», «20 числа»
    if client:
        try:
            result = _parse_load_datetime_ai(text)
            if result:
                return result
        except Exception as e:
            logger.warning("AI load_datetime parse failed: %s", e)

    return None


def _parse_load_datetime_ai(text: str):
    """AI извлекает дату и время из естественной фразы."""
    today_str = datetime.now().strftime("%d.%m.%Y")
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {
                "role": "system",
                "content": f"""Из фразы пользователя извлеки дату загрузки и время.
Сегодня: {today_str}. Верни ТОЛЬКО JSON: {{"date": "ДД.ММ.ГГГГ", "time": "ЧЧ:ММ" или null}}.
Примеры: "завтра в 10" -> {{"date": "...", "time": "10:00"}}, "послезавтра" -> {{"date": "...", "time": null}}.""",
            },
            {"role": "user", "content": text},
        ],
        max_tokens=80,
        temperature=0,
    )
    out = response.choices[0].message.content.strip()
    if "{" not in out or "}" not in out:
        return None
    try:
        j = json.loads(out[out.find("{") : out.rfind("}") + 1])
        d = datetime.strptime(str(j["date"]).strip(), "%d.%m.%Y")
        t = j.get("time")
        if t is not None and str(t).strip():
            t = str(t).strip()
            if ":" not in t:
                t = f"{int(t):02d}:00"
            elif len(t) == 4 and t[1] == ":":
                t = "0" + t
            load_time_str = t
        else:
            load_time_str = None
        return (d, load_time_str)
    except (ValueError, KeyError, TypeError):
        return None
    """Парсит запрос на груз из естественного языка"""
    if not client:
        return None

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты помощник для парсинга заявок на грузоперевозки. 
Извлеки из текста: откуда, куда, вес (тонны), цену (рубли), тип груза.
Верни JSON: {\"from_city\": \"...\", \"to_city\": \"...\", \"weight\": число, \"price\": число, \"cargo_type\": \"...\"}
Если чего-то нет — не включай в JSON. Города пиши полностью с большой буквы."""
            }, {
                "role": "user",
                "content": text
            }],
            max_tokens=200,
            temperature=0
        )
        result = response.choices[0].message.content.strip()
        # Извлекаем JSON
        if "{" in result and "}" in result:
            json_str = result[result.find("{"):result.rfind("}")+1]
            data = json.loads(json_str)
            logger.info(f"AI parsed cargo: {text} -> {data}")
            return data
    except Exception as e:
        logger.error(f"AI cargo parse error: {e}")
    return None


def _extract_cities_fallback(text_lower: str) -> tuple[str | None, str | None]:
    hits: list[tuple[int, str]] = []
    for alias, city in CITY_ALIASES.items():
        idx = text_lower.find(alias)
        if idx != -1:
            hits.append((idx, city))
    hits.sort(key=lambda item: item[0])
    ordered: list[str] = []
    for _, city in hits:
        if city not in ordered:
            ordered.append(city)
    if len(ordered) >= 2:
        return ordered[0], ordered[1]
    if ordered:
        return ordered[0], None
    return None, None


def _infer_body_type(text_lower: str, fallback: str | None = None) -> str:
    for body_type, hints in _BODY_TYPE_HINTS.items():
        if any(hint in text_lower for hint in hints):
            return body_type
    return fallback or "тент"


def _infer_cargo_profile(text_lower: str) -> tuple[str, str]:
    for cargo_type, hints, body_type in _CARGO_TYPE_HINTS:
        if any(hint in text_lower for hint in hints):
            return cargo_type, _infer_body_type(text_lower, body_type)
    return "Груз", _infer_body_type(text_lower, "тент")


async def transcribe_voice(file_bytes: bytes) -> str | None:
    """Транскрибирует голосовое через Groq Whisper REST API."""
    import httpx
    api_key = settings.groq_api_key
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("voice.ogg", file_bytes, "audio/ogg")},
                data={"model": "whisper-large-v3-turbo", "language": "ru", "response_format": "text"},
            )
            r.raise_for_status()
            return r.text.strip() or None
    except Exception as exc:
        logger.warning("transcribe_voice error: %s", exc)
        return None


_ROUTE_NLP_RE = re.compile(
    r"(?P<from>[А-Яа-яЁёA-Za-z]{3,20})\s+(?P<to>[А-Яа-яЁёA-Za-z]{3,20})",
)


def _parse_cargo_nlp_regex(raw_text: str) -> dict | None:
    """Regex-only fallback for parse_cargo_nlp when no LLM API key is set."""
    text_lc = raw_text.lower()
    result: dict = {}

    # Volume
    vol_match = _VOLUME_RE.search(raw_text)
    if vol_match:
        result["volume_m3"] = float(vol_match.group(1).replace(",", "."))

    # Weight
    weight_match = _WEIGHT_RE.search(raw_text)
    if weight_match:
        val = float(weight_match.group(1).replace(",", "."))
        unit = weight_match.group(2).lower()
        if unit in ("кг", "kg"):
            val /= 1000
        result["weight"] = val

    # Price
    price_match = _PRICE_SHORT_RE.search(raw_text)
    if price_match:
        result["price"] = int(float(price_match.group(1).replace(",", ".")) * 1000)
    else:
        price_full = _PRICE_FULL_RE.search(raw_text)
        if price_full:
            result["price"] = int(price_full.group(1))

    # Cargo type & body type from hints
    for cargo_name, hints, body in _CARGO_TYPE_HINTS:
        if any(h in text_lc for h in hints):
            result["cargo_type"] = cargo_name
            result["body_type"] = body
            break

    if "body_type" not in result:
        for body_name, hints in _BODY_TYPE_HINTS.items():
            if any(h in text_lc for h in hints):
                result["body_type"] = body_name
                break

    # Cities from aliases
    words = re.split(r"[\s\-–—/|,]+", raw_text)
    cities: list[str] = []
    for w in words:
        key = w.lower().strip(".,;:!?()")
        if key in CITY_ALIASES:
            cities.append(CITY_ALIASES[key])
    if len(cities) >= 2:
        result["from_city"] = cities[0]
        result["to_city"] = cities[1]
    elif len(cities) == 1:
        result["from_city"] = cities[0]

    if not result:
        return None

    result.setdefault("cargo_type", "тент")
    return result


async def parse_cargo_nlp(text: str) -> dict | None:
    """
    Parse a free-form cargo message using Gemini 1.5 Flash.
    Falls back to regex extraction when no API key is available.
    """
    raw_text = (text or "").strip()
    if not raw_text or len(raw_text) < 5:
        return None

    import httpx
    api_key = settings.gemini_api_key
    if not api_key:
        return _parse_cargo_nlp_regex(raw_text)

    from datetime import date as _date
    today_iso = _date.today().isoformat()

    prompt = (
        f"Сегодня: {today_iso}. Используй этот год при разборе дат (например 'завтра', 'в пятницу').\n"
        "Ты экспертный парсер грузовых заявок (Россия). Извлеки данные из текста.\n"
        "ПРАВИЛА:\n"
        "1. Исправляй опечатки в названиях городов: 'самра' -> 'Самара', 'мск' -> 'Москва'.\n"
        "2. Внимательно смотри на предлоги: 'из', 'от' -> from_city; 'в', 'до', 'на' -> to_city.\n"
        "3. Если города указаны просто через тире (А-Б), то первый - from, второй - to.\n"
        "4. Если есть слово 'наоборот', поменяй города местами.\n"
        "5. Вес всегда в Тоннах (число).\n"
        "6. Парси деньги: '45к' -> 45000, 'полтинник' -> 50000. Цена всегда числовая.\n"
        "7. Типы оплаты: 'ндс' -> 'nds', 'нал' -> 'cash', 'б/н' -> 'no_nds'.\n"
        "8. cargo_type: если явно не указан — верни 'тент'.\n"
        "9. Возвращай строго JSON.\n\n"
        f"Текст: \"{raw_text}\"\n\n"
        "Верни ТОЛЬКО JSON:\n"
        "{\n"
        '  "from_city": "Полное название или null",\n'
        '  "to_city": "Полное название или null",\n'
        '  "weight": float или null,\n'
        '  "volume_m3": float или null,\n'
        '  "price": int или null,\n'
        '  "cargo_type": "тент/реф/борт/... (обязательно)",\n'
        '  "body_type": "тент/реф/борт/...",\n'
        '  "load_date": "YYYY-MM-DD или null",\n'
        '  "is_urgent": boolean,\n'
        '  "payment_type": "nds/no_nds/cash/null"\n'
        "}"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "response_mime_type": "application/json"}
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client_http:
            resp = await client_http.post(url, json=payload)
            if resp.status_code != 200:
                return None
            data_raw = resp.json()
            llm_text = data_raw['candidates'][0]['content']['parts'][0]['text'].strip()
            data = json.loads(llm_text)
            
            # Приводим к формату бота
            result = {k: v for k, v in data.items() if v is not None}
            if "weight" in result: result["weight"] = float(result["weight"])
            result.setdefault("cargo_type", "тент")
            return result
    except Exception as e:
        logger.error(f"Gemini parse_cargo_nlp error: {e}")
        return None

async def parse_cargo_search(text: str) -> dict | None:
    """
    Парсит поисковый запрос из естественного языка.
    Примеры:
    - "москва питер" → {from_city: "Москва", to_city: "Санкт-Петербург"}
    - "мск спб 20т" → {from_city: "Москва", to_city: "Санкт-Петербург", min_weight: 20, max_weight: 20}
    - "из казани 10-15 тонн до 100000" → {from_city: "Казань", min_weight: 10, max_weight: 15, max_price: 100000}
    """
    if not client:
        return _parse_search_simple(text)

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты парсер поисковых запросов для грузоперевозок.
Извлеки параметры из текста. Верни ТОЛЬКО JSON без пояснений:
{
  "from_city": "Город отправления",
  "to_city": "Город назначения",
  "min_weight": число,
  "max_weight": число,
  "max_price": число
}

Правила:
- Города пиши полностью с большой буквы
- Сокращения: мск=Москва, спб/питер=Санкт-Петербург, екб=Екатеринбург, нск=Новосибирск, рнд=Ростов-на-Дону, нн=Нижний Новгород, крд=Краснодар
- "20т" или "20 тонн" → min_weight=20, max_weight=20
- "10-15т" → min_weight=10, max_weight=15
- "от 10т" → min_weight=10
- "до 20т" → max_weight=20
- "до 100к" или "до 100000" → max_price=100000
- "50к" = 50000
- Если параметр не указан — НЕ включай его в JSON
- Если указан только один город без предлогов — это from_city"""
            }, {
                "role": "user",
                "content": text
            }],
            max_tokens=150,
            temperature=0
        )

        result = response.choices[0].message.content.strip()
        logger.info(f"AI search parse: {text} -> {result}")

        if "{" in result and "}" in result:
            json_str = result[result.find("{"):result.rfind("}") + 1]
            data = json.loads(json_str)
            return _normalize_search_params(data)

    except Exception as e:
        logger.error(f"AI search parse error: {e}")

    return _parse_search_simple(text)


def _normalize_search_params(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None

    out: dict = {}
    if data.get("from_city"):
        out["from_city"] = str(data["from_city"]).strip()
    if data.get("to_city"):
        out["to_city"] = str(data["to_city"]).strip()

    for key in ("min_weight", "max_weight"):
        if key in data and data[key] is not None:
            try:
                out[key] = float(str(data[key]).replace(",", ".").strip())
            except Exception:
                pass

    if "max_price" in data and data["max_price"] is not None:
        try:
            raw = str(data["max_price"]).replace(" ", "").replace(",", ".").strip()
            out["max_price"] = int(float(raw))
        except Exception:
            pass

    return out if out else None


def _parse_search_simple(text: str) -> dict | None:
    """Простой парсинг без AI"""
    result: dict = {}
    text_lower = (text or "").lower()

    matches: list[tuple[int, str, str | None]] = []
    for alias, city in CITY_ALIASES.items():
        alias_key = alias.lower()
        idx = text_lower.find(alias_key)
        if idx == -1 and len(alias_key) > 4:
            idx = text_lower.find(alias_key[:-1])
        if idx != -1:
            prefix = text_lower[max(0, idx - 12):idx]
            role = None
            if re.search(r"(из|от)\s+$", prefix):
                role = "from"
            elif re.search(r"(в|до|к)\s+$", prefix):
                role = "to"
            matches.append((idx, city, role))

    has_explicit_from = False
    has_explicit_to = False
    for _, city, role in sorted(matches, key=lambda x: x[0]):
        if role == "from" and not result.get("from_city"):
            result["from_city"] = city
            has_explicit_from = True
        elif role == "to" and not result.get("to_city"):
            result["to_city"] = city
            has_explicit_to = True

    for _, city, _role in sorted(matches, key=lambda x: x[0]):
        if not result.get("from_city"):
            if has_explicit_to and not has_explicit_from:
                continue
            result["from_city"] = city
        elif not result.get("to_city") and result.get("from_city") != city:
            result["to_city"] = city
            break

    kg_range_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*кг", text_lower)
    if kg_range_match:
        result["min_weight"] = float(kg_range_match.group(1).replace(",", ".")) / 1000.0
        result["max_weight"] = float(kg_range_match.group(2).replace(",", ".")) / 1000.0
    else:
        kg_from = re.search(r"от\s*(\d+(?:[.,]\d+)?)\s*кг", text_lower)
        kg_to = re.search(r"до\s*(\d+(?:[.,]\d+)?)\s*кг", text_lower)
        if kg_from:
            result["min_weight"] = float(kg_from.group(1).replace(",", ".")) / 1000.0
        if kg_to:
            result["max_weight"] = float(kg_to.group(1).replace(",", ".")) / 1000.0
        if not kg_from and not kg_to:
            kg_match = re.search(r"(\d+(?:[.,]\d+)?)\s*кг", text_lower)
            if kg_match:
                w = float(kg_match.group(1).replace(",", ".")) / 1000.0
                result["min_weight"] = w
                result["max_weight"] = w

    has_weight_filter = "min_weight" in result or "max_weight" in result
    if not has_weight_filter:
        weight_match = re.search(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(?:т|тонн)", text_lower)
        if weight_match:
            result["min_weight"] = float(weight_match.group(1).replace(",", "."))
            result["max_weight"] = float(weight_match.group(2).replace(",", "."))
        else:
            w_from = re.search(r"от\s*(\d+(?:[.,]\d+)?)\s*(?:т|тонн)", text_lower)
            w_to = re.search(r"до\s*(\d+(?:[.,]\d+)?)\s*(?:т|тонн)", text_lower)
            if w_from:
                result["min_weight"] = float(w_from.group(1).replace(",", "."))
            if w_to:
                result["max_weight"] = float(w_to.group(1).replace(",", "."))
            if not w_from and not w_to:
                weight_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:т|тонн)", text_lower)
                if weight_match:
                    w = float(weight_match.group(1).replace(",", "."))
                    result["min_weight"] = w
                    result["max_weight"] = w

    price_match = re.search(r"до\s*(\d+(?:[.,]\d+)?)\s*к", text_lower)
    if price_match:
        result["max_price"] = int(float(price_match.group(1).replace(",", ".")) * 1000)
    else:
        price_match = re.search(r"до\s*(\d{4,})", text_lower)
        if price_match:
            result["max_price"] = int(price_match.group(1))

    return result if result else None

async def estimate_price(from_city: str, to_city: str, weight: float) -> int | None:
    """Оценка цены перевозки"""
    if not client:
        return None

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": """Ты эксперт по грузоперевозкам в России. 
Оцени примерную стоимость перевозки груза.
Учитывай: расстояние между городами, вес груза.
Средняя ставка: 30-50 руб/км для фуры, минимум 5000 руб.
Верни только число в рублях, без пояснений."""
            }, {
                "role": "user",
                "content": f"Перевозка {weight} тонн из {from_city} в {to_city}"
            }],
            max_tokens=50,
            temperature=0.3
        )
        result = response.choices[0].message.content.strip()
        # Извлекаем число
        price = int(''.join(filter(str.isdigit, result)))
        logger.info(f"AI estimated price: {from_city}->{to_city}, {weight}t = {price}₽")
        return price
    except Exception as e:
        logger.error(f"AI price estimate error: {e}")
    return None

def _normalize_city_key(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = t.replace("ё", "е")
    t = t.replace("-", " ")
    t = re.sub(r"[^0-9a-zа-я\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

CITY_COORDS: dict[str, tuple[float, float]] = {
    "москва": (55.7558, 37.6173),
    "санкт петербург": (59.9311, 30.3609),
    "новосибирск": (55.0084, 82.9357),
    "екатеринбург": (56.8389, 60.6057),
    "нижний новгород": (56.2965, 43.9361),
    "казань": (55.7961, 49.1064),
    "самара": (53.1959, 50.1002),
    "омск": (54.9885, 73.3242),
    "ростов на дону": (47.2357, 39.7015),
    "уфа": (54.7388, 55.9721),
    "красноярск": (56.0097, 92.7917),
    "пермь": (58.0105, 56.2502),
    "воронеж": (51.6608, 39.2003),
    "волгоград": (48.7080, 44.5133),
    "краснодар": (45.0355, 38.9753),
    "челябинск": (55.1644, 61.4368),
    "тюмень": (57.1530, 65.5343),
    "симферополь": (44.9521, 34.1024),
    "мурманск": (68.9585, 33.0827),
    "ставрополь": (45.0428, 41.9734),
    "набережные челны": (55.7436, 52.3958),
}

_FOREIGN_CITY_KEYS = {
    "минск",
    "брест",
    "гомель",
    "пинск",
    "борисов",
    "алматы",
    "астана",
    "нур султан",
    "шымкент",
    "чимкент",
    "бишкек",
    "ош",
    "ташкент",
    "андижан",
    "наманган",
    "фергана",
    "самарканд",
    "карши",
    "бухара",
    "навои",
    "зарафшан",
    "ургенч",
    "нукус",
    "джизак",
    "коканд",
    "мерсин",
    "стамбул",
    "истанбул",
    "ankara",
    "анкара",
    "izmir",
    "измир",
    "antalya",
    "анталья",
}

_OPEN_BODY_TOKENS = ("борт", "открыт")
_REF_BODY_TOKENS = ("реф", "рефриж", "холод", "изотерм")
_HAZARDOUS_TOKENS = ("adr", "опас", "хим", "chemical")
MARKET_BENCHMARKS: dict[str, dict[str, object]] = {
    "MOSCOW_KEMEROVO": {
        "from": "Москва",
        "to": "Кемерово",
        "price": 301682,
        "distance_km": 3560,
        "body_type": "тент",
        "weight_t": 20.0,
    },
    "MOSCOW_CHELYABINSK": {
        "from": "Москва",
        "to": "Челябинск",
        "price": 146394,
        "distance_km": 1780,
        "body_type": "тент",
        "weight_t": 20.0,
    },
    "SPB_EKATERINBURG": {
        "from": "Санкт-Петербург",
        "to": "Екатеринбург",
        "price": 162025,
        "distance_km": 2200,
        "body_type": "тент",
        "weight_t": 20.0,
    },
    "MOSCOW_NOVOSIBIRSK": {
        "from": "Москва",
        "to": "Новосибирск",
        "price": 264303,
        "distance_km": 3330,
        "body_type": "тент",
        "weight_t": 20.0,
    },
    "MOSCOW_KAZAN_REF": {
        "from": "Москва",
        "to": "Казань",
        "price": 135855,
        "distance_km": 800,
        "body_type": "рефрижератор",
        "weight_t": 20.0,
    },
}

def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))

def _resolve_effective_body_type(cargo_type: str | None, body_type: str | None = None) -> str:
    body_hint = (body_type or "").strip()
    if body_hint:
        return body_hint
    _, inferred = _infer_cargo_profile((cargo_type or "").strip().lower())
    return inferred or "тент"


def _is_international_route(from_city: str | None, to_city: str | None) -> bool:
    origin_key = _normalize_city_key(from_city or "")
    destination_key = _normalize_city_key(to_city or "")
    return bool(origin_key and origin_key in _FOREIGN_CITY_KEYS) or bool(
        destination_key and destination_key in _FOREIGN_CITY_KEYS
    )


def _scale_reference_price(base_price: int, weight: float, base_weight: float = 20.0) -> int:
    if weight >= base_weight:
        extra_tons = max(0.0, min(float(weight) - float(base_weight), 10.0))
        return int(round(base_price * (1.0 + extra_tons * 0.015)))
    if weight >= 10:
        return int(round(base_price * (0.6 + 0.02 * float(weight))))
    if weight >= 5:
        return int(round(base_price * (0.4 + 0.02 * float(weight))))
    return int(round(base_price * 0.4))


def _lookup_market_benchmark(
    from_city: str | None,
    to_city: str | None,
    body_type: str | None = None,
) -> dict[str, object] | None:
    if not from_city or not to_city:
        return None
    origin_key = _normalize_city_key(from_city)
    destination_key = _normalize_city_key(to_city)
    body_key = _normalize_city_key(_resolve_effective_body_type(None, body_type))

    for item in MARKET_BENCHMARKS.values():
        benchmark_origin = _normalize_city_key(str(item["from"]))
        benchmark_destination = _normalize_city_key(str(item["to"]))
        benchmark_body = _normalize_city_key(str(item["body_type"]))
        if origin_key == benchmark_origin and destination_key == benchmark_destination and body_key == benchmark_body:
            return {
                "price": int(item["price"]),
                "distance_km": int(item["distance_km"]),
                "body_type": str(item["body_type"]),
                "weight_t": float(item.get("weight_t") or 20.0),
                "source": "benchmark_feb_2026",
            }
    return None


def calculate_market_rate(
    *,
    from_city: str | None,
    to_city: str | None,
    distance_km: int | float,
    weight: float,
    cargo_type: str | None = None,
    body_type: str | None = None,
    volume_m3: float | None = None,
) -> dict:
    distance = max(1, int(distance_km))
    weight_t = max(0.5, float(weight or 0.0))
    volume = float(volume_m3) if volume_m3 is not None else None
    resolved_body_type = _resolve_effective_body_type(cargo_type, body_type)
    factors: list[str] = []
    benchmark = _lookup_market_benchmark(from_city, to_city, resolved_body_type)

    if benchmark:
        benchmark_price = _scale_reference_price(
            int(benchmark["price"]),
            weight_t,
            float(benchmark["weight_t"]),
        )
        benchmark_distance = max(1, int(benchmark["distance_km"]))
        benchmark_rate_per_km = benchmark_price / benchmark_distance
        recommended_rate = int(round(distance * benchmark_rate_per_km))
        min_price = int(round(recommended_rate * 0.90))
        max_price = int(round(recommended_rate * 1.10))
        return {
            "price": recommended_rate,
            "distance": distance,
            "rate_per_km": int(round(benchmark_rate_per_km)),
            "min_price": max(1, min_price),
            "max_price": max(max_price, recommended_rate),
            "body_type": resolved_body_type,
            "is_international": _is_international_route(from_city, to_city),
            "factors": ["рыночный benchmark февраль 2026"],
            "source": str(benchmark["source"]),
        }

    body_key = (resolved_body_type or "").strip().lower()
    if any(token in body_key for token in _REF_BODY_TOKENS):
        rate_per_km = 110.0
        factors.append("рефрижератор")
    elif any(token in body_key for token in _OPEN_BODY_TOKENS):
        rate_per_km = 88.0
        factors.append("открытая погрузка")
    else:
        rate_per_km = 80.0

    rate_per_km += min(weight_t, 20.0) * 0.35
    if weight_t > 20.0:
        rate_per_km += min(weight_t - 20.0, 10.0) * 0.15
        factors.append("тяжёлый груз")
    elif weight_t < 3.0:
        rate_per_km *= 1.08
        factors.append("лёгкий экспресс-груз")

    if volume is not None and weight_t > 0 and (volume / weight_t) >= 8:
        rate_per_km *= 1.05
        factors.append("объёмный груз")

    if distance < 500:
        rate_per_km *= 1.40
        factors.append("короткое плечо")

    if _is_international_route(from_city, to_city):
        rate_per_km *= 1.45
        factors.append("международное направление")

    cargo_key = (cargo_type or "").strip().lower()
    if cargo_key and any(token in cargo_key for token in _HAZARDOUS_TOKENS):
        rate_per_km *= 1.15
        factors.append("сложный груз")

    if distance > 2500:
        rate_per_km *= 0.95
        factors.append("длинное плечо")

    rate_per_km = max(72.0, rate_per_km)
    recommended_rate = int(round(distance * rate_per_km))
    min_price = int(round(recommended_rate * 0.88))
    max_price = int(round(recommended_rate * 1.12))

    return {
        "price": recommended_rate,
        "distance": distance,
        "rate_per_km": int(round(rate_per_km)),
        "min_price": max(1, min_price),
        "max_price": max(max_price, recommended_rate),
        "body_type": resolved_body_type,
        "is_international": _is_international_route(from_city, to_city),
        "factors": factors,
        "source": "calculated",
    }


def estimate_price_local(
    from_city: str,
    to_city: str,
    weight: float,
    *,
    cargo_type: str | None = None,
    body_type: str | None = None,
    volume_m3: float | None = None,
) -> dict | None:
    """Локальная оценка цены по расстоянию (если известны координаты городов)"""
    from src.core.geo import city_coords

    a = city_coords(from_city) or CITY_COORDS.get(_normalize_city_key(from_city))
    b = city_coords(to_city) or CITY_COORDS.get(_normalize_city_key(to_city))
    if not a or not b:
        return None

    distance = _haversine_km(a, b)
    return calculate_market_rate(
        from_city=from_city,
        to_city=to_city,
        distance_km=max(1, int(distance)),
        weight=weight,
        cargo_type=cargo_type,
        body_type=body_type,
        volume_m3=volume_m3,
    )

async def get_market_price(
    from_city: str,
    to_city: str,
    weight: float,
    cargo_type: str = "тент",
    body_type: str | None = None,
) -> dict | None:
    """Получить рыночную цену с учётом веса"""
    from src.core.database import async_session
    from src.core.models import MarketPrice
    from sqlalchemy import select

    cargo_type_key = _resolve_effective_body_type(cargo_type, body_type).strip()
    benchmark = _lookup_market_benchmark(from_city, to_city, cargo_type_key)
    if benchmark:
        base_price = int(benchmark["price"])
        base_weight = float(benchmark["weight_t"])
        adjusted_price = _scale_reference_price(base_price, float(weight), base_weight)
        benchmark_distance = max(1, int(benchmark["distance_km"]))
        return {
            "market_price": base_price,
            "adjusted_price": adjusted_price,
            "base_weight": base_weight,
            "your_weight": weight,
            "source": str(benchmark["source"]),
            "updated": "02.2026",
            "cargo_type": str(benchmark["body_type"]),
            "rate_per_km": int(round(base_price / benchmark_distance)),
        }

    async with async_session() as session:
        price_data = await session.scalar(
            select(MarketPrice).where(
                MarketPrice.from_city == from_city,
                MarketPrice.to_city == to_city,
                MarketPrice.cargo_type.ilike(f"%{cargo_type_key[:3]}%"),
            )
        )

        if not price_data:
            price_data = await session.scalar(
                select(MarketPrice).where(
                    MarketPrice.from_city == to_city,
                    MarketPrice.to_city == from_city,
                    MarketPrice.cargo_type.ilike(f"%{cargo_type_key[:3]}%"),
                )
            )

        if not price_data:
            return None

        base_price = price_data.price
        base_weight = price_data.weight or 20.0

        if weight >= base_weight:
            adjusted_price = base_price
        elif weight >= 10:
            adjusted_price = int(base_price * (0.6 + 0.02 * weight))
        elif weight >= 5:
            adjusted_price = int(base_price * (0.4 + 0.02 * weight))
        else:
            adjusted_price = int(base_price * 0.4)

        return {
            "market_price": base_price,
            "adjusted_price": adjusted_price,
            "base_weight": base_weight,
            "your_weight": weight,
            "source": price_data.source,
            "updated": price_data.updated_at.strftime("%d.%m.%Y"),
            "cargo_type": price_data.cargo_type,
        }

async def estimate_price_smart(
    from_city: str,
    to_city: str,
    weight: float,
    cargo_type: str = "тент",
    body_type: str | None = None,
    volume_m3: float | None = None,
) -> dict:
    """Умная оценка цены: ATI (живой рынок) → справочник → расчёт"""
    # 1. Пробуем ATI.SU — живые данные с биржи
    try:
        from src.services.ati_service import get_route_rate_cached
        ati = await get_route_rate_cached(from_city, to_city, timeout=8.0)
        if ati and ati.price_rub:
            km_info = f"\n• Расстояние: ~{ati.distance_km} км" if ati.distance_km else ""
            per_km = f"\n• Ставка: ~{ati.price_per_km} ₽/км" if ati.price_per_km else ""
            rng = ""
            if ati.raw_prices:
                rng = f"\n• Диапазон: {min(ati.raw_prices):,} — {max(ati.raw_prices):,} ₽"
            return {
                "price": ati.price_rub,
                "source": "ati.su",
                "distance": ati.distance_km,
                "details": (
                    f"📊 Рынок ATI.SU ({ati.loads_count} грузов)"
                    f"{km_info}{per_km}{rng}"
                ),
            }
    except Exception as _e:
        logger.debug("ATI rate skipped in estimate_price_smart: %s", _e)

    # 2. Справочник рыночных цен (база данных)
    market = await get_market_price(from_city, to_city, weight, cargo_type, body_type=body_type)
    if market:
        rate_line = (
            f"\\n• Индекс: ~{int(market['rate_per_km'])} ₽/км"
            if market.get("rate_per_km")
            else ""
        )
        return {
            "price": market["adjusted_price"],
            "source": str(market.get("source") or "market"),
            "market_price_20t": market["market_price"],
            "details": (
                f"📊 Рыночная цена ({market['source']})\\n"
                f"• За 20т: {market['market_price']:,} ₽\\n"
                f"• За {weight}т: {market['adjusted_price']:,} ₽\\n"
                f"• Данные от {market['updated']}"
                f"{rate_line}"
            ),
        }

    effective_body_type = _resolve_effective_body_type(cargo_type, body_type)
    local = estimate_price_local(
        from_city,
        to_city,
        weight,
        cargo_type=cargo_type,
        body_type=effective_body_type,
        volume_m3=volume_m3,
    )
    if local:
        factors = ", ".join(local["factors"]) if local.get("factors") else "базовый маршрут"
        return {
            "price": local["price"],
            "source": str(local.get("source") or "calculated"),
            "distance": local["distance"],
            "details": (
                "📐 Динамическая ставка\\n"
                f"• Расстояние: ~{local['distance']} км\\n"
                f"• Ставка: ~{local['rate_per_km']} ₽/км\\n"
                f"• Диапазон: {local['min_price']:,} — {local['max_price']:,} ₽\\n"
                f"• Факторы: {factors}"
            ),
        }

    return {
        "price": None,
        "source": "unknown",
        "details": "❓ Недостаточно данных для оценки",
    }

async def _call_gemini_vision(image_bytes: bytes, prompt: str) -> dict:
    """Вызов Gemini 2.0 Flash для анализа изображений (OCR/Moderation)."""
    import httpx
    import base64
    api_key = settings.gemini_api_key
    if not api_key: return {"is_valid": False, "error": "No API key"}
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode('utf-8')
                    }
                }
            ]
        }],
        "generationConfig": {"temperature": 0.0, "response_mime_type": "application/json"}
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200: return {"error": f"API error {resp.status_code}"}
            data = resp.json()
            raw_json = data['candidates'][0]['content']['parts'][0]['text'].strip()
            return json.loads(raw_json)
    except Exception as e:
        return {"error": str(e)}

async def _call_gemini(prompt: str) -> str:
    """Вспомогательная функция для вызова Gemini 2.0 Flash с текстовым промптом."""
    import httpx
    api_key = settings.gemini_api_key
    if not api_key:
        raise ValueError("Gemini API key is not set in settings.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1}
    }

    async with httpx.AsyncClient(timeout=30.0) as client_http:
        resp = await client_http.post(url, json=payload)
        resp.raise_for_status()
        data_raw = resp.json()
        return data_raw['candidates'][0]['content']['parts'][0]['text'].strip()


async def moderate_content_ai(text: str | None = None, image_bytes: bytes | None = None) -> dict:
    """Модерация текста или фото через Gemini."""
    if image_bytes:
        prompt = "Проверь фото на NSFW, насилие, спам. Верни JSON: {\"is_safe\": bool, \"reason\": \"string\"}"
        return await _call_gemini_vision(image_bytes, prompt)
    
    if text:
        prompt = f"Проверь текст на мат и спам: \"{text}\". Верни JSON: {{\"is_safe\": bool, \"reason\": \"string\"}}"
        try:
            res = await _call_gemini(prompt)
            return json.loads(res)
        except Exception as e:
            logger.error(f"Gemini moderate_content_ai text parse error: {e}")
            return {"is_safe": True}
    return {"is_safe": True}

async def verify_document_ai(image_bytes: bytes, doc_type: str = "СТС") -> dict:
    """OCR Верификация документов через Gemini."""
    prompt = (
        f"Проанализируй фото документа {doc_type}. Извлеки ФИО, номер, для СТС: госномер и марку. "
        "Верни JSON: {\"is_valid\": bool, \"full_name\": \"...\", \"number\": \"...\", \"vehicle_plate\": \"...\", \"vehicle_model\": \"...\", \"comment\": \"...\"}"
    )
    return await _call_gemini_vision(image_bytes, prompt)

async def negotiate_price_ai(user_text: str, current_price: float, history: list = None) -> dict:
    """Логика торга через Gemini."""
    prompt = (
        f"Пользователь торгуется: \"{user_text}\". Текущая ставка: {current_price}. "
        "Лимит повышения +15%. Прими решение. "
        "Верни JSON: {\"negotiation_status\": \"accepted/counter/rejected\", \"counter_offer\": float, \"next_ask\": \"текст ответа\"}"
    )
    res = await _call_gemini(prompt)
    try: return json.loads(res)
    except: return {"negotiation_status": "rejected", "next_ask": "К сожалению, не можем изменить ставку."}
