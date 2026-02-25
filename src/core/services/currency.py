"""Currency engine — CBR rates + multi-currency converter."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_cache: dict[str, Any] = {}
_cache_ts: float = 0
CACHE_TTL = 3600

SUPPORTED = {
    "RUB": {"symbol": "₽", "name": "Рубль"},
    "USD": {"symbol": "$", "name": "Доллар"},
    "EUR": {"symbol": "€", "name": "Евро"},
    "KZT": {"symbol": "₸", "name": "Тенге"},
    "BYN": {"symbol": "Br", "name": "Бел. руб"},
    "UZS": {"symbol": "сум", "name": "Сум"},
    "CNY": {"symbol": "¥", "name": "Юань"},
    "TRY": {"symbol": "₺", "name": "Лира"},
}


async def fetch_rates() -> dict[str, float]:
    """Fetch fresh rates from CBR. Returns rates to RUB."""
    global _cache, _cache_ts

    if _cache and (time.time() - _cache_ts) < CACHE_TTL:
        return _cache

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(CBR_URL)
            resp.raise_for_status()
            data = resp.json()

        valutes = data.get("Valute", {})
        rates = {"RUB": 1.0}
        for code in SUPPORTED:
            if code == "RUB":
                continue
            v = valutes.get(code)
            if v:
                nominal = float(v.get("Nominal", 1))
                value = float(v.get("Value", 0))
                if value > 0:
                    rates[code] = value / nominal

        _cache = rates
        _cache_ts = time.time()
        logger.info("currency: fetched %d rates from CBR", len(rates))
        return rates
    except Exception as exc:
        logger.warning("currency: CBR fetch failed: %s", exc)
        return _cache or {"RUB": 1.0}


async def convert(amount_rub: float, to_currency: str) -> dict[str, Any]:
    """Convert RUB amount to target currency."""
    rates = await fetch_rates()
    to_code = to_currency.upper()
    rate = rates.get(to_code)
    if rate is None or rate == 0:
        return {"error": f"unknown currency: {to_code}"}

    converted = round(amount_rub / rate, 2) if to_code != "RUB" else amount_rub
    symbol = SUPPORTED.get(to_code, {}).get("symbol", to_code)

    return {
        "amount_rub": amount_rub,
        "converted": converted,
        "currency": to_code,
        "symbol": symbol,
        "rate": round(rate, 4),
        "display": f"{converted:,.2f} {symbol}",
    }
