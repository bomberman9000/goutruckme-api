from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from jose import jwt
from app.db.database import SessionLocal
from app.models.models import Load, User
from app.core.security import SECRET_KEY, ALGORITHM
from typing import Optional

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_from_token(authorization: Optional[str] = Header(None)):
    """Получить user_id из токена в заголовке Authorization"""
    if not authorization:
        return None
    try:
        # Bearer token
        token = authorization.replace("Bearer ", "") if authorization.startswith("Bearer ") else authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload["id"]
    except:
        return None


@router.post("/create")
def create_load(
    from_city: str,
    to_city: str,
    price: float,
    weight: Optional[float] = 0,
    volume: Optional[float] = 0,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Depends(get_user_from_token)
):
    """Создать новую заявку на груз"""
    if not user_id:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    new_load = Load(
        user_id=user_id,
        from_city=from_city,
        to_city=to_city,
        weight=weight or 0,
        volume=volume or 0,
        price=price
    )

    db.add(new_load)
    db.commit()
    db.refresh(new_load)

    # Начисление баллов за создание заявки
    try:
        from app.services.rating_system import rating_system
        rating_system.on_load_created(db, user_id, new_load.id)
    except Exception as e:
        # Не критично, если не удалось начислить баллы
        pass

    return {"msg": "load created", "load_id": new_load.id}


@router.get("/list")
def list_loads(db: Session = Depends(get_db)):
    """Список всех открытых заявок с информацией о пользователях."""
    loads = db.query(Load).filter(Load.status == "open").all()
    
    result = []
    for load in loads:
        creator = db.query(User).filter(User.id == load.user_id).first()
        load_dict = {
            "id": load.id,
            "from_city": load.from_city,
            "to_city": load.to_city,
            "weight": load.weight,
            "volume": load.volume,
            "price": load.price,
            "status": load.status,
            "created_at": load.created_at.isoformat() if load.created_at else None,
            "creator": {
                "id": creator.id if creator else None,
                "fullname": creator.fullname if creator else "Неизвестно",
                "company": creator.company if creator else None,
                "rating": creator.rating if creator else 5.0,
                "points": creator.points if creator else 100,
                "trust_level": creator.trust_level if creator else "new",
                "verified": creator.verified if creator else False
            } if creator else None
        }
        result.append(load_dict)
    
    return result
