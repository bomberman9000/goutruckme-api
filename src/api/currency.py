"""Currency conversion API."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.core.services.currency import convert, fetch_rates, SUPPORTED

router = APIRouter(prefix="/api/v1/currency", tags=["currency"])


class ConvertResponse(BaseModel):
    amount_rub: float
    converted: float
    currency: str
    symbol: str
    rate: float
    display: str


class RatesResponse(BaseModel):
    rates: dict[str, float]
    currencies: dict[str, dict]


@router.get("/rates", response_model=RatesResponse)
async def get_rates():
    """Current CBR exchange rates."""
    rates = await fetch_rates()
    return RatesResponse(rates=rates, currencies=SUPPORTED)


@router.get("/convert", response_model=ConvertResponse)
async def convert_amount(
    amount_rub: float = Query(..., ge=0),
    to: str = Query(default="USD"),
):
    """Convert RUB to target currency."""
    result = await convert(amount_rub, to)
    if "error" in result:
        return ConvertResponse(
            amount_rub=amount_rub, converted=0, currency=to.upper(),
            symbol="?", rate=0, display="N/A",
        )
    return ConvertResponse(**result)
