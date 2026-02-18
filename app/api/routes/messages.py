from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from jose import jwt
from app.db.database import SessionLocal
from app.models.models import Message
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


@router.post("/send")
def send_message(token: str, load_id: int, text: str, db: Session = Depends(get_db)):
    sender_id = get_user(token)

    msg = Message(
        load_id=load_id,
        sender_id=sender_id,
        message=text
    )
    db.add(msg)
    db.commit()
    return {"msg": "sent"}


@router.get("/{load_id}")
def get_messages(load_id: int, db: Session = Depends(get_db)):
    msgs = db.query(Message).filter(Message.load_id == load_id).all()
    return msgs
