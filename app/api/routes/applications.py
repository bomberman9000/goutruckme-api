"""
API для работы с заявками (applications/документами).
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import User, Deal, Document
from app.trust.service import recalc_company_trust

logger = logging.getLogger(__name__)
router = APIRouter()


def _recalc_trust_safely(db: Session, *company_ids: int) -> None:
    seen: set[int] = set()
    for company_id in company_ids:
        if not company_id or company_id in seen:
            continue
        seen.add(company_id)
        try:
            recalc_company_trust(db, int(company_id))
        except Exception as e:
            logger.warning("recalc_company_trust failed for company_id=%s: %s", company_id, e)


# ============================================
# Схемы
# ============================================

class ApplicationListItem(BaseModel):
    id: int
    deal_id: int
    doc_type: str
    status: str
    created_at: str

    class Config:
        from_attributes = True


class ApplicationDetail(BaseModel):
    id: int
    deal_id: int
    doc_type: str
    status: str
    created_at: str
    pdf_path: Optional[str] = None
    signed_at: Optional[str] = None

    class Config:
        from_attributes = True


class SignRequest(BaseModel):
    signature: str


# ============================================
# Эндпоинты
# ============================================

@router.get("/applications", response_model=List[ApplicationListItem])
async def get_applications(
    deal_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получить список заявок."""
    query = db.query(Document)
    if deal_id:
        query = query.filter(Document.deal_id == deal_id)
    query = query.join(Deal).filter(
        (Deal.shipper_id == current_user.id) |
        (Deal.carrier_id == current_user.id)
    )
    documents = query.all()
    return [
        {
            "id": d.id,
            "deal_id": d.deal_id,
            "doc_type": d.doc_type,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else "",
        }
        for d in documents
    ]


@router.get("/applications/{app_id}", response_model=ApplicationDetail)
async def get_application_detail(
    app_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получить детали заявки."""
    doc = db.query(Document).filter(Document.id == app_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    deal = db.query(Deal).filter(Deal.id == doc.deal_id).first()
    not_owner = deal and (
        deal.shipper_id != current_user.id and deal.carrier_id != current_user.id
    )
    if not deal or not_owner:
        raise HTTPException(status_code=403, detail="Нет доступа")

    return {
        "id": doc.id,
        "deal_id": doc.deal_id,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
        "pdf_path": doc.pdf_path,
        "signed_at": doc.signed_at.isoformat() if doc.signed_at else None,
    }


@router.post("/applications/{app_id}/sign")
async def sign_application(
    app_id: int,
    data: SignRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Подписать заявку."""
    doc = db.query(Document).filter(Document.id == app_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    deal = db.query(Deal).filter(Deal.id == doc.deal_id).first()
    not_owner = deal and (
        deal.shipper_id != current_user.id and deal.carrier_id != current_user.id
    )
    if not deal or not_owner:
        raise HTTPException(status_code=403, detail="Нет доступа")

    doc.status = "signed"
    doc.signed_at = datetime.utcnow()
    doc.signed_by = current_user.id

    if doc.doc_type == "contract":
        deal.status = "CONTRACTED"

    db.commit()
    db.refresh(deal)
    _recalc_trust_safely(db, deal.shipper_id, deal.carrier_id)

    try:
        from app.services.bot_webhooks import notify_application_signed
        import asyncio
        if current_user.telegram_id:
            asyncio.create_task(
                notify_application_signed(app_id, deal.id, current_user.telegram_id)
            )
    except Exception as e:
        logger.warning("Telegram notification failed for app_id=%s: %s", app_id, e)

    return {
        "success": True,
        "message": "Документ подписан",
        "deal_status": deal.status
    }


@router.get("/applications/{app_id}/pdf")
async def get_application_pdf(
    app_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Получить PDF заявки."""
    doc = db.query(Document).filter(Document.id == app_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    deal = db.query(Deal).filter(Deal.id == doc.deal_id).first()
    not_owner = deal and (
        deal.shipper_id != current_user.id and deal.carrier_id != current_user.id
    )
    if not deal or not_owner:
        raise HTTPException(status_code=403, detail="Нет доступа")

    if not doc.pdf_path:
        raise HTTPException(status_code=404, detail="PDF не сгенерирован")

    pdf_file = Path(doc.pdf_path)
    if not pdf_file.exists():
        raise HTTPException(status_code=404, detail="PDF файл не найден")

    return FileResponse(
        path=str(pdf_file),
        media_type="application/pdf",
        filename=f"{doc.doc_type}_{doc.id}.pdf"
    )
