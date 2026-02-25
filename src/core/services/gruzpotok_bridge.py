"""Bridge to gruzpotok-api — calls remote endpoints for route calculation,
INN/phone verification, company trust, and document generation.

All methods are async, use internal token auth, and fail gracefully
(return None on errors) so the bot/feed never breaks if the remote
service is down.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15


def _base_url() -> str:
    return (settings.gruzpotok_public_url or settings.gruzpotok_api_internal_url or "").rstrip("/")


def _headers() -> dict[str, str]:
    token = (settings.internal_token or settings.internal_api_token or "").strip()
    h: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        h["X-Internal-Token"] = token
    return h


# ─── Route calculation ───────────────────────────────────────────────

async def calc_route(from_city: str, to_city: str) -> dict[str, Any] | None:
    """Calculate exact road distance between two cities via gruzpotok-api.

    Returns dict with distance_km, from/to city info, or None on failure.
    First resolves city names to IDs via /api/geo/cities, then calls /api/route/calc.
    """
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            from_id = await _resolve_city_id(client, base, from_city)
            to_id = await _resolve_city_id(client, base, to_city)
            if not from_id or not to_id:
                return None

            resp = await client.post(
                f"{base}/api/route/calc",
                headers=_headers(),
                json={"from_city_id": from_id, "to_city_id": to_id},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            logger.info("bridge.route %s→%s = %s km", from_city, to_city, data.get("distance_km"))
            return data
    except Exception as exc:
        logger.warning("bridge.route failed: %s", exc)
        return None


async def _resolve_city_id(client: httpx.AsyncClient, base: str, city_name: str) -> int | None:
    try:
        resp = await client.get(f"{base}/api/geo/cities", params={"q": city_name.strip()})
        if resp.status_code != 200:
            return None
        cities = resp.json()
        if isinstance(cities, list) and cities:
            return cities[0].get("id")
    except Exception:
        pass
    return None


# ─── INN verification ────────────────────────────────────────────────

async def verify_inn(inn: str) -> dict[str, Any] | None:
    """Verify INN via gruzpotok-api AI module.

    Returns dict with valid, type, status, checks.
    """
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/ai/verify/inn",
                params={"inn": inn.strip()},
                headers=_headers(),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            logger.info("bridge.inn %s → valid=%s", inn, data.get("valid"))
            return data
    except Exception as exc:
        logger.warning("bridge.inn failed: %s", exc)
        return None


# ─── Phone verification ──────────────────────────────────────────────

async def verify_phone(phone: str) -> dict[str, Any] | None:
    """Verify phone number via gruzpotok-api AI module.

    Returns dict with valid, country, operator, formatted.
    """
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/ai/verify/phone",
                params={"phone": phone.strip()},
                headers=_headers(),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            logger.info("bridge.phone %s → valid=%s operator=%s", phone, data.get("valid"), data.get("operator"))
            return data
    except Exception as exc:
        logger.warning("bridge.phone failed: %s", exc)
        return None


# ─── Company trust ────────────────────────────────────────────────────

async def get_company_trust(company_id: int) -> dict[str, Any] | None:
    """Get company trust rating from gruzpotok-api."""
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/api/companies/{company_id}/trust",
                headers=_headers(),
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:
        logger.warning("bridge.trust failed: %s", exc)
        return None


# ─── Full INN report (blacklist + verification) ──────────────────────

async def full_inn_report(inn: str) -> dict[str, Any] | None:
    """Get full verification report for an INN."""
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/ai/verify/full-report",
                headers=_headers(),
                json={"inn": inn.strip()},
            )
            if resp.status_code != 200:
                resp2 = await client.post(
                    f"{base}/ai/verify/contractor",
                    headers=_headers(),
                    json={"inn": inn.strip()},
                )
                if resp2.status_code == 200:
                    return resp2.json()
                return None
            return resp.json()
    except Exception as exc:
        logger.warning("bridge.full_report failed: %s", exc)
        return None


# ─── Market rates (AI-powered) ───────────────────────────────────────

async def get_market_rates(from_city: str, to_city: str, weight: float = 20.0) -> dict[str, Any] | None:
    """Get AI-powered market rate estimation."""
    base = _base_url()
    if not base:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/ai/analytics/market-rates",
                headers=_headers(),
                json={
                    "from_city": from_city.strip(),
                    "to_city": to_city.strip(),
                    "weight_tons": weight,
                },
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:
        logger.warning("bridge.market_rates failed: %s", exc)
        return None
