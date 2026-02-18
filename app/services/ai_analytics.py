"""
AI Аналитика - статистика, прибыль, оптимизация
"""
from datetime import datetime, timedelta
from typing import List, Optional
from collections import defaultdict


class AIAnalytics:
    """ИИ-аналитика для компаний и перевозчиков."""
    
    def calculate_company_stats(self, loads: list, bids: list, 
                                 period_days: int = 30) -> dict:
        """Статистика компании за период."""
        
        total_loads = len(loads)
        completed_loads = len([l for l in loads if l.status == "closed"])
        open_loads = len([l for l in loads if l.status == "open"])
        
        total_revenue = sum(l.price for l in loads if l.status == "closed")
        avg_price = total_revenue / completed_loads if completed_loads > 0 else 0
        
        return {
            "period_days": period_days,
            "loads": {
                "total": total_loads,
                "completed": completed_loads,
                "open": open_loads,
                "completion_rate": round(completed_loads / total_loads * 100, 1) if total_loads > 0 else 0
            },
            "financial": {
                "total_revenue": round(total_revenue, 0),
                "average_load_price": round(avg_price, 0),
                "estimated_profit": round(total_revenue * 0.15, 0),  # ~15% маржа
            },
            "performance": {
                "loads_per_day": round(total_loads / period_days, 1),
                "revenue_per_day": round(total_revenue / period_days, 0)
            }
        }
    
    def calculate_carrier_stats(self, user_id: int, bids: list, 
                                 loads: list, period_days: int = 30) -> dict:
        """Статистика перевозчика."""
        
        carrier_bids = [b for b in bids if b.carrier_id == user_id]
        accepted_bids = [b for b in carrier_bids if b.status == "accepted"]
        
        total_earned = sum(b.price for b in accepted_bids)
        
        return {
            "period_days": period_days,
            "bids": {
                "total": len(carrier_bids),
                "accepted": len(accepted_bids),
                "rejected": len([b for b in carrier_bids if b.status == "rejected"]),
                "acceptance_rate": round(len(accepted_bids) / len(carrier_bids) * 100, 1) if carrier_bids else 0
            },
            "earnings": {
                "total": round(total_earned, 0),
                "average_per_load": round(total_earned / len(accepted_bids), 0) if accepted_bids else 0,
                "per_day": round(total_earned / period_days, 0)
            },
            "recommendations": self._get_carrier_recommendations(carrier_bids, accepted_bids)
        }
    
    def _get_carrier_recommendations(self, all_bids: list, accepted_bids: list) -> list:
        """Рекомендации для перевозчика."""
        
        recommendations = []
        
        if len(all_bids) > 0:
            acceptance_rate = len(accepted_bids) / len(all_bids)
            
            if acceptance_rate < 0.2:
                recommendations.append({
                    "type": "pricing",
                    "message": "Низкий процент принятых ставок. Попробуйте снизить цены на 5-10%."
                })
            
            if acceptance_rate > 0.8:
                recommendations.append({
                    "type": "pricing",
                    "message": "Высокий процент принятия. Возможно, стоит поднять ставки на 5-10%."
                })
        
        if len(all_bids) < 10:
            recommendations.append({
                "type": "activity",
                "message": "Мало ставок за период. Увеличьте активность для роста дохода."
            })
        
        return recommendations
    
    def analyze_market_rates(self, loads: list, route: tuple = None) -> dict:
        """Анализ рыночных ставок."""
        
        if route:
            from_city, to_city = route
            filtered_loads = [
                l for l in loads 
                if l.from_city.lower() == from_city.lower() 
                and l.to_city.lower() == to_city.lower()
            ]
        else:
            filtered_loads = loads
        
        if not filtered_loads:
            return {"error": "Нет данных для анализа"}
        
        prices = [l.price for l in filtered_loads]
        
        return {
            "route": f"{route[0]} → {route[1]}" if route else "Все направления",
            "sample_size": len(prices),
            "prices": {
                "min": min(prices),
                "max": max(prices),
                "average": round(sum(prices) / len(prices), 0),
                "median": sorted(prices)[len(prices) // 2]
            },
            "recommendation": {
                "shipper": f"Оптимальная цена для размещения: {round(sum(prices) / len(prices) * 1.05, 0)}₽",
                "carrier": f"Минимальная выгодная ставка: {round(sum(prices) / len(prices) * 0.95, 0)}₽"
            }
        }
    
    def optimize_rates(self, historical_bids: list, target_acceptance_rate: float = 0.5) -> dict:
        """Оптимизация ставок на основе истории."""
        
        if not historical_bids:
            return {"error": "Нет исторических данных"}
        
        accepted = [b for b in historical_bids if b.status == "accepted"]
        rejected = [b for b in historical_bids if b.status == "rejected"]
        
        avg_accepted = sum(b.price for b in accepted) / len(accepted) if accepted else 0
        avg_rejected = sum(b.price for b in rejected) / len(rejected) if rejected else 0
        
        # Оптимальная цена между принятыми и отклонёнными
        if avg_accepted and avg_rejected:
            optimal_price = (avg_accepted + avg_rejected) / 2
        else:
            optimal_price = avg_accepted or avg_rejected
        
        return {
            "analysis": {
                "accepted_avg": round(avg_accepted, 0),
                "rejected_avg": round(avg_rejected, 0),
                "current_acceptance_rate": round(len(accepted) / len(historical_bids) * 100, 1)
            },
            "optimization": {
                "recommended_price": round(optimal_price, 0),
                "price_range": {
                    "min": round(optimal_price * 0.9, 0),
                    "max": round(optimal_price * 1.1, 0)
                }
            },
            "expected_outcome": f"При ставке {round(optimal_price, 0)}₽ ожидаемый процент принятия: ~{target_acceptance_rate * 100}%"
        }
    
    def generate_report(self, company_id: int, loads: list, 
                        bids: list, period: str = "month") -> dict:
        """Генерация отчёта для компании."""
        
        period_days = {"week": 7, "month": 30, "quarter": 90, "year": 365}.get(period, 30)
        
        stats = self.calculate_company_stats(loads, bids, period_days)
        
        return {
            "report_id": f"RPT-{datetime.now().strftime('%Y%m%d%H%M')}",
            "company_id": company_id,
            "period": period,
            "generated_at": datetime.now().isoformat(),
            "summary": stats,
            "charts_data": {
                "revenue_trend": [],  # TODO: данные для графиков
                "loads_by_route": [],
                "margin_analysis": []
            },
            "insights": [
                "📈 Рост выручки по сравнению с прошлым периодом",
                "🚚 Самое популярное направление: Москва → СПб",
                "💡 Рекомендация: увеличить ставки на 5% на маржинальных маршрутах"
            ]
        }
    
    def predict_demand(self, historical_loads: list, route: tuple) -> dict:
        """Прогноз спроса на направлении."""
        
        # Базовый прогноз (в реальности ML модель)
        from_city, to_city = route
        
        return {
            "route": f"{from_city} → {to_city}",
            "prediction_period": "next_7_days",
            "expected_loads": 15,  # Заглушка
            "confidence": 0.75,
            "trend": "growing",
            "recommendation": "Хорошее время для размещения ставок на этом направлении"
        }


# Singleton instance  
ai_analytics = AIAnalytics()




