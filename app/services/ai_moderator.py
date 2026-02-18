"""
AI Модератор - проверка заявок и антифрод
"""
import re
from app.models.models import Load, User, Bid


class AIModerator:
    """ИИ-модератор для проверки заявок и пользователей."""
    
    # Подозрительные паттерны
    SUSPICIOUS_PATTERNS = [
        r'\b(предоплата|аванс|срочно перевод)\b',
        r'\b(гарантия 100%|без обмана)\b',
        r'\+7\s*\d{3}.*\+7\s*\d{3}',  # несколько номеров
    ]
    
    # Минимальные/максимальные цены за км (примерные)
    MIN_PRICE_PER_KM = 15  # рублей
    MAX_PRICE_PER_KM = 150
    
    def check_load(self, load: Load) -> dict:
        """Проверка заявки на груз."""
        issues = []
        risk_score = 0
        
        # Проверка цены
        if load.price < 1000:
            issues.append("Слишком низкая цена")
            risk_score += 30
        
        if load.price > 1000000:
            issues.append("Подозрительно высокая цена")
            risk_score += 20
        
        # Проверка веса
        if load.weight and load.weight > 40:
            issues.append("Вес превышает стандартный лимит (40т)")
            risk_score += 10
        
        # Проверка городов
        if load.from_city.lower() == load.to_city.lower():
            issues.append("Город отправления совпадает с городом назначения")
            risk_score += 40
        
        return {
            "approved": risk_score < 50,
            "risk_score": risk_score,
            "issues": issues,
            "recommendation": "approve" if risk_score < 30 else "review" if risk_score < 50 else "reject"
        }
    
    def check_user(self, user: User) -> dict:
        """Проверка пользователя на подозрительную активность."""
        issues = []
        risk_score = 0
        
        # Проверка рейтинга
        if user.rating < 3.0:
            issues.append("Низкий рейтинг пользователя")
            risk_score += 30
        
        # Проверка телефона (базовая)
        if not user.phone.startswith('+'):
            issues.append("Некорректный формат телефона")
            risk_score += 10
        
        return {
            "trusted": risk_score < 30,
            "risk_score": risk_score,
            "issues": issues
        }
    
    def check_bid(self, bid: Bid, load: Load) -> dict:
        """Проверка ставки."""
        issues = []
        risk_score = 0
        
        # Ставка слишком низкая (демпинг)
        if bid.price < load.price * 0.3:
            issues.append("Подозрительно низкая ставка (возможный демпинг)")
            risk_score += 40
        
        # Ставка выше заявленной цены
        if bid.price > load.price * 1.5:
            issues.append("Ставка значительно выше заявленной цены")
            risk_score += 20
        
        # Проверка комментария на спам
        if bid.comment:
            for pattern in self.SUSPICIOUS_PATTERNS:
                if re.search(pattern, bid.comment, re.IGNORECASE):
                    issues.append("Подозрительный текст в комментарии")
                    risk_score += 30
                    break
        
        return {
            "approved": risk_score < 50,
            "risk_score": risk_score,
            "issues": issues
        }
    
    def detect_fraud(self, text: str) -> dict:
        """Обнаружение мошенничества в тексте."""
        fraud_indicators = []
        
        for pattern in self.SUSPICIOUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                fraud_indicators.append(pattern)
        
        return {
            "is_suspicious": len(fraud_indicators) > 0,
            "indicators": fraud_indicators
        }


# Singleton instance
ai_moderator = AIModerator()




