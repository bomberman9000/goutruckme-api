"""
API синхронизации сделок с фронта (localStorage → сервер).

Схема: id (PK) = server_id, local_id (unique), payload (JSON), created_at, updated_at.
Защита: X-Client-Key (или Authorization Bearer).
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import get_settings
from app.core.security import ALGORITHM, SECRET_KEY
from app.db.database import get_db
from app.models.models import DealSync, Load, User
from app.trust.service import recalc_company_trust

router = APIRouter()


def _safe_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_company_ids_from_payload(payload: dict | None, db: Session) -> set[int]:
    if not isinstance(payload, dict):
        return set()

    ids: set[int] = set()
    for key in (
        "user_id",
        "userId",
        "shipper_id",
        "shipperId",
        "client_id",
        "clientId",
        "carrier_id",
        "carrierId",
        "counterparty_id",
        "counterpartyId",
        "owner_id",
        "ownerId",
    ):
        maybe_id = _safe_int(payload.get(key))
        if maybe_id is not None:
            ids.add(maybe_id)

    for key in ("shipper", "client", "carrier", "counterparty", "owner", "user"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_key in ("id", "user_id", "userId"):
                maybe_id = _safe_int(nested.get(nested_key))
                if maybe_id is not None:
                    ids.add(maybe_id)

    cargo_id = None
    for key in ("cargo_id", "cargoId", "load_id", "loadId"):
        maybe_id = _safe_int(payload.get(key))
        if maybe_id is not None:
            cargo_id = maybe_id
            break

    if cargo_id is None and isinstance(payload.get("cargoSnapshot"), dict):
        cargo_id = _safe_int(payload["cargoSnapshot"].get("id"))

    if cargo_id is not None:
        owner_id = db.query(Load.user_id).filter(Load.id == cargo_id).scalar()
        owner_id = _safe_int(owner_id)
        if owner_id is not None:
            ids.add(owner_id)

    return ids


def _recalc_trust_safely(db: Session, payload: dict | None) -> None:
    company_ids = _extract_company_ids_from_payload(payload, db)
    for company_id in company_ids:
        try:
            recalc_company_trust(db, int(company_id))
        except Exception as e:
            logger.warning("recalc_company_trust failed for company_id=%s: %s", company_id, e)


def _get_request_user(
    authorization: Optional[str],
    db: Session,
) -> User | None:
    if not authorization:
        return None
    try:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub") or payload.get("id")
        if user_id is None:
            return None
        return db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        return None


def require_sync_access(
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_client_key: Optional[str] = Header(default=None, alias="X-Client-Key"),
):
    """Доступ к deals-sync только по JWT или service-to-service key."""
    settings = get_settings()
    key = getattr(settings, "CLIENT_SYNC_KEY", None) or ""
    if key and x_client_key and x_client_key.strip() == key.strip():
        return {"mode": "client_key"}

    user = _get_request_user(authorization, db)
    if user:
        return {"mode": "user", "user_id": int(user.id)}

    raise HTTPException(status_code=401, detail="Необходима авторизация")


class DealSyncCreate(BaseModel):
    local_id: str
    payload: dict  # весь объект Deal с фронта


class DealSyncUpdate(BaseModel):
    payload: dict  # обновлённый объект Deal


@router.get("/deals-sync", response_model=List[dict])
def list_deals_sync(
    db: Session = Depends(get_db),
    _: dict = Depends(require_sync_access),
):
    """Список всех синхронизированных сделок. server_id = id в БД."""
    rows = db.query(DealSync).order_by(DealSync.updated_at.desc()).all()
    iso = lambda d: d.isoformat() if d else ""
    return [
        {
            "server_id": r.id,
            "local_id": r.local_id,
            "payload": r.payload,
            "created_at": iso(r.created_at),
            "updated_at": iso(r.updated_at),
        }
        for r in rows
    ]


@router.post("/deals-sync", response_model=dict)
def create_deal_sync(
    body: DealSyncCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: dict = Depends(require_sync_access),
):
    """Upsert по local_id. Возвращает только {server_id, updated_at}. Запускает модерацию в фоне."""
    existing = db.query(DealSync).filter(DealSync.local_id == body.local_id).first()
    if existing:
        existing.payload = body.payload
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        server_id = existing.id
        out_row = existing
    else:
        row = DealSync(local_id=body.local_id, payload=body.payload)
        db.add(row)
        db.commit()
        db.refresh(row)
        server_id = row.id
        out_row = row
    from app.moderation.service import run_deal_review_background, set_review_pending
    set_review_pending(db, "deal", server_id)
    background_tasks.add_task(run_deal_review_background, server_id)
    _recalc_trust_safely(db, body.payload)
    return {
        "server_id": server_id,
        "updated_at": out_row.updated_at.isoformat() if getattr(out_row, "updated_at", None) else "",
    }


@router.patch("/deals-sync/{server_id}", response_model=dict)
def update_deal_sync(
    server_id: int,
    body: DealSyncUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: dict = Depends(require_sync_access),
):
    """Обновить сделку по server_id (id в БД). Перезапускает модерацию в фоне."""
    row = db.query(DealSync).filter(DealSync.id == server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    row.payload = body.payload
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    from app.moderation.service import run_deal_review_background, set_review_pending
    set_review_pending(db, "deal", server_id)
    background_tasks.add_task(run_deal_review_background, server_id)
    _recalc_trust_safely(db, body.payload)
    return {
        "server_id": row.id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/deals-sync/{server_id}", response_model=dict)
def get_deal_sync(
    server_id: int,
    db: Session = Depends(get_db),
    _: dict = Depends(require_sync_access),
):
    """Получить одну сделку по server_id (id в БД)."""
    row = db.query(DealSync).filter(DealSync.id == server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found")
    iso = lambda d: d.isoformat() if d else ""
    return {
        "server_id": row.id,
        "local_id": row.local_id,
        "payload": row.payload,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }
