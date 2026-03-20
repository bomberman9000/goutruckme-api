from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.services.route_calc import calculate_route_distance_km, resolve_city_point

router = APIRouter()


class RouteCalcRequest(BaseModel):
    from_city_id: Optional[int] = Field(default=None, ge=1)
    to_city_id: Optional[int] = Field(default=None, ge=1)
    from_address: Optional[str] = None
    to_address: Optional[str] = None


@router.post("/route/calc")
@router.post("/route/calcLogic")
def calculate_route(
    payload: RouteCalcRequest,
    db: Session = Depends(get_db),
) -> dict:
    if not payload.from_city_id or not payload.to_city_id:
        raise HTTPException(status_code=422, detail="Для расчёта нужны from_city_id и to_city_id")

    from_point = resolve_city_point(db, int(payload.from_city_id))
    if not from_point:
        raise HTTPException(status_code=422, detail="Не удалось определить координаты города отправления")

    to_point = resolve_city_point(db, int(payload.to_city_id))
    if not to_point:
        raise HTTPException(status_code=422, detail="Не удалось определить координаты города назначения")

    distance_km = calculate_route_distance_km(from_point=from_point, to_point=to_point)
    if distance_km <= 0:
        raise HTTPException(status_code=422, detail="Не удалось рассчитать расстояние для выбранного маршрута")

    return {
        "distance_km": distance_km,
        "source": "cities",
        "from": {
            "city_id": from_point.city_id,
            "city_name": from_point.city_name,
            "lat": from_point.lat,
            "lon": from_point.lon,
        },
        "to": {
            "city_id": to_point.city_id,
            "city_name": to_point.city_name,
            "lat": to_point.lat,
            "lon": to_point.lon,
        },
    }
