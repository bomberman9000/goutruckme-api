"""Bridge API — proxy endpoints that call gruzpotok-api and return
enriched data to the TWA/bot.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core.services.gruzpotok_bridge import (
    calc_route,
    verify_inn,
    verify_phone,
    get_market_rates,
)

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])


class RouteResponse(BaseModel):
    distance_km: int | None = None
    from_city: str | None = None
    to_city: str | None = None
    source: str | None = None


class InnResponse(BaseModel):
    inn: str
    valid: bool | None = None
    type: str | None = None
    status: str | None = None
    message: str | None = None
    ati_link: str | None = None


class PhoneResponse(BaseModel):
    phone: str
    valid: bool | None = None
    country: str | None = None
    operator: str | None = None
    formatted: str | None = None


class MarketRateResponse(BaseModel):
    from_city: str
    to_city: str
    data: dict | None = None
    available: bool = False


@router.get("/route", response_model=RouteResponse)
async def bridge_route(
    from_city: str = Query(..., min_length=1),
    to_city: str = Query(..., min_length=1),
):
    """Calculate exact road distance via gruzpotok-api."""
    data = await calc_route(from_city, to_city)
    if not data:
        return RouteResponse(from_city=from_city, to_city=to_city)

    return RouteResponse(
        distance_km=data.get("distance_km"),
        from_city=data.get("from", {}).get("city_name", from_city),
        to_city=data.get("to", {}).get("city_name", to_city),
        source=data.get("source"),
    )


@router.get("/verify-inn", response_model=InnResponse)
async def bridge_verify_inn(inn: str = Query(..., min_length=10, max_length=12)):
    """Verify INN via gruzpotok-api + generate ATI link."""
    data = await verify_inn(inn)
    return InnResponse(
        inn=inn,
        valid=data.get("valid") if data else None,
        type=data.get("type") if data else None,
        status=data.get("status") if data else None,
        message=data.get("message") if data else None,
        ati_link=f"https://ati.su/firms?inn={inn}",
    )


@router.get("/verify-phone", response_model=PhoneResponse)
async def bridge_verify_phone(phone: str = Query(..., min_length=5)):
    """Verify phone via gruzpotok-api."""
    data = await verify_phone(phone)
    return PhoneResponse(
        phone=phone,
        valid=data.get("valid") if data else None,
        country=data.get("country") if data else None,
        operator=data.get("operator") if data else None,
        formatted=data.get("formatted") if data else None,
    )


@router.get("/market-rate", response_model=MarketRateResponse)
async def bridge_market_rate(
    from_city: str = Query(..., min_length=1),
    to_city: str = Query(..., min_length=1),
    weight: float = Query(default=20.0, ge=0.1, le=100),
):
    """Get AI-powered market rate estimation."""
    data = await get_market_rates(from_city, to_city, weight)
    return MarketRateResponse(
        from_city=from_city,
        to_city=to_city,
        data=data,
        available=data is not None,
    )
