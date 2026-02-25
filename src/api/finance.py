"""Finance API: transactions, payments, net profit calculator."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.services.finance import (
    create_transaction,
    get_carrier_ledger,
    update_transaction_status,
)

router = APIRouter(prefix="/api/v1/finance", tags=["finance"])


class CreateTransactionRequest(BaseModel):
    feed_id: int
    amount_rub: int
    payment_terms: str | None = None
    payment_days: int = 5


class UpdateStatusRequest(BaseModel):
    status: str


class ProfitCalcRequest(BaseModel):
    rate_rub: int
    distance_km: int
    fuel_consumption_l_per_100km: float = 35.0
    fuel_price_rub: float = 60.0
    tax_percent: float = 6.0
    other_expenses_rub: int = 0


class ProfitCalcResponse(BaseModel):
    rate_rub: int
    fuel_cost: int
    tax_amount: int
    other_expenses: int
    net_profit: int
    profitability_percent: float
    rate_per_km: float


@router.post("/transactions")
async def create_txn(
    body: CreateTransactionRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    result = await create_transaction(
        feed_id=body.feed_id,
        carrier_user_id=tma_user.user_id,
        amount_rub=body.amount_rub,
        payment_terms=body.payment_terms,
        payment_days=body.payment_days,
    )
    return result


@router.patch("/transactions/{txn_id}")
async def update_txn(
    txn_id: int,
    body: UpdateStatusRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    result = await update_transaction_status(txn_id, body.status)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/ledger")
async def get_ledger(
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    return await get_carrier_ledger(tma_user.user_id)


@router.post("/profit-calc", response_model=ProfitCalcResponse)
async def calculate_profit(body: ProfitCalcRequest):
    """Net profit calculator for a trip.

    Takes rate, distance, fuel consumption, fuel price, tax rate,
    and other expenses — returns net profit and profitability %.
    """
    fuel_cost = int(body.distance_km * body.fuel_consumption_l_per_100km / 100 * body.fuel_price_rub)
    tax_amount = int(body.rate_rub * body.tax_percent / 100)
    total_expenses = fuel_cost + tax_amount + body.other_expenses_rub
    net_profit = body.rate_rub - total_expenses
    profitability = round((net_profit / body.rate_rub * 100) if body.rate_rub > 0 else 0, 1)
    rate_per_km = round(body.rate_rub / body.distance_km, 1) if body.distance_km > 0 else 0

    return ProfitCalcResponse(
        rate_rub=body.rate_rub,
        fuel_cost=fuel_cost,
        tax_amount=tax_amount,
        other_expenses=body.other_expenses_rub,
        net_profit=net_profit,
        profitability_percent=profitability,
        rate_per_km=rate_per_km,
    )
