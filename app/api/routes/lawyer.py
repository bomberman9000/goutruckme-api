"""
🤖 API для AI-Юриста
Эндпоинты для юридического анализа заявок
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.db.database import SessionLocal
from app.models.models import Load, User
from app.services.ai_lawyer_llm import ai_lawyer_llm

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ SCHEMAS ============

class LoadAnalysisRequest(BaseModel):
    """Запрос на анализ заявки."""
    from_city: str
    to_city: str
    weight: Optional[float] = None
    volume: Optional[float] = None
    price: float
    description: Optional[str] = None
    shipper_inn: Optional[str] = None
    carrier_inn: Optional[str] = None
    truck_type: Optional[str] = None
    loading_date: Optional[str] = None
    payment_terms: Optional[str] = None
    additional_info: Optional[str] = None


class ContractCheckRequest(BaseModel):
    """Запрос на проверку договора."""
    contract_text: str


class RequisitesCheckRequest(BaseModel):
    """Запрос на проверку реквизитов."""
    inn: Optional[str] = None
    ogrn: Optional[str] = None
    company_name: Optional[str] = None


class PricingAnalysisRequest(BaseModel):
    """Запрос на анализ цены."""
    from_city: str
    to_city: str
    weight: float
    price: float


# ============ ENDPOINTS ============

@router.post("/analyze")
def analyze_load_request(request: LoadAnalysisRequest):
    """
    🔍 Полный анализ заявки на груз.
    
    Возвращает юридическое заключение с:
    - Уровнем риска (ok / low / medium / high / critical)
    - Риск-скором (0-100)
    - Списком проблем
    - Рекомендациями
    - Анализом цены
    - Проверкой реквизитов
    """
    load_data = request.dict()
    conclusion = ai_lawyer_llm.get_legal_conclusion(load_data)
    return conclusion


@router.post("/analyze/{load_id}")
def analyze_load_by_id(load_id: int, db: Session = Depends(get_db)):
    """
    🔍 Анализ заявки из базы данных по ID.
    """
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Получаем данные заказчика
    shipper = db.query(User).filter(User.id == load.user_id).first()
    
    load_data = {
        "from_city": load.from_city,
        "to_city": load.to_city,
        "weight": load.weight,
        "volume": load.volume,
        "price": load.price,
        "shipper_inn": shipper.company if shipper else None,  # В реальности тут ИНН
    }
    
    conclusion = ai_lawyer_llm.get_legal_conclusion(load_data)
    conclusion["load_id"] = load_id
    return conclusion


@router.post("/check-contract")
def check_contract(request: ContractCheckRequest):
    """
    📄 Проверка текста договора.
    
    Анализирует:
    - Юридические ошибки
    - Невыгодные условия
    - Отсутствующие пункты
    - Риски для сторон
    """
    result = ai_lawyer_llm.check_contract(request.contract_text)
    return result


@router.post("/verify-requisites")
def verify_requisites(request: RequisitesCheckRequest):
    """
    ✅ Проверка реквизитов контрагента.
    
    Проверяет:
    - ИНН (формат и контрольная сумма)
    - ОГРН/ОГРНИП
    - Название компании
    """
    result = ai_lawyer_llm.verify_requisites(
        inn=request.inn,
        ogrn=request.ogrn,
        company_name=request.company_name
    )
    return result


@router.post("/analyze-pricing")
def analyze_pricing(request: PricingAnalysisRequest):
    """
    💰 Анализ адекватности цены.
    
    Сравнивает с рыночными ставками и выдаёт:
    - Статус (too_low / adequate / too_high)
    - Рекомендуемую цену
    - Минимальную и максимальную приемлемую цену
    """
    result = ai_lawyer_llm.analyze_pricing(
        from_city=request.from_city,
        to_city=request.to_city,
        weight=request.weight,
        price=request.price
    )
    return result


@router.post("/quick-check")
def quick_check(from_city: str, to_city: str, price: float, 
                weight: float = 10, shipper_inn: str = None):
    """
    ⚡ Быстрая проверка заявки (упрощённый вызов).
    """
    load_data = {
        "from_city": from_city,
        "to_city": to_city,
        "price": price,
        "weight": weight,
        "shipper_inn": shipper_inn
    }
    
    # Только базовый анализ без LLM для скорости
    result = ai_lawyer_llm.analyze_load(load_data, use_llm=False)
    
    # Добавляем эмодзи-статус для удобства
    if result["risk_score"] < 20:
        result["emoji_status"] = "✅ Безопасно"
    elif result["risk_score"] < 40:
        result["emoji_status"] = "⚠️ Низкий риск"
    elif result["risk_score"] < 60:
        result["emoji_status"] = "🟠 Средний риск"
    elif result["risk_score"] < 80:
        result["emoji_status"] = "🔴 Высокий риск"
    else:
        result["emoji_status"] = "🚨 Критический риск"
    
    return result


@router.get("/status")
def get_lawyer_status():
    """
    📊 Статус AI-Юриста.
    """
    return {
        "service": "AI-Lawyer",
        "version": "1.0.0",
        "status": "active",
        "llm_provider": ai_lawyer_llm.llm_provider,
        "llm_available": ai_lawyer_llm.llm_provider != "mock",
        "features": [
            "Анализ заявок на грузоперевозки",
            "Проверка договоров",
            "Верификация реквизитов (ИНН/ОГРН)",
            "Анализ ценообразования",
            "Риск-скоринг",
            "Юридические рекомендации"
        ],
        "endpoints": {
            "POST /lawyer/analyze": "Полный анализ заявки",
            "POST /lawyer/analyze/{load_id}": "Анализ заявки из БД",
            "POST /lawyer/check-contract": "Проверка договора",
            "POST /lawyer/verify-requisites": "Проверка реквизитов",
            "POST /lawyer/analyze-pricing": "Анализ цены",
            "POST /lawyer/quick-check": "Быстрая проверка"
        }
    }




