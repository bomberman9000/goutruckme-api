"""
API модерации:
- legacy endpoints with X-Client-Key
- new role-based moderation endpoints for AI Moderation UI
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import DealSync, DocumentSync, ModerationReview, User
from app.moderation.engine import review_deal, review_document
from app.moderation.service import (
    get_review,
    list_reviews,
    run_deal_review_background,
    run_document_review_background,
    set_review_error,
    set_review_pending,
    upsert_review,
)
from app.trust.service import get_company_trust_snapshot, get_related_company_ids_for_review

router = APIRouter()


_ALLOWED_ENTITY_TYPES = {"deal", "document"}
_ALLOWED_PATCH_STATUSES = {"pending", "done", "error"}


def _normalize_role(role: Any) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "expeditor":
        return "forwarder"
    if value == "shipper":
        return "client"
    return value


def require_moderation_user(current_user: User = Depends(get_current_user)) -> User:
    role = _normalize_role(current_user.role)
    if role not in {"admin", "forwarder"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return current_user


def _ensure_admin_mutations_enabled() -> None:
    if not settings.ADMIN_MUTATIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Административные изменения временно отключены")


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


def _risk_rank(level: str) -> int:
    value = str(level or "").lower()
    if value == "high":
        return 3
    if value == "medium":
        return 2
    return 1


def _review_trust_summary(db: Session, row: ModerationReview) -> dict[str, Any] | None:
    try:
        company_ids = get_related_company_ids_for_review(db, row.entity_type, row.entity_id)
        if not company_ids:
            return None

        snapshots = [get_company_trust_snapshot(db, company_id) for company_id in sorted(company_ids)]
        valid = [snap for snap in snapshots if isinstance(snap, dict)]
        if not valid:
            return None

        trust_scores = [int(snap.get("trust_score") or 50) for snap in valid]
        trust_stars = [int(snap.get("stars") or 3) for snap in valid]

        return {
            "trust_score": round(sum(trust_scores) / max(len(trust_scores), 1), 1),
            "trust_stars": round(sum(trust_stars) / max(len(trust_stars), 1), 1),
            "company_ids": sorted(company_ids),
        }
    except Exception:
        return None


def _review_to_dict(r: ModerationReview, db: Session | None = None) -> dict:
    payload = {
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
    if db is not None:
        trust_summary = _review_trust_summary(db, r)
        if trust_summary:
            payload.update(trust_summary)
    return payload


def _resolve_documents_dir() -> str:
    try:
        from app.api.routes.documents_sync import DOCUMENTS_DIR

        return DOCUMENTS_DIR
    except Exception:
        app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return os.path.join(app_root, "media", "documents")


def _run_review_now(db: Session, entity_type: str, entity_id: int) -> ModerationReview:
    if entity_type == "deal":
        deal = db.query(DealSync).filter(DealSync.id == entity_id).first()
        if not deal:
            set_review_error(db, "deal", entity_id, "Deal not found")
            row = get_review(db, "deal", entity_id)
            if row:
                return row
            raise HTTPException(status_code=404, detail="Deal not found")

        result = review_deal(deal)
        return upsert_review(db, "deal", entity_id, result, status="done")

    doc = db.query(DocumentSync).filter(DocumentSync.id == entity_id).first()
    if not doc:
        set_review_error(db, "document", entity_id, "Document not found")
        row = get_review(db, "document", entity_id)
        if row:
            return row
        raise HTTPException(status_code=404, detail="Document not found")

    documents_dir = _resolve_documents_dir()
    file_path = os.path.join(documents_dir, doc.file_path) if doc.file_path else ""
    file_exists = os.path.isfile(file_path)
    file_size = os.path.getsize(file_path) if file_exists else 0

    same_hash = (
        db.query(DocumentSync)
        .filter(DocumentSync.file_hash == doc.file_hash, DocumentSync.id != entity_id)
        .first()
        if doc.file_hash
        else None
    )

    deal_payload = None
    if doc.deal_server_id:
        linked_deal = db.query(DealSync).filter(DealSync.id == doc.deal_server_id).first()
        if linked_deal:
            deal_payload = linked_deal.payload

    result = review_document(
        doc,
        deal_payload=deal_payload,
        file_exists=file_exists,
        file_size=file_size,
        file_hash_seen_elsewhere=bool(same_hash),
    )
    return upsert_review(db, "document", entity_id, result, status="done")


class ReviewRunRequest(BaseModel):
    entity_type: str
    entity_id: int = Field(gt=0)
    force: bool = False


class ReviewBatchRequest(BaseModel):
    entity_type: str
    entity_ids: list[int] = Field(default_factory=list)
    force: bool = False


class ReviewPatchRequest(BaseModel):
    status: str


# ==================== NEW ROLE-BASED ENDPOINTS ====================


@router.get("/moderation/reviews", response_model=List[dict])
def moderation_reviews(
    status: Optional[str] = Query(default=None, description="pending|done|error"),
    entity_type: Optional[str] = Query(default=None, description="deal|document"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_moderation_user),
):
    q = db.query(ModerationReview).order_by(ModerationReview.updated_at.desc())

    if status:
        status_normalized = status.strip().lower()
        if status_normalized not in _ALLOWED_PATCH_STATUSES:
            raise HTTPException(status_code=422, detail="status должен быть pending|done|error")
        q = q.filter(ModerationReview.status == status_normalized)

    if entity_type:
        entity_type_normalized = entity_type.strip().lower()
        if entity_type_normalized not in _ALLOWED_ENTITY_TYPES:
            raise HTTPException(status_code=422, detail="entity_type должен быть deal|document")
        q = q.filter(ModerationReview.entity_type == entity_type_normalized)

    rows = q.limit(limit).all()
    return [_review_to_dict(row, db) for row in rows]


@router.post("/moderation/review", response_model=dict)
def moderation_review_run(
    body: ReviewRunRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_moderation_user),
):
    _ensure_admin_mutations_enabled()
    entity_type = (body.entity_type or "").strip().lower()
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="entity_type должен быть deal|document")

    existing = get_review(db, entity_type, body.entity_id)
    if existing and existing.status == "done" and not body.force:
        return _review_to_dict(existing, db)

    row = _run_review_now(db, entity_type, body.entity_id)
    return _review_to_dict(row, db)


@router.post("/moderation/review/batch", response_model=List[dict])
def moderation_review_batch(
    body: ReviewBatchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_moderation_user),
):
    _ensure_admin_mutations_enabled()
    entity_type = (body.entity_type or "").strip().lower()
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="entity_type должен быть deal|document")

    entity_ids = [int(item) for item in body.entity_ids if int(item) > 0]
    if not entity_ids:
        return []

    results: list[dict] = []
    for entity_id in sorted(set(entity_ids)):
        try:
            existing = get_review(db, entity_type, entity_id)
            if existing and existing.status == "done" and not body.force:
                row = existing
            else:
                row = _run_review_now(db, entity_type, entity_id)
            results.append(_review_to_dict(row, db))
        except HTTPException as exc:
            results.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "status": "error",
                    "risk_level": None,
                    "flags": [],
                    "comment": str(exc.detail),
                    "recommended_action": "Проверить входные данные",
                    "model_used": "rules",
                    "created_at": None,
                    "updated_at": None,
                }
            )

    results.sort(key=lambda item: (_risk_rank(item.get("risk_level") or "low"), item.get("entity_id", 0)), reverse=True)
    return results


@router.patch("/moderation/reviews/{review_id}", response_model=dict)
def moderation_review_patch(
    review_id: int,
    body: ReviewPatchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_moderation_user),
):
    _ensure_admin_mutations_enabled()
    status = (body.status or "").strip().lower()
    if status not in _ALLOWED_PATCH_STATUSES:
        raise HTTPException(status_code=422, detail="status должен быть pending|done|error")

    row = db.query(ModerationReview).filter(ModerationReview.id == review_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")

    row.status = status
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _review_to_dict(row, db)


@router.get("/moderation/discover", response_model=dict)
def moderation_discover(
    entity_type: str = Query(..., description="deal|document"),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_moderation_user),
):
    normalized = (entity_type or "").strip().lower()
    if normalized not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="entity_type должен быть deal|document")

    if normalized == "deal":
        source_ids = [
            int(row[0])
            for row in db.query(DealSync.id)
            .order_by(DealSync.updated_at.desc(), DealSync.id.desc())
            .limit(limit * 5)
            .all()
        ]
    else:
        source_ids = [
            int(row[0])
            for row in db.query(DocumentSync.id)
            .order_by(DocumentSync.created_at.desc(), DocumentSync.id.desc())
            .limit(limit * 5)
            .all()
        ]

    if not source_ids:
        return {"entity_type": normalized, "entity_ids": [], "count": 0}

    reviewed_ids = {
        int(row[0])
        for row in db.query(ModerationReview.entity_id)
        .filter(
            ModerationReview.entity_type == normalized,
            ModerationReview.entity_id.in_(source_ids),
        )
        .all()
    }

    entity_ids = [entity_id for entity_id in source_ids if entity_id not in reviewed_ids][:limit]
    return {"entity_type": normalized, "entity_ids": entity_ids, "count": len(entity_ids)}


# ==================== LEGACY ENDPOINTS (X-Client-Key) ====================


@router.get("/moderation", response_model=List[dict])
def moderation_list_legacy(
    entity_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    rows = list_reviews(
        db,
        risk_level=risk_level,
        entity_type=entity_type,
        search=q,
        limit=limit,
        offset=offset,
    )
    return [_review_to_dict(r) for r in rows]


@router.post("/moderation/{entity_type}/{entity_id}/run", response_model=dict)
def moderation_run_legacy(
    entity_type: str,
    entity_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    _ensure_admin_mutations_enabled()
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="entity_type must be 'deal' or 'document'")

    set_review_pending(db, entity_type, entity_id)
    if entity_type == "deal":
        background_tasks.add_task(run_deal_review_background, entity_id)
    else:
        background_tasks.add_task(run_document_review_background, entity_id)

    row = get_review(db, entity_type, entity_id)
    return _review_to_dict(row) if row else {"entity_type": entity_type, "entity_id": entity_id, "status": "pending"}


@router.get("/moderation/{entity_type}/{entity_id}", response_model=dict)
def moderation_get_legacy(
    entity_type: str,
    entity_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="entity_type must be 'deal' or 'document'")
    row = get_review(db, entity_type, entity_id)
    if not row:
        raise HTTPException(status_code=404, detail="Review not found")
    return _review_to_dict(row)
