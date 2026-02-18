"""
AI Диспетчер - автоматический подбор машин и грузов
"""
from datetime import datetime
from typing import List, Optional
from app.models.models import Load, Truck, User, Bid


class AIDispatcher:
    """ИИ-диспетчер для автоматического матчинга грузов и машин."""
    
    # Типы машин и их характеристики
    TRUCK_TYPES = {
        "газель": {"max_weight": 1.5, "max_volume": 9},
        "5т": {"max_weight": 5, "max_volume": 30},
        "10т": {"max_weight": 10, "max_volume": 45},
        "20т": {"max_weight": 20, "max_volume": 82},
        "фура": {"max_weight": 22, "max_volume": 92},
    }
    
    def find_matching_trucks(self, load: Load, trucks: List[Truck]) -> List[dict]:
        """Поиск подходящих машин для груза по 20 параметрам."""
        
        matches = []
        
        for truck in trucks:
            score = 0
            reasons = []
            
            # 1. Статус машины
            if truck.status != "free":
                continue
            
            # 2. Грузоподъёмность
            truck_specs = self.TRUCK_TYPES.get(truck.type, {})
            max_weight = truck_specs.get("max_weight", truck.capacity or 20)
            
            if load.weight and load.weight <= max_weight:
                score += 20
                reasons.append("✓ Грузоподъёмность подходит")
            elif load.weight:
                continue  # Не подходит по весу
            
            # 3. Объём
            max_volume = truck_specs.get("max_volume", 80)
            if load.volume and load.volume <= max_volume:
                score += 15
                reasons.append("✓ Объём подходит")
            
            # 4. Регион
            if truck.region and load.from_city.lower() in truck.region.lower():
                score += 25
                reasons.append("✓ Машина в регионе погрузки")
            
            # 5. Рейтинг владельца
            if truck.owner and truck.owner.rating >= 4.5:
                score += 20
                reasons.append("✓ Высокий рейтинг перевозчика")
            elif truck.owner and truck.owner.rating >= 4.0:
                score += 10
                reasons.append("○ Хороший рейтинг перевозчика")
            
            # 6. Тип груза vs тип машины (базовая логика)
            score += 10  # Базовый балл за доступность
            
            if score >= 30:
                matches.append({
                    "truck_id": truck.id,
                    "truck_type": truck.type,
                    "owner_id": truck.user_id,
                    "owner_name": truck.owner.fullname if truck.owner else "Unknown",
                    "owner_rating": truck.owner.rating if truck.owner else 0,
                    "match_score": score,
                    "reasons": reasons,
                    "region": truck.region
                })
        
        # Сортировка по score
        matches.sort(key=lambda x: x["match_score"], reverse=True)
        
        return matches[:10]  # Топ-10 машин
    
    def check_rate_adequacy(self, load: Load, proposed_rate: float) -> dict:
        """Проверка адекватности ставки."""
        
        # Базовые расценки (руб/км) по типам
        BASE_RATES = {
            "light": 25,   # до 3т
            "medium": 35,  # 3-10т
            "heavy": 45,   # 10-20т
            "extra": 55,   # 20т+
        }
        
        # Определяем категорию
        weight = load.weight or 10
        if weight <= 3:
            category = "light"
        elif weight <= 10:
            category = "medium"
        elif weight <= 20:
            category = "heavy"
        else:
            category = "extra"
        
        base_rate = BASE_RATES[category]
        
        # Примерное расстояние (в реальности нужен API)
        estimated_distance = 500  # км по умолчанию
        
        min_acceptable = base_rate * estimated_distance * 0.7
        max_acceptable = base_rate * estimated_distance * 1.5
        optimal = base_rate * estimated_distance
        
        if proposed_rate < min_acceptable:
            status = "too_low"
            message = f"⚠️ Ставка слишком низкая! Минимум: {min_acceptable:.0f}₽"
            recommendation = "reject"
        elif proposed_rate > max_acceptable:
            status = "too_high"
            message = f"⚠️ Ставка выше рынка. Оптимально: {optimal:.0f}₽"
            recommendation = "negotiate"
        else:
            status = "adequate"
            message = f"✓ Ставка в рынке. Оптимально: {optimal:.0f}₽"
            recommendation = "accept"
        
        return {
            "status": status,
            "message": message,
            "recommendation": recommendation,
            "proposed_rate": proposed_rate,
            "min_acceptable": min_acceptable,
            "max_acceptable": max_acceptable,
            "optimal_rate": optimal,
            "profit_margin": round((proposed_rate - min_acceptable) / proposed_rate * 100, 1) if proposed_rate > 0 else 0
        }
    
    def check_dangerous_combinations(self, loads: List[Load]) -> List[dict]:
        """Проверка опасных комбинаций грузов."""
        
        warnings = []
        
        # Ключевые слова опасных грузов
        dangerous_keywords = {
            "химия": ["кислота", "щёлочь", "химикат", "реагент"],
            "горючее": ["бензин", "дизель", "масло", "топливо", "газ"],
            "хрупкое": ["стекло", "керамика", "электроника", "хрупкий"],
            "продукты": ["продукты", "еда", "мясо", "молоко", "овощи"],
        }
        
        load_categories = {}
        
        for load in loads:
            # Здесь в реальности анализ описания груза
            # Пока базовая логика
            load_categories[load.id] = "general"
        
        # Проверка несовместимости
        # В реальной системе здесь сложная логика
        
        return warnings
    
    def auto_distribute_loads(self, loads: List[Load], trucks: List[Truck]) -> dict:
        """Автоматическое распределение заявок по машинам."""
        
        distribution = []
        unassigned = []
        
        available_trucks = [t for t in trucks if t.status == "free"]
        
        for load in loads:
            if load.status != "open":
                continue
            
            matches = self.find_matching_trucks(load, available_trucks)
            
            if matches:
                best_match = matches[0]
                distribution.append({
                    "load_id": load.id,
                    "load_route": f"{load.from_city} → {load.to_city}",
                    "assigned_truck": best_match["truck_id"],
                    "driver": best_match["owner_name"],
                    "match_score": best_match["match_score"]
                })
                # Убираем машину из доступных
                available_trucks = [t for t in available_trucks if t.id != best_match["truck_id"]]
            else:
                unassigned.append({
                    "load_id": load.id,
                    "load_route": f"{load.from_city} → {load.to_city}",
                    "reason": "Нет подходящих машин"
                })
        
        return {
            "distributed": distribution,
            "unassigned": unassigned,
            "total_loads": len(loads),
            "assigned_count": len(distribution),
            "efficiency": round(len(distribution) / len(loads) * 100, 1) if loads else 0
        }
    
    def generate_alerts(self, load: Load, carrier: User) -> List[dict]:
        """Генерация предупреждений для диспетчера."""
        
        alerts = []
        
        # Проверка рейтинга
        if carrier.rating < 3.0:
            alerts.append({
                "type": "danger",
                "icon": "🚨",
                "message": f"У перевозчика низкий рейтинг: {carrier.rating}",
                "action": "Рекомендуем отказать"
            })
        
        # Проверка цены
        if load.price < 5000:
            alerts.append({
                "type": "warning",
                "icon": "⚠️",
                "message": "Подозрительно низкая цена заявки",
                "action": "Проверить условия"
            })
        
        # Проверка маршрута
        if load.from_city == load.to_city:
            alerts.append({
                "type": "error",
                "icon": "❌",
                "message": "Город отправления = город назначения",
                "action": "Уточнить маршрут"
            })
        
        return alerts


# Singleton instance
ai_dispatcher = AIDispatcher()




