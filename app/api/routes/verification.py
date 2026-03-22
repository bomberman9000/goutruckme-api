"""
Верификация компании.
POST /api/me/request-verification  — подать заявку
GET  /api/me/verification-status   — статус заявки
PATCH /api/admin/users/{id}/verify — одобрить / отклонить (admin only)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.database import get_db
from app.models.models import User

router = APIRouter()


def _auth(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Необходима авторизация")
    token = authorization.split(" ", 1)[1]
    user = db.query(User).filter(User.api_token == token, User.is_active == True).first()
    if not user:
        raise HTTPException(401, "Недействительный токен")
    return user


class VerifyDecision(BaseModel):
    action: str          # "approve" | "reject"
    comment: str | None = None


@router.post("/me/request-verification")
def request_verification(
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Подать заявку на верификацию компании."""
    user = _auth(authorization, db)

    if user.verified:
        return {"ok": True, "status": "approved", "message": "Компания уже верифицирована"}

    vs = getattr(user, "verification_status", None) or "none"
    if vs == "pending":
        return {"ok": True, "status": "pending", "message": "Заявка уже на рассмотрении"}

    if not user.inn:
        raise HTTPException(422, "Укажите ИНН в профиле перед верификацией")
    if not user.ogrn and not user.company:
        raise HTTPException(422, "Укажите ОГРН или название компании")

    user.verification_status = "pending"
    user.verification_comment = None
    db.commit()
    return {"ok": True, "status": "pending", "message": "Заявка отправлена — рассмотрим в течение 24 ч"}


@router.get("/me/verification-status")
def get_verification_status(
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    user = _auth(authorization, db)
    if user.verified:
        return {"status": "approved", "verified": True, "comment": None}
    vs = getattr(user, "verification_status", None) or "none"
    return {
        "status": vs,
        "verified": False,
        "comment": getattr(user, "verification_comment", None),
    }


@router.patch("/admin/users/{user_id}/verify")
def admin_verify(
    user_id: int,
    body: VerifyDecision,
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Одобрить или отклонить верификацию (admin only)."""
    admin = _auth(authorization, db)
    if admin.role not in ("admin", "moderator"):
        raise HTTPException(403, "Только admin")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "Пользователь не найден")

    if body.action == "approve":
        target.verified = True
        target.verification_status = "approved"
        target.trust_level = "verified"
        target.verification_comment = body.comment or "Верифицировано командой ГрузПоток"
    elif body.action == "reject":
        target.verified = False
        target.verification_status = "rejected"
        target.verification_comment = body.comment or "Заявка отклонена"
    else:
        raise HTTPException(422, "action: approve | reject")

    db.commit()
    return {
        "ok": True,
        "user_id": user_id,
        "verified": target.verified,
        "status": target.verification_status,
    }


@router.get("/admin/verification-queue")
def verification_queue(
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Список заявок на верификацию (admin)."""
    admin = _auth(authorization, db)
    if admin.role not in ("admin", "moderator"):
        raise HTTPException(403, "Только admin")

    pending = db.query(User).filter(User.verification_status == "pending").all()
    return [
        {
            "id": u.id,
            "company": u.company or u.organization_name,
            "inn": u.inn,
            "ogrn": u.ogrn,
            "phone": u.phone,
            "fullname": u.fullname,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in pending
    ]
