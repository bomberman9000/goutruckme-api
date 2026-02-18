from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models.models import Load, User, Bid, Message, Truck
from app.services.ai_moderator import ai_moderator
from app.services.ai_lawyer import ai_lawyer
from app.services.ai_support import ai_support
from app.services.ai_dispatcher import ai_dispatcher
from app.services.ai_driver import ai_driver
from app.services.ai_analytics import ai_analytics
from app.services.ai_verification import ai_verification

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ AI MODERATOR ============

@router.post("/moderate/load/{load_id}")
def moderate_load(load_id: int, db: Session = Depends(get_db)):
    """Проверка заявки ИИ-модератором."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        return {"error": "Load not found"}
    return ai_moderator.check_load(load)


@router.post("/moderate/user/{user_id}")
def moderate_user(user_id: int, db: Session = Depends(get_db)):
    """Проверка пользователя ИИ-модератором."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "User not found"}
    return ai_moderator.check_user(user)


@router.post("/moderate/bid/{bid_id}")
def moderate_bid(bid_id: int, db: Session = Depends(get_db)):
    """Проверка ставки ИИ-модератором."""
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        return {"error": "Bid not found"}
    load = db.query(Load).filter(Load.id == bid.load_id).first()
    return ai_moderator.check_bid(bid, load)


@router.post("/moderate/fraud-check")
def check_fraud(text: str):
    """Проверка текста на мошенничество."""
    return ai_moderator.detect_fraud(text)


# ============ AI LAWYER ============

@router.post("/lawyer/contract/{load_id}/{bid_id}")
def generate_contract(load_id: int, bid_id: int, db: Session = Depends(get_db)):
    """Генерация договора ИИ-юристом."""
    load = db.query(Load).filter(Load.id == load_id).first()
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not load or not bid:
        return {"error": "Load or Bid not found"}
    shipper = db.query(User).filter(User.id == load.user_id).first()
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    return ai_lawyer.generate_contract(load, shipper, carrier, bid)


@router.post("/lawyer/contract-text/{load_id}/{bid_id}")
def generate_contract_text(load_id: int, bid_id: int, db: Session = Depends(get_db)):
    """Генерация текста договора."""
    load = db.query(Load).filter(Load.id == load_id).first()
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not load or not bid:
        return {"error": "Load or Bid not found"}
    shipper = db.query(User).filter(User.id == load.user_id).first()
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    return {"contract_text": ai_lawyer.generate_contract_text(load, shipper, carrier, bid)}


@router.get("/lawyer/verify/{user_id}")
def verify_carrier(user_id: int, db: Session = Depends(get_db)):
    """Проверка документов перевозчика."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "User not found"}
    return ai_lawyer.check_documents(user)


@router.post("/lawyer/dispute/{load_id}")
def analyze_dispute(load_id: int, db: Session = Depends(get_db)):
    """Анализ спора по заявке."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        return {"error": "Load not found"}
    messages = db.query(Message).filter(Message.load_id == load_id).all()
    return ai_lawyer.analyze_dispute(messages, load)


# ============ AI SUPPORT ============

@router.post("/support/chat")
def chat_support(message: str):
    """Чат с ИИ-поддержкой."""
    return ai_support.get_response(message)


@router.post("/support/complaint")
def create_complaint(user_id: int, complaint_text: str):
    """Создание жалобы."""
    return ai_support.handle_complaint(user_id, complaint_text)


@router.get("/support/help")
def get_help():
    """Получить меню помощи."""
    return ai_support.get_help_menu()


# ============ AI DISPATCHER ============

@router.post("/dispatcher/match/{load_id}")
def find_trucks_for_load(load_id: int, db: Session = Depends(get_db)):
    """Поиск подходящих машин для груза."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        return {"error": "Load not found"}
    trucks = db.query(Truck).all()
    return ai_dispatcher.find_matching_trucks(load, trucks)


@router.post("/dispatcher/check-rate")
def check_rate(load_id: int, proposed_rate: float, db: Session = Depends(get_db)):
    """Проверка адекватности ставки."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        return {"error": "Load not found"}
    return ai_dispatcher.check_rate_adequacy(load, proposed_rate)


@router.post("/dispatcher/distribute")
def auto_distribute(db: Session = Depends(get_db)):
    """Автоматическое распределение заявок по машинам."""
    loads = db.query(Load).filter(Load.status == "open").all()
    trucks = db.query(Truck).filter(Truck.status == "free").all()
    return ai_dispatcher.auto_distribute_loads(loads, trucks)


@router.post("/dispatcher/alerts/{load_id}/{carrier_id}")
def get_alerts(load_id: int, carrier_id: int, db: Session = Depends(get_db)):
    """Получить предупреждения по заявке и перевозчику."""
    load = db.query(Load).filter(Load.id == load_id).first()
    carrier = db.query(User).filter(User.id == carrier_id).first()
    if not load or not carrier:
        return {"error": "Load or Carrier not found"}
    return ai_dispatcher.generate_alerts(load, carrier)


# ============ AI DRIVER ============

@router.post("/driver/route")
def calculate_route(from_city: str, to_city: str):
    """Расчёт маршрута."""
    return ai_driver.calculate_route(from_city, to_city)


@router.post("/driver/fuel")
def calculate_fuel(distance_km: float, truck_type: str):
    """Расчёт топлива."""
    return ai_driver.calculate_fuel(distance_km, truck_type)


@router.post("/driver/profitability")
def calculate_profitability(load_price: float, distance_km: float, 
                            truck_type: str, toll_cost: float = 0):
    """Расчёт прибыльности рейса."""
    return ai_driver.calculate_profitability(load_price, distance_km, truck_type, toll_cost)


@router.post("/driver/loading-time")
def estimate_loading_time(cargo_type: str = "general", weight: float = 10):
    """Оценка времени погрузки."""
    return ai_driver.estimate_loading_time(cargo_type, weight)


@router.post("/driver/voice-hints")
def get_voice_hints(from_city: str, to_city: str):
    """Голосовые подсказки для водителя."""
    route_info = ai_driver.calculate_route(from_city, to_city)
    return ai_driver.generate_voice_hints(route_info)


@router.post("/driver/waybill/{load_id}")
def generate_waybill(load_id: int, driver_name: str, driver_phone: str,
                     truck_plate: str, truck_type: str, db: Session = Depends(get_db)):
    """Генерация путевого листа."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        return {"error": "Load not found"}
    driver = {"name": driver_name, "phone": driver_phone}
    truck = {"plate": truck_plate, "type": truck_type}
    return ai_driver.generate_waybill(load, driver, truck)


# ============ AI ANALYTICS ============

@router.get("/analytics/company/{user_id}")
def company_stats(user_id: int, period_days: int = 30, db: Session = Depends(get_db)):
    """Статистика компании."""
    loads = db.query(Load).filter(Load.user_id == user_id).all()
    bids = db.query(Bid).all()
    return ai_analytics.calculate_company_stats(loads, bids, period_days)


@router.get("/analytics/carrier/{user_id}")
def carrier_stats(user_id: int, period_days: int = 30, db: Session = Depends(get_db)):
    """Статистика перевозчика."""
    bids = db.query(Bid).all()
    loads = db.query(Load).all()
    return ai_analytics.calculate_carrier_stats(user_id, bids, loads, period_days)


@router.post("/analytics/market-rates")
def market_rates(from_city: str = None, to_city: str = None, db: Session = Depends(get_db)):
    """Анализ рыночных ставок."""
    loads = db.query(Load).all()
    route = (from_city, to_city) if from_city and to_city else None
    return ai_analytics.analyze_market_rates(loads, route)


@router.post("/analytics/optimize-rates/{user_id}")
def optimize_rates(user_id: int, db: Session = Depends(get_db)):
    """Оптимизация ставок."""
    bids = db.query(Bid).filter(Bid.carrier_id == user_id).all()
    return ai_analytics.optimize_rates(bids)


@router.get("/analytics/report/{user_id}")
def generate_report(user_id: int, period: str = "month", db: Session = Depends(get_db)):
    """Генерация отчёта."""
    loads = db.query(Load).filter(Load.user_id == user_id).all()
    bids = db.query(Bid).all()
    return ai_analytics.generate_report(user_id, loads, bids, period)


@router.post("/analytics/demand-forecast")
def demand_forecast(from_city: str, to_city: str, db: Session = Depends(get_db)):
    """Прогноз спроса на направлении."""
    loads = db.query(Load).all()
    return ai_analytics.predict_demand(loads, (from_city, to_city))


# ============ AI VERIFICATION ============

@router.post("/verify/inn")
def verify_inn(inn: str):
    """Проверка ИНН."""
    return ai_verification.verify_inn(inn)


@router.post("/verify/ogrn")
def verify_ogrn(ogrn: str):
    """Проверка ОГРН."""
    return ai_verification.verify_ogrn(ogrn)


@router.post("/verify/phone")
def verify_phone(phone: str):
    """Проверка телефона."""
    return ai_verification.verify_phone(phone)


@router.post("/verify/contractor")
def verify_contractor(inn: str = None, ogrn: str = None, phone: str = None):
    """Комплексная проверка контрагента."""
    return ai_verification.verify_contractor(inn, ogrn, phone)


@router.post("/verify/blacklist")
def check_blacklist(inn: str = None, phone: str = None):
    """Проверка по чёрному списку."""
    return ai_verification.check_blacklist(inn, phone)


@router.post("/verify/full-report")
def full_verification_report(inn: str = None, ogrn: str = None, 
                             phone: str = None, company_name: str = None):
    """Полный отчёт верификации."""
    contractor_data = {
        "inn": inn,
        "ogrn": ogrn,
        "phone": phone,
        "company_name": company_name
    }
    return ai_verification.generate_verification_report(contractor_data)
