"""
Blacklist (Чёрный список) — публичный реестр недобросовестных компаний.
GET  /api/blacklist          — список (публичный, с пагинацией и поиском)
GET  /api/blacklist/{inn}    — карточка по ИНН
POST /api/blacklist          — добавить (только admin/moderator)
DELETE /api/blacklist/{inn}  — удалить (только admin)
"""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db.database import get_db
from app.models.models import Blacklist, User
from app.core.config import settings

router = APIRouter()

FLAG_LABELS = {
    "fraud":       "Мошенничество",
    "non_payment": "Невыплата",
    "fake_docs":   "Поддельные документы",
    "double_load": "Двойной груз",
    "hijack":      "Хищение груза",
    "other":       "Прочее",
}


def _bl_payload(entry: Blacklist) -> dict:
    flags = entry.flags or []
    return {
        "id":         entry.id,
        "inn":        entry.inn,
        "name":       entry.name,
        "reason":     entry.reason,
        "flags":      flags,
        "flag_labels": [FLAG_LABELS.get(f, f) for f in flags],
        "source":     entry.source,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _get_user(authorization: str | None, db: Session) -> User | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    return db.query(User).filter(User.api_token == token, User.is_active == True).first()


@router.get("/blacklist")
def list_blacklist(
    q:      str | None = Query(None, description="Поиск по ИНН или названию"),
    flag:   str | None = Query(None, description="Фильтр по флагу"),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Публичный список недобросовестных компаний."""
    query = db.query(Blacklist)
    if q:
        q_like = f"%{q.strip()}%"
        query = query.filter(
            or_(Blacklist.inn.ilike(q_like), Blacklist.name.ilike(q_like))
        )
    if flag:
        # JSON contains filter — PostgreSQL specific
        query = query.filter(Blacklist.flags.contains([flag]))

    total = query.count()
    entries = query.order_by(Blacklist.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "total":   total,
        "offset":  offset,
        "limit":   limit,
        "results": [_bl_payload(e) for e in entries],
    }


@router.get("/blacklist/check/{inn}")
def check_inn(inn: str, db: Session = Depends(get_db)):
    """Быстрая проверка ИНН — есть ли в чёрном списке."""
    inn = inn.strip()
    entry = db.query(Blacklist).filter(Blacklist.inn == inn).first()
    if not entry:
        return {"inn": inn, "blacklisted": False}
    return {"inn": inn, "blacklisted": True, "entry": _bl_payload(entry)}


@router.post("/blacklist", status_code=201)
def add_to_blacklist(
    inn:    str = Query(..., min_length=10, max_length=12),
    name:   str | None = Query(None),
    reason: str | None = Query(None),
    flags:  str | None = Query(None, description="Через запятую: fraud,non_payment,..."),
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Добавить компанию в чёрный список (только admin)."""
    user = _get_user(authorization, db)
    if not user or user.role not in ("admin", "moderator"):
        raise HTTPException(403, "Недостаточно прав")

    inn = inn.strip()
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(422, "Некорректный ИНН")

    existing = db.query(Blacklist).filter(Blacklist.inn == inn).first()
    if existing:
        raise HTTPException(409, "Компания уже в чёрном списке")

    flag_list = [f.strip() for f in (flags or "").split(",") if f.strip()] or ["other"]

    entry = Blacklist(
        inn=inn, name=name, reason=reason,
        flags=flag_list, added_by=user.id, source="manual",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _bl_payload(entry)


@router.delete("/blacklist/{inn}", status_code=200)
def remove_from_blacklist(
    inn: str,
    authorization: str | None = None,
    db: Session = Depends(get_db),
):
    """Удалить из чёрного списка (только admin)."""
    user = _get_user(authorization, db)
    if not user or user.role != "admin":
        raise HTTPException(403, "Только admin")

    entry = db.query(Blacklist).filter(Blacklist.inn == inn).first()
    if not entry:
        raise HTTPException(404, "Не найдено")
    db.delete(entry)
    db.commit()
    return {"ok": True, "inn": inn}
