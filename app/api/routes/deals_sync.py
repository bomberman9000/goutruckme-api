"""
API синхронизации сделок с фронта (localStorage → сервер).

Схема: id (PK) = server_id, local_id (unique), payload (JSON), created_at, updated_at.
Защита: X-Client-Key (или Authorization Bearer).
"""

from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

from app.db.database import get_db
from app.models.models import DealSync

router = APIRouter()


def verify_client_sync_key(
    x_client_key: Optional[str] = Header(default=None, alias="X-Client-Key"),
):
    """Проверка X-Client-Key для доступа к deals-sync (минимум защиты)."""
    from app.core.config import get_settings
    settings = get_settings()
    key = getattr(settings, "CLIENT_SYNC_KEY", None) or ""
    if not key:
        return
    if not x_client_key or x_client_key.strip() != key.strip():
        raise HTTPException(status_code=401, detail="Invalid or missing X-Client-Key")


class DealSyncCreate(BaseModel):
    local_id: str
    payload: dict  # весь объект Deal с фронта


class DealSyncUpdate(BaseModel):
    payload: dict  # обновлённый объект Deal


@router.get("/deals-sync", response_model=List[dict])
def list_deals_sync(
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
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
    _: None = Depends(verify_client_sync_key),
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
    _: None = Depends(verify_client_sync_key),
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
    return {
        "server_id": row.id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/deals-sync/{server_id}", response_model=dict)
def get_deal_sync(
    server_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
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
