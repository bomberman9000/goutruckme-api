"""
🚛 AI-ЛОГИСТ (Модуль 2)
Автоматический подбор машин, сравнение ставок, рекомендации

Это персональный логист 24/7
"""
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class TruckType(str, Enum):
    GAZEL = "газель"
    T5 = "5т"
    T10 = "10т"
    T20 = "20т"
    FURA = "фура"
    REF = "рефрижератор"
    TENT = "тент"
    BOARD = "бортовой"


@dataclass
class TruckMatch:
    """Результат матчинга машины."""
    truck_id: int
    driver_name: str
    driver_phone: str
    truck_type: str
    rating: float
    price: float
    match_score: int
    eta_hours: float
    reasons: List[str]


class AILogist:
    """
    🧠 AI-Логист — мозг поиска машин.
    
    Функции:
    - Подбор машины по маршруту
    - Сравнение ставок
    - Прогнозирование стоимости
    - Рекомендация типа ТС
    - Подбор водителей по регионам
    - Автоматические рассылки водителям
    - Сбор ставок
    - Формирование ТОП-3 предложений
    """
    
    # Характеристики типов ТС
    TRUCK_SPECS = {
        "газель": {
            "max_weight": 1.5,
            "max_volume": 9,
            "max_length": 3,
            "base_rate": 18,  # руб/км
            "loading_types": ["задняя", "боковая"],
        },
        "5т": {
            "max_weight": 5,
            "max_volume": 36,
            "max_length": 6,
            "base_rate": 28,
            "loading_types": ["задняя", "боковая", "верхняя"],
        },
        "10т": {
            "max_weight": 10,
            "max_volume": 54,
            "max_length": 7.5,
            "base_rate": 38,
            "loading_types": ["задняя", "боковая", "верхняя"],
        },
        "20т": {
            "max_weight": 20,
            "max_volume": 82,
            "max_length": 13.6,
            "base_rate": 48,
            "loading_types": ["задняя", "боковая", "верхняя"],
        },
        "фура": {
            "max_weight": 22,
            "max_volume": 92,
            "max_length": 13.6,
            "base_rate": 52,
            "loading_types": ["задняя"],
        },
        "рефрижератор": {
            "max_weight": 20,
            "max_volume": 76,
            "max_length": 13.6,
            "base_rate": 65,
            "loading_types": ["задняя"],
            "special": "температурный режим",
        },
    }
    
    # Примерные расстояния между городами
    DISTANCES = {
        ("москва", "санкт-петербург"): 710,
        ("москва", "казань"): 820,
        ("москва", "нижний новгород"): 420,
        ("москва", "екатеринбург"): 1780,
        ("москва", "новосибирск"): 3350,
        ("москва", "воронеж"): 520,
        ("москва", "ростов-на-дону"): 1070,
        ("москва", "краснодар"): 1350,
        ("москва", "самара"): 1060,
        ("москва", "уфа"): 1340,
        ("санкт-петербург", "казань"): 1530,
        ("санкт-петербург", "нижний новгород"): 1130,
    }
    
    def recommend_truck_type(self, weight: float = None, volume: float = None,
                             length: float = None, cargo_type: str = None) -> Dict[str, Any]:
        """
        🚛 Рекомендация типа ТС по параметрам груза.
        
        Говорит: «нужно газель/5т/фура»
        """
        
        recommendations = []
        
        for truck_type, specs in self.TRUCK_SPECS.items():
            score = 100  # Начальный балл
            reasons = []
            suitable = True
            
            # Проверка веса
            if weight:
                if weight > specs["max_weight"]:
                    suitable = False
                    reasons.append(f"❌ Вес {weight}т превышает лимит {specs['max_weight']}т")
                elif weight > specs["max_weight"] * 0.8:
                    score -= 10
                    reasons.append(f"⚠️ Вес близок к лимиту")
                else:
                    load_percent = weight / specs["max_weight"] * 100
                    reasons.append(f"✓ Загрузка по весу: {load_percent:.0f}%")
            
            # Проверка объёма
            if volume:
                if volume > specs["max_volume"]:
                    suitable = False
                    reasons.append(f"❌ Объём {volume}м³ превышает лимит {specs['max_volume']}м³")
                elif volume > specs["max_volume"] * 0.8:
                    score -= 10
                    reasons.append(f"⚠️ Объём близок к лимиту")
                else:
                    load_percent = volume / specs["max_volume"] * 100
                    reasons.append(f"✓ Загрузка по объёму: {load_percent:.0f}%")
            
            # Проверка длины
            if length:
                if length > specs["max_length"]:
                    suitable = False
                    reasons.append(f"❌ Длина {length}м превышает лимит {specs['max_length']}м")
            
            # Спецтребования
            if cargo_type:
                cargo_lower = cargo_type.lower()
                if "холод" in cargo_lower or "замороз" in cargo_lower or "продукт" in cargo_lower:
                    if truck_type != "рефрижератор":
                        score -= 50
                        reasons.append("⚠️ Для данного груза лучше рефрижератор")
                    else:
                        score += 20
                        reasons.append("✓ Рефрижератор подходит для температурного груза")
            
            # Оптимальность по цене (меньше машина = дешевле)
            if suitable:
                if weight and weight < specs["max_weight"] * 0.5:
                    score -= 15  # Машина слишком большая
                    reasons.append("💡 Машина избыточна по грузоподъёмности")
            
            if suitable:
                recommendations.append({
                    "truck_type": truck_type,
                    "score": score,
                    "specs": specs,
                    "reasons": reasons,
                    "suitable": True
                })
        
        # Сортируем по score
        recommendations.sort(key=lambda x: x["score"], reverse=True)
        
        # Лучшая рекомендация
        best = recommendations[0] if recommendations else None
        
        return {
            "recommended": best["truck_type"] if best else None,
            "message": f"🚛 Рекомендуем: {best['truck_type'].upper()}" if best else "Нет подходящих ТС",
            "all_options": recommendations[:3],  # ТОП-3
            "cargo_params": {
                "weight": weight,
                "volume": volume,
                "length": length,
                "cargo_type": cargo_type
            }
        }
    
    def get_distance(self, from_city: str, to_city: str) -> int:
        """Получить расстояние между городами."""
        key = (from_city.lower(), to_city.lower())
        reverse_key = (to_city.lower(), from_city.lower())
        return self.DISTANCES.get(key) or self.DISTANCES.get(reverse_key) or 500
    
    def calculate_price(self, from_city: str, to_city: str, 
                        truck_type: str, weight: float = None) -> Dict[str, Any]:
        """
        💰 Прогнозирование стоимости перевозки.
        """
        
        distance = self.get_distance(from_city, to_city)
        specs = self.TRUCK_SPECS.get(truck_type, self.TRUCK_SPECS["10т"])
        base_rate = specs["base_rate"]
        
        # Базовая цена
        base_price = distance * base_rate
        
        # Корректировки
        adjustments = []
        
        # Дальность
        if distance > 2000:
            base_price *= 0.9  # Скидка на дальние
            adjustments.append("−10% скидка за дальность")
        elif distance < 200:
            base_price *= 1.2  # Наценка на короткие
            adjustments.append("+20% наценка за короткое плечо")
        
        # Сезонность (упрощённо)
        month = datetime.now().month
        if month in [12, 1, 8]:  # Высокий сезон
            base_price *= 1.15
            adjustments.append("+15% высокий сезон")
        
        min_price = base_price * 0.85
        max_price = base_price * 1.25
        
        return {
            "route": f"{from_city} → {to_city}",
            "distance_km": distance,
            "truck_type": truck_type,
            "pricing": {
                "recommended": round(base_price),
                "min": round(min_price),
                "max": round(max_price),
                "per_km": round(base_price / distance, 1)
            },
            "adjustments": adjustments,
            "breakdown": {
                "base_rate_per_km": base_rate,
                "distance": distance,
                "base_total": distance * base_rate
            }
        }
    
    def find_trucks(self, load: Dict, trucks: List[Dict], 
                    drivers: List[Dict] = None) -> Dict[str, Any]:
        """
        🔍 Поиск и подбор машин для груза.
        
        Подбирает по:
        - Типу ТС
        - Грузоподъёмности
        - Региону
        - Рейтингу водителя
        - Доступности
        """
        
        from_city = load.get("from_city", "").lower()
        to_city = load.get("to_city", "").lower()
        weight = load.get("weight", 10)
        volume = load.get("volume")
        price = load.get("price", 0)
        
        # Рекомендуем тип ТС
        truck_rec = self.recommend_truck_type(weight=weight, volume=volume)
        recommended_type = truck_rec.get("recommended", "10т")
        
        matches = []
        
        for truck in trucks:
            score = 0
            reasons = []
            
            # Проверка статуса
            if truck.get("status") != "free":
                continue
            
            truck_type = truck.get("type", "").lower()
            truck_specs = self.TRUCK_SPECS.get(truck_type, {})
            
            # Проверка грузоподъёмности
            max_weight = truck_specs.get("max_weight", truck.get("capacity", 20))
            if weight and weight > max_weight:
                continue  # Не подходит
            
            # Балл за тип ТС
            if truck_type == recommended_type:
                score += 30
                reasons.append("✓ Оптимальный тип ТС")
            elif truck_type in ["фура", "20т"] and recommended_type in ["фура", "20т"]:
                score += 20
                reasons.append("✓ Подходящий тип ТС")
            else:
                score += 10
            
            # Балл за регион
            region = truck.get("region", "").lower()
            if from_city in region:
                score += 25
                reasons.append("✓ Машина в регионе погрузки")
            
            # Балл за рейтинг
            owner = truck.get("owner", {})
            rating = owner.get("rating", 4.0) if isinstance(owner, dict) else 4.0
            if rating >= 4.8:
                score += 25
                reasons.append(f"⭐ Высокий рейтинг: {rating}")
            elif rating >= 4.5:
                score += 20
                reasons.append(f"⭐ Хороший рейтинг: {rating}")
            elif rating >= 4.0:
                score += 10
                reasons.append(f"○ Нормальный рейтинг: {rating}")
            else:
                score -= 10
                reasons.append(f"⚠️ Низкий рейтинг: {rating}")
            
            # Добавляем в результаты
            matches.append({
                "truck_id": truck.get("id"),
                "truck_type": truck_type,
                "capacity": max_weight,
                "region": truck.get("region"),
                "owner_id": truck.get("user_id"),
                "owner_name": owner.get("fullname", "Unknown") if isinstance(owner, dict) else "Unknown",
                "owner_phone": owner.get("phone", "") if isinstance(owner, dict) else "",
                "rating": rating,
                "match_score": score,
                "reasons": reasons
            })
        
        # Сортируем по score
        matches.sort(key=lambda x: x["match_score"], reverse=True)
        
        # ТОП-3 предложения
        top_3 = matches[:3]
        
        return {
            "load": {
                "route": f"{from_city} → {to_city}",
                "weight": weight,
                "volume": volume,
                "price": price
            },
            "recommended_truck_type": recommended_type,
            "total_found": len(matches),
            "top_3": top_3,
            "all_matches": matches[:10],
            "message": f"🚛 Найдено {len(matches)} машин. ТОП-3 предложения готовы." if matches else "❌ Подходящих машин не найдено"
        }
    
    def compare_bids(self, bids: List[Dict], load: Dict) -> Dict[str, Any]:
        """
        📊 Сравнение ставок от перевозчиков.
        """
        
        if not bids:
            return {"error": "Нет ставок для сравнения"}
        
        # Расчёт референсной цены
        price_calc = self.calculate_price(
            from_city=load.get("from_city", ""),
            to_city=load.get("to_city", ""),
            truck_type=load.get("truck_type", "10т"),
            weight=load.get("weight")
        )
        
        reference_price = price_calc["pricing"]["recommended"]
        
        analyzed_bids = []
        
        for bid in bids:
            bid_price = bid.get("price", 0)
            carrier = bid.get("carrier", {})
            
            # Оценка цены
            price_diff = ((bid_price - reference_price) / reference_price) * 100 if reference_price else 0
            
            if price_diff < -20:
                price_status = "🔥 Очень выгодно"
                price_score = 30
            elif price_diff < -5:
                price_status = "✓ Выгодно"
                price_score = 20
            elif price_diff < 10:
                price_status = "○ В рынке"
                price_score = 10
            elif price_diff < 25:
                price_status = "⚠️ Выше рынка"
                price_score = 0
            else:
                price_status = "❌ Дорого"
                price_score = -10
            
            # Общий score
            rating = carrier.get("rating", 4.0) if isinstance(carrier, dict) else 4.0
            total_score = price_score + (rating * 10)
            
            analyzed_bids.append({
                "bid_id": bid.get("id"),
                "price": bid_price,
                "price_diff_percent": round(price_diff, 1),
                "price_status": price_status,
                "carrier_id": bid.get("carrier_id"),
                "carrier_name": carrier.get("fullname", "Unknown") if isinstance(carrier, dict) else "Unknown",
                "carrier_rating": rating,
                "comment": bid.get("comment", ""),
                "total_score": total_score,
                "recommendation": "Рекомендуем" if total_score >= 40 else "Можно рассмотреть" if total_score >= 20 else "Не рекомендуем"
            })
        
        # Сортируем по score
        analyzed_bids.sort(key=lambda x: x["total_score"], reverse=True)
        
        best = analyzed_bids[0] if analyzed_bids else None
        
        return {
            "reference_price": reference_price,
            "total_bids": len(bids),
            "best_bid": best,
            "all_bids": analyzed_bids,
            "summary": {
                "min_price": min(b["price"] for b in analyzed_bids),
                "max_price": max(b["price"] for b in analyzed_bids),
                "avg_price": round(sum(b["price"] for b in analyzed_bids) / len(analyzed_bids)),
            },
            "recommendation": f"🏆 Лучшее предложение: {best['carrier_name']} за {best['price']}₽" if best else "Нет предложений"
        }
    
    def generate_driver_message(self, load: Dict, 
                                 message_type: str = "offer") -> Dict[str, Any]:
        """
        📨 Генерация сообщения для рассылки водителям.
        """
        
        from_city = load.get("from_city", "")
        to_city = load.get("to_city", "")
        weight = load.get("weight", "")
        volume = load.get("volume", "")
        price = load.get("price", "")
        loading_date = load.get("loading_date", "по договорённости")
        
        if message_type == "offer":
            message = f"""🚛 НОВАЯ ЗАЯВКА

📍 Маршрут: {from_city} → {to_city}
📦 Вес: {weight} т
📐 Объём: {volume} м³
💰 Ставка: {price} ₽
📅 Погрузка: {loading_date}

Интересно? Ответьте вашей ценой!"""

        elif message_type == "urgent":
            message = f"""🔥 СРОЧНАЯ ЗАЯВКА!

📍 {from_city} → {to_city}
📦 {weight} т / {volume} м³
💰 {price} ₽

Нужна машина СЕГОДНЯ!
Ответьте СРОЧНО если готовы!"""

        elif message_type == "request_price":
            message = f"""Добрый день!

Ищем машину:
📍 {from_city} → {to_city}
📦 Вес: {weight} т

Какая у вас ставка на данный маршрут?"""

        else:
            message = f"Заявка: {from_city} → {to_city}, {weight}т, {price}₽"
        
        return {
            "message": message,
            "message_type": message_type,
            "load_summary": f"{from_city} → {to_city}",
            "char_count": len(message)
        }
    
    def auto_dispatch(self, loads: List[Dict], trucks: List[Dict]) -> Dict[str, Any]:
        """
        🤖 Автоматическое распределение заявок по машинам.
        """
        
        assignments = []
        unassigned = []
        available_trucks = [t for t in trucks if t.get("status") == "free"]
        
        for load in loads:
            if load.get("status") != "open":
                continue
            
            # Ищем лучшую машину
            result = self.find_trucks(load, available_trucks)
            
            if result["top_3"]:
                best_match = result["top_3"][0]
                
                assignments.append({
                    "load_id": load.get("id"),
                    "load_route": f"{load.get('from_city')} → {load.get('to_city')}",
                    "assigned_truck_id": best_match["truck_id"],
                    "assigned_driver": best_match["owner_name"],
                    "match_score": best_match["match_score"],
                    "status": "assigned"
                })
                
                # Убираем машину из доступных
                available_trucks = [t for t in available_trucks if t.get("id") != best_match["truck_id"]]
            else:
                unassigned.append({
                    "load_id": load.get("id"),
                    "load_route": f"{load.get('from_city')} → {load.get('to_city')}",
                    "reason": "Нет подходящих машин"
                })
        
        return {
            "total_loads": len(loads),
            "assigned": len(assignments),
            "unassigned": len(unassigned),
            "efficiency": round(len(assignments) / len(loads) * 100, 1) if loads else 0,
            "assignments": assignments,
            "unassigned_loads": unassigned,
            "remaining_trucks": len(available_trucks)
        }
    
    def get_route_analytics(self, from_city: str, to_city: str) -> Dict[str, Any]:
        """
        📊 Аналитика по маршруту.
        """
        
        distance = self.get_distance(from_city, to_city)
        
        # Средние цены по типам ТС
        prices_by_type = {}
        for truck_type, specs in self.TRUCK_SPECS.items():
            price = distance * specs["base_rate"]
            prices_by_type[truck_type] = {
                "price": round(price),
                "per_km": specs["base_rate"]
            }
        
        # Время в пути (примерно 60 км/ч средняя)
        travel_time = distance / 60
        
        return {
            "route": f"{from_city} → {to_city}",
            "distance_km": distance,
            "estimated_travel_time_hours": round(travel_time, 1),
            "prices_by_truck_type": prices_by_type,
            "market_info": {
                "demand": "high" if distance < 500 else "medium",
                "competition": "high",
                "season_factor": 1.0
            },
            "recommendations": [
                f"Оптимальное время отправления: утро (6-8 часов)",
                f"Рекомендуемый отдых: каждые 4 часа",
                f"Платные дороги на маршруте: да" if distance > 300 else "Платные дороги: нет"
            ]
        }


# Singleton instance
ai_logist = AILogist()




