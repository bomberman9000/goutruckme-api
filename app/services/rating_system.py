"""
⭐ Система баллов и рейтинга ГрузПоток (как в АТИ)

Начисление и списание баллов:
- +10 баллов за успешную сделку
- +5 баллов за завершённую заявку
- +2 балла за активность (создание заявки, ставка)
- -20 баллов за жалобу
- -50 баллов за спор
- -100 баллов за мошенничество

Рейтинг рассчитывается на основе:
- Количества успешных сделок
- Количества жалоб
- Количества споров
- Времени на платформе
- Верификации аккаунта
"""
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from app.models.models import User, RatingHistory, Load, Bid


class RatingSystem:
    """Система управления баллами и рейтингом."""
    
    # Константы начисления баллов
    POINTS_SUCCESSFUL_DEAL = 10  # Успешная сделка
    POINTS_COMPLETED_LOAD = 5   # Завершённая заявка
    POINTS_CREATE_LOAD = 2      # Создание заявки
    POINTS_CREATE_BID = 2       # Создание ставки
    POINTS_VERIFIED = 50        # Верификация аккаунта
    
    # Штрафы
    POINTS_PENALTY_COMPLAINT = -20   # Жалоба
    POINTS_PENALTY_DISPUTE = -50     # Спор
    POINTS_PENALTY_FRAUD = -100      # Мошенничество
    
    # Минимальные значения
    MIN_POINTS = 0
    MIN_RATING = 0.0
    MAX_RATING = 5.0
    
    # Пороги для уровней доверия
    TRUST_LEVELS = {
        "new": {"min_points": 0, "min_rating": 0.0, "name": "Новый"},
        "trusted": {"min_points": 50, "min_rating": 3.5, "name": "Доверенный"},
        "verified": {"min_points": 200, "min_rating": 4.0, "name": "Верифицированный"},
        "premium": {"min_points": 500, "min_rating": 4.5, "name": "Премиум"}
    }
    
    def calculate_rating(self, user: User) -> float:
        """
        Расчёт рейтинга пользователя на основе статистики.
        
        Формула:
        - Базовый рейтинг: 5.0
        - -0.1 за каждую жалобу
        - -0.2 за каждый спор
        - +0.05 за каждую успешную сделку (макс +1.0)
        - +0.5 если верифицирован
        - Бонус за время на платформе (макс +0.5)
        """
        rating = 5.0
        
        # Штрафы
        rating -= user.complaints * 0.1
        rating -= user.disputes * 0.2
        
        # Бонусы
        successful_bonus = min(user.successful_deals * 0.05, 1.0)
        rating += successful_bonus
        
        if user.verified:
            rating += 0.5
        
        # Бонус за время на платформе
        days_on_platform = (datetime.utcnow() - user.created_at).days
        time_bonus = min(days_on_platform / 365 * 0.5, 0.5)  # Макс +0.5 за год
        rating += time_bonus
        
        # Ограничения
        rating = max(self.MIN_RATING, min(self.MAX_RATING, rating))
        
        return round(rating, 2)
    
    def calculate_trust_level(self, user: User) -> str:
        """Определение уровня доверия пользователя."""
        rating = user.rating
        points = user.points
        
        # Проверяем от высшего к низшему
        for level, criteria in reversed(list(self.TRUST_LEVELS.items())):
            if points >= criteria["min_points"] and rating >= criteria["min_rating"]:
                return level
        
        return "new"
    
    def add_points(self, db: Session, user_id: int, points: int, 
                   reason: str, deal_id: Optional[int] = None,
                   load_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Начисление/списание баллов пользователю.
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            points: Количество баллов (+/-)
            reason: Причина изменения
            deal_id: ID сделки (опционально)
            load_id: ID заявки (опционально)
        
        Returns:
            Результат операции
        """
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": "User not found"}
        
        rating_before = user.rating
        points_before = user.points
        
        # Обновляем баллы
        user.points = max(self.MIN_POINTS, user.points + points)
        
        # Пересчитываем рейтинг
        user.rating = self.calculate_rating(user)
        user.trust_level = self.calculate_trust_level(user)
        
        # Обновляем статистику
        if "успешная сделка" in reason.lower() or "successful deal" in reason.lower():
            user.successful_deals += 1
        elif "жалоба" in reason.lower() or "complaint" in reason.lower():
            user.complaints += 1
        elif "спор" in reason.lower() or "dispute" in reason.lower():
            user.disputes += 1
        
        user.last_activity = datetime.utcnow()
        
        # Сохраняем историю
        history = RatingHistory(
            user_id=user_id,
            points_change=points,
            rating_before=rating_before,
            rating_after=user.rating,
            reason=reason,
            deal_id=deal_id,
            load_id=load_id
        )
        db.add(history)
        db.commit()
        db.refresh(user)
        
        return {
            "success": True,
            "user_id": user_id,
            "points_before": points_before,
            "points_after": user.points,
            "points_change": points,
            "rating_before": rating_before,
            "rating_after": user.rating,
            "trust_level": user.trust_level,
            "reason": reason
        }
    
    def on_successful_deal(self, db: Session, shipper_id: int, carrier_id: int, 
                          load_id: int) -> Dict[str, Any]:
        """Начисление баллов за успешную сделку."""
        results = {}
        
        # Баллы грузоотправителю
        results["shipper"] = self.add_points(
            db, shipper_id,
            self.POINTS_SUCCESSFUL_DEAL,
            f"Успешная сделка по заявке #{load_id}",
            load_id=load_id
        )
        
        # Баллы перевозчику
        results["carrier"] = self.add_points(
            db, carrier_id,
            self.POINTS_SUCCESSFUL_DEAL,
            f"Успешная сделка по заявке #{load_id}",
            load_id=load_id
        )
        
        return results
    
    def on_load_completed(self, db: Session, user_id: int, load_id: int) -> Dict[str, Any]:
        """Начисление баллов за завершённую заявку."""
        return self.add_points(
            db, user_id,
            self.POINTS_COMPLETED_LOAD,
            f"Завершена заявка #{load_id}",
            load_id=load_id
        )
    
    def on_load_created(self, db: Session, user_id: int, load_id: int) -> Dict[str, Any]:
        """Начисление баллов за создание заявки."""
        return self.add_points(
            db, user_id,
            self.POINTS_CREATE_LOAD,
            f"Создана заявка #{load_id}",
            load_id=load_id
        )
    
    def on_bid_created(self, db: Session, user_id: int, bid_id: int, load_id: int) -> Dict[str, Any]:
        """Начисление баллов за создание ставки."""
        return self.add_points(
            db, user_id,
            self.POINTS_CREATE_BID,
            f"Создана ставка #{bid_id} на заявку #{load_id}",
            load_id=load_id
        )
    
    def on_complaint(self, db: Session, user_id: int, complaint_type: str = "general") -> Dict[str, Any]:
        """Списание баллов за жалобу."""
        return self.add_points(
            db, user_id,
            self.POINTS_PENALTY_COMPLAINT,
            f"Жалоба: {complaint_type}"
        )
    
    def on_dispute(self, db: Session, user_id: int, load_id: Optional[int] = None) -> Dict[str, Any]:
        """Списание баллов за спор."""
        return self.add_points(
            db, user_id,
            self.POINTS_PENALTY_DISPUTE,
            f"Спор по заявке #{load_id}" if load_id else "Спор",
            load_id=load_id
        )
    
    def on_fraud(self, db: Session, user_id: int) -> Dict[str, Any]:
        """Списание баллов за мошенничество."""
        return self.add_points(
            db, user_id,
            self.POINTS_PENALTY_FRAUD,
            "Мошенничество"
        )
    
    def verify_user(self, db: Session, user_id: int) -> Dict[str, Any]:
        """Верификация пользователя и начисление бонусных баллов."""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": "User not found"}
        
        if user.verified:
            return {"error": "User already verified"}
        
        user.verified = True
        
        # Начисляем бонусные баллы
        result = self.add_points(
            db, user_id,
            self.POINTS_VERIFIED,
            "Верификация аккаунта"
        )
        
        return result
    
    def get_user_stats(self, db: Session, user_id: int) -> Dict[str, Any]:
        """Получить статистику пользователя."""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return {"error": "User not found"}
        
        # История за последние 30 дней
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_history = db.query(RatingHistory).filter(
            RatingHistory.user_id == user_id,
            RatingHistory.created_at >= thirty_days_ago
        ).order_by(RatingHistory.created_at.desc()).limit(20).all()
        
        # Статистика по периодам
        total_points_earned = sum(h.points_change for h in recent_history if h.points_change > 0)
        total_points_lost = sum(h.points_change for h in recent_history if h.points_change < 0)
        
        return {
            "user_id": user_id,
            "fullname": user.fullname,
            "rating": user.rating,
            "points": user.points,
            "trust_level": user.trust_level,
            "trust_level_name": self.TRUST_LEVELS.get(user.trust_level, {}).get("name", "Неизвестно"),
            "successful_deals": user.successful_deals,
            "complaints": user.complaints,
            "disputes": user.disputes,
            "verified": user.verified,
            "days_on_platform": (datetime.utcnow() - user.created_at).days,
            "recent_points_earned": total_points_earned,
            "recent_points_lost": abs(total_points_lost),
            "recent_history": [
                {
                    "points_change": h.points_change,
                    "reason": h.reason,
                    "rating_before": h.rating_before,
                    "rating_after": h.rating_after,
                    "created_at": h.created_at.isoformat()
                }
                for h in recent_history
            ]
        }


# Singleton instance
rating_system = RatingSystem()



