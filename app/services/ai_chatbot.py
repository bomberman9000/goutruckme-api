"""
💬 AI-ЧАТБОТ (Модуль 5) — Автоматический диспетчер
Общается с водителями, собирает данные, ведёт переговоры, пишет ставки

Заменяет 70% ручной работы логиста!
"""
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import json


class ConversationState(str, Enum):
    """Состояния диалога."""
    IDLE = "idle"                           # Ожидание
    GREETING = "greeting"                   # Приветствие
    COLLECTING_INFO = "collecting_info"     # Сбор информации
    NEGOTIATING = "negotiating"             # Переговоры
    CONFIRMING = "confirming"               # Подтверждение
    COMPLETED = "completed"                 # Завершено
    CANCELLED = "cancelled"                 # Отменено


class MessageType(str, Enum):
    """Типы сообщений."""
    TEXT = "text"
    LOAD_OFFER = "load_offer"               # Предложение заявки
    BID_REQUEST = "bid_request"             # Запрос ставки
    BID_RESPONSE = "bid_response"           # Ответ со ставкой
    CONFIRMATION = "confirmation"           # Подтверждение
    REJECTION = "rejection"                 # Отказ
    INFO_REQUEST = "info_request"           # Запрос информации
    INFO_RESPONSE = "info_response"         # Ответ с информацией


@dataclass
class Conversation:
    """Диалог с пользователем."""
    user_id: int
    state: ConversationState = ConversationState.IDLE
    context: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def add_message(self, role: str, text: str, msg_type: str = "text"):
        self.history.append({
            "role": role,  # "bot" or "user"
            "text": text,
            "type": msg_type,
            "timestamp": datetime.now().isoformat()
        })
        self.updated_at = datetime.now()


class AIChatbot:
    """
    💬 AI-Чатбот — автоматический диспетчер.
    
    Возможности:
    - 📨 Рассылка заявок водителям
    - 💬 Сбор информации (вес, габариты, тип ТС)
    - 💰 Сбор ставок от водителей
    - 🤝 Ведение переговоров по цене
    - ✅ Подтверждение и бронирование
    - 📊 Формирование отчёта для диспетчера
    
    Заменяет 70% ручной работы!
    """
    
    # Хранилище диалогов
    conversations: Dict[int, Conversation] = {}
    
    # Собранные ставки
    collected_bids: Dict[int, List[Dict]] = defaultdict(list)  # load_id -> bids
    
    # Паттерны для распознавания
    PATTERNS = {
        "price": r'(\d+[\s,.]?\d*)\s*(руб|₽|р\.?|тыс|к)?',
        "weight": r'(\d+[\s,.]?\d*)\s*(т\.?|тонн|кг)',
        "phone": r'(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',
        "yes": r'\b(да|ок|окей|согласен|принимаю|беру|готов|подтверждаю|хорошо|ладно)\b',
        "no": r'\b(нет|не|отказ|отмена|не могу|не подходит|дорого|мало)\b',
        "truck_type": r'\b(газель|5\s*т|10\s*т|20\s*т|фура|рефрижератор|тент|борт)\b',
    }
    
    # Шаблоны сообщений
    TEMPLATES = {
        "greeting": "Добро пожаловать в ГрузПоток! Я — AI-диспетчер платформы. 🚛",
        
        "load_offer": """🚛 НОВАЯ ЗАЯВКА

📍 Маршрут: {from_city} → {to_city}
📦 Груз: {cargo_name}
⚖️ Вес: {weight} т
📐 Объём: {volume} м³
💰 Ставка заказчика: {price} ₽
📅 Погрузка: {loading_date}

Интересно? Напишите вашу цену!""",

        "load_offer_short": "🚛 {from_city} → {to_city}, {weight}т, {price}₽. Ваша ставка?",
        
        "urgent_load": """🔥 СРОЧНАЯ ЗАЯВКА!

📍 {from_city} → {to_city}
📦 {weight} т
💰 {price} ₽

Машина нужна СЕГОДНЯ! Готовы?""",

        "ask_price": "Какая у вас ставка на этот маршрут? 💰",
        
        "ask_truck_type": "Какой у вас тип машины? (газель / 5т / 10т / 20т / фура)",
        
        "ask_availability": "Когда сможете быть на погрузке? 📅",
        
        "price_too_low": "⚠️ Ваша ставка {bid_price}₽ ниже заявленной ({load_price}₽). Готовы рассмотреть {suggested_price}₽?",
        
        "price_accepted": "✅ Отлично! Ставка {price}₽ принята. Передаю заказчику для подтверждения.",
        
        "price_counter": "Заказчик предлагает {counter_price}₽. Согласны?",
        
        "confirm_deal": """✅ ПОДТВЕРЖДЕНИЕ

Маршрут: {from_city} → {to_city}
Ставка: {price} ₽
Погрузка: {loading_date}

Всё верно? Подтвердите, пожалуйста.""",

        "deal_confirmed": """🎉 Отлично! Заявка за вами!

📍 {from_city} → {to_city}
💰 {price} ₽
📅 {loading_date}

Контакт заказчика: {shipper_phone}
Адрес погрузки: {loading_address}

Удачной перевозки! 🚛""",

        "deal_rejected": "Понял, отменяю. Если передумаете — напишите!",
        
        "thanks": "Спасибо за ответ! 🙏",
        
        "not_understood": "Извините, не понял. Напишите вашу ставку цифрами или ответьте Да/Нет.",
        
        "goodbye": "До связи! Если будут вопросы — пишите. 👋",
        
        "collecting_bids_status": "⏳ Собираю ставки... Уже получено: {count}",
        
        "best_bid_found": """📊 ЛУЧШЕЕ ПРЕДЛОЖЕНИЕ

Водитель: {driver_name}
Телефон: {driver_phone}
Ставка: {price} ₽
Тип ТС: {truck_type}
Рейтинг: ⭐ {rating}

Подтвердить этого перевозчика?""",
    }
    
    def __init__(self):
        self.conversations = {}
        self.collected_bids = defaultdict(list)
    
    # ================== ОСНОВНЫЕ МЕТОДЫ ==================
    
    def process_message(self, user_id: int, message: str, 
                        context: Dict = None) -> Dict[str, Any]:
        """
        💬 Обработка входящего сообщения от пользователя.
        
        Главный метод — точка входа для всех сообщений.
        """
        
        # Получаем или создаём диалог
        conv = self._get_or_create_conversation(user_id)
        
        # Добавляем сообщение пользователя в историю
        conv.add_message("user", message)
        
        # Обновляем контекст если передан
        if context:
            conv.context.update(context)
        
        # Анализируем сообщение
        intent = self._detect_intent(message)
        entities = self._extract_entities(message)
        
        # Генерируем ответ в зависимости от состояния
        response = self._generate_response(conv, intent, entities, message)
        
        # Добавляем ответ бота в историю
        conv.add_message("bot", response["text"], response.get("type", "text"))
        
        return {
            "user_id": user_id,
            "state": conv.state.value,
            "response": response,
            "intent": intent,
            "entities": entities,
            "context": conv.context
        }
    
    def send_load_offer(self, user_id: int, load: Dict, 
                        urgent: bool = False) -> Dict[str, Any]:
        """
        📨 Отправка предложения заявки водителю.
        """
        conv = self._get_or_create_conversation(user_id)
        conv.state = ConversationState.COLLECTING_INFO
        conv.context["current_load"] = load
        conv.context["awaiting"] = "bid"
        
        # Выбираем шаблон
        template = "urgent_load" if urgent else "load_offer"
        
        message = self.TEMPLATES[template].format(
            from_city=load.get("from_city", ""),
            to_city=load.get("to_city", ""),
            cargo_name=load.get("cargo_name", "Груз"),
            weight=load.get("weight", ""),
            volume=load.get("volume", ""),
            price=load.get("price", ""),
            loading_date=load.get("loading_date", "по договорённости")
        )
        
        conv.add_message("bot", message, "load_offer")
        
        return {
            "user_id": user_id,
            "message": message,
            "type": "load_offer",
            "load_id": load.get("id"),
            "state": conv.state.value
        }
    
    def broadcast_load(self, load: Dict, user_ids: List[int], 
                       urgent: bool = False) -> Dict[str, Any]:
        """
        📢 Массовая рассылка заявки водителям.
        """
        results = []
        
        for user_id in user_ids:
            result = self.send_load_offer(user_id, load, urgent)
            results.append(result)
        
        return {
            "load_id": load.get("id"),
            "sent_to": len(user_ids),
            "results": results,
            "message": f"📢 Заявка отправлена {len(user_ids)} водителям"
        }
    
    def collect_bids_status(self, load_id: int) -> Dict[str, Any]:
        """
        📊 Статус сбора ставок по заявке.
        """
        bids = self.collected_bids.get(load_id, [])
        
        if not bids:
            return {
                "load_id": load_id,
                "status": "waiting",
                "bids_count": 0,
                "message": "⏳ Ожидаем ставки от водителей..."
            }
        
        # Сортируем по цене
        sorted_bids = sorted(bids, key=lambda x: x.get("price", float("inf")))
        best_bid = sorted_bids[0]
        
        return {
            "load_id": load_id,
            "status": "collecting",
            "bids_count": len(bids),
            "bids": sorted_bids,
            "best_bid": best_bid,
            "message": self.TEMPLATES["best_bid_found"].format(
                driver_name=best_bid.get("driver_name", ""),
                driver_phone=best_bid.get("driver_phone", ""),
                price=best_bid.get("price", ""),
                truck_type=best_bid.get("truck_type", ""),
                rating=best_bid.get("rating", "")
            )
        }
    
    def start_negotiation(self, user_id: int, load: Dict, 
                          target_price: float) -> Dict[str, Any]:
        """
        🤝 Начало переговоров с водителем.
        """
        conv = self._get_or_create_conversation(user_id)
        conv.state = ConversationState.NEGOTIATING
        conv.context["current_load"] = load
        conv.context["target_price"] = target_price
        conv.context["negotiation_rounds"] = 0
        
        message = f"""🤝 Давайте обсудим цену!

Маршрут: {load.get('from_city')} → {load.get('to_city')}
Заказчик готов на: {target_price} ₽

Какая минимальная цена для вас?"""
        
        conv.add_message("bot", message, "negotiation_start")
        
        return {
            "user_id": user_id,
            "message": message,
            "state": "negotiating",
            "target_price": target_price
        }
    
    def confirm_deal(self, user_id: int, load: Dict, price: float,
                     shipper_info: Dict) -> Dict[str, Any]:
        """
        ✅ Подтверждение сделки.
        """
        conv = self._get_or_create_conversation(user_id)
        conv.state = ConversationState.CONFIRMING
        conv.context["deal"] = {
            "load": load,
            "price": price,
            "shipper": shipper_info
        }
        
        message = self.TEMPLATES["deal_confirmed"].format(
            from_city=load.get("from_city", ""),
            to_city=load.get("to_city", ""),
            price=price,
            loading_date=load.get("loading_date", ""),
            shipper_phone=shipper_info.get("phone", ""),
            loading_address=load.get("loading_address", "")
        )
        
        conv.add_message("bot", message, "deal_confirmed")
        conv.state = ConversationState.COMPLETED
        
        return {
            "user_id": user_id,
            "message": message,
            "status": "confirmed",
            "deal": conv.context["deal"]
        }
    
    # ================== СБОР ИНФОРМАЦИИ ==================
    
    def ask_for_info(self, user_id: int, info_type: str) -> Dict[str, Any]:
        """
        ❓ Запрос информации у водителя.
        """
        conv = self._get_or_create_conversation(user_id)
        conv.context["awaiting"] = info_type
        
        questions = {
            "price": self.TEMPLATES["ask_price"],
            "truck_type": self.TEMPLATES["ask_truck_type"],
            "availability": self.TEMPLATES["ask_availability"],
            "phone": "Напишите ваш номер телефона для связи 📱",
            "name": "Как вас зовут? 👤",
            "truck_plate": "Номер вашего ТС? 🚛",
        }
        
        message = questions.get(info_type, "Пожалуйста, уточните информацию.")
        conv.add_message("bot", message, "info_request")
        
        return {
            "user_id": user_id,
            "message": message,
            "awaiting": info_type
        }
    
    def collect_driver_info(self, user_id: int) -> Dict[str, Any]:
        """
        📋 Пошаговый сбор информации о водителе.
        """
        conv = self._get_or_create_conversation(user_id)
        driver_info = conv.context.get("driver_info", {})
        
        # Определяем что ещё нужно собрать
        required = ["name", "phone", "truck_type", "truck_plate"]
        missing = [f for f in required if f not in driver_info]
        
        if not missing:
            # Вся информация собрана
            return {
                "user_id": user_id,
                "status": "complete",
                "driver_info": driver_info,
                "message": "✅ Спасибо! Вся информация получена."
            }
        
        # Запрашиваем следующее поле
        next_field = missing[0]
        return self.ask_for_info(user_id, next_field)
    
    # ================== ВНУТРЕННИЕ МЕТОДЫ ==================
    
    def _get_or_create_conversation(self, user_id: int) -> Conversation:
        """Получить или создать диалог."""
        if user_id not in self.conversations:
            self.conversations[user_id] = Conversation(user_id=user_id)
        return self.conversations[user_id]
    
    def _detect_intent(self, message: str) -> str:
        """Определение намерения пользователя."""
        message_lower = message.lower().strip()
        
        # Проверка на согласие
        if re.search(self.PATTERNS["yes"], message_lower):
            return "agree"
        
        # Проверка на отказ
        if re.search(self.PATTERNS["no"], message_lower):
            return "disagree"
        
        # Проверка на цену
        if re.search(self.PATTERNS["price"], message_lower):
            return "price_offer"
        
        # Приветствие
        if any(word in message_lower for word in ["привет", "здравствуй", "добрый", "hello"]):
            return "greeting"
        
        # Вопрос
        if "?" in message or any(word in message_lower for word in ["какой", "где", "когда", "сколько"]):
            return "question"
        
        return "unknown"
    
    def _extract_entities(self, message: str) -> Dict[str, Any]:
        """Извлечение сущностей из сообщения."""
        entities = {}
        
        # Извлекаем цену
        price_match = re.search(self.PATTERNS["price"], message)
        if price_match:
            price_str = price_match.group(1).replace(" ", "").replace(",", ".")
            try:
                price = float(price_str)
                # Если указано "тыс" или "к", умножаем на 1000
                if price_match.group(2) and price_match.group(2).lower() in ["тыс", "к"]:
                    price *= 1000
                entities["price"] = price
            except:
                pass
        
        # Извлекаем вес
        weight_match = re.search(self.PATTERNS["weight"], message)
        if weight_match:
            weight_str = weight_match.group(1).replace(" ", "").replace(",", ".")
            try:
                weight = float(weight_str)
                if "кг" in weight_match.group(2).lower():
                    weight /= 1000
                entities["weight"] = weight
            except:
                pass
        
        # Извлекаем телефон
        phone_match = re.search(self.PATTERNS["phone"], message)
        if phone_match:
            entities["phone"] = phone_match.group(0)
        
        # Извлекаем тип ТС
        truck_match = re.search(self.PATTERNS["truck_type"], message, re.IGNORECASE)
        if truck_match:
            entities["truck_type"] = truck_match.group(0).lower()
        
        return entities
    
    def _generate_response(self, conv: Conversation, intent: str, 
                           entities: Dict, original_message: str) -> Dict[str, Any]:
        """Генерация ответа на основе контекста."""
        
        awaiting = conv.context.get("awaiting")
        current_load = conv.context.get("current_load", {})
        
        # Обработка ставки
        if awaiting == "bid" and "price" in entities:
            return self._handle_bid(conv, entities["price"])
        
        # Обработка согласия
        if intent == "agree":
            if conv.state == ConversationState.NEGOTIATING:
                return self._handle_agreement(conv)
            elif conv.state == ConversationState.CONFIRMING:
                return {
                    "text": "✅ Отлично! Сделка подтверждена!",
                    "type": "confirmation",
                    "action": "deal_confirmed"
                }
        
        # Обработка отказа
        if intent == "disagree":
            return self._handle_rejection(conv)
        
        # Обработка приветствия
        if intent == "greeting":
            return {
                "text": self.TEMPLATES["greeting"] + "\n\nЧем могу помочь?",
                "type": "greeting"
            }
        
        # Сбор информации
        if awaiting and awaiting in entities:
            return self._save_info(conv, awaiting, entities[awaiting])
        
        # Не поняли
        if intent == "unknown":
            return {
                "text": self.TEMPLATES["not_understood"],
                "type": "clarification"
            }
        
        return {
            "text": "Чем могу помочь? 🚛",
            "type": "default"
        }
    
    def _handle_bid(self, conv: Conversation, price: float) -> Dict[str, Any]:
        """Обработка полученной ставки."""
        load = conv.context.get("current_load", {})
        load_price = load.get("price", 0)
        load_id = load.get("id")
        
        # Сохраняем ставку
        bid = {
            "user_id": conv.user_id,
            "price": price,
            "timestamp": datetime.now().isoformat(),
            "driver_name": conv.context.get("driver_info", {}).get("name", ""),
            "driver_phone": conv.context.get("driver_info", {}).get("phone", ""),
            "truck_type": conv.context.get("driver_info", {}).get("truck_type", ""),
            "rating": conv.context.get("rating", 4.5)
        }
        
        if load_id:
            self.collected_bids[load_id].append(bid)
        
        conv.context["last_bid"] = price
        
        # Анализируем ставку
        if price <= load_price:
            # Ставка в рамках бюджета
            conv.state = ConversationState.CONFIRMING
            return {
                "text": self.TEMPLATES["price_accepted"].format(price=int(price)),
                "type": "bid_accepted",
                "action": "forward_to_shipper",
                "bid": bid
            }
        else:
            # Ставка выше — переговоры
            conv.state = ConversationState.NEGOTIATING
            suggested = int(load_price * 1.05)  # +5% от заявленной
            return {
                "text": self.TEMPLATES["price_too_low"].format(
                    bid_price=int(price),
                    load_price=int(load_price),
                    suggested_price=suggested
                ),
                "type": "counter_offer",
                "suggested_price": suggested
            }
    
    def _handle_agreement(self, conv: Conversation) -> Dict[str, Any]:
        """Обработка согласия."""
        load = conv.context.get("current_load", {})
        price = conv.context.get("last_bid") or conv.context.get("target_price")
        
        conv.state = ConversationState.CONFIRMING
        
        return {
            "text": self.TEMPLATES["confirm_deal"].format(
                from_city=load.get("from_city", ""),
                to_city=load.get("to_city", ""),
                price=int(price) if price else "",
                loading_date=load.get("loading_date", "")
            ),
            "type": "confirmation_request"
        }
    
    def _handle_rejection(self, conv: Conversation) -> Dict[str, Any]:
        """Обработка отказа."""
        conv.state = ConversationState.CANCELLED
        
        return {
            "text": self.TEMPLATES["deal_rejected"],
            "type": "rejection"
        }
    
    def _save_info(self, conv: Conversation, field: str, value: Any) -> Dict[str, Any]:
        """Сохранение информации."""
        if "driver_info" not in conv.context:
            conv.context["driver_info"] = {}
        
        conv.context["driver_info"][field] = value
        conv.context["awaiting"] = None
        
        return {
            "text": self.TEMPLATES["thanks"] + f"\n{field}: {value}",
            "type": "info_saved",
            "saved": {field: value}
        }
    
    # ================== ОТЧЁТЫ ==================
    
    def get_conversation_summary(self, user_id: int) -> Dict[str, Any]:
        """📊 Сводка по диалогу."""
        conv = self.conversations.get(user_id)
        
        if not conv:
            return {"error": "Диалог не найден"}
        
        return {
            "user_id": user_id,
            "state": conv.state.value,
            "messages_count": len(conv.history),
            "context": conv.context,
            "created_at": conv.created_at.isoformat(),
            "updated_at": conv.updated_at.isoformat(),
            "last_messages": conv.history[-5:] if conv.history else []
        }
    
    def get_dispatcher_report(self, load_id: int) -> Dict[str, Any]:
        """
        📋 Отчёт для диспетчера по заявке.
        
        Формирует готовый отчёт со всеми собранными ставками.
        """
        bids = self.collected_bids.get(load_id, [])
        
        if not bids:
            return {
                "load_id": load_id,
                "status": "no_bids",
                "message": "Ставок пока нет"
            }
        
        # Сортируем по цене
        sorted_bids = sorted(bids, key=lambda x: x.get("price", float("inf")))
        
        # ТОП-3
        top_3 = sorted_bids[:3]
        
        # Статистика
        prices = [b["price"] for b in bids]
        
        return {
            "load_id": load_id,
            "status": "ready",
            "total_bids": len(bids),
            "statistics": {
                "min_price": min(prices),
                "max_price": max(prices),
                "avg_price": round(sum(prices) / len(prices))
            },
            "top_3": [
                {
                    "rank": i + 1,
                    "driver": b.get("driver_name", f"Водитель {b.get('user_id')}"),
                    "phone": b.get("driver_phone", ""),
                    "price": b.get("price"),
                    "truck_type": b.get("truck_type", ""),
                    "rating": b.get("rating", "")
                } for i, b in enumerate(top_3)
            ],
            "all_bids": sorted_bids,
            "recommendation": f"🏆 Рекомендуем: {top_3[0].get('driver_name', 'Водитель')} за {top_3[0].get('price')}₽" if top_3 else ""
        }
    
    def generate_auto_messages(self, load: Dict, drivers: List[Dict]) -> List[Dict]:
        """
        🤖 Генерация автоматических сообщений для рассылки.
        """
        messages = []
        
        for driver in drivers:
            # Персонализируем сообщение
            name = driver.get("fullname", "").split()[0] if driver.get("fullname") else ""
            greeting = f"{name}, добрый день!\n\n" if name else ""
            
            message = greeting + self.TEMPLATES["load_offer_short"].format(
                from_city=load.get("from_city", ""),
                to_city=load.get("to_city", ""),
                weight=load.get("weight", ""),
                price=load.get("price", "")
            )
            
            messages.append({
                "user_id": driver.get("id"),
                "phone": driver.get("phone"),
                "message": message,
                "type": "load_offer"
            })
        
        return messages


# Singleton instance
ai_chatbot = AIChatbot()



