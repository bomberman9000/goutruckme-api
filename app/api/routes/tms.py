"""
TMS (Transport Management System) API.
Auth: X-Api-Key header.
Rate limits: Free=100/day, Pro=1000/day, Business=unlimited.

Key management:
  GET    /api/tms/keys         — list user keys
  POST   /api/tms/keys         — create key
  DELETE /api/tms/keys/{id}    — revoke key

TMS endpoints (X-Api-Key):
  GET  /tms/cargos             — list active cargos
  POST /tms/cargos             — create cargo
  GET  /tms/trucks             — list available vehicles
  GET  /tms/tenders            — list tenders
  GET  /tms/ping               — check key validity
"""
import logging
import secrets
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import ApiKey, User, Load, Vehicle
from app.core.security import SECRET_KEY, ALGORITHM
from jose import jwt

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Plan limits (calls/day) ───────────────────────────────────────────────────
PLAN_LIMITS = {"free": 100, "pro": 1000, "business": None}  # None = unlimited


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _user_from_token(authorization: Optional[str], db: Session) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1].strip(), SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub") or payload.get("id")
        return db.query(User).filter(User.id == int(uid), User.is_active == True).first() if uid else None
    except Exception:
        return None


def _require_user(authorization: Optional[str], db: Session) -> User:
    u = _user_from_token(authorization, db)
    if not u:
        raise HTTPException(401, "Требуется авторизация")
    return u


def _resolve_api_key(x_api_key: Optional[str], db: Session) -> ApiKey:
    if not x_api_key:
        raise HTTPException(401, detail="Укажите X-Api-Key заголовок")
    ak = db.query(ApiKey).filter(ApiKey.key == x_api_key, ApiKey.is_active == True).first()
    if not ak:
        raise HTTPException(401, detail="Неверный или отозванный API ключ")
    # Rate limit
    today = date.today()
    if ak.reset_date is None or ak.reset_date.date() < today:
        ak.calls_today = 0
        ak.reset_date  = datetime.utcnow()
    limit = PLAN_LIMITS.get(ak.plan or "free")
    if limit is not None and ak.calls_today >= limit:
        raise HTTPException(429, detail=f"Превышен лимит запросов ({limit}/день). Перейдите на план Pro/Business.")
    ak.calls_today += 1
    ak.last_used    = datetime.utcnow()
    db.commit()
    return ak


# ══════════════════════════════════════════════════════════════════════════════
# Key management (JWT auth)
# ══════════════════════════════════════════════════════════════════════════════

class KeyCreate(BaseModel):
    name: str = "Мой ключ"


def _key_out(ak: ApiKey) -> dict:
    limit = PLAN_LIMITS.get(ak.plan or "free")
    return {
        "id":         ak.id,
        "key":        ak.key,
        "name":       ak.name,
        "plan":       ak.plan,
        "is_active":  ak.is_active,
        "calls_today": ak.calls_today,
        "limit_day":  limit,
        "last_used":  ak.last_used.isoformat() if ak.last_used else None,
        "created_at": ak.created_at.isoformat() if ak.created_at else None,
    }


@router.get("/tms/keys")
def list_keys(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    keys = db.query(ApiKey).filter(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc()).all()
    return {"keys": [_key_out(k) for k in keys]}


@router.post("/tms/keys", status_code=201)
def create_key(
    body: KeyCreate,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    existing = db.query(ApiKey).filter(ApiKey.user_id == user.id, ApiKey.is_active == True).count()
    if existing >= 5:
        raise HTTPException(400, "Максимум 5 активных ключей")
    ak = ApiKey(
        user_id=user.id,
        key=secrets.token_urlsafe(32),
        name=body.name[:128],
        plan="free",
    )
    db.add(ak)
    db.commit()
    db.refresh(ak)
    return _key_out(ak)


@router.delete("/tms/keys/{key_id}", status_code=204)
def revoke_key(
    key_id: int,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    user = _require_user(authorization, db)
    ak = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == user.id).first()
    if not ak:
        raise HTTPException(404, "Ключ не найден")
    ak.is_active = False
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# TMS endpoints (X-Api-Key auth)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/tms/ping")
def tms_ping(
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    """Check key validity and remaining quota."""
    ak = _resolve_api_key(x_api_key, db)
    limit = PLAN_LIMITS.get(ak.plan or "free")
    return {
        "ok": True,
        "plan": ak.plan,
        "calls_today": ak.calls_today,
        "limit_day": limit,
        "remaining": (limit - ak.calls_today) if limit else None,
    }


@router.get("/tms/cargos")
def tms_cargos(
    from_city: Optional[str] = Query(None),
    to_city:   Optional[str] = Query(None),
    limit:     int = Query(50, ge=1, le=500),
    offset:    int = Query(0, ge=0),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    """List active cargos."""
    _resolve_api_key(x_api_key, db)
    q = db.query(Load).filter(Load.status.in_(["active", "new", "pending"]))
    if from_city:
        q = q.filter(Load.from_city.ilike(f"%{from_city}%"))
    if to_city:
        q = q.filter(Load.to_city.ilike(f"%{to_city}%"))
    total = q.count()
    items = q.order_by(Load.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "results": [_cargo_out(c) for c in items],
    }


@router.post("/tms/cargos", status_code=201)
def tms_create_cargo(
    body: dict,
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    """Create a cargo via API."""
    ak = _resolve_api_key(x_api_key, db)
    required = ("from_city", "to_city", "weight_t", "price")
    for f in required:
        if f not in body:
            raise HTTPException(422, f"Обязательное поле: {f}")
    c = Load(
        user_id=ak.user_id,
        from_city=str(body["from_city"]),
        to_city=str(body["to_city"]),
        weight_t=float(body["weight_t"]),
        price=int(body["price"]),
        body_type=body.get("body_type"),
        load_date=body.get("load_date"),
        status="active",
        source="api",
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _cargo_out(c)


@router.get("/tms/trucks")
def tms_trucks(
    city:  Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    """List available vehicles."""
    _resolve_api_key(x_api_key, db)
    q = db.query(Vehicle).filter(Vehicle.status.in_(["active", "available", "free"]))
    if city:
        q = q.filter(Vehicle.location_city.ilike(f"%{city}%"))
    total = q.count()
    items = q.order_by(Vehicle.id.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "results": [_vehicle_out(v) for v in items],
    }


@router.get("/tms/tenders")
def tms_tenders(
    from_city: Optional[str] = Query(None),
    to_city:   Optional[str] = Query(None),
    limit:     int = Query(50, ge=1, le=500),
    offset:    int = Query(0, ge=0),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    """List active tenders."""
    from app.models.models import Tender
    _resolve_api_key(x_api_key, db)
    q = db.query(Tender).filter(Tender.status == "active")
    if from_city:
        q = q.filter(Tender.from_city.ilike(f"%{from_city}%"))
    if to_city:
        q = q.filter(Tender.to_city.ilike(f"%{to_city}%"))
    total = q.count()
    items = q.order_by(Tender.created_at.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "results": [{
            "id":          t.id,
            "title":       t.title,
            "from_city":   t.from_city,
            "to_city":     t.to_city,
            "weight":      t.weight,
            "body_type":   t.body_type,
            "budget_max":  t.budget_max,
            "deadline":    t.deadline.isoformat() if t.deadline else None,
            "bids_count":  len(t.bids or []),
        } for t in items],
    }


# ── Serializers ───────────────────────────────────────────────────────────────

def _cargo_out(c: Load) -> dict:
    return {
        "id":        c.id,
        "from_city": c.from_city,
        "to_city":   c.to_city,
        "weight_t":  c.weight_t,
        "price":     c.price,
        "body_type": c.body_type,
        "load_date": str(c.load_date) if c.load_date else None,
        "status":    str(c.status) if c.status else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _vehicle_out(v: Vehicle) -> dict:
    return {
        "id":            v.id,
        "body_type":     v.body_type,
        "capacity_tons": v.capacity_tons,
        "city":          v.location_city,
        "status":        str(v.status) if v.status else None,
    }
