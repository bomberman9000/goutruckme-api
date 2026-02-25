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

CITY_ALIASES = {
    "мск": "Москва", "москва": "Москва",
    "спб": "Санкт-Петербург", "питер": "Санкт-Петербург", "петербург": "Санкт-Петербург",
    "нск": "Новосибирск", "новосиб": "Новосибирск",
    "екб": "Екатеринбург", "ёбург": "Екатеринбург",
    "казань": "Казань", "кзн": "Казань",
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

def estimate_price_local(from_city: str, to_city: str, weight: float) -> dict | None:
    """Локальная оценка цены по расстоянию (если известны координаты городов)"""
    a = CITY_COORDS.get(_normalize_city_key(from_city))
    b = CITY_COORDS.get(_normalize_city_key(to_city))
    if not a or not b:
        return None

    distance = _haversine_km(a, b)
    distance_km = max(1, int(distance))

    rate_per_km = 35 + min(weight, 20) * 0.5
    rate_per_km = max(30, min(50, rate_per_km))

    min_price = int(distance_km * 30)
    max_price = int(distance_km * 50)
    price = int(distance_km * rate_per_km)

    return {
        "price": price,
        "distance": distance_km,
        "rate_per_km": int(rate_per_km),
        "min_price": min_price,
        "max_price": max_price,
    }

async def get_market_price(from_city: str, to_city: str, weight: float, cargo_type: str = "тент") -> dict | None:
    """Получить рыночную цену с учётом веса"""
    from src.core.database import async_session
    from src.core.models import MarketPrice
    from sqlalchemy import select

    cargo_type_key = (cargo_type or "тент").strip()

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

async def estimate_price_smart(from_city: str, to_city: str, weight: float, cargo_type: str = "тент") -> dict:
    """Умная оценка цены: сначала рынок, потом расчёт"""
    market = await get_market_price(from_city, to_city, weight, cargo_type)
    if market:
        return {
            "price": market["adjusted_price"],
            "source": "market",
            "market_price_20t": market["market_price"],
            "details": (
                f"📊 Рыночная цена ({market['source']})\\n"
                f"• За 20т: {market['market_price']:,} ₽\\n"
                f"• За {weight}т: {market['adjusted_price']:,} ₽\\n"
                f"• Данные от {market['updated']}"
            ),
        }

    local = estimate_price_local(from_city, to_city, weight)
    if local:
        return {
            "price": local["price"],
            "source": "calculated",
            "distance": local["distance"],
            "details": (
                "📐 Расчётная цена\\n"
                f"• Расстояние: ~{local['distance']} км\\n"
                f"• Ставка: ~{local['rate_per_km']} ₽/км\\n"
                f"• Диапазон: {local['min_price']:,} — {local['max_price']:,} ₽"
            ),
        }

    return {
        "price": None,
        "source": "unknown",
        "details": "❓ Недостаточно данных для оценки",
    }

async def chat_response(user_message: str, context: str = "") -> str:
    """Ответ на вопрос пользователя"""
    if not client:
        return "AI временно недоступен"

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system",
                "content": f"""Ты помощник в боте грузоперевозок. Отвечай кратко и по делу на русском языке.
{context}
Если вопрос не по теме — вежливо направь к функциям бота."""
            }, {
                "role": "user",
                "content": user_message
            }],
            max_tokens=300,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return "Произошла ошибка. Попробуйте позже."
