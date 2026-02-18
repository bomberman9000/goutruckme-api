"""
API модерации: список отзывов, получение по entity, принудительный перезапуск проверки.
Защита: X-Client-Key.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import ModerationReview
from app.moderation.service import (
    get_review,
    list_reviews,
    run_deal_review_background,
    run_document_review_background,
    set_review_pending,
    upsert_review,
)

router = APIRouter()


def verify_client_sync_key(
    x_client_key: Optional[str] = Header(default=None, alias="X-Client-Key"),
):
    from app.core.config import get_settings
    s = get_settings()
    key = getattr(s, "CLIENT_SYNC_KEY", None) or ""
    if not key:
        return
    if not x_client_key or x_client_key.strip() != key.strip():
        raise HTTPException(status_code=401, detail="Invalid or missing X-Client-Key")


def _review_to_dict(r: ModerationReview) -> dict:
    return {
        "id": r.id,
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "status": r.status,
        "risk_level": r.risk_level,
        "flags": r.flags,
        "comment": r.comment,
        "recommended_action": r.recommended_action,
        "model_used": r.model_used,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/moderation", response_model=List[dict])
def moderation_list(
    entity_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    """Список отзывов модерации с фильтрами."""
    rows = list_reviews(
        db,
        risk_level=risk_level,
        entity_type=entity_type,
        search=q,
        limit=limit,
        offset=offset,
    )
    return [_review_to_dict(r) for r in rows]


@router.get("/moderation/{entity_type}/{entity_id}", response_model=dict)
def moderation_get(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    """Получить отзыв модерации по типу и id сущности."""
    if entity_type not in ("deal", "document"):
        raise HTTPException(status_code=400, detail="entity_type must be 'deal' or 'document'")
    row = get_review(db, entity_type, entity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")
    return _review_to_dict(row)


@router.post("/moderation/{entity_type}/{entity_id}/run", response_model=dict)
def moderation_run(
    entity_type: str,
    entity_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    """Запустить модерацию для сущности (и вернуть результат после выполнения)."""
    if entity_type not in ("deal", "document"):
        raise HTTPException(status_code=400, detail="entity_type must be 'deal' or 'document'")
    set_review_pending(db, entity_type, entity_id)
    if entity_type == "deal":
        background_tasks.add_task(run_deal_review_background, entity_id)
    else:
        background_tasks.add_task(run_document_review_background, entity_id)
    row = get_review(db, entity_type, entity_id)
    return _review_to_dict(row) if row else {"entity_type": entity_type, "entity_id": entity_id, "status": "pending"}
