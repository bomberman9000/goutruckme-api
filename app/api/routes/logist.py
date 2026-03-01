"""
🚛 API для AI-Логиста
Подбор машин, сравнение ставок, автоматизация
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from app.db.database import SessionLocal
from app.models.models import Load, Truck, Bid, User
from app.services.cargo_status import apply_cargo_status_filter, expire_outdated_cargos
from app.services.ai_logist import ai_logist
from app.trust.service import get_company_trust_snapshot

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _trust_snapshot_for(db: Session, user_id: Optional[int]) -> dict:
    if not user_id:
        return get_company_trust_snapshot(db, None)
    return get_company_trust_snapshot(db, int(user_id))


# ============ SCHEMAS ============

class TruckRecommendRequest(BaseModel):
    """Запрос рекомендации типа ТС."""
    weight: Optional[float] = None
    volume: Optional[float] = None
    length: Optional[float] = None
    cargo_type: Optional[str] = None


class PriceCalculateRequest(BaseModel):
    """Запрос расчёта цены."""
    from_city: str
    to_city: str
    truck_type: str = "10т"
    weight: Optional[float] = None


class FindTrucksRequest(BaseModel):
    """Запрос поиска машин."""
    from_city: str
    to_city: str
    weight: Optional[float] = None
    volume: Optional[float] = None
    price: Optional[float] = None


class DriverMessageRequest(BaseModel):
    """Запрос генерации сообщения."""
    from_city: str
    to_city: str
    weight: Optional[float] = None
    volume: Optional[float] = None
    price: Optional[float] = None
    loading_date: Optional[str] = None
    message_type: str = "offer"  # offer / urgent / request_price


# ============ ENDPOINTS ============

@router.post("/recommend-truck")
def recommend_truck(request: TruckRecommendRequest):
    """
    🚛 Рекомендация типа ТС по параметрам груза.
    
    Отвечает: «Нужна газель / 5т / 10т / фура»
    """
    result = ai_logist.recommend_truck_type(
        weight=request.weight,
        volume=request.volume,
        length=request.length,
        cargo_type=request.cargo_type
    )
    return result


@router.post("/calculate-price")
def calculate_price(request: PriceCalculateRequest):
    """
    💰 Прогноз стоимости перевозки.
    
    Возвращает:
    - Рекомендуемую цену
    - Минимальную/максимальную цену
    - Цену за км
    """
    result = ai_logist.calculate_price(
        from_city=request.from_city,
        to_city=request.to_city,
        truck_type=request.truck_type,
        weight=request.weight
    )
    return result


@router.post("/find-trucks")
def find_trucks(request: FindTrucksRequest, db: Session = Depends(get_db)):
    """
    🔍 Поиск и подбор машин для груза.
    
    Возвращает ТОП-3 лучших предложений.
    """
    # Получаем все машины из БД
    trucks_db = db.query(Truck).filter(Truck.status == "free").all()
    
    # Преобразуем в dict с владельцами
    trucks = []
    for truck in trucks_db:
        owner = db.query(User).filter(User.id == truck.user_id).first()
        trust = _trust_snapshot_for(db, owner.id if owner else None)
        trucks.append({
            "id": truck.id,
            "type": truck.type,
            "capacity": truck.capacity,
            "region": truck.region,
            "status": truck.status,
            "user_id": truck.user_id,
            "owner": {
                "id": owner.id,
                "fullname": owner.fullname,
                "phone": owner.phone,
                "rating": owner.rating,
                "trust_score": trust.get("trust_score", 50),
                "trust_stars": trust.get("stars", 3),
                "trust_flags_high": (trust.get("signals") or {}).get("flags_high", 0),
            } if owner else {}
        })
    
    load = {
        "from_city": request.from_city,
        "to_city": request.to_city,
        "weight": request.weight,
        "volume": request.volume,
        "price": request.price,
        "client_trust_score": 50,
        "client_flags_high": 0,
    }
    
    result = ai_logist.find_trucks(load, trucks)
    return result


@router.post("/find-trucks/{load_id}")
def find_trucks_for_load(load_id: int, db: Session = Depends(get_db)):
    """
    🔍 Поиск машин для заявки из БД.
    """
    load_db = db.query(Load).filter(Load.id == load_id).first()
    if not load_db:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Получаем машины
    trucks_db = db.query(Truck).filter(Truck.status == "free").all()
    trucks = []
    for truck in trucks_db:
        owner = db.query(User).filter(User.id == truck.user_id).first()
        trust = _trust_snapshot_for(db, owner.id if owner else None)
        trucks.append({
            "id": truck.id,
            "type": truck.type,
            "capacity": truck.capacity,
            "region": truck.region,
            "status": truck.status,
            "user_id": truck.user_id,
            "owner": {
                "id": owner.id,
                "fullname": owner.fullname,
                "phone": owner.phone,
                "rating": owner.rating,
                "trust_score": trust.get("trust_score", 50),
                "trust_stars": trust.get("stars", 3),
                "trust_flags_high": (trust.get("signals") or {}).get("flags_high", 0),
            } if owner else {}
        })

    client_trust = _trust_snapshot_for(db, load_db.user_id)
    
    load = {
        "id": load_db.id,
        "from_city": load_db.from_city,
        "to_city": load_db.to_city,
        "weight": load_db.weight,
        "volume": load_db.volume,
        "price": load_db.price,
        "client_trust_score": client_trust.get("trust_score", 50),
        "client_flags_high": (client_trust.get("signals") or {}).get("flags_high", 0),
    }
    
    result = ai_logist.find_trucks(load, trucks)
    result["load_id"] = load_id
    return result


@router.post("/compare-bids/{load_id}")
def compare_bids(load_id: int, db: Session = Depends(get_db)):
    """
    📊 Сравнение ставок на заявку.
    
    Анализирует все ставки и выдаёт лучшее предложение.
    """
    load_db = db.query(Load).filter(Load.id == load_id).first()
    if not load_db:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    bids_db = db.query(Bid).filter(Bid.load_id == load_id).all()
    
    bids = []
    client_trust = _trust_snapshot_for(db, load_db.user_id)
    for bid in bids_db:
        carrier = db.query(User).filter(User.id == bid.carrier_id).first()
        trust = _trust_snapshot_for(db, carrier.id if carrier else None)
        bids.append({
            "id": bid.id,
            "price": bid.price,
            "comment": bid.comment,
            "status": bid.status,
            "carrier_id": bid.carrier_id,
            "carrier": {
                "id": carrier.id,
                "fullname": carrier.fullname,
                "rating": carrier.rating,
                "trust_score": trust.get("trust_score", 50),
                "trust_stars": trust.get("stars", 3),
                "trust_flags_high": (trust.get("signals") or {}).get("flags_high", 0),
            } if carrier else {}
        })
    
    load = {
        "from_city": load_db.from_city,
        "to_city": load_db.to_city,
        "weight": load_db.weight,
        "truck_type": "10т",
        "client_trust_score": client_trust.get("trust_score", 50),
        "client_flags_high": (client_trust.get("signals") or {}).get("flags_high", 0),
    }
    
    result = ai_logist.compare_bids(bids, load)
    result["load_id"] = load_id
    return result


@router.post("/generate-message")
def generate_driver_message(request: DriverMessageRequest):
    """
    📨 Генерация сообщения для рассылки водителям.
    
    Типы:
    - offer: стандартное предложение
    - urgent: срочная заявка
    - request_price: запрос цены
    """
    load = {
        "from_city": request.from_city,
        "to_city": request.to_city,
        "weight": request.weight,
        "volume": request.volume,
        "price": request.price,
        "loading_date": request.loading_date
    }
    
    result = ai_logist.generate_driver_message(load, request.message_type)
    return result


@router.post("/auto-dispatch")
def auto_dispatch(db: Session = Depends(get_db)):
    """
    🤖 Автоматическое распределение заявок по машинам.
    """
    # Получаем только актуальные заявки.
    expire_outdated_cargos(db)
    loads_db = apply_cargo_status_filter(db.query(Load), "active").all()
    loads = [{
        "id": l.id,
        "from_city": l.from_city,
        "to_city": l.to_city,
        "weight": l.weight,
        "volume": l.volume,
        "price": l.price,
        "status": l.status
    } for l in loads_db]
    
    # Получаем свободные машины
    trucks_db = db.query(Truck).filter(Truck.status == "free").all()
    trucks = []
    for truck in trucks_db:
        owner = db.query(User).filter(User.id == truck.user_id).first()
        trucks.append({
            "id": truck.id,
            "type": truck.type,
            "capacity": truck.capacity,
            "region": truck.region,
            "status": truck.status,
            "user_id": truck.user_id,
            "owner": {
                "fullname": owner.fullname,
                "rating": owner.rating
            } if owner else {}
        })
    
    result = ai_logist.auto_dispatch(loads, trucks)
    return result


@router.get("/route-analytics")
def route_analytics(from_city: str, to_city: str):
    """
    📊 Аналитика по маршруту.
    """
    result = ai_logist.get_route_analytics(from_city, to_city)
    return result


@router.get("/quick-price")
def quick_price(from_city: str, to_city: str, weight: float = 10, 
                truck_type: str = "10т"):
    """
    ⚡ Быстрый расчёт цены.
    """
    result = ai_logist.calculate_price(from_city, to_city, truck_type, weight)
    
    pricing = result.get("pricing") or {}
    min_price = pricing.get("min")
    max_price = pricing.get("max")

    return {
        "route": f"{from_city} → {to_city}",
        "truck_type": truck_type,
        "weight": weight,
        "price": pricing.get("recommended"),
        "price_range": f"{min_price} - {max_price} ₽" if min_price is not None and max_price is not None else None,
        "per_km": pricing.get("per_km"),
        "warning": result.get("warning")
    }


@router.get("/status")
def get_logist_status():
    """
    📊 Статус AI-Логиста.
    """
    return {
        "service": "AI-Logist",
        "version": "1.0.0",
        "status": "active",
        "features": [
            "Рекомендация типа ТС",
            "Прогноз стоимости",
            "Поиск и подбор машин",
            "Сравнение ставок",
            "ТОП-3 предложений",
            "Trust score в матчинге",
            "Авто-распределение",
            "Генерация сообщений для водителей",
            "Аналитика по маршрутам"
        ],
        "truck_types": list(ai_logist.TRUCK_SPECS.keys()),
        "endpoints": {
            "POST /logist/recommend-truck": "Рекомендация типа ТС",
            "POST /logist/calculate-price": "Расчёт цены",
            "POST /logist/find-trucks": "Поиск машин",
            "POST /logist/compare-bids/{load_id}": "Сравнение ставок",
            "POST /logist/generate-message": "Сообщение для водителей",
            "POST /logist/auto-dispatch": "Авто-распределение",
            "GET /logist/route-analytics": "Аналитика маршрута",
            "GET /logist/quick-price": "Быстрый расчёт"
        }
    }
