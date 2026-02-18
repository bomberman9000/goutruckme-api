"""AI-модерация претензий: просмотр и ручной запуск анализа.

Готово для кнопки в админке:
- GET  /api/complaints/{complaint_id}/ai-analysis — посмотреть текущий анализ (MVP)
- POST /api/complaints/{complaint_id}/ai-analysis/run — запустить и записать в audit_events
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.core.security import get_current_user
from app.models.models import User, Complaint
from app.services.ai_moderation import analyze_complaint_text
from app.services.audit import audit_log

router = APIRouter()


@router.get("/complaints/{complaint_id}/ai-analysis")
def get_ai_analysis(
    complaint_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Получить AI-анализ претензии (MVP: анализ по текущему описанию)."""
    c = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")

    # Пока без кеша в БД — считаем по текущему тексту
    res = analyze_complaint_text(c.description or "", source="view")
    return res.to_dict()


@router.post("/complaints/{complaint_id}/ai-analysis/run")
def run_ai_analysis(
    complaint_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Запустить AI-анализ претензии и записать событие в audit_events."""
    c = db.query(Complaint).filter(Complaint.id == complaint_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Not found")

    res = analyze_complaint_text(c.description or "", source="manual")

    audit_log(
        db,
        entity_type="complaint",
        entity_id=complaint_id,
        action="ai_moderation",
        actor_role="system",
        actor_user_id=current_user.id,
        meta=res.to_dict(),
    )

    db.commit()
    return res.to_dict()

