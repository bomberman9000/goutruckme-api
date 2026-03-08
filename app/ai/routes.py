from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.ai.scoring import MarketStats, compute_ai_score
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import Load, User
from app.services.cargo_status import apply_cargo_status_filter, expire_outdated_cargos


router = APIRouter()


def _safe_created_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.min
    return datetime.min


@router.get("/ai/best-loads", response_model=list[dict])
def get_best_loads(
    limit: int = Query(default=3, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expire_outdated_cargos(db)
    loads = (
        apply_cargo_status_filter(db.query(Load), "active")
        .order_by(Load.created_at.desc())
        .limit(500)
        .all()
    )
    if not loads:
        return []

    stats = MarketStats.from_db(db, lookback_days=60)
    scored_rows: list[dict[str, Any]] = []

    for load in loads:
        score = compute_ai_score(load, stats)
        route = f"{load.from_city} → {load.to_city}"
        scored_rows.append(
            {
                "id": load.id,
                "route": route,
                "from_city": load.from_city,
                "to_city": load.to_city,
                "price": load.price,
                "distance_km": score.get("distance_km"),
                "rate_per_km": score.get("rate_per_km"),
                "ai_risk": score.get("ai_risk", "low"),
                "ai_score": score.get("ai_score", 0),
                "ai_explain": score.get("ai_explain") or "",
                "ai_flags": score.get("ai_flags") or [],
                "created_at": load.created_at.isoformat() if load.created_at else None,
            }
        )

    scored_rows.sort(
        key=lambda row: (
            int(row.get("ai_score") or 0),
            _safe_created_at(row.get("created_at")),
        ),
        reverse=True,
    )
    return scored_rows[:limit]
