"""
📄 API для AI-Документов
Генерация УПД, ТТН, договоров, счетов, актов, путевых листов
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict
from app.db.database import SessionLocal
from app.models.models import Load, User, Bid
from app.services.ai_documents import ai_documents
from app.services.geo import canonicalize_city_name

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ SCHEMAS ============

class CompanyData(BaseModel):
    """Данные компании."""
    fullname: str
    company: Optional[str] = None
    inn: Optional[str] = None
    kpp: Optional[str] = None
    ogrn: Optional[str] = None
    address: Optional[str] = None
    bank: Optional[str] = None
    bik: Optional[str] = None
    account: Optional[str] = None
    corr_account: Optional[str] = None
    director: Optional[str] = None
    phone: Optional[str] = None


class CargoData(BaseModel):
    """Данные груза."""
    name: str = "Груз"
    weight: float = 0
    volume: float = 0
    quantity: int = 1
    unit: str = "шт"
    price: float = 0
    packaging: str = ""


class LoadData(BaseModel):
    """Данные заявки."""
    from_city: str
    to_city: str
    price: float
    weight: Optional[float] = None
    volume: Optional[float] = None
    loading_date: Optional[str] = None
    cargo_name: Optional[str] = None


class DriverData(BaseModel):
    """Данные водителя."""
    name: str
    license: Optional[str] = None
    phone: Optional[str] = None


class VehicleData(BaseModel):
    """Данные ТС."""
    model: str
    plate: str
    type: Optional[str] = None
    trailer_plate: Optional[str] = None


class GenerateUPDRequest(BaseModel):
    """Запрос на генерацию УПД."""
    shipper: CompanyData
    carrier: CompanyData
    cargo: CargoData
    load: LoadData
    price: float


class GenerateTTNRequest(BaseModel):
    """Запрос на генерацию ТТН."""
    shipper: CompanyData
    carrier: CompanyData
    cargo: CargoData
    load: LoadData
    driver: DriverData
    vehicle: VehicleData


class GenerateContractRequest(BaseModel):
    """Запрос на генерацию договора."""
    shipper: CompanyData
    carrier: CompanyData
    load: LoadData
    payment_terms: Optional[str] = None
    delivery_deadline: Optional[str] = None


class GenerateInvoiceRequest(BaseModel):
    """Запрос на генерацию счёта."""
    seller: CompanyData
    buyer: CompanyData
    items: List[Dict]


class GenerateActRequest(BaseModel):
    """Запрос на генерацию акта."""
    customer: CompanyData
    contractor: CompanyData
    services: List[Dict]


class GenerateWaybillRequest(BaseModel):
    """Запрос на генерацию путевого листа."""
    carrier: CompanyData
    driver: DriverData
    vehicle: VehicleData
    route_from: str
    route_to: str


class FullPackageRequest(BaseModel):
    """Запрос на генерацию полного пакета."""
    shipper: CompanyData
    carrier: CompanyData
    driver: DriverData
    vehicle: VehicleData
    cargo: CargoData
    load: LoadData


# ============ ENDPOINTS ============

@router.post("/upd")
def generate_upd(request: GenerateUPDRequest):
    """
    📄 Генерация УПД (Универсальный передаточный документ)
    """
    result = ai_documents.generate_upd(
        shipper=request.shipper.dict(),
        carrier=request.carrier.dict(),
        cargo=request.cargo.dict(),
        load=request.load.dict(),
        price=request.price
    )
    return result


@router.post("/ttn")
def generate_ttn(request: GenerateTTNRequest):
    """
    📄 Генерация ТТН (Товарно-транспортная накладная)
    """
    result = ai_documents.generate_ttn(
        shipper=request.shipper.dict(),
        carrier=request.carrier.dict(),
        cargo=request.cargo.dict(),
        load=request.load.dict(),
        driver=request.driver.dict(),
        vehicle=request.vehicle.dict()
    )
    return result


@router.post("/contract")
def generate_contract(request: GenerateContractRequest):
    """
    📄 Генерация Договора перевозки
    """
    terms = {}
    if request.payment_terms:
        terms["payment_terms"] = request.payment_terms
    if request.delivery_deadline:
        terms["delivery_deadline"] = request.delivery_deadline
    
    result = ai_documents.generate_contract(
        shipper=request.shipper.dict(),
        carrier=request.carrier.dict(),
        load=request.load.dict(),
        terms=terms
    )
    return result


@router.post("/invoice")
def generate_invoice(request: GenerateInvoiceRequest):
    """
    📄 Генерация Счёта на оплату
    """
    result = ai_documents.generate_invoice(
        seller=request.seller.dict(),
        buyer=request.buyer.dict(),
        items=request.items
    )
    return result


@router.post("/act")
def generate_act(request: GenerateActRequest):
    """
    📄 Генерация Акта сдачи-приёмки
    """
    result = ai_documents.generate_act(
        customer=request.customer.dict(),
        contractor=request.contractor.dict(),
        services=request.services
    )
    return result


@router.post("/waybill")
def generate_waybill(request: GenerateWaybillRequest):
    """
    📄 Генерация Путевого листа
    """
    result = ai_documents.generate_waybill(
        carrier=request.carrier.dict(),
        driver=request.driver.dict(),
        vehicle=request.vehicle.dict(),
        route={"from": request.route_from, "to": request.route_to}
    )
    return result


@router.post("/full-package")
def generate_full_package(request: FullPackageRequest):
    """
    📦 Генерация ПОЛНОГО ПАКЕТА документов:
    - Договор перевозки
    - Счёт на оплату
    - ТТН
    - Путевой лист
    - УПД
    - Акт сдачи-приёмки
    """
    result = ai_documents.generate_full_package(
        shipper=request.shipper.dict(),
        carrier=request.carrier.dict(),
        driver=request.driver.dict(),
        vehicle=request.vehicle.dict(),
        cargo=request.cargo.dict(),
        load=request.load.dict()
    )
    return result


@router.post("/for-load/{load_id}")
def generate_docs_for_load(load_id: int, bid_id: int, 
                           driver: DriverData, vehicle: VehicleData,
                           db: Session = Depends(get_db)):
    """
    📦 Генерация документов для заявки из БД
    """
    # Получаем заявку
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Получаем ставку
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Ставка не найдена")
    
    # Получаем участников
    shipper = db.query(User).filter(User.id == load.user_id).first()
    carrier = db.query(User).filter(User.id == bid.carrier_id).first()
    
    if not shipper or not carrier:
        raise HTTPException(status_code=404, detail="Участники не найдены")
    
    # Формируем данные
    shipper_data = {
        "fullname": shipper.fullname,
        "company": shipper.company,
        "phone": shipper.phone
    }
    
    carrier_data = {
        "fullname": carrier.fullname,
        "company": carrier.company,
        "phone": carrier.phone
    }
    
    load_data = {
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "price": bid.price,
        "weight": load.weight,
        "volume": load.volume
    }
    
    cargo_data = {
        "name": "Груз",
        "weight": load.weight or 0,
        "volume": load.volume or 0
    }
    
    # Генерируем пакет
    result = ai_documents.generate_full_package(
        shipper=shipper_data,
        carrier=carrier_data,
        driver=driver.dict(),
        vehicle=vehicle.dict(),
        cargo=cargo_data,
        load=load_data
    )
    
    result["load_id"] = load_id
    result["bid_id"] = bid_id
    
    return result


@router.get("/get/{doc_number}")
def get_document(doc_number: str):
    """
    📄 Получить документ по номеру
    """
    result = ai_documents.get_document(doc_number)
    return result


@router.get("/html/{doc_number}", response_class=HTMLResponse)
def get_document_html(doc_number: str):
    """
    📄 Получить HTML документа (для печати/PDF)
    """
    html = ai_documents.get_document_html(doc_number)
    return HTMLResponse(content=html)


@router.get("/list")
def list_documents(doc_type: str = None, limit: int = 50):
    """
    📋 Список документов
    """
    result = ai_documents.list_documents(doc_type, limit)
    return result


@router.get("/types")
def get_document_types():
    """
    📋 Доступные типы документов
    """
    return {
        "types": [
            {"code": "upd", "name": "УПД", "description": "Универсальный передаточный документ"},
            {"code": "ttn", "name": "ТТН", "description": "Товарно-транспортная накладная"},
            {"code": "contract", "name": "Договор", "description": "Договор перевозки груза"},
            {"code": "invoice", "name": "Счёт", "description": "Счёт на оплату"},
            {"code": "act", "name": "Акт", "description": "Акт сдачи-приёмки"},
            {"code": "waybill", "name": "Путевой лист", "description": "Путевой лист грузового автомобиля"}
        ]
    }


@router.get("/status")
def get_documents_status():
    """
    📊 Статус AI-Документов
    """
    return {
        "service": "AI-Documents",
        "version": "1.0.0",
        "status": "active",
        "total_documents": len(ai_documents.documents_storage),
        "counters": ai_documents.doc_counters,
        "features": [
            "📄 УПД — Универсальный передаточный документ",
            "📄 ТТН — Товарно-транспортная накладная (форма 1-Т)",
            "📄 Договор перевозки груза",
            "📄 Счёт на оплату",
            "📄 Акт сдачи-приёмки",
            "📄 Путевой лист (форма 4-П)",
            "📦 Полный пакет документов",
            "🖨️ HTML для печати",
            "📊 История документов"
        ],
        "endpoints": {
            "POST /documents/upd": "Генерация УПД",
            "POST /documents/ttn": "Генерация ТТН",
            "POST /documents/contract": "Генерация договора",
            "POST /documents/invoice": "Генерация счёта",
            "POST /documents/act": "Генерация акта",
            "POST /documents/waybill": "Генерация путевого листа",
            "POST /documents/full-package": "Полный пакет документов",
            "POST /documents/for-load/{id}": "Документы для заявки из БД",
            "GET /documents/get/{number}": "Получить документ",
            "GET /documents/html/{number}": "HTML документа",
            "GET /documents/list": "Список документов"
        }
    }



