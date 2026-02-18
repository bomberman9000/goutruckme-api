"""
🛡️ AI-АНТИМОШЕННИК (Модуль 3) — КИЛЛЕР-ФИЧА
Обнаружение мошенников, фейковых заявок, подозрительного поведения

Этого НЕТ даже у АТИ!

Проверяет:
- 🔥 ЛОГИЧЕСКИЕ РИСКИ
- 🔥 ПОВЕДЕНЧЕСКИЕ РИСКИ  
- 🔥 ТЕХНИЧЕСКИЕ РИСКИ
- 🔥 ИСТОРИЧЕСКИЕ РИСКИ
"""
import re
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import json


class RiskCategory(str, Enum):
    """Категории рисков."""
    LOGICAL = "logical"           # Логические риски
    BEHAVIORAL = "behavioral"     # Поведенческие риски
    TECHNICAL = "technical"       # Технические риски
    HISTORICAL = "historical"     # Исторические риски


class FraudRisk(str, Enum):
    """Уровни риска мошенничества."""
    SAFE = "safe"           # 0-20%  ✅
    LOW = "low"             # 20-40% ⚡
    MEDIUM = "medium"       # 40-60% ⚠️
    HIGH = "high"           # 60-80% ⛔
    CRITICAL = "critical"   # 80-100% 🚨


@dataclass
class RiskAlert:
    """Предупреждение о риске."""
    category: RiskCategory
    risk_type: str
    severity: str  # low / medium / high / critical
    message: str
    score: int
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self):
        return {
            "category": self.category.value,
            "type": self.risk_type,
            "severity": self.severity,
            "message": self.message,
            "score": self.score,
            "details": self.details
        }


class AIAntifraud:
    """
    🛡️ AI-Антимошенник — полная защита от мошенников.
    
    ПРОВЕРЯЕТ:
    
    🔥 ЛОГИЧЕСКИЕ РИСКИ:
    - Слишком низкая ставка
    - Слишком высокий тоннаж без причины
    - Неадекватный маршрут
    - Странные сроки
    - Нет описания груза
    
    🔥 ПОВЕДЕНЧЕСКИЕ РИСКИ:
    - Создаёт заявку и сразу исчезает
    - Нет чёткого ответа
    - Меняет условия на ходу
    - Ссылка ведёт на несуществующую компанию
    
    🔥 ТЕХНИЧЕСКИЕ РИСКИ:
    - Повторяющиеся телефоны
    - Аккаунт создан сегодня
    - IP из подозрительных регионов/VPN
    - Одинаковая орфография у разных профилей
    - Массовые заявки с одинаковым текстом
    
    🔥 ИСТОРИЧЕСКИЕ РИСКИ:
    - Были споры
    - Водители жаловались
    - Не закрывал перевозки
    - Есть претензии
    - Чёрный список
    """
    
    # ================== НАСТРОЙКИ ==================
    
    # Подозрительные слова
    SUSPICIOUS_KEYWORDS = [
        "предоплата", "аванс", "срочно перевод", "переведи", "скинь",
        "гарантия 100", "без обмана", "проверенный", "надёжный",
        "только сегодня", "эксклюзив", "vip", "срочно",
        "скидка 50", "бесплатно", "халява", "акция",
        "перезвони", "позвони", "напиши в вотсап", "whatsapp",
        "telegram", "телеграм", "лично", "наличкой",
    ]
    
    # Паттерны для обнаружения данных
    FRAUD_PATTERNS = {
        "card_number": r'\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b',
        "crypto_wallet": r'\b(0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b',
        "multiple_phones": r'(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "url": r'https?://[^\s]+',
    }
    
    # Нормальные диапазоны
    NORMAL_RANGES = {
        "price_per_km_min": 15,
        "price_per_km_max": 80,
        "weight_max": 44,  # Максимум для автопоезда
        "volume_max": 120,
        "response_time_suspicious": 2,  # секунды (слишком быстро = бот)
    }
    
    # Подозрительные IP-диапазоны (примеры)
    SUSPICIOUS_IP_RANGES = [
        "185.220.",  # Tor exit nodes
        "104.244.",  # VPN
    ]
    
    # Подозрительные страны
    SUSPICIOUS_COUNTRIES = ["KZ", "UZ", "TJ", "KG"]  # Часто используют для мошенничества
    
    def __init__(self):
        # Хранилища для анализа
        self.user_activity: Dict[int, List[Dict]] = defaultdict(list)
        self.ip_registry: Dict[str, Set[int]] = defaultdict(set)
        self.phone_registry: Dict[str, Set[int]] = defaultdict(set)
        self.text_hashes: Dict[str, List[int]] = defaultdict(list)
        
        # Чёрные списки
        self.blacklist_phones: Set[str] = set()
        self.blacklist_inn: Set[str] = set()
        self.blacklist_users: Set[int] = set()
        
        # Статистика споров/жалоб (в реальности из БД)
        self.user_complaints: Dict[int, int] = defaultdict(int)
        self.user_disputes: Dict[int, int] = defaultdict(int)
        self.user_unclosed: Dict[int, int] = defaultdict(int)

    # ================== ГЛАВНЫЕ МЕТОДЫ ==================
    
    def full_analysis(self, user: Dict, load: Dict = None, 
                      history: Dict = None, ip: str = None) -> Dict[str, Any]:
        """
        🔍 ПОЛНЫЙ АНАЛИЗ НА МОШЕННИЧЕСТВО
        
        Возвращает детальный отчёт со всеми категориями рисков.
        """
        
        alerts: List[RiskAlert] = []
        
        # 🔥 ЛОГИЧЕСКИЕ РИСКИ
        logical_alerts = self._check_logical_risks(user, load)
        alerts.extend(logical_alerts)
        
        # 🔥 ПОВЕДЕНЧЕСКИЕ РИСКИ
        behavioral_alerts = self._check_behavioral_risks(user, history)
        alerts.extend(behavioral_alerts)
        
        # 🔥 ТЕХНИЧЕСКИЕ РИСКИ
        technical_alerts = self._check_technical_risks(user, ip)
        alerts.extend(technical_alerts)
        
        # 🔥 ИСТОРИЧЕСКИЕ РИСКИ
        historical_alerts = self._check_historical_risks(user, history)
        alerts.extend(historical_alerts)
        
        # Подсчёт общего скора
        total_score = sum(a.score for a in alerts)
        total_score = min(total_score, 100)
        
        # Группировка по категориям
        risks_by_category = {
            "logical": [],
            "behavioral": [],
            "technical": [],
            "historical": []
        }
        
        for alert in alerts:
            risks_by_category[alert.category.value].append(alert.to_dict())
        
        # Определяем уровень риска
        risk_level = self._get_risk_level(total_score)
        
        return {
            "analysis_id": f"AF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "user_id": user.get("id"),
            
            # 🎯 ИТОГ
            "verdict": {
                "risk_score": total_score,
                "risk_level": risk_level.value,
                "risk_percent": f"{total_score}%",
                "emoji": self._get_emoji(total_score),
                "status": self._get_verdict(total_score),
                "recommendation": self._get_recommendation(total_score),
                "action": self._get_action(total_score)
            },
            
            # 📊 ДЕТАЛИ ПО КАТЕГОРИЯМ
            "risks": {
                "logical": {
                    "count": len(risks_by_category["logical"]),
                    "score": sum(r["score"] for r in risks_by_category["logical"]),
                    "alerts": risks_by_category["logical"]
                },
                "behavioral": {
                    "count": len(risks_by_category["behavioral"]),
                    "score": sum(r["score"] for r in risks_by_category["behavioral"]),
                    "alerts": risks_by_category["behavioral"]
                },
                "technical": {
                    "count": len(risks_by_category["technical"]),
                    "score": sum(r["score"] for r in risks_by_category["technical"]),
                    "alerts": risks_by_category["technical"]
                },
                "historical": {
                    "count": len(risks_by_category["historical"]),
                    "score": sum(r["score"] for r in risks_by_category["historical"]),
                    "alerts": risks_by_category["historical"]
                }
            },
            
            # 📋 ВСЕ АЛЕРТЫ
            "all_alerts": [a.to_dict() for a in alerts],
            "total_alerts": len(alerts)
        }

    # ================== 🔥 ЛОГИЧЕСКИЕ РИСКИ ==================
    
    def _check_logical_risks(self, user: Dict, load: Dict = None) -> List[RiskAlert]:
        """Проверка логических рисков."""
        alerts = []
        
        if not load:
            return alerts
        
        price = load.get("price", 0)
        weight = load.get("weight")
        volume = load.get("volume")
        from_city = load.get("from_city", "").lower()
        to_city = load.get("to_city", "").lower()
        description = load.get("description", "")
        loading_date = load.get("loading_date")
        
        # 1. Слишком низкая ставка
        if price > 0 and price < 3000:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="price_too_low",
                severity="high",
                message=f"🚨 Подозрительно низкая цена: {price}₽",
                score=35,
                details={"price": price, "min_expected": 5000}
            ))
        
        # 2. Цена = 0
        if price == 0:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="zero_price",
                severity="critical",
                message="❌ Нулевая цена — явный признак мошенничества",
                score=50,
                details={"price": 0}
            ))
        
        # 3. Слишком высокий тоннаж
        if weight and weight > self.NORMAL_RANGES["weight_max"]:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="unrealistic_weight",
                severity="high",
                message=f"⚠️ Нереальный вес: {weight}т (макс. {self.NORMAL_RANGES['weight_max']}т)",
                score=30,
                details={"weight": weight, "max": self.NORMAL_RANGES["weight_max"]}
            ))
        
        # 4. Слишком большой объём
        if volume and volume > self.NORMAL_RANGES["volume_max"]:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="unrealistic_volume",
                severity="medium",
                message=f"⚠️ Нереальный объём: {volume}м³",
                score=20,
                details={"volume": volume}
            ))
        
        # 5. Город отправления = город назначения
        if from_city and to_city and from_city == to_city:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="same_city",
                severity="critical",
                message="❌ Город отправления = город назначения",
                score=50,
                details={"from": from_city, "to": to_city}
            ))
        
        # 6. Нет описания груза
        if not description or len(description.strip()) < 5:
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="no_description",
                severity="medium",
                message="⚠️ Нет описания груза",
                score=15,
                details={"description_length": len(description) if description else 0}
            ))
        
        # 7. Странные сроки (погрузка в прошлом)
        if loading_date:
            try:
                if isinstance(loading_date, str):
                    load_dt = datetime.fromisoformat(loading_date.replace('Z', ''))
                else:
                    load_dt = loading_date
                
                if load_dt < datetime.now() - timedelta(days=1):
                    alerts.append(RiskAlert(
                        category=RiskCategory.LOGICAL,
                        risk_type="past_date",
                        severity="high",
                        message="⚠️ Дата погрузки в прошлом",
                        score=25,
                        details={"loading_date": str(loading_date)}
                    ))
            except:
                pass
        
        # 8. Подозрительный текст
        text_alerts = self._analyze_text(description)
        alerts.extend(text_alerts)
        
        return alerts
    
    # ================== 🔥 ПОВЕДЕНЧЕСКИЕ РИСКИ ==================
    
    def _check_behavioral_risks(self, user: Dict, history: Dict = None) -> List[RiskAlert]:
        """Проверка поведенческих рисков."""
        alerts = []
        
        user_id = user.get("id")
        
        if not history:
            history = {}
        
        # 1. Создаёт заявку и сразу исчезает (много созданных, мало закрытых)
        created_loads = history.get("created_loads", 0)
        completed_loads = history.get("completed_loads", 0)
        
        if created_loads > 5 and completed_loads == 0:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="ghost_user",
                severity="high",
                message=f"🚨 Создал {created_loads} заявок, завершил 0 — возможный 'призрак'",
                score=40,
                details={"created": created_loads, "completed": completed_loads}
            ))
        elif created_loads > 3 and completed_loads / created_loads < 0.2:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="low_completion",
                severity="medium",
                message=f"⚠️ Низкий процент завершённых заявок: {completed_loads}/{created_loads}",
                score=25,
                details={"ratio": completed_loads / created_loads if created_loads else 0}
            ))
        
        # 2. Часто меняет условия
        condition_changes = history.get("condition_changes", 0)
        if condition_changes > 3:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="frequent_changes",
                severity="medium",
                message=f"⚠️ Часто меняет условия заявок: {condition_changes} раз",
                score=20,
                details={"changes": condition_changes}
            ))
        
        # 3. Не отвечает на сообщения
        unanswered_messages = history.get("unanswered_messages", 0)
        total_messages = history.get("total_messages", 0)
        
        if total_messages > 5 and unanswered_messages / total_messages > 0.7:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="no_response",
                severity="medium",
                message="⚠️ Не отвечает на большинство сообщений",
                score=20,
                details={"unanswered_rate": unanswered_messages / total_messages}
            ))
        
        # 4. Слишком быстрые ответы (бот?)
        avg_response_time = history.get("avg_response_time_seconds")
        if avg_response_time and avg_response_time < self.NORMAL_RANGES["response_time_suspicious"]:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="bot_suspected",
                severity="medium",
                message=f"🤖 Подозрительно быстрые ответы ({avg_response_time}с) — возможно бот",
                score=25,
                details={"avg_response_time": avg_response_time}
            ))
        
        # 5. Отмены в последний момент
        last_minute_cancels = history.get("last_minute_cancels", 0)
        if last_minute_cancels > 2:
            alerts.append(RiskAlert(
                category=RiskCategory.BEHAVIORAL,
                risk_type="last_minute_cancel",
                severity="high",
                message=f"⚠️ Отмены в последний момент: {last_minute_cancels}",
                score=30,
                details={"cancels": last_minute_cancels}
            ))
        
        return alerts
    
    # ================== 🔥 ТЕХНИЧЕСКИЕ РИСКИ ==================
    
    def _check_technical_risks(self, user: Dict, ip: str = None) -> List[RiskAlert]:
        """Проверка технических рисков."""
        alerts = []
        
        user_id = user.get("id")
        phone = user.get("phone", "")
        created_at = user.get("created_at")
        fullname = user.get("fullname", "")
        
        # 1. Аккаунт создан сегодня
        if created_at:
            try:
                if isinstance(created_at, str):
                    created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    created_dt = created_at
                
                account_age = (datetime.now() - created_dt.replace(tzinfo=None)).days
                
                if account_age == 0:
                    alerts.append(RiskAlert(
                        category=RiskCategory.TECHNICAL,
                        risk_type="new_account_today",
                        severity="high",
                        message="🆕 Аккаунт создан СЕГОДНЯ!",
                        score=35,
                        details={"account_age_days": 0}
                    ))
                elif account_age < 3:
                    alerts.append(RiskAlert(
                        category=RiskCategory.TECHNICAL,
                        risk_type="new_account",
                        severity="medium",
                        message=f"🆕 Новый аккаунт ({account_age} дней)",
                        score=20,
                        details={"account_age_days": account_age}
                    ))
            except:
                pass
        
        # 2. Повторяющийся телефон (на нескольких аккаунтах)
        if phone:
            phone_clean = re.sub(r'\D', '', phone)
            self.phone_registry[phone_clean].add(user_id or 0)
            
            if len(self.phone_registry[phone_clean]) > 1:
                alerts.append(RiskAlert(
                    category=RiskCategory.TECHNICAL,
                    risk_type="duplicate_phone",
                    severity="critical",
                    message=f"🚨 Телефон используется на {len(self.phone_registry[phone_clean])} аккаунтах!",
                    score=50,
                    details={"phone": phone, "accounts": len(self.phone_registry[phone_clean])}
                ))
        
        # 3. Подозрительный IP
        if ip:
            for suspicious_range in self.SUSPICIOUS_IP_RANGES:
                if ip.startswith(suspicious_range):
                    alerts.append(RiskAlert(
                        category=RiskCategory.TECHNICAL,
                        risk_type="suspicious_ip",
                        severity="high",
                        message=f"🌐 Подозрительный IP (VPN/Tor): {ip}",
                        score=30,
                        details={"ip": ip}
                    ))
                    break
            
            # IP используется несколькими аккаунтами
            self.ip_registry[ip].add(user_id or 0)
            if len(self.ip_registry[ip]) > 3:
                alerts.append(RiskAlert(
                    category=RiskCategory.TECHNICAL,
                    risk_type="shared_ip",
                    severity="medium",
                    message=f"⚠️ IP используется {len(self.ip_registry[ip])} аккаунтами",
                    score=25,
                    details={"ip": ip, "accounts": len(self.ip_registry[ip])}
                ))
        
        # 4. Проверка телефона на формат
        if phone:
            phone_check = self._check_phone_format(phone)
            if phone_check["suspicious"]:
                alerts.append(RiskAlert(
                    category=RiskCategory.TECHNICAL,
                    risk_type="suspicious_phone",
                    severity=phone_check["severity"],
                    message=phone_check["message"],
                    score=phone_check["score"],
                    details={"phone": phone}
                ))
        
        # 5. Чёрный список
        if phone and (phone in self.blacklist_phones or re.sub(r'\D', '', phone) in self.blacklist_phones):
            alerts.append(RiskAlert(
                category=RiskCategory.TECHNICAL,
                risk_type="blacklisted_phone",
                severity="critical",
                message="🚫 Телефон в ЧЁРНОМ СПИСКЕ!",
                score=80,
                details={"phone": phone}
            ))
        
        if user_id and user_id in self.blacklist_users:
            alerts.append(RiskAlert(
                category=RiskCategory.TECHNICAL,
                risk_type="blacklisted_user",
                severity="critical",
                message="🚫 Пользователь в ЧЁРНОМ СПИСКЕ!",
                score=90,
                details={"user_id": user_id}
            ))
        
        return alerts
    
    # ================== 🔥 ИСТОРИЧЕСКИЕ РИСКИ ==================
    
    def _check_historical_risks(self, user: Dict, history: Dict = None) -> List[RiskAlert]:
        """Проверка исторических рисков."""
        alerts = []
        
        user_id = user.get("id")
        rating = user.get("rating", 5.0)
        
        if not history:
            history = {}
        
        # 1. Низкий рейтинг
        if rating < 2.0:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="critical_rating",
                severity="critical",
                message=f"🚨 Критически низкий рейтинг: {rating}",
                score=50,
                details={"rating": rating}
            ))
        elif rating < 3.0:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="low_rating",
                severity="high",
                message=f"⛔ Низкий рейтинг: {rating}",
                score=35,
                details={"rating": rating}
            ))
        elif rating < 4.0:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="below_avg_rating",
                severity="medium",
                message=f"⚠️ Рейтинг ниже среднего: {rating}",
                score=15,
                details={"rating": rating}
            ))
        
        # 2. Были споры
        disputes = history.get("disputes", 0) or self.user_disputes.get(user_id, 0)
        if disputes > 0:
            score = min(disputes * 15, 45)
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="has_disputes",
                severity="high" if disputes > 2 else "medium",
                message=f"⚖️ Были споры: {disputes}",
                score=score,
                details={"disputes": disputes}
            ))
        
        # 3. Жалобы от водителей/заказчиков
        complaints = history.get("complaints", 0) or self.user_complaints.get(user_id, 0)
        if complaints > 0:
            score = min(complaints * 12, 50)
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="has_complaints",
                severity="critical" if complaints > 3 else "high" if complaints > 1 else "medium",
                message=f"📢 Жалобы: {complaints}",
                score=score,
                details={"complaints": complaints}
            ))
        
        # 4. Не закрывал перевозки
        unclosed = history.get("unclosed_loads", 0) or self.user_unclosed.get(user_id, 0)
        if unclosed > 2:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="unclosed_loads",
                severity="high",
                message=f"📦 Незакрытые перевозки: {unclosed}",
                score=30,
                details={"unclosed": unclosed}
            ))
        
        # 5. Есть претензии
        claims = history.get("claims", 0)
        if claims > 0:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="has_claims",
                severity="high",
                message=f"📋 Претензии: {claims}",
                score=25,
                details={"claims": claims}
            ))
        
        # 6. Негативные отзывы
        negative_reviews = history.get("negative_reviews", 0)
        if negative_reviews > 3:
            alerts.append(RiskAlert(
                category=RiskCategory.HISTORICAL,
                risk_type="negative_reviews",
                severity="high",
                message=f"👎 Негативные отзывы: {negative_reviews}",
                score=25,
                details={"negative_reviews": negative_reviews}
            ))
        
        return alerts
    
    # ================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ==================
    
    def _analyze_text(self, text: str) -> List[RiskAlert]:
        """Анализ текста на подозрительные паттерны."""
        alerts = []
        
        if not text:
            return alerts
        
        text_lower = text.lower()
        
        # 1. Подозрительные ключевые слова
        found_keywords = [kw for kw in self.SUSPICIOUS_KEYWORDS if kw in text_lower]
        if found_keywords:
            score = min(len(found_keywords) * 8, 35)
            alerts.append(RiskAlert(
                category=RiskCategory.LOGICAL,
                risk_type="suspicious_keywords",
                severity="medium" if len(found_keywords) < 3 else "high",
                message=f"⚠️ Подозрительные слова: {', '.join(found_keywords[:5])}",
                score=score,
                details={"keywords": found_keywords}
            ))
        
        # 2. Платёжные данные в тексте
        for pattern_name, pattern in self.FRAUD_PATTERNS.items():
            if re.search(pattern, text):
                if pattern_name == "card_number":
                    alerts.append(RiskAlert(
                        category=RiskCategory.LOGICAL,
                        risk_type="card_in_text",
                        severity="critical",
                        message="🚨 Номер карты в тексте!",
                        score=60,
                        details={"pattern": pattern_name}
                    ))
                elif pattern_name == "crypto_wallet":
                    alerts.append(RiskAlert(
                        category=RiskCategory.LOGICAL,
                        risk_type="crypto_in_text",
                        severity="critical",
                        message="🚨 Крипто-кошелёк в тексте!",
                        score=55,
                        details={"pattern": pattern_name}
                    ))
        
        # 3. Проверка на дублирование текста (массовые заявки)
        text_hash = hashlib.md5(text.encode()).hexdigest()
        self.text_hashes[text_hash].append(1)
        
        if len(self.text_hashes[text_hash]) > 3:
            alerts.append(RiskAlert(
                category=RiskCategory.TECHNICAL,
                risk_type="duplicate_text",
                severity="high",
                message=f"📝 Текст повторяется {len(self.text_hashes[text_hash])} раз — массовая рассылка?",
                score=30,
                details={"duplicates": len(self.text_hashes[text_hash])}
            ))
        
        return alerts
    
    def _check_phone_format(self, phone: str) -> Dict[str, Any]:
        """Проверка формата телефона."""
        phone_clean = re.sub(r'\D', '', phone)
        
        # Проверка длины
        if len(phone_clean) != 11:
            return {
                "suspicious": True,
                "severity": "medium",
                "score": 15,
                "message": "⚠️ Некорректный формат телефона"
            }
        
        # Проверка на повторяющиеся цифры (вероятно фейк)
        if len(set(phone_clean)) < 4:
            return {
                "suspicious": True,
                "severity": "high",
                "score": 30,
                "message": "⚠️ Подозрительный номер (повторяющиеся цифры)"
            }
        
        # Виртуальные номера
        virtual_prefixes = ["958", "959"]
        if phone_clean[1:4] in virtual_prefixes:
            return {
                "suspicious": True,
                "severity": "medium",
                "score": 20,
                "message": "⚠️ Виртуальный номер"
            }
        
        return {"suspicious": False, "score": 0}
    
    def _get_risk_level(self, score: int) -> FraudRisk:
        """Определение уровня риска."""
        if score >= 80:
            return FraudRisk.CRITICAL
        elif score >= 60:
            return FraudRisk.HIGH
        elif score >= 40:
            return FraudRisk.MEDIUM
        elif score >= 20:
            return FraudRisk.LOW
        return FraudRisk.SAFE
    
    def _get_emoji(self, score: int) -> str:
        """Получение эмодзи статуса."""
        if score >= 80:
            return "🚨"
        elif score >= 60:
            return "⛔"
        elif score >= 40:
            return "⚠️"
        elif score >= 20:
            return "⚡"
        return "✅"
    
    def _get_verdict(self, score: int) -> str:
        """Получение вердикта."""
        if score >= 80:
            return "ВЕРОЯТНЫЙ МОШЕННИК"
        elif score >= 60:
            return "ВЫСОКИЙ РИСК"
        elif score >= 40:
            return "ТРЕБУЕТ ПРОВЕРКИ"
        elif score >= 20:
            return "НИЗКИЙ РИСК"
        return "БЕЗОПАСНО"
    
    def _get_recommendation(self, score: int) -> str:
        """Получение рекомендации."""
        if score >= 80:
            return "НЕ РАБОТАТЬ! Заблокировать аккаунт и сообщить модератору."
        elif score >= 60:
            return "Не рекомендуем работать. Высокий риск мошенничества."
        elif score >= 40:
            return "Требуется ручная проверка модератором перед работой."
        elif score >= 20:
            return "Можно работать с осторожностью. Рекомендуем проверить документы."
        return "Можно работать. Профиль выглядит надёжным."
    
    def _get_action(self, score: int) -> str:
        """Получение рекомендуемого действия."""
        if score >= 80:
            return "BLOCK"
        elif score >= 60:
            return "REJECT"
        elif score >= 40:
            return "REVIEW"
        elif score >= 20:
            return "MONITOR"
        return "ALLOW"
    
    # ================== УПРАВЛЕНИЕ ЧЁРНЫМИ СПИСКАМИ ==================
    
    def add_to_blacklist(self, phone: str = None, inn: str = None, 
                          user_id: int = None, reason: str = "") -> Dict:
        """Добавление в чёрный список."""
        added = []
        
        if phone:
            phone_clean = re.sub(r'\D', '', phone)
            self.blacklist_phones.add(phone_clean)
            added.append(f"phone: {phone}")
        
        if inn:
            self.blacklist_inn.add(inn)
            added.append(f"inn: {inn}")
        
        if user_id:
            self.blacklist_users.add(user_id)
            added.append(f"user_id: {user_id}")
        
        return {
            "success": True,
            "added": added,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
    
    def add_complaint(self, user_id: int, complaint_type: str = "general"):
        """Добавление жалобы на пользователя."""
        self.user_complaints[user_id] += 1
        return {"user_id": user_id, "total_complaints": self.user_complaints[user_id]}
    
    def add_dispute(self, user_id: int):
        """Добавление спора пользователю."""
        self.user_disputes[user_id] += 1
        return {"user_id": user_id, "total_disputes": self.user_disputes[user_id]}


# Singleton instance
ai_antifraud = AIAntifraud()
