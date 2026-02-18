from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from jose import jwt
from app.db.database import SessionLocal
from app.models.models import Bid, Load
from app.core.security import SECRET_KEY, ALGORITHM

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["id"]
    except:
        return None


@router.post("/{load_id}/add")
def add_bid(load_id: int, token: str, price: float, comment: str = "", db: Session = Depends(get_db)):
    user_id = get_user(token)

    bid = Bid(
        load_id=load_id,
        carrier_id=user_id,
        price=price,
        comment=comment
    )
    db.add(bid)
    db.commit()
    db.refresh(bid)
    
    # Начисление баллов за создание ставки
    try:
        from app.services.rating_system import rating_system
        rating_system.on_bid_created(db, user_id, bid.id, load_id)
    except Exception as e:
        # Не критично, если не удалось начислить баллы
        pass
    
    return {"msg": "bid added", "bid_id": bid.id}


@router.get("/{load_id}")
def get_bids(load_id: int, db: Session = Depends(get_db)):
    bids = db.query(Bid).filter(Bid.load_id == load_id).all()
    return bids


@router.post("/{bid_id}/accept")
def accept_bid(bid_id: int, db: Session = Depends(get_db)):
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        return {"error": "Bid not found"}
    
    bid.status = "accepted"

    load = db.query(Load).filter(Load.id == bid.load_id).first()
    if not load:
        return {"error": "Load not found"}
    
    load.status = "covered"

    db.commit()
    
    # Начисление баллов за успешную сделку
    try:
        from app.services.rating_system import rating_system
        rating_system.on_successful_deal(
            db, 
            shipper_id=load.user_id,
            carrier_id=bid.carrier_id,
            load_id=load.id
        )
        # Также начисляем за завершение заявки
        rating_system.on_load_completed(db, load.user_id, load.id)
        rating_system.on_load_completed(db, bid.carrier_id, load.id)
    except Exception as e:
        # Не критично, если не удалось начислить баллы
        pass
    
    return {"msg": "bid accepted", "load_id": load.id, "carrier_id": bid.carrier_id}
