"""
AI Помощник водителя - маршруты, топливо, документы
"""
from datetime import datetime, timedelta
from typing import Optional
from app.services.ai_logist import ai_logist
from app.services.geo import canonicalize_city_name
from app.services.load_public import build_public_load_context


class AIDriver:
    """ИИ-помощник для водителей."""
    
    # Средний расход топлива по типам (л/100км)
    FUEL_CONSUMPTION = {
        "газель": 12,
        "5т": 18,
        "10т": 25,
        "20т": 32,
        "фура": 35,
    }
    
    # Средняя цена топлива
    FUEL_PRICE = 58  # руб/литр ДТ
    
    def calculate_route(self, from_city: str, to_city: str) -> dict:
        """Расчёт маршрута на общем route-layer, без фейковых 500 км."""
        from_city = canonicalize_city_name(from_city)
        to_city = canonicalize_city_name(to_city)
        distance = ai_logist.get_distance(from_city, to_city)
        if distance is None:
            return {
                "from": from_city,
                "to": to_city,
                "distance_km": None,
                "duration_hours": None,
                "toll_roads_cost": None,
                "route_type": "unknown",
                "warnings": [{
                    "type": "route",
                    "message": "⚠️ Точное расстояние не определено. Уточните маршрут."
                }],
            }

        duration_hours = max(1, round(distance / 70, 1))
        toll_cost = round(distance * 0.9) if distance > 300 else 0
        route_data = {
            "distance": distance,
            "duration": duration_hours,
            "tolls": toll_cost,
        }

        return {
            "from": from_city,
            "to": to_city,
            "distance_km": route_data["distance"],
            "duration_hours": route_data["duration"],
            "toll_roads_cost": route_data["tolls"],
            "route_type": "optimal",
            "warnings": self._get_route_warnings(route_data)
        }
    
    def _get_route_warnings(self, route_data: dict) -> list:
        """Предупреждения по маршруту."""
        warnings = []
        
        if route_data.get("tolls") and route_data["tolls"] > 1000:
            warnings.append({
                "type": "toll",
                "message": f"⚠️ Платные дороги: {route_data['tolls']}₽"
            })
        
        if route_data.get("duration") and route_data["duration"] > 8:
            warnings.append({
                "type": "rest",
                "message": "⚠️ Маршрут более 8 часов. Требуется отдых по тахографу."
            })
        
        return warnings
    
    def calculate_fuel(self, distance_km: float, truck_type: str) -> dict:
        """Расчёт топлива."""
        
        consumption = self.FUEL_CONSUMPTION.get(truck_type, 30)
        
        fuel_needed = (distance_km / 100) * consumption
        fuel_cost = fuel_needed * self.FUEL_PRICE
        
        return {
            "distance_km": distance_km,
            "truck_type": truck_type,
            "consumption_per_100km": consumption,
            "fuel_needed_liters": round(fuel_needed, 1),
            "fuel_cost_rub": round(fuel_cost, 0),
            "fuel_price_per_liter": self.FUEL_PRICE,
            "recommendation": f"Заправить минимум {round(fuel_needed * 1.1, 0)} литров (+10% запас)"
        }
    
    def calculate_profitability(self, load_price: float, distance_km: float, 
                                truck_type: str, toll_cost: float = 0) -> dict:
        """Расчёт прибыльности рейса."""
        
        # Расходы на топливо
        fuel = self.calculate_fuel(distance_km, truck_type)
        fuel_cost = fuel["fuel_cost_rub"]
        
        # Прочие расходы (примерно)
        other_costs = distance_km * 2  # 2 руб/км на износ, ТО
        
        total_costs = fuel_cost + toll_cost + other_costs
        profit = load_price - total_costs
        margin = (profit / load_price * 100) if load_price > 0 else 0
        
        # Рекомендация
        if margin < 10:
            recommendation = "❌ Рейс в минус или близко к нулю. Не рекомендуем."
            status = "reject"
        elif margin < 20:
            recommendation = "⚠️ Низкая маржа. Попробуйте поторговаться."
            status = "negotiate"
        elif margin < 35:
            recommendation = "✓ Нормальная прибыльность."
            status = "accept"
        else:
            recommendation = "🔥 Отличная ставка! Берите!"
            status = "excellent"
        
        return {
            "load_price": load_price,
            "costs": {
                "fuel": round(fuel_cost, 0),
                "tolls": toll_cost,
                "other": round(other_costs, 0),
                "total": round(total_costs, 0)
            },
            "profit": round(profit, 0),
            "margin_percent": round(margin, 1),
            "recommendation": recommendation,
            "status": status,
            "price_per_km": round(load_price / distance_km, 1) if distance_km > 0 else 0
        }
    
    def estimate_loading_time(self, cargo_type: str = "general", 
                              weight: float = 10) -> dict:
        """Оценка времени погрузки/разгрузки."""
        
        # Базовое время в минутах
        base_time = 30
        
        # Корректировка по весу
        if weight > 15:
            base_time += 30
        elif weight > 10:
            base_time += 15
        
        return {
            "loading_time_min": base_time,
            "unloading_time_min": base_time,
            "total_time_min": base_time * 2,
            "recommendation": f"Закладывайте {base_time * 2 + 30} минут с запасом"
        }
    
    def generate_voice_hints(self, route_info: dict) -> list:
        """Генерация голосовых подсказок для водителя."""
        
        hints = []
        
        hints.append({
            "time": "start",
            "text": f"Маршрут построен. До точки назначения {route_info['distance_km']} км. "
                   f"Примерное время в пути {route_info['duration_hours']} часов."
        })
        
        if route_info.get("toll_roads_cost", 0) > 0:
            hints.append({
                "time": "before_toll",
                "text": f"Внимание! Впереди платная дорога. Приготовьте {route_info['toll_roads_cost']} рублей "
                       "или транспондер."
            })
        
        if route_info.get("duration_hours", 0) > 4:
            hints.append({
                "time": "4h",
                "text": "Вы в пути 4 часа. Рекомендуем сделать остановку на 15 минут."
            })
        
        if route_info.get("duration_hours", 0) > 8:
            hints.append({
                "time": "8h",
                "text": "Внимание! Норма рабочего времени. Требуется отдых минимум 45 минут."
            })
        
        hints.append({
            "time": "arrival",
            "text": "Вы прибыли в пункт назначения. Хорошей разгрузки!"
        })
        
        return hints
    
    def generate_waybill(self, load, driver: dict, truck: dict) -> dict:
        """Генерация путевого листа."""
        
        waybill_number = f"ПЛ-{datetime.now().strftime('%Y%m%d%H%M')}"
        load_context = build_public_load_context(load)
        
        return {
            "waybill_number": waybill_number,
            "date": datetime.now().strftime("%d.%m.%Y"),
            "driver": {
                "name": driver.get("name", ""),
                "license": driver.get("license", ""),
                "phone": driver.get("phone", "")
            },
            "truck": {
                "model": truck.get("model", ""),
                "plate": truck.get("plate", ""),
                "type": truck.get("type", "")
            },
            "route": {
                "from": load_context["from_city"],
                "to": load_context["to_city"],
                "departure_time": "",
                "arrival_time": ""
            },
            "cargo": {
                "description": f"Груз {load.weight or ''}т",
                "weight": load.weight,
                "volume": load.volume
            },
            "odometer": {
                "start": "",
                "end": ""
            },
            "fuel": {
                "start": "",
                "received": "",
                "end": ""
            },
            "signatures": {
                "dispatcher": "",
                "driver": "",
                "mechanic": ""
            }
        }


# Singleton instance
ai_driver = AIDriver()


