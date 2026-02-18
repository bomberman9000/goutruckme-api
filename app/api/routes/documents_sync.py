"""
API документов по сделкам: список по deal_server_id, загрузка PDF, скачивание по id.
Защита: X-Client-Key (как в deals-sync).
"""

import os
import hashlib
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import DocumentSync

router = APIRouter()

# Директория для хранения PDF (app/media/documents)
_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOCUMENTS_DIR = os.path.join(_APP_ROOT, "media", "documents")


def _ensure_documents_dir():
    os.makedirs(DOCUMENTS_DIR, exist_ok=True)


def verify_client_sync_key(
    x_client_key: Optional[str] = Header(default=None, alias="X-Client-Key"),
):
    from app.core.config import get_settings
    settings = get_settings()
    key = getattr(settings, "CLIENT_SYNC_KEY", None) or ""
    if not key:
        return
    if not x_client_key or x_client_key.strip() != key.strip():
        raise HTTPException(status_code=401, detail="Invalid or missing X-Client-Key")


@router.get("/documents")
def list_documents(
    deal_server_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    """Список документов. Фильтр: ?deal_server_id=..."""
    q = db.query(DocumentSync).order_by(DocumentSync.created_at.desc())
    if deal_server_id is not None:
        q = q.filter(DocumentSync.deal_server_id == deal_server_id)
    rows = q.all()
    return [
        {
            "document_id": r.id,
            "deal_server_id": r.deal_server_id,
            "type": r.doc_type,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "status": r.status,
            "has_file": bool(
                r.file_path and os.path.isfile(os.path.join(DOCUMENTS_DIR, r.file_path))
            ),
        }
        for r in rows
    ]


@router.post("/documents")
def upload_document(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
    file: UploadFile = File(...),
    deal_server_id: int = Form(...),
    doc_type: str = Form(...),
):
    """Загрузить PDF документа. doc_type: CONTRACT | TTN | UPD. Запускает модерацию в фоне."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Expected PDF file")
    if doc_type not in ("CONTRACT", "TTN", "UPD"):
        raise HTTPException(status_code=400, detail="doc_type must be CONTRACT, TTN or UPD")

    _ensure_documents_dir()
    content = file.file.read()
    file_hash = hashlib.sha256(content).hexdigest()[:32]
    rec = DocumentSync(
        deal_server_id=deal_server_id,
        doc_type=doc_type,
        status="draft",
        file_hash=file_hash,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    safe_name = f"doc_{rec.id}_{file_hash}.pdf"
    full_path = os.path.join(DOCUMENTS_DIR, safe_name)
    rec.file_path = safe_name
    db.commit()

    with open(full_path, "wb") as f:
        f.write(content)

    from app.moderation.service import run_document_review_background, set_review_pending
    set_review_pending(db, "document", rec.id)
    background_tasks.add_task(run_document_review_background, rec.id)

    return {
        "document_id": rec.id,
        "deal_server_id": rec.deal_server_id,
        "type": rec.doc_type,
        "created_at": rec.created_at.isoformat() if rec.created_at else "",
        "status": rec.status,
    }


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(verify_client_sync_key),
):
    """Скачать PDF по document_id. 404 если файла нет."""
    rec = db.query(DocumentSync).filter(DocumentSync.id == document_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Document not found")
    full_path = os.path.join(DOCUMENTS_DIR, rec.file_path) if rec.file_path else ""
    if not rec.file_path or not os.path.isfile(full_path):
        raise HTTPException(
            status_code=404,
            detail="File not available (regenerate on client)",
        )
    return FileResponse(
        full_path,
        media_type="application/pdf",
        filename=f"document_{document_id}.pdf",
    )
