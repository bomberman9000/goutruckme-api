"""
ATI.SU market price analyzer via REST API.

Получает рыночные ставки с биржи ATI.SU по маршрутам.

Требует свежих cookies в ati_state.json (loads.ati.su сессия).
Экспорт кук: uv run python scripts/ati_refresh_cookies.py

Запуск теста:
    uv run python src/parser_bot/ati_analyzer.py
"""
import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "ati_state.json")

# Простой TTL-кэш: (from_city, to_city) → (RouteRate, timestamp)
_RATE_CACHE: dict[tuple[str, str], tuple["RouteRate", float]] = {}
_CACHE_TTL = 1800  # 30 минут

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://loads.ati.su",
    "Referer": "https://loads.ati.su/",
}

# Кэш: название города → ATI city_id
_CITY_ID_CACHE: dict[str, int] = {
    "Москва": 1,
    "Санкт-Петербург": 2,
    "Нижний Новгород": 4,
    "Казань": 80,
    "Самара": 220,
    "Екатеринбург": 54,
    "Уфа": 89,
    "Пермь": 56,
    "Тольятти": 221,
    "Саратов": 222,
    "Ростов-на-Дону": 76,
    "Краснодар": 35,
    "Воронеж": 36,
    "Челябинск": 75,
    "Новосибирск": 55,
    "Омск": 57,
    "Красноярск": 37,
    "Иркутск": 38,
    "Хабаровск": 90,
    "Владивосток": 28,
    "Тюмень": 88,
    "Набережные Челны": 81,
    "Ульяновск": 227,
    "Пенза": 228,
    "Оренбург": 229,
    "Тольятти": 221,
    "Балашиха": 3,
    "Красноdar": 35,
}


@dataclass
class RouteRate:
    from_city: str
    to_city: str
    price_rub: Optional[int]
    distance_km: Optional[int]
    price_per_km: Optional[float]
    loads_count: int = 0
    source: str = "ati.su"
    raw_prices: list[int] = field(default_factory=list)


def _load_cookies() -> dict[str, str]:
    """Загружает cookies из ati_state.json."""
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"Cookie файл не найден: {STATE_FILE}\n"
            "Запусти: uv run python scripts/ati_refresh_cookies.py"
        )
    with open(STATE_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {c["name"]: c["value"] for c in raw}
    if isinstance(raw, dict) and "cookies" in raw:
        return {c["name"]: c["value"] for c in raw["cookies"]}
    raise ValueError(f"Неизвестный формат {STATE_FILE}")


async def _get_city_id(
    client: httpx.AsyncClient,
    city_name: str,
) -> Optional[int]:
    """Получить ATI city_id по названию города через автодополнение."""
    for key, val in _CITY_ID_CACHE.items():
        if key.lower() == city_name.lower():
            return val
    try:
        r = await client.post(
            "https://loads.ati.su/gw/gis-dict/v1/autocomplete/suggestions",
            json={"prefix": city_name, "suggestion_types": 15, "limit": 5},
        )
        if r.status_code != 200:
            logger.warning("Autocomplete %s: %s", city_name, r.status_code)
            return None
        for s in r.json().get("suggestions", []):
            city = s.get("city", {})
            city_id = city.get("id")
            if city_id:
                _CITY_ID_CACHE[city_name] = city_id
                logger.debug("City %s → id=%s", city_name, city_id)
                return city_id
    except Exception as e:
        logger.warning("Autocomplete error %s: %s", city_name, e)
    return None


def _extract_price(rate: dict) -> Optional[int]:
    """Извлечь цену из объекта rate."""
    for field_name in ("priceNds", "priceNoNds", "price"):
        val = rate.get(field_name)
        if val and isinstance(val, (int, float)) and val > 0:
            return int(val)
    return None


async def get_route_rate(
    from_city: str,
    to_city: str,
    max_loads: int = 20,
) -> Optional[RouteRate]:
    """
    Получить рыночную ставку по маршруту через ATI.SU API.

    Args:
        from_city: Город отправления (по-русски)
        to_city: Город назначения (по-русски)
        max_loads: Сколько грузов взять для расчёта медианы

    Returns:
        RouteRate с медианной ценой, или None
    """
    try:
        cookies = _load_cookies()
    except (FileNotFoundError, ValueError) as e:
        logger.error("Cookie error: %s", e)
        return None

    async with httpx.AsyncClient(
        headers=HEADERS,
        cookies=cookies,
        timeout=30,
        follow_redirects=True,
    ) as client:
        from_id, to_id = await asyncio.gather(
            _get_city_id(client, from_city),
            _get_city_id(client, to_city),
        )

        if from_id is None or to_id is None:
            logger.warning(
                "City IDs not found: %s=%s, %s=%s",
                from_city, from_id, to_city, to_id,
            )
            return None

        payload = {
            "exclude_geo_dicts": True,
            "page": 1,
            "items_per_page": max_loads,
            "filter": {
                "from": {"id": from_id, "type": 2, "exact_only": False},
                "to":   {"id": to_id,   "type": 2, "exact_only": False},
                "dates": {"date_option": "today-plus"},
                "extra_params": 0,
                "exclude_tenders": False,
                "sorting_type": 2,
            },
        }

        try:
            r = await client.post(
                "https://loads.ati.su/webapi/v1.0/loads/search",
                json=payload,
            )
        except httpx.HTTPError as e:
            logger.error("ATI search failed: %s", e)
            return None

        if r.status_code != 200:
            logger.warning("ATI search status %s for %s→%s", r.status_code, from_city, to_city)
            return None

        data = r.json()

        if not data.get("isUserAuthorized"):
            logger.error("ATI session expired. Run: uv run python scripts/ati_refresh_cookies.py")
            return None

        loads = data.get("loads", [])
        total_items = data.get("totalItems", 0)
        logger.info(
            "ATI %s→%s: total=%s returned=%s",
            from_city, to_city, total_items, len(loads),
        )

        if not loads:
            return None

        prices: list[int] = []
        distance_km: Optional[int] = None

        for load in loads:
            # Цена
            rate = load.get("rate", {})
            if isinstance(rate, dict):
                price = _extract_price(rate)
                if price and 5_000 <= price <= 10_000_000:
                    prices.append(price)

            # Расстояние (из первого груза с ненулевым значением)
            if distance_km is None:
                route = load.get("route", {})
                dist = route.get("distance") or route.get("totalDistance")
                if dist and isinstance(dist, (int, float)) and dist > 0:
                    distance_km = int(dist)

        if not prices:
            logger.info(
                "No prices in %d loads for %s→%s", len(loads), from_city, to_city
            )
            return None

        sorted_prices = sorted(prices)
        median_price = sorted_prices[len(sorted_prices) // 2]

        rate_result = RouteRate(
            from_city=from_city,
            to_city=to_city,
            price_rub=median_price,
            distance_km=distance_km,
            price_per_km=round(median_price / distance_km, 1) if distance_km else None,
            loads_count=len(prices),
            raw_prices=sorted_prices,
        )
        logger.info(
            "ATI rate %s→%s: %s ₽ median (n=%d, range=%s–%s ₽)",
            from_city, to_city, median_price,
            len(prices), min(prices), max(prices),
        )
        return rate_result


async def get_route_rate_cached(
    from_city: str,
    to_city: str,
    timeout: float = 8.0,
) -> Optional[RouteRate]:
    """
    Получить рыночную ставку с кэшем (TTL 30 мин) и таймаутом.
    Безопасен для вызова из обработчиков бота — никогда не бросает исключений.
    """
    import time
    key = (from_city.strip(), to_city.strip())
    now = time.monotonic()
    cached = _RATE_CACHE.get(key)
    if cached:
        rate, ts = cached
        if now - ts < _CACHE_TTL:
            logger.debug("ATI cache hit: %s→%s", *key)
            return rate

    try:
        rate = await asyncio.wait_for(get_route_rate(from_city, to_city), timeout=timeout)
        _RATE_CACHE[key] = (rate, now)
        return rate
    except asyncio.TimeoutError:
        logger.warning("ATI rate timeout for %s→%s", *key)
    except Exception as e:
        logger.warning("ATI rate error for %s→%s: %s", *key, e)
    return None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    test_routes = [
        ("Самара", "Казань"),
        ("Москва", "Казань"),
        ("Самара", "Москва"),
        ("Москва", "Екатеринбург"),
    ]

    for from_city, to_city in test_routes:
        rate = await get_route_rate(from_city, to_city)
        if rate:
            price_str = f"{rate.price_rub:,}".replace(",", " ")
            per_km = f" ({rate.price_per_km} ₽/км)" if rate.price_per_km else ""
            km = f" {rate.distance_km} км" if rate.distance_km else ""
            rng = f"[{min(rate.raw_prices):,}–{max(rate.raw_prices):,} ₽, {rate.loads_count} грузов]"
            print(f"✅ {rate.from_city} → {rate.to_city}:{km} {price_str} ₽{per_km} {rng}")
        else:
            print(f"❌ {from_city} → {to_city}: нет данных")
        await asyncio.sleep(random.uniform(2, 3))


if __name__ == "__main__":
    asyncio.run(main())
