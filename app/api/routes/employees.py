"""Company employees management endpoints."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import User
from app.api.routes.me import get_current_user_flexible

router = APIRouter()

ALLOWED_ROLES = {"manager", "driver", "accountant"}
ROLE_LABELS = {
    "manager":    "Логист / Диспетчер",
    "driver":     "Водитель",
    "accountant": "Бухгалтер",
}


class InviteRequest(BaseModel):
    phone: str
    role: str = "manager"


class UpdateRoleRequest(BaseModel):
    role: str


def _serialize_employee(emp: User) -> dict:
    return {
        "id":           emp.id,
        "fullname":     emp.fullname or emp.organization_name or emp.contact_person or "—",
        "phone":        emp.phone,
        "email":        emp.email or "",
        "role":         emp.employee_role or "manager",
        "role_label":   ROLE_LABELS.get(emp.employee_role or "manager", emp.employee_role or ""),
        "verified":     emp.verified,
        "created_at":   emp.created_at.isoformat() if emp.created_at else None,
    }


@router.get("/me/employees")
def list_employees(
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    employees = db.query(User).filter(User.employer_id == current_user.id).all()
    return {"employees": [_serialize_employee(e) for e in employees]}


@router.post("/me/employees/invite")
def invite_employee(
    body: InviteRequest,
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    role = body.role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail=f"Неверная роль. Допустимо: {', '.join(ALLOWED_ROLES)}")

    phone = body.phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    # Try with and without +
    candidates = [phone, "+" + phone.lstrip("+")]
    if phone.startswith("8") and len(phone) == 11:
        candidates.append("+7" + phone[1:])
    if phone.startswith("7") and len(phone) == 11:
        candidates.append("+7" + phone[1:])

    user = None
    for p in candidates:
        user = db.query(User).filter(User.phone == p).first()
        if user:
            break

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь с таким телефоном не найден. Сначала он должен зарегистрироваться.")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя добавить себя")

    if user.employer_id and user.employer_id != current_user.id:
        raise HTTPException(status_code=409, detail="Этот пользователь уже является сотрудником другой компании")

    user.employer_id = current_user.id
    user.employee_role = role
    db.commit()

    return {"ok": True, "employee": _serialize_employee(user)}


@router.patch("/me/employees/{employee_id}/role")
def update_employee_role(
    employee_id: int,
    body: UpdateRoleRequest,
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    role = body.role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail="Неверная роль")

    emp = db.query(User).filter(User.id == employee_id, User.employer_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    emp.employee_role = role
    db.commit()
    return {"ok": True, "employee": _serialize_employee(emp)}


@router.delete("/me/employees/{employee_id}")
def remove_employee(
    employee_id: int,
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    emp = db.query(User).filter(User.id == employee_id, User.employer_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")

    emp.employer_id = None
    emp.employee_role = None
    db.commit()
    return {"ok": True}
