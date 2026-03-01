"""
💬 API для AI-Чатбота
Автоматический диспетчер: общение с водителями, сбор ставок, переговоры
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict
from app.db.database import SessionLocal
from app.models.models import Load, User, Truck, Bid
from app.services.ai_chatbot import ai_chatbot
from app.services.geo import canonicalize_city_name

router = APIRouter()


# ============ CHATGPT SIMPLE CHAT ============

class ChatMessage(BaseModel):
    """Простое сообщение для ChatGPT."""
    message: str
    conversation_id: Optional[str] = None


@router.post("/chat")
async def chat_with_gpt(request: ChatMessage):
    """
    💬 Простой чат с ChatGPT.
    Универсальный помощник для любых вопросов.
    """
    try:
        import os
        from openai import OpenAI
        
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return {
                "response": "⚠️ ChatGPT не настроен. Добавьте OPENAI_API_KEY в .env файл.",
                "error": "no_api_key"
            }
        
        client = OpenAI(api_key=api_key)
        
        # Системный промпт для контекста грузоперевозок
        system_prompt = """Ты — AI-помощник платформы ГрузПоток, умной биржи грузоперевозок.

Твоя задача — помогать пользователям с вопросами о:
- Грузоперевозках
- Логистике
- Документах (УПД, ТТН, договоры)
- Поиске грузов и машин
- Ценообразовании
- Юридических вопросах
- Использовании платформы

Отвечай дружелюбно, профессионально и по делу. Если вопрос не связан с грузоперевозками, всё равно помоги пользователю."""
        
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.message}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        return {
            "response": response.choices[0].message.content,
            "conversation_id": request.conversation_id or "default"
        }
        
    except Exception as e:
        return {
            "response": f"❌ Ошибка: {str(e)}",
            "error": str(e)
        }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ SCHEMAS ============

class MessageRequest(BaseModel):
    """Входящее сообщение от пользователя."""
    user_id: int
    message: str
    context: Optional[Dict] = None


class SendOfferRequest(BaseModel):
    """Запрос на отправку предложения."""
    user_id: int
    load_id: int
    urgent: bool = False


class BroadcastRequest(BaseModel):
    """Запрос на массовую рассылку."""
    load_id: int
    user_ids: List[int]
    urgent: bool = False


class NegotiationRequest(BaseModel):
    """Запрос на начало переговоров."""
    user_id: int
    load_id: int
    target_price: float


class ConfirmDealRequest(BaseModel):
    """Запрос на подтверждение сделки."""
    user_id: int
    load_id: int
    price: float


# ============ ENDPOINTS ============

@router.post("/message")
def process_message(request: MessageRequest):
    """
    💬 Обработка входящего сообщения.
    
    Главный эндпоинт — принимает сообщения от водителей,
    анализирует и генерирует ответ.
    """
    result = ai_chatbot.process_message(
        user_id=request.user_id,
        message=request.message,
        context=request.context
    )
    return result


@router.post("/send-offer")
def send_load_offer(request: SendOfferRequest, db: Session = Depends(get_db)):
    """
    📨 Отправка предложения заявки водителю.
    """
    # Получаем заявку
    load = db.query(Load).filter(Load.id == request.load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    load_data = {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "weight": load.weight,
        "volume": load.volume,
        "price": load.price,
        "cargo_name": "Груз",
        "loading_date": "по договорённости"
    }
    
    result = ai_chatbot.send_load_offer(
        user_id=request.user_id,
        load=load_data,
        urgent=request.urgent
    )
    
    return result


@router.post("/broadcast")
def broadcast_load(request: BroadcastRequest, db: Session = Depends(get_db)):
    """
    📢 Массовая рассылка заявки водителям.
    
    Отправляет предложение всем указанным водителям.
    """
    # Получаем заявку
    load = db.query(Load).filter(Load.id == request.load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    load_data = {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "weight": load.weight,
        "volume": load.volume,
        "price": load.price
    }
    
    result = ai_chatbot.broadcast_load(
        load=load_data,
        user_ids=request.user_ids,
        urgent=request.urgent
    )
    
    return result


@router.post("/broadcast-to-region/{load_id}")
def broadcast_to_region(load_id: int, db: Session = Depends(get_db)):
    """
    📢 Рассылка водителям в регионе погрузки.
    
    Автоматически находит всех свободных водителей в регионе.
    """
    # Получаем заявку
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    from_city = canonicalize_city_name(load.from_city)
    to_city = canonicalize_city_name(load.to_city)

    # Находим водителей в регионе
    trucks = db.query(Truck).filter(
        Truck.status == "free",
        Truck.region.ilike(f"%{from_city}%")
    ).all()
    
    user_ids = list(set(t.user_id for t in trucks))
    
    if not user_ids:
        return {
            "load_id": load_id,
            "message": "В регионе нет свободных водителей",
            "sent_to": 0
        }
    
    load_data = {
        "id": load.id,
        "from_city": from_city,
        "to_city": to_city,
        "weight": load.weight,
        "volume": load.volume,
        "price": load.price
    }
    
    result = ai_chatbot.broadcast_load(load_data, user_ids)
    return result


@router.post("/negotiate")
def start_negotiation(request: NegotiationRequest, db: Session = Depends(get_db)):
    """
    🤝 Начало переговоров с водителем.
    """
    load = db.query(Load).filter(Load.id == request.load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    load_data = {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "price": load.price
    }
    
    result = ai_chatbot.start_negotiation(
        user_id=request.user_id,
        load=load_data,
        target_price=request.target_price
    )
    
    return result


@router.post("/confirm-deal")
def confirm_deal(request: ConfirmDealRequest, db: Session = Depends(get_db)):
    """
    ✅ Подтверждение сделки с водителем.
    """
    load = db.query(Load).filter(Load.id == request.load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    shipper = db.query(User).filter(User.id == load.user_id).first()
    
    load_data = {
        "id": load.id,
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "loading_date": "по договорённости",
        "loading_address": ""
    }
    
    shipper_info = {
        "name": shipper.fullname if shipper else "",
        "phone": shipper.phone if shipper else ""
    }
    
    result = ai_chatbot.confirm_deal(
        user_id=request.user_id,
        load=load_data,
        price=request.price,
        shipper_info=shipper_info
    )
    
    return result


@router.get("/bids-status/{load_id}")
def get_bids_status(load_id: int):
    """
    📊 Статус сбора ставок по заявке.
    """
    result = ai_chatbot.collect_bids_status(load_id)
    return result


@router.get("/dispatcher-report/{load_id}")
def get_dispatcher_report(load_id: int):
    """
    📋 Отчёт для диспетчера со всеми ставками.
    
    Возвращает:
    - ТОП-3 лучших предложения
    - Статистику по ставкам
    - Рекомендацию
    """
    result = ai_chatbot.get_dispatcher_report(load_id)
    return result


@router.get("/conversation/{user_id}")
def get_conversation(user_id: int):
    """
    💬 История диалога с пользователем.
    """
    result = ai_chatbot.get_conversation_summary(user_id)
    return result


@router.post("/ask-info/{user_id}")
def ask_for_info(user_id: int, info_type: str):
    """
    ❓ Запросить информацию у водителя.
    
    info_type: price / truck_type / availability / phone / name
    """
    result = ai_chatbot.ask_for_info(user_id, info_type)
    return result


@router.post("/collect-driver-info/{user_id}")
def collect_driver_info(user_id: int):
    """
    📋 Пошаговый сбор информации о водителе.
    """
    result = ai_chatbot.collect_driver_info(user_id)
    return result


@router.post("/generate-messages/{load_id}")
def generate_auto_messages(load_id: int, db: Session = Depends(get_db)):
    """
    🤖 Генерация сообщений для рассылки.
    
    Создаёт персонализированные сообщения для всех водителей.
    """
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Получаем всех водителей (carriers)
    from app.models.models import UserRole
    drivers = db.query(User).filter(User.role == UserRole.carrier).limit(50).all()
    
    load_data = {
        "from_city": canonicalize_city_name(load.from_city),
        "to_city": canonicalize_city_name(load.to_city),
        "weight": load.weight,
        "price": load.price
    }
    
    drivers_data = [
        {"id": d.id, "fullname": d.fullname, "phone": d.phone}
        for d in drivers
    ]
    
    messages = ai_chatbot.generate_auto_messages(load_data, drivers_data)
    
    return {
        "load_id": load_id,
        "generated_messages": len(messages),
        "messages": messages
    }


@router.get("/stats")
def get_chatbot_stats():
    """
    📊 Статистика чатбота.
    """
    total_conversations = len(ai_chatbot.conversations)
    total_bids = sum(len(bids) for bids in ai_chatbot.collected_bids.values())
    
    return {
        "total_conversations": total_conversations,
        "total_collected_bids": total_bids,
        "active_loads": len(ai_chatbot.collected_bids)
    }


@router.get("/status")
def get_chatbot_status():
    """
    📊 Статус AI-Чатбота.
    """
    return {
        "service": "ГрузПоток Бот",
        "description": "Добро пожаловать в ГрузПоток. Автоматический диспетчер для грузоперевозок.",
        "version": "1.0.0",
        "status": "active",
        "stats": {
            "conversations": len(ai_chatbot.conversations),
            "collected_bids": sum(len(b) for b in ai_chatbot.collected_bids.values())
        },
        "features": [
            "💬 Обработка сообщений от водителей",
            "📨 Рассылка предложений",
            "📢 Массовая рассылка по региону",
            "💰 Сбор ставок",
            "🤝 Автоматические переговоры",
            "✅ Подтверждение сделок",
            "📋 Сбор информации о водителях",
            "📊 Отчёты для диспетчера",
            "🤖 Генерация персональных сообщений"
        ],
        "capabilities": {
            "intent_detection": ["agree", "disagree", "price_offer", "greeting", "question"],
            "entity_extraction": ["price", "weight", "phone", "truck_type"],
            "conversation_states": ["idle", "collecting_info", "negotiating", "confirming", "completed"]
        },
        "endpoints": {
            "POST /chatbot/message": "💬 Обработка сообщения",
            "POST /chatbot/send-offer": "📨 Отправить предложение",
            "POST /chatbot/broadcast": "📢 Массовая рассылка",
            "POST /chatbot/broadcast-to-region/{id}": "📢 Рассылка по региону",
            "POST /chatbot/negotiate": "🤝 Начать переговоры",
            "POST /chatbot/confirm-deal": "✅ Подтвердить сделку",
            "GET /chatbot/bids-status/{id}": "📊 Статус ставок",
            "GET /chatbot/dispatcher-report/{id}": "📋 Отчёт для диспетчера",
            "GET /chatbot/conversation/{id}": "💬 История диалога"
        }
    }

