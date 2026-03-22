"""Public platform stats endpoint — no auth required."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.models.models import Load, User, Vehicle, CargoStatus

router = APIRouter()

_cache: dict = {}
_CACHE_TTL = 120  # seconds


@router.get("/public-stats")
def public_stats(db: Session = Depends(get_db)):
    """Live platform counters for landing page."""
    now = datetime.utcnow()
    if _cache.get("ts") and (now - _cache["ts"]).total_seconds() < _CACHE_TTL:
        return _cache["data"]

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    active_loads = db.query(func.count(Load.id)).filter(
        Load.status == CargoStatus.active.value
    ).scalar() or 0

    loads_today = db.query(func.count(Load.id)).filter(
        Load.created_at >= today_start
    ).scalar() or 0

    total_companies = db.query(func.count(User.id)).scalar() or 0

    active_vehicles = db.query(func.count(Vehicle.id)).filter(
        Vehicle.available_from != None
    ).scalar() or 0

    routes_count = db.query(
        func.count(func.distinct(Load.from_city + '-' + Load.to_city))
    ).scalar() or 0

    data = {
        "active_loads": active_loads,
        "loads_today": loads_today,
        "total_companies": total_companies,
        "active_vehicles": active_vehicles,
        "routes_count": routes_count,
    }
    _cache["data"] = data
    _cache["ts"] = now
    return data
