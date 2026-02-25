"""Price prediction engine — trend analysis + 7-day forecast.

Uses linear regression on the last 30 days of parser data to
predict rate direction. No external ML libs — pure math.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func

from src.core.database import async_session
from src.core.models import ParserIngestEvent

logger = logging.getLogger(__name__)


def _linear_regression(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Simple OLS: returns (slope, intercept). slope > 0 means price rising."""
    n = len(points)
    if n < 2:
        return 0.0, 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return 0.0, sy / n if n else 0.0
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


async def predict_route_price(
    from_city: str,
    to_city: str,
    days_history: int = 30,
    days_forecast: int = 7,
) -> dict[str, Any]:
    """Predict price trend for a route.

    Returns current avg, predicted avg in N days, trend direction,
    percent change, daily data points, and recommendation.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_history)

    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    func.date(ParserIngestEvent.created_at).label("day"),
                    func.avg(ParserIngestEvent.rate_rub).label("avg_rate"),
                    func.count().label("cnt"),
                )
                .where(
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.rate_rub.isnot(None),
                    ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"),
                    ParserIngestEvent.to_city.ilike(f"%{to_city.strip()}%"),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .group_by(func.date(ParserIngestEvent.created_at))
                .order_by(func.date(ParserIngestEvent.created_at))
            )
        ).all()

    if not rows:
        return {
            "from_city": from_city,
            "to_city": to_city,
            "available": False,
            "reason": "no_data",
        }

    points = [(float(i), float(r.avg_rate)) for i, r in enumerate(rows)]
    slope, intercept = _linear_regression(points)

    current_avg = int(points[-1][1])
    last_x = points[-1][0]
    predicted_avg = int(slope * (last_x + days_forecast) + intercept)
    predicted_avg = max(0, predicted_avg)

    if current_avg > 0:
        pct_change = round((predicted_avg - current_avg) / current_avg * 100, 1)
    else:
        pct_change = 0.0

    if pct_change > 5:
        trend = "rising"
        recommendation = f"Ставки растут (+{pct_change}%). Рекомендуем фиксировать цену сейчас."
    elif pct_change < -5:
        trend = "falling"
        recommendation = f"Ставки падают ({pct_change}%). Можно подождать лучшего предложения."
    else:
        trend = "stable"
        recommendation = "Ставки стабильны. Хорошее время для планирования."

    daily = [
        {"date": str(r.day), "avg_rate": int(r.avg_rate), "count": r.cnt}
        for r in rows
    ]

    return {
        "from_city": from_city,
        "to_city": to_city,
        "available": True,
        "current_avg": current_avg,
        "predicted_avg": predicted_avg,
        "days_forecast": days_forecast,
        "pct_change": pct_change,
        "trend": trend,
        "recommendation": recommendation,
        "slope_per_day": round(slope, 1),
        "data_points": len(rows),
        "daily": daily,
    }
