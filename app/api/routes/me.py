from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from jose import jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.security import ALGORITHM, SECRET_KEY, get_current_user
from app.db.database import get_db
from app.models.models import User
from app.api.routes.profile import build_me_payload, clean_str
from app.trust.service import recalc_company_trust

router = APIRouter()


class CompanyPatchRequest(BaseModel):
    name: Optional[str] = None
    inn: Optional[str] = None
    ogrn: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    contact_person: Optional[str] = None
    website: Optional[str] = None
    edo_enabled: Optional[bool] = None


def _decode_user_from_token(token: str, db: Session) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub") or payload.get("id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Невалидный токен")
        user_id = int(user_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Невалидный или истёкший токен")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


def get_current_user_flexible(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    # 1) Стандартный Bearer (основной сценарий)
    if authorization and authorization.startswith("Bearer "):
        return get_current_user(authorization=authorization, db=db)

    # 2) Cookie fallback (для клиентов, где токен живет в cookie)
    cookie_token = request.cookies.get("authToken") or request.cookies.get("access_token")
    if not cookie_token:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    token = cookie_token.replace("Bearer ", "").strip()
    return _decode_user_from_token(token, db)


@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    return build_me_payload(db, current_user)


@router.patch("/me/company")
def patch_me_company(
    body: CompanyPatchRequest,
    current_user: User = Depends(get_current_user_flexible),
    db: Session = Depends(get_db),
):
    if body.phone is not None:
        next_phone = clean_str(body.phone)
        if not next_phone:
            raise HTTPException(status_code=422, detail="Телефон не может быть пустым")
        conflict = (
            db.query(User)
            .filter(User.phone == next_phone, User.id != current_user.id)
            .first()
        )
        if conflict:
            raise HTTPException(status_code=409, detail="Телефон уже используется")
        current_user.phone = next_phone

    if body.name is not None:
        name = clean_str(body.name)
        current_user.organization_name = name
        current_user.company = name

    if body.inn is not None:
        current_user.inn = clean_str(body.inn)

    if body.ogrn is not None:
        current_user.ogrn = clean_str(body.ogrn)

    if body.city is not None:
        current_user.city = clean_str(body.city)

    if body.contact_person is not None:
        current_user.contact_person = clean_str(body.contact_person)

    if body.website is not None:
        current_user.website = clean_str(body.website)

    if body.edo_enabled is not None:
        current_user.edo_enabled = bool(body.edo_enabled)

    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    recalc_state = "recalculated"
    try:
        recalc_company_trust(db, int(current_user.id))
    except Exception:
        recalc_state = "needs_recalc"

    payload = build_me_payload(db, current_user)
    payload["trust_recalc"] = recalc_state
    return payload
