"""
⭐ API для системы баллов и рейтинга
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Optional
from app.db.database import SessionLocal
from app.models.models import User
from app.services.rating_system import rating_system
from app.core.security import SECRET_KEY, ALGORITHM
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


@router.get("/stats/{user_id}")
def get_user_stats(user_id: int, db: Session = Depends(get_db)):
    """Получить статистику пользователя (баллы, рейтинг, история)."""
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
    limit: int = 10,
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
                "user_id": user.id,
                "fullname": user.fullname,
                "company": user.company,
                "rating": user.rating,
                "points": user.points,
                "trust_level": user.trust_level,
                "successful_deals": user.successful_deals,
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
    if not admin or admin.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    
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
    if not admin or admin.role != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    
    result = rating_system.add_points(db, user_id, points, reason)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    
    return result




