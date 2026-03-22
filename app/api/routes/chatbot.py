"""
💬 API для AI-Чатбота
Автоматический диспетчер: общение с водителями, сбор ставок, переговоры
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict
from app.core.security import get_current_user
from app.db.database import SessionLocal
from app.models.models import Load, User, Truck, Bid
from app.services.ai_chatbot import ai_chatbot
from app.services.load_public import build_public_load_context

router = APIRouter(dependencies=[Depends(get_current_user)])


# ============ CHATGPT SIMPLE CHAT ============

class ChatMessage(BaseModel):
    """Простое сообщение для ChatGPT."""
    message: str
    conversation_id: Optional[str] = None


def _build_chat_messages(user_message: str) -> list[dict[str, str]]:
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
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def _call_openai_chat(user_message: str) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("no_openai_api_key")

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=_build_chat_messages(user_message),
        temperature=0.7,
        max_tokens=500,
    )
    return response.choices[0].message.content


def _is_complex_question(message: str) -> bool:
    """Простой классификатор: сложный вопрос → Gemini, простой → Ollama."""
    words = message.strip().split()
    if len(words) <= 5:
        return False
    complex_keywords = [
        "документ", "договор", "ттн", "упд", "накладн", "юридич", "налог",
        "страхов", "ответственност", "закон", "штраф", "арбитраж", "суд",
        "рассчитай", "проанализируй", "объясни подробно", "почему", "как правильно",
        "calculate", "analyze", "explain",
    ]
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in complex_keywords):
        return True
    return len(words) > 15


def _call_gemini_chat(user_message: str) -> str:
    import urllib.request
    import json

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("no_gemini_api_key")

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    system_prompt = _build_chat_messages("")
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system_prompt[0]["content"]}]},
        "contents": [{"parts": [{"text": user_message}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_ollama_chat(user_message: str) -> str:
    import urllib.request
    import json

    base_url = os.getenv("OLLAMA_URL", "http://10.0.0.2:11434")
    model = os.getenv("OLLAMA_MODEL", "qwen3:30b")
    url = f"{base_url}/api/chat"

    system_prompt = _build_chat_messages("")[0]["content"]
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {"num_predict": 300},
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return data["message"]["content"]


def _is_openai_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    return "insufficient_quota" in text or "exceeded your current quota" in text or "429" in text


@router.post("/chat")
async def chat_with_gpt(request: ChatMessage):
    """
    💬 Умный чат: сложные вопросы → Gemini, простые → Ollama.
    Fallback: OpenAI → Groq.
    """
    message = request.message
    is_complex = _is_complex_question(message)

    # Сначала пробуем AI по сложности
    primary = "gemini" if is_complex else "ollama"
    secondary = "ollama" if is_complex else "gemini"

    for provider in [primary, secondary, "openai"]:
        try:
            if provider == "gemini":
                content = _call_gemini_chat(message)
            elif provider == "ollama":
                content = _call_ollama_chat(message)
            else:
                content = _call_openai_chat(message)
            return {
                "response": content,
                "conversation_id": request.conversation_id or "default",
                "provider": provider,
            }
        except Exception:
            continue

    return {
        "response": "⚠️ Все AI-провайдеры недоступны. Проверьте настройки.",
        "error": "all_providers_failed"
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
        **build_public_load_context(load),
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
    
    load_data = build_public_load_context(load)
    
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
    
    load_context = build_public_load_context(load)
    from_city = load_context["from_city"]

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
    
    load_data = load_context
    
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
    
    load_data = build_public_load_context(load)
    
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
        **build_public_load_context(load),
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
    
    load_data = build_public_load_context(load)
    
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
