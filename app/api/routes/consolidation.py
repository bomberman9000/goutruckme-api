from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.consolidation.service import build_plans, serialize_plan
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import ConsolidationPlan, User, UserRole, Vehicle

router = APIRouter()


class ConsolidationBuildRequest(BaseModel):
    max_stops: int | None = Field(default=None, ge=1, le=8)
    radius_km: float | None = Field(default=None, ge=10, le=1500)
    max_detour_km: float | None = Field(default=None, ge=1, le=1000)
    top_k: int | None = Field(default=None, ge=1, le=200)
    variants: int | None = Field(default=None, ge=1, le=30)
    profile_overrides: dict[str, Any] | None = None


@router.post("/consolidation/build/{vehicle_id}")
def build_consolidation_plans(
    vehicle_id: int,
    payload: ConsolidationBuildRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина не найдена")

    is_admin = current_user.role == UserRole.admin
    if not is_admin and vehicle.carrier_id != current_user.id:
        raise HTTPException(status_code=403, detail="Недостаточно прав для сборки по этой машине")

    try:
        result = build_plans(
            db,
            vehicle_id=vehicle_id,
            max_stops=payload.max_stops,
            radius_km=payload.radius_km,
            max_detour_km=payload.max_detour_km,
            top_k=payload.top_k,
            variants=payload.variants,
            profile_overrides=payload.profile_overrides,
            created_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


@router.post("/consolidation/confirm/{plan_id}")
def confirm_consolidation_plan(
    plan_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = db.query(ConsolidationPlan).filter(ConsolidationPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="План консолидации не найден")

    vehicle = db.query(Vehicle).filter(Vehicle.id == plan.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Машина плана не найдена")

    is_admin = current_user.role == UserRole.admin
    if not is_admin and plan.created_by != current_user.id and vehicle.carrier_id != current_user.id:
        raise HTTPException(status_code=403, detail="Недостаточно прав для подтверждения плана")

    plan.status = "confirmed"
    db.commit()
    db.refresh(plan)

    return {
        "ok": True,
        "plan": serialize_plan(db, plan),
    }
