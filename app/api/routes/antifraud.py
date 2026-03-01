"""
🛡️ API для AI-Антимошенника
Защита от мошенников и фейковых заявок
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.db.database import SessionLocal
from app.models.models import Load, User, Bid
from app.services.ai_antifraud import ai_antifraud
from app.services.geo import canonicalize_city_name

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ SCHEMAS ============

class UserCheckRequest(BaseModel):
    """Запрос проверки пользователя."""
    fullname: str
    phone: str
    rating: Optional[float] = 5.0
    created_at: Optional[str] = None


class LoadCheckRequest(BaseModel):
    """Запрос проверки заявки."""
    from_city: str
    to_city: str
    price: float
    weight: Optional[float] = None
    volume: Optional[float] = None
    description: Optional[str] = None


class BidCheckRequest(BaseModel):
    """Запрос проверки ставки."""
    price: float
    comment: Optional[str] = None
    load_price: float


class PhoneCheckRequest(BaseModel):
    """Запрос проверки телефона."""
    phone: str


class BlacklistRequest(BaseModel):
    """Запрос добавления в чёрный список."""
    phone: Optional[str] = None
    inn: Optional[str] = None
    reason: str


class TextCheckRequest(BaseModel):
    """Запрос проверки текста."""
    text: str


# ============ ENDPOINTS ============

@router.post("/check-user")
def check_user(request: UserCheckRequest):
    """
    👤 Проверка пользователя на мошенничество.
    
    Возвращает риск-скор и вердикт:
    - ✅ БЕЗОПАСНО (0-20%)
    - ⚡ НИЗКИЙ РИСК (20-40%)
    - ⚠️ ТРЕБУЕТ ПРОВЕРКИ (40-60%)
    - ⛔ ВЫСОКИЙ РИСК (60-80%)
    - 🚨 ВЕРОЯТНЫЙ МОШЕННИК (80-100%)
    """
    user_data = {
        "fullname": request.fullname,
        "phone": request.phone,
        "rating": request.rating,
        "created_at": request.created_at
    }
    
    result = ai_antifraud.analyze_user(user_data)
    return result


@router.post("/check-user/{user_id}")
def check_user_by_id(user_id: int, db: Session = Depends(get_db)):
    """
    👤 Проверка пользователя из БД по ID.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user_data = {
        "id": user.id,
        "fullname": user.fullname,
        "phone": user.phone,
        "rating": user.rating,
        "created_at": str(user.created_at) if user.created_at else None
    }
    
    # Получаем историю (заявки и ставки)
    loads = db.query(Load).filter(Load.user_id == user_id).all()
    bids = db.query(Bid).filter(Bid.carrier_id == user_id).all()
    
    history = []
    for load in loads:
        history.append({"type": "load", "status": load.status})
    for bid in bids:
        history.append({"type": "bid", "status": bid.status})
    
    result = ai_antifraud.analyze_user(user_data, history)
    return result


@router.post("/check-load")
def check_load(request: LoadCheckRequest):
    """
    📦 Проверка заявки на мошенничество.
    
    Определяет вероятность фейковой заявки.
    """
    load_data = {
        "from_city": request.from_city,
        "to_city": request.to_city,
        "price": request.price,
        "weight": request.weight,
        "volume": request.volume,
        "description": request.description
    }
    
    result = ai_antifraud.analyze_load(load_data)
    return result


@router.post("/check-load/{load_id}")
def check_load_by_id(load_id: int, db: Session = Depends(get_db)):
    """
    📦 Проверка заявки из БД по ID.
    """
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Получаем создателя
    creator = db.query(User).filter(User.id == load.user_id).first()
    creator_data = None
    if creator:
        creator_data = {
            "id": creator.id,
            "fullname": creator.fullname,
            "phone": creator.phone,
            "rating": creator.rating,
            "created_at": str(creator.created_at) if creator.created_at else None
        }
    
    load_data = {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "price": load.price,
        "weight": load.weight,
        "volume": load.volume
    }
    
    result = ai_antifraud.analyze_load(load_data, creator_data)
    return result


@router.post("/check-bid")
def check_bid(request: BidCheckRequest):
    """
    💰 Проверка ставки на мошенничество.
    """
    bid_data = {
        "price": request.price,
        "comment": request.comment
    }
    
    load_data = {
        "price": request.load_price
    }
    
    result = ai_antifraud.analyze_bid(bid_data, load_data)
    return result


@router.post("/check-bid/{bid_id}")
def check_bid_by_id(bid_id: int, db: Session = Depends(get_db)):
    """
    💰 Проверка ставки из БД по ID.
    """
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    
    load = db.query(Load).filter(Load.id == bid.load_id).first()
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    
    bid_data = {
        "id": bid.id,
        "price": bid.price,
        "comment": bid.comment
    }
    
    load_data = {
        "price": load.price if load else 0
    }
    
    carrier_data = None
    if carrier:
        carrier_data = {
            "id": carrier.id,
            "fullname": carrier.fullname,
            "phone": carrier.phone,
            "rating": carrier.rating
        }
    
    result = ai_antifraud.analyze_bid(bid_data, load_data, carrier_data)
    return result


@router.post("/check-phone")
def check_phone(request: PhoneCheckRequest):
    """
    📱 Проверка телефона.
    """
    result = ai_antifraud.check_phone(request.phone)
    return result


@router.post("/check-text")
def check_text(request: TextCheckRequest):
    """
    📝 Проверка текста на подозрительные паттерны.
    """
    text_check = ai_antifraud._check_text(request.text)
    leak_check = ai_antifraud._check_data_leak(request.text)
    
    risk_score = text_check.get("score", 0)
    if leak_check.get("found"):
        risk_score += 60
    
    return {
        "text_length": len(request.text),
        "suspicious_keywords": text_check,
        "data_leak": leak_check,
        "total_risk_score": min(risk_score, 100),
        "verdict": ai_antifraud._get_verdict(risk_score)
    }


@router.post("/full-report/{entity_type}/{entity_id}")
def get_full_report(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    """
    📊 Полный отчёт о мошенничестве.
    
    entity_type: user / load / bid
    """
    if entity_type == "user":
        entity = db.query(User).filter(User.id == entity_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        entity_data = {
            "id": entity.id,
            "fullname": entity.fullname,
            "phone": entity.phone,
            "rating": entity.rating,
            "created_at": str(entity.created_at) if entity.created_at else None
        }
        related_data = None
        
    elif entity_type == "load":
        entity = db.query(Load).filter(Load.id == entity_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
        entity_data = {
            "id": entity.id,
            "from_city": entity.from_city,
            "to_city": entity.to_city,
            "price": entity.price,
            "weight": entity.weight
        }
        creator = db.query(User).filter(User.id == entity.user_id).first()
        related_data = {"creator": {
            "fullname": creator.fullname,
            "phone": creator.phone,
            "rating": creator.rating
        }} if creator else None
        
    elif entity_type == "bid":
        entity = db.query(Bid).filter(Bid.id == entity_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Ставка не найдена")
        entity_data = {
            "id": entity.id,
            "price": entity.price,
            "comment": entity.comment
        }
        load = db.query(Load).filter(Load.id == entity.load_id).first()
        carrier = db.query(User).filter(User.id == entity.carrier_id).first()
        related_data = {
            "load": {"price": load.price} if load else {},
            "carrier": {"fullname": carrier.fullname, "rating": carrier.rating} if carrier else None
        }
    else:
        raise HTTPException(status_code=400, detail="Неверный тип сущности")
    
    result = ai_antifraud.get_fraud_report(entity_type, entity_id, entity_data, related_data)
    return result


@router.post("/blacklist/add")
def add_to_blacklist(request: BlacklistRequest):
    """
    🚫 Добавить в чёрный список.
    """
    result = ai_antifraud.add_to_blacklist(
        phone=request.phone,
        inn=request.inn,
        reason=request.reason
    )
    return result


@router.get("/blacklist/check")
def check_blacklist(phone: str = None, inn: str = None):
    """
    🔍 Проверить в чёрном списке.
    """
    in_blacklist = False
    found = []
    
    if phone and phone in ai_antifraud.BLACKLIST_PHONES:
        in_blacklist = True
        found.append(f"phone: {phone}")
    
    if inn and inn in ai_antifraud.BLACKLIST_INN:
        in_blacklist = True
        found.append(f"inn: {inn}")
    
    return {
        "in_blacklist": in_blacklist,
        "found": found
    }


@router.get("/quick-check")
def quick_fraud_check(phone: str = None, price: float = None, 
                      from_city: str = None, to_city: str = None):
    """
    ⚡ Быстрая проверка на мошенничество.
    """
    risk_score = 0
    alerts = []
    
    # Проверка телефона
    if phone:
        phone_check = ai_antifraud.check_phone(phone)
        if phone_check["suspicious"]:
            risk_score += phone_check["score"]
            alerts.append(phone_check["message"])
    
    # Проверка маршрута
    if from_city and to_city and from_city.lower() == to_city.lower():
        risk_score += 50
        alerts.append("❌ Город отправления = город назначения")
    
    # Проверка цены
    if price is not None:
        if price <= 0:
            risk_score += 40
            alerts.append("❌ Нулевая цена")
        elif price < 1000:
            risk_score += 30
            alerts.append("⚠️ Подозрительно низкая цена")
    
    risk_level = ai_antifraud._get_risk_level(risk_score)
    
    return {
        "risk_score": min(risk_score, 100),
        "risk_level": risk_level.value,
        "alerts": alerts,
        "verdict": ai_antifraud._get_verdict(risk_score),
        "emoji_verdict": "🚨" if risk_score >= 80 else "⛔" if risk_score >= 60 else "⚠️" if risk_score >= 40 else "⚡" if risk_score >= 20 else "✅"
    }


@router.post("/full-analysis")
def full_fraud_analysis(user_id: int, load_id: int = None, ip: str = None, 
                        db: Session = Depends(get_db)):
    """
    🔍 ПОЛНЫЙ АНАЛИЗ НА МОШЕННИЧЕСТВО
    
    Проверяет ВСЕ категории рисков:
    - 🔥 Логические риски
    - 🔥 Поведенческие риски
    - 🔥 Технические риски
    - 🔥 Исторические риски
    
    Возвращает детальный отчёт с вердиктом.
    """
    # Получаем пользователя
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user_data = {
        "id": user.id,
        "fullname": user.fullname,
        "phone": user.phone,
        "rating": user.rating,
        "created_at": str(user.created_at) if user.created_at else None
    }
    
    # Получаем заявку если указана
    load_data = None
    if load_id:
        load = db.query(Load).filter(Load.id == load_id).first()
        if load:
            load_data = {
                "id": load.id,
                "from_city": canonicalize_city_name(load.from_city),
                "to_city": canonicalize_city_name(load.to_city),
                "price": load.price,
                "weight": load.weight,
                "volume": load.volume
            }
    
    # Собираем историю
    loads = db.query(Load).filter(Load.user_id == user_id).all()
    bids = db.query(Bid).filter(Bid.carrier_id == user_id).all()
    
    created_loads = len(loads)
    completed_loads = len([l for l in loads if l.status == "closed"])
    
    history = {
        "created_loads": created_loads,
        "completed_loads": completed_loads,
        "total_bids": len(bids),
        "accepted_bids": len([b for b in bids if b.status == "accepted"])
    }
    
    # Полный анализ
    result = ai_antifraud.full_analysis(user_data, load_data, history, ip)
    return result


@router.post("/complaint/{user_id}")
def add_complaint(user_id: int, complaint_type: str = "general"):
    """
    📢 Добавить жалобу на пользователя.
    """
    result = ai_antifraud.add_complaint(user_id, complaint_type)
    return result


@router.post("/dispute/{user_id}")
def add_dispute(user_id: int):
    """
    ⚖️ Зафиксировать спор по пользователю.
    """
    result = ai_antifraud.add_dispute(user_id)
    return result


@router.get("/status")
def get_antifraud_status():
    """
    📊 Статус AI-Антимошенника.
    """
    return {
        "service": "AI-Antifraud",
        "version": "2.0.0",
        "status": "active",
        "blacklist_stats": {
            "phones": len(ai_antifraud.blacklist_phones),
            "inns": len(ai_antifraud.blacklist_inn),
            "users": len(ai_antifraud.blacklist_users)
        },
        "risk_categories": {
            "logical": "🔥 Логические риски (цена, вес, маршрут)",
            "behavioral": "🔥 Поведенческие риски (ответы, отмены)",
            "technical": "🔥 Технические риски (IP, телефон, аккаунт)",
            "historical": "🔥 Исторические риски (споры, жалобы, рейтинг)"
        },
        "risk_levels": {
            "safe": "0-20% — ✅ БЕЗОПАСНО",
            "low": "20-40% — ⚡ НИЗКИЙ РИСК",
            "medium": "40-60% — ⚠️ ТРЕБУЕТ ПРОВЕРКИ",
            "high": "60-80% — ⛔ ВЫСОКИЙ РИСК",
            "critical": "80-100% — 🚨 ВЕРОЯТНЫЙ МОШЕННИК"
        },
        "actions": {
            "ALLOW": "Разрешить работу",
            "MONITOR": "Работать с мониторингом",
            "REVIEW": "Требуется ручная проверка",
            "REJECT": "Отклонить",
            "BLOCK": "Заблокировать"
        },
        "endpoints": {
            "POST /antifraud/full-analysis": "🔍 Полный анализ (все риски)",
            "POST /antifraud/check-user": "Проверка пользователя",
            "POST /antifraud/check-load": "Проверка заявки",
            "POST /antifraud/check-bid": "Проверка ставки",
            "GET /antifraud/quick-check": "⚡ Быстрая проверка",
            "POST /antifraud/blacklist/add": "Добавить в ЧС",
            "POST /antifraud/complaint/{id}": "Добавить жалобу",
            "POST /antifraud/dispute/{id}": "Зафиксировать спор"
        }
    }
