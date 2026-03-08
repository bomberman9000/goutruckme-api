"""
⭐ API для системы баллов и рейтинга
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.db.database import SessionLocal
from app.models.models import User
from app.services.rating_system import rating_system
from app.core.security import SECRET_KEY, ALGORITHM
from app.core.config import settings
from jose import jwt

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_from_token(authorization: Optional[str] = Header(None)):
    """Получить user_id из токена."""
    if not authorization:
        return None
    try:
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["id"]
    except:
        return None


def _is_admin(user: User | None) -> bool:
    if user is None:
        return False
    role = getattr(user.role, "value", user.role)
    return str(role or "").strip().lower().endswith("admin") or str(role or "").strip().lower() == "admin"


def _ensure_admin_mutations_enabled() -> None:
    if not settings.ADMIN_MUTATIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Административные изменения временно отключены")


@router.get("/stats/{user_id}")
def get_user_stats(
    user_id: int,
    db: Session = Depends(get_db),
    current_user_id: Optional[int] = Depends(get_user_from_token),
):
    """Получить статистику пользователя (баллы, рейтинг, история)."""
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    current_user = db.query(User).filter(User.id == current_user_id).first()
    if not current_user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    if current_user_id != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Доступ разрешен только владельцу профиля")

    stats = rating_system.get_user_stats(db, user_id)
    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])
    return stats


@router.get("/my-stats")
def get_my_stats(
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Получить свою статистику."""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    stats = rating_system.get_user_stats(db, user_id)
    if "error" in stats:
        raise HTTPException(status_code=404, detail=stats["error"])
    return stats


@router.get("/leaderboard")
def get_leaderboard(
    limit: int = Query(10, ge=1, le=50),
    role: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Топ пользователей по баллам."""
    query = db.query(User)
    
    if role:
        from app.models.models import UserRole
        query = query.filter(User.role == role)
    
    users = query.order_by(User.points.desc(), User.rating.desc()).limit(limit).all()
    
    return {
        "leaderboard": [
            {
                "rank": idx + 1,
                "name": user.company or user.fullname or f"Участник #{idx + 1}",
                "rating": user.rating,
                "points": user.points,
                "verified": user.verified
            }
            for idx, user in enumerate(users)
        ]
    }


@router.post("/verify/{user_id}")
def verify_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin_id: Optional[int] = Depends(get_user_from_token)
):
    """Верификация пользователя (только для админов)."""
    if not admin_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    admin = db.query(User).filter(User.id == admin_id).first()
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администраторов")
    _ensure_admin_mutations_enabled()
    
    result = rating_system.verify_user(db, user_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result


@router.post("/add-points/{user_id}")
def add_points_manual(
    user_id: int,
    points: int,
    reason: str,
    db: Session = Depends(get_db),
    admin_id: Optional[int] = Depends(get_user_from_token)
):
    """Ручное начисление/списание баллов (только для админов)."""
    if not admin_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")
    
    admin = db.query(User).filter(User.id == admin_id).first()
    if not _is_admin(admin):
        raise HTTPException(status_code=403, detail="Только для администраторов")
    _ensure_admin_mutations_enabled()
    
    result = rating_system.add_points(db, user_id, points, reason)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result

