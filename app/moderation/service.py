"""
Moderation service: upsert review, get by entity, list with filters.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import ModerationReview


def upsert_review(
    db: Session,
    entity_type: str,
    entity_id: int,
    result_dict: Dict[str, Any],
    status: str = "done",
) -> ModerationReview:
    """Insert or update moderation_review by (entity_type, entity_id)."""
    row = (
        db.query(ModerationReview)
        .filter(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
        .first()
    )
    if not row:
        row = ModerationReview(
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
        )
        db.add(row)
        db.flush()
    row.status = status
    row.risk_level = result_dict.get("risk_level")
    row.flags = result_dict.get("flags")
    row.comment = result_dict.get("comment")
    row.recommended_action = result_dict.get("recommended_action")
    row.model_used = result_dict.get("model_used")
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def set_review_pending(db: Session, entity_type: str, entity_id: int) -> ModerationReview:
    """Create or update review with status=pending (before background run)."""
    row = (
        db.query(ModerationReview)
        .filter(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
        .first()
    )
    if not row:
        row = ModerationReview(
            entity_type=entity_type,
            entity_id=entity_id,
            status="pending",
        )
        db.add(row)
        db.flush()
    else:
        row.status = "pending"
        row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def set_review_error(db: Session, entity_type: str, entity_id: int, message: str) -> None:
    """Set review status to error and store message in comment."""
    row = (
        db.query(ModerationReview)
        .filter(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
        .first()
    )
    if not row:
        row = ModerationReview(
            entity_type=entity_type,
            entity_id=entity_id,
            status="error",
            comment=message[:2000] if message else None,
        )
        db.add(row)
    else:
        row.status = "error"
        row.comment = (row.comment or "") + "\n" + (message[:2000] if message else "")
        row.updated_at = datetime.utcnow()
    db.commit()


def get_review(
    db: Session,
    entity_type: str,
    entity_id: int,
) -> Optional[ModerationReview]:
    """Get latest review for entity."""
    return (
        db.query(ModerationReview)
        .filter(
            ModerationReview.entity_type == entity_type,
            ModerationReview.entity_id == entity_id,
        )
        .first()
    )


def list_reviews(
    db: Session,
    risk_level: Optional[str] = None,
    entity_type: Optional[str] = None,
    updated_after: Optional[datetime] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[ModerationReview]:
    """List reviews with optional filters."""
    q = db.query(ModerationReview).order_by(ModerationReview.updated_at.desc())
    if risk_level:
        q = q.filter(ModerationReview.risk_level == risk_level)
    if entity_type:
        q = q.filter(ModerationReview.entity_type == entity_type)
    if updated_after:
        q = q.filter(ModerationReview.updated_at >= updated_after)
    if search and search.strip():
        s = f"%{search.strip()}%"
        q = q.filter(
            (ModerationReview.comment.like(s)) | (ModerationReview.recommended_action.like(s))
        )
    return q.offset(offset).limit(limit).all()


def run_deal_review_background(server_id: int) -> None:
    """Background task: run deal moderation and upsert (call from API after set_review_pending)."""
    from app.db.database import SessionLocal
    from app.models.models import DealSync
    from app.moderation.engine import review_deal

    session = SessionLocal()
    try:
        deal = session.query(DealSync).filter(DealSync.id == server_id).first()
        if not deal:
            set_review_error(session, "deal", server_id, "Deal not found")
            return
        result = review_deal(deal)
        upsert_review(session, "deal", server_id, result, status="done")
        if result.get("risk_level") == "high":
            try:
                from app.telegram.alerts import send_high_risk_alert
                send_high_risk_alert("deal", server_id, result, deal)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Telegram alert failed: %s", e)
    except Exception as e:
        set_review_error(session, "deal", server_id, str(e))
    finally:
        session.close()


def run_document_review_background(document_id: int) -> None:
    """Background task: run document moderation and upsert."""
    import os
    from app.db.database import SessionLocal
    from app.models.models import DealSync, DocumentSync
    from app.moderation.engine import review_document

    try:
        from app.api.routes.documents_sync import DOCUMENTS_DIR
    except ImportError:
        DOCUMENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "media", "documents")

    session = SessionLocal()
    try:
        doc = session.query(DocumentSync).filter(DocumentSync.id == document_id).first()
        if not doc:
            set_review_error(session, "document", document_id, "Document not found")
            return
        file_path = os.path.join(DOCUMENTS_DIR, doc.file_path) if doc.file_path else ""
        file_exists = os.path.isfile(file_path)
        file_size = os.path.getsize(file_path) if file_exists else 0
        same_hash = (
            session.query(DocumentSync)
            .filter(DocumentSync.file_hash == doc.file_hash, DocumentSync.id != document_id)
            .first()
        ) if doc.file_hash else None
        deal_payload = None
        if doc.deal_server_id:
            d = session.query(DealSync).filter(DealSync.id == doc.deal_server_id).first()
            if d:
                deal_payload = d.payload
        result = review_document(
            doc,
            deal_payload=deal_payload,
            file_exists=file_exists,
            file_size=file_size,
            file_hash_seen_elsewhere=bool(same_hash),
        )
        upsert_review(session, "document", document_id, result, status="done")
        if result.get("risk_level") == "high":
            try:
                from app.telegram.alerts import send_high_risk_alert
                send_high_risk_alert("document", document_id, result, doc)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Telegram alert failed: %s", e)
    except Exception as e:
        set_review_error(session, "document", document_id, str(e))
    finally:
        session.close()
