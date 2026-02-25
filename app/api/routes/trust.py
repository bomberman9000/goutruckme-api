from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import User
from app.trust.service import get_company_trust_payload, recalc_company_trust

router = APIRouter()


def _normalize_role(role: Any) -> str:
    raw = role.value if hasattr(role, "value") else role
    value = str(raw or "").strip().lower()
    if value.startswith("userrole."):
        value = value.split(".", 1)[1]
    if value == "shipper":
        return "client"
    if value == "expeditor":
        return "forwarder"
    if value in {"carrier", "client", "forwarder", "admin"}:
        return value
    return "forwarder"


@router.get("/companies/{company_id}/trust")
def get_company_trust(company_id: int, db: Session = Depends(get_db)):
    try:
        return get_company_trust_payload(db, company_id, force_recalc=False)
    except ValueError:
        raise HTTPException(status_code=404, detail="Компания не найдена")


@router.post("/trust/recalc/{company_id}")
def recalc_trust(
    company_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _normalize_role(current_user.role) != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")

    try:
        recalc_company_trust(db, company_id)
        return get_company_trust_payload(db, company_id, force_recalc=False)
    except ValueError:
        raise HTTPException(status_code=404, detail="Компания не найдена")
