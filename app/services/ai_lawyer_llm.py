"""
🤖 AI-ЮРИСТ (Модуль 1) - Главный приоритет
Проверка заявок, анализ рисков, юридическая экспертиза

Интеграция с LLM (OpenAI GPT / YandexGPT / GigaChat)
"""
import os
import json
import httpx
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    OK = "ok"
    LOW = "low_risk"
    MEDIUM = "medium_risk"
    HIGH = "high_risk"
    CRITICAL = "critical"


@dataclass
class LegalConclusion:
    """Юридическое заключение по заявке."""
    status: RiskLevel
    score: int  # 0-100
    issues: List[str]
    recommendations: List[str]
    summary: str
    details: Dict[str, Any]


class AILawyerLLM:
    """
    AI-Юрист с интеграцией LLM.
    
    Функции:
    - Анализ текста заявки
    - Проверка условий перевозки
    - Проверка адресов и реквизитов
    - Проверка ИНН/ОГРН
    - Анализ штрафных рисков
    - Анализ соответствия ТС грузу
    - Выдача заключения: OK / риск / высокий риск
    """
    
    # Конфигурация LLM
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = "gpt-4o-mini"  # или gpt-4o для лучшего качества
    
    # Альтернативные LLM
    YANDEX_GPT_KEY = os.getenv("YANDEX_GPT_KEY", "")
    GIGACHAT_KEY = os.getenv("GIGACHAT_KEY", "")
    
    # Системный промпт для юриста
    SYSTEM_PROMPT = """Ты — AI-юрист платформы грузоперевозок GouTruckMe.

Твоя задача — анализировать заявки на перевозку грузов и выявлять:
1. Юридические риски
2. Ошибки в документах
3. Несоответствия условий
4. Признаки мошенничества
5. Проблемы с ценообразованием

Отвечай ТОЛЬКО в формате JSON:
{
    "risk_level": "ok|low_risk|medium_risk|high_risk|critical",
    "risk_score": 0-100,
    "issues": ["список проблем"],
    "recommendations": ["список рекомендаций"],
    "summary": "краткое заключение",
    "legal_notes": ["юридические замечания"],
    "document_errors": ["ошибки в документах"],
    "price_analysis": "анализ цены",
    "route_analysis": "анализ маршрута",
    "cargo_compliance": "соответствие груза и ТС"
}

Будь строгим и внимательным. Защищай интересы пользователей платформы."""

    def __init__(self):
        # Импортируем настройки
        try:
            from app.core.config import settings
            self.OPENAI_API_KEY = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
            self.OPENAI_MODEL = settings.OPENAI_MODEL or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            self.YANDEX_GPT_KEY = settings.YANDEX_GPT_KEY or os.getenv("YANDEX_GPT_KEY", "")
            self.GIGACHAT_KEY = settings.GIGACHAT_KEY or os.getenv("GIGACHAT_KEY", "")
            self.AI_USE_LLM = settings.AI_USE_LLM
            self.AI_FALLBACK_TO_LOCAL = settings.AI_FALLBACK_TO_LOCAL
            self.AI_TIMEOUT_SECONDS = settings.AI_TIMEOUT_SECONDS
        except:
            # Fallback если config не доступен
            self.AI_USE_LLM = os.getenv("AI_USE_LLM", "false").lower() == "true"
            self.AI_FALLBACK_TO_LOCAL = os.getenv("AI_FALLBACK_TO_LOCAL", "true").lower() == "true"
            self.AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))
        
        self.llm_provider = self._detect_llm_provider()
    
    def _detect_llm_provider(self) -> str:
        """Определение доступного LLM провайдера."""
        if not self.AI_USE_LLM:
            return "mock"  # Локальный режим
        
        if self.OPENAI_API_KEY and len(self.OPENAI_API_KEY) > 10:
            return "openai"
        elif self.YANDEX_GPT_KEY and len(self.YANDEX_GPT_KEY) > 10:
            return "yandex"
        elif self.GIGACHAT_KEY and len(self.GIGACHAT_KEY) > 10:
            return "gigachat"
        return "mock"  # Заглушка для тестов
    
    async def _call_openai(self, prompt: str) -> str:
        """Вызов OpenAI API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                },
                timeout=30.0
            )
            data = response.json()
            return data["choices"][0]["message"]["content"]
    
    def _call_openai_sync(self, prompt: str) -> str:
        """Синхронный вызов OpenAI API."""
        timeout = getattr(self, 'AI_TIMEOUT_SECONDS', 30.0)
        with httpx.Client() as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                },
                timeout=timeout
            )
            response.raise_for_status()  # Проверка на ошибки HTTP
            data = response.json()
            if "choices" not in data or len(data["choices"]) == 0:
                raise ValueError("Empty response from OpenAI API")
            return data["choices"][0]["message"]["content"]
    
    def _mock_analysis(self, load_data: dict) -> dict:
        """Заглушка для анализа без LLM."""
        issues = []
        recommendations = []
        risk_score = 0
        
        # Проверка цены
        price = load_data.get("price", 0)
        if price < 5000:
            issues.append("⚠️ Подозрительно низкая цена")
            risk_score += 20
        elif price > 500000:
            issues.append("⚠️ Очень высокая цена — проверьте условия")
            risk_score += 15
        
        # Проверка маршрута
        from_city = load_data.get("from_city", "")
        to_city = load_data.get("to_city", "")
        if from_city.lower() == to_city.lower():
            issues.append("❌ Город отправления = город назначения")
            risk_score += 40
        
        # Проверка веса
        weight = load_data.get("weight", 0)
        if weight and weight > 40:
            issues.append("⚠️ Вес превышает 40 тонн — требуется спецразрешение")
            risk_score += 25
        
        # Проверка объёма
        volume = load_data.get("volume", 0)
        if volume and volume > 120:
            issues.append("⚠️ Объём превышает стандартный (120 м³)")
            risk_score += 15
        
        # Проверка ИНН
        inn = load_data.get("shipper_inn", "")
        if inn and len(inn) not in [10, 12]:
            issues.append("❌ Некорректный ИНН отправителя")
            risk_score += 30
        
        # Определение уровня риска
        if risk_score >= 70:
            risk_level = RiskLevel.CRITICAL
        elif risk_score >= 50:
            risk_level = RiskLevel.HIGH
        elif risk_score >= 30:
            risk_level = RiskLevel.MEDIUM
        elif risk_score >= 10:
            risk_level = RiskLevel.LOW
        else:
            risk_level = RiskLevel.OK
        
        # Рекомендации
        if risk_score > 0:
            recommendations.append("Проверьте данные заявки перед принятием")
        if "ИНН" in str(issues):
            recommendations.append("Запросите корректные реквизиты")
        if risk_score >= 50:
            recommendations.append("Рекомендуем отказаться от заявки")
        if risk_score < 30:
            recommendations.append("Заявка выглядит безопасной")
        
        return {
            "risk_level": risk_level.value,
            "risk_score": risk_score,
            "issues": issues,
            "recommendations": recommendations,
            "summary": f"Риск-скор: {risk_score}/100. {'Заявка безопасна.' if risk_score < 30 else 'Требуется внимание.' if risk_score < 50 else 'Высокий риск!'}",
            "legal_notes": [],
            "document_errors": [],
            "price_analysis": f"Цена {price}₽ — {'в норме' if 5000 <= price <= 500000 else 'требует проверки'}",
            "route_analysis": f"Маршрут: {from_city} → {to_city}",
            "cargo_compliance": "Требуется проверка соответствия ТС"
        }
    
    def analyze_load(self, load_data: dict, use_llm: bool = None) -> dict:
        """
        Анализ заявки на груз.
        
        Args:
            load_data: Данные заявки
            use_llm: Использовать LLM или локальный анализ (None = автоопределение)
        
        Returns:
            Юридическое заключение
        """
        
        # Автоопределение режима
        if use_llm is None:
            use_llm = self.AI_USE_LLM and self.llm_provider != "mock"
        
        # Формируем запрос для LLM
        prompt = f"""Проанализируй заявку на грузоперевозку:

ДАННЫЕ ЗАЯВКИ:
- Маршрут: {load_data.get('from_city', 'Не указан')} → {load_data.get('to_city', 'Не указан')}
- Вес груза: {load_data.get('weight', 'Не указан')} тонн
- Объём: {load_data.get('volume', 'Не указан')} м³
- Цена: {load_data.get('price', 'Не указана')} рублей
- Описание груза: {load_data.get('description', 'Не указано')}
- ИНН отправителя: {load_data.get('shipper_inn', 'Не указан')}
- ИНН перевозчика: {load_data.get('carrier_inn', 'Не указан')}
- Тип ТС: {load_data.get('truck_type', 'Не указан')}
- Дата погрузки: {load_data.get('loading_date', 'Не указана')}
- Условия оплаты: {load_data.get('payment_terms', 'Не указаны')}
- Дополнительно: {load_data.get('additional_info', 'Нет')}

Выполни полный юридический анализ и выдай заключение."""

        # Используем LLM если доступен и запрошен
        if use_llm and self.llm_provider == "openai" and self.OPENAI_API_KEY:
            try:
                llm_response = self._call_openai_sync(prompt)
                # Парсим JSON из ответа
                # LLM может вернуть JSON в markdown блоке или просто текст
                llm_response = llm_response.strip()
                if "```json" in llm_response:
                    llm_response = llm_response.split("```json")[1].split("```")[0].strip()
                elif "```" in llm_response:
                    llm_response = llm_response.split("```")[1].split("```")[0].strip()
                
                result = json.loads(llm_response)
                result["llm_used"] = True
                result["llm_provider"] = "openai"
                return result
            except json.JSONDecodeError as e:
                # Если не удалось распарсить JSON, используем локальный анализ
                if self.AI_FALLBACK_TO_LOCAL:
                    result = self._mock_analysis(load_data)
                    result["llm_error"] = f"JSON parse error: {str(e)}"
                    result["llm_used"] = False
                    result["llm_response_raw"] = llm_response[:200]  # Первые 200 символов
                    return result
                else:
                    raise
            except Exception as e:
                # Fallback на локальный анализ
                if self.AI_FALLBACK_TO_LOCAL:
                    result = self._mock_analysis(load_data)
                    result["llm_error"] = str(e)
                    result["llm_used"] = False
                    return result
                else:
                    raise
        else:
            # Локальный анализ
            result = self._mock_analysis(load_data)
            result["llm_used"] = False
            result["llm_provider"] = "local"
            return result
    
    def check_contract(self, contract_text: str) -> dict:
        """Проверка текста договора."""
        
        prompt = f"""Проанализируй договор на грузоперевозку:

ТЕКСТ ДОГОВОРА:
{contract_text}

Найди:
1. Юридические ошибки
2. Невыгодные условия
3. Отсутствующие пункты
4. Риски для сторон
5. Несоответствия законодательству

Выдай заключение в JSON формате."""

        if self.llm_provider == "openai" and self.OPENAI_API_KEY:
            try:
                response = self._call_openai_sync(prompt)
                return json.loads(response)
            except:
                pass
        
        # Базовый анализ
        issues = []
        if "ответственность" not in contract_text.lower():
            issues.append("Отсутствует раздел об ответственности сторон")
        if "оплата" not in contract_text.lower():
            issues.append("Не указаны условия оплаты")
        if "срок" not in contract_text.lower():
            issues.append("Не указаны сроки исполнения")
        
        return {
            "risk_level": "medium_risk" if issues else "ok",
            "issues": issues,
            "recommendations": ["Добавить недостающие разделы"] if issues else ["Договор в порядке"],
            "llm_used": False
        }
    
    def verify_requisites(self, inn: str = None, ogrn: str = None, 
                          company_name: str = None) -> dict:
        """Проверка реквизитов контрагента."""
        
        result = {
            "verification_id": f"VRF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "overall_status": "pending"
        }
        
        # Проверка ИНН
        if inn:
            inn_clean = ''.join(filter(str.isdigit, inn))
            if len(inn_clean) == 10:
                result["checks"]["inn"] = {
                    "value": inn_clean,
                    "type": "organization",
                    "format_valid": True,
                    "status": "valid"
                }
            elif len(inn_clean) == 12:
                result["checks"]["inn"] = {
                    "value": inn_clean,
                    "type": "individual",
                    "format_valid": True,
                    "status": "valid"
                }
            else:
                result["checks"]["inn"] = {
                    "value": inn,
                    "format_valid": False,
                    "status": "invalid",
                    "error": "Неверная длина ИНН"
                }
        
        # Проверка ОГРН
        if ogrn:
            ogrn_clean = ''.join(filter(str.isdigit, ogrn))
            if len(ogrn_clean) == 13:
                result["checks"]["ogrn"] = {
                    "value": ogrn_clean,
                    "type": "ОГРН",
                    "format_valid": True,
                    "status": "valid"
                }
            elif len(ogrn_clean) == 15:
                result["checks"]["ogrn"] = {
                    "value": ogrn_clean,
                    "type": "ОГРНИП",
                    "format_valid": True,
                    "status": "valid"
                }
            else:
                result["checks"]["ogrn"] = {
                    "value": ogrn,
                    "format_valid": False,
                    "status": "invalid",
                    "error": "Неверная длина ОГРН"
                }
        
        # Определяем общий статус
        all_valid = all(
            check.get("status") == "valid" 
            for check in result["checks"].values()
        )
        result["overall_status"] = "verified" if all_valid else "failed"
        
        return result
    
    def analyze_pricing(self, from_city: str, to_city: str, 
                        weight: float, price: float) -> dict:
        """Анализ адекватности цены."""
        
        # Примерные тарифы (руб/км*тонна)
        BASE_RATE = 3.5
        
        # Примерные расстояния (заглушка)
        distances = {
            ("москва", "санкт-петербург"): 710,
            ("москва", "казань"): 820,
            ("москва", "нижний новгород"): 420,
            ("москва", "екатеринбург"): 1780,
            ("москва", "новосибирск"): 3350,
        }
        
        key = (from_city.lower(), to_city.lower())
        distance = distances.get(key, 500)
        
        # Расчёт рекомендуемой цены
        recommended_price = distance * weight * BASE_RATE
        min_price = recommended_price * 0.7
        max_price = recommended_price * 1.4
        
        if price < min_price:
            status = "too_low"
            message = f"⚠️ Цена подозрительно низкая! Минимум: {min_price:.0f}₽"
            risk = "high"
        elif price > max_price:
            status = "too_high"
            message = f"⚠️ Цена выше рыночной. Оптимально: {recommended_price:.0f}₽"
            risk = "low"
        else:
            status = "adequate"
            message = f"✓ Цена в рынке"
            risk = "none"
        
        return {
            "status": status,
            "message": message,
            "risk": risk,
            "analysis": {
                "proposed_price": price,
                "recommended_price": round(recommended_price),
                "min_acceptable": round(min_price),
                "max_acceptable": round(max_price),
                "distance_km": distance,
                "weight_tons": weight,
                "price_per_km": round(price / distance, 2),
                "price_per_ton_km": round(price / (distance * weight), 2) if weight > 0 else 0
            }
        }
    
    def get_legal_conclusion(self, load_data: dict) -> dict:
        """
        Получить полное юридическое заключение.
        Главный метод модуля.
        """
        
        # 1. Анализ заявки
        load_analysis = self.analyze_load(load_data, use_llm=True)
        
        # 2. Проверка реквизитов
        requisites = self.verify_requisites(
            inn=load_data.get("shipper_inn"),
            ogrn=load_data.get("shipper_ogrn")
        )
        
        # 3. Анализ цены
        pricing = self.analyze_pricing(
            from_city=load_data.get("from_city", ""),
            to_city=load_data.get("to_city", ""),
            weight=load_data.get("weight", 10),
            price=load_data.get("price", 0)
        )
        
        # Формируем итоговое заключение
        conclusion = {
            "conclusion_id": f"LC-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "verdict": {
                "status": load_analysis.get("risk_level", "unknown"),
                "risk_score": load_analysis.get("risk_score", 0),
                "summary": load_analysis.get("summary", ""),
                "recommendation": "ОДОБРЕНО" if load_analysis.get("risk_score", 100) < 30 else 
                                  "ТРЕБУЕТ ПРОВЕРКИ" if load_analysis.get("risk_score", 100) < 50 else
                                  "НЕ РЕКОМЕНДУЕТСЯ"
            },
            "load_analysis": load_analysis,
            "requisites_check": requisites,
            "pricing_analysis": pricing,
            "issues": load_analysis.get("issues", []),
            "recommendations": load_analysis.get("recommendations", []),
            "legal_notes": load_analysis.get("legal_notes", []),
            "llm_used": load_analysis.get("llm_used", False)
        }
        
        return conclusion
    
    def analyze_complaint(self, complaint_data: dict) -> dict:
        """
        🤖 Анализ претензии AI-Юристом.
        
        Проверяет:
        - Обоснованность претензии
        - Серьёзность нарушения
        - История нарушителя
        - Риск повторения
        """
        
        title = complaint_data.get("complaint_title", "")
        description = complaint_data.get("complaint_description", "")
        complaint_type = complaint_data.get("complaint_type", "general")
        defendant_rating = complaint_data.get("defendant_rating", 5.0)
        defendant_points = complaint_data.get("defendant_points", 100)
        defendant_complaints = complaint_data.get("defendant_complaints", 0)
        
        # Локальный анализ (без LLM)
        risk_score = 0
        issues = []
        recommendations = []
        
        # Анализ текста претензии
        description_lower = description.lower()
        
        # Ключевые слова высокой серьёзности
        critical_keywords = ["мошенничество", "обман", "украл", "не заплатил", "пропал", "исчез", "не отвечает"]
        high_keywords = ["задержка", "опоздал", "повредил", "сломал", "не выполнил"]
        
        critical_count = sum(1 for word in critical_keywords if word in description_lower)
        high_count = sum(1 for word in high_keywords if word in description_lower)
        
        if critical_count > 0:
            risk_score += 40
            issues.append("🚨 Обнаружены признаки серьёзного нарушения")
        elif high_count > 0:
            risk_score += 25
            issues.append("⚠️ Обнаружены признаки нарушения")
        
        # Анализ типа претензии
        if complaint_type == "fraud":
            risk_score += 30
            issues.append("🚨 Претензия о мошенничестве")
        elif complaint_type == "damage":
            risk_score += 20
            issues.append("⚠️ Претензия о повреждении груза")
        elif complaint_type == "payment":
            risk_score += 15
            issues.append("⚠️ Претензия о проблемах с оплатой")
        
        # История нарушителя
        if defendant_rating < 3.0:
            risk_score += 25
            issues.append(f"⚠️ Низкий рейтинг нарушителя: {defendant_rating}")
        
        if defendant_complaints > 2:
            risk_score += min(defendant_complaints * 10, 30)
            issues.append(f"⚠️ Множественные претензии: {defendant_complaints}")
        
        if defendant_points < 50:
            risk_score += 15
            issues.append(f"⚠️ Низкий рейтинг баллов: {defendant_points}")
        
        # Ограничиваем риск-скор до 100
        risk_score = min(risk_score, 100)
        
        # Определение уровня риска
        if risk_score >= 70:
            risk_level = RiskLevel.CRITICAL
            recommendations.append("🚨 КРИТИЧЕСКИЙ СЛУЧАЙ: Требуется немедленное рассмотрение администратором")
            recommendations.append("Рекомендуется заблокировать пользователя до выяснения")
        elif risk_score >= 50:
            risk_level = RiskLevel.HIGH
            recommendations.append("⚠️ Высокий риск: Претензия обоснована, требуется проверка")
            recommendations.append("Рекомендуется снять баллы и предупредить пользователя")
        elif risk_score >= 30:
            risk_level = RiskLevel.MEDIUM
            recommendations.append("⚠️ Средний риск: Претензия требует рассмотрения")
        elif risk_score >= 10:
            risk_level = RiskLevel.LOW
            recommendations.append("✓ Низкий риск: Претензия может быть рассмотрена в обычном порядке")
        else:
            risk_level = RiskLevel.OK
            recommendations.append("✓ Претензия выглядит обоснованной")
        
        # Формируем заключение
        conclusion = {
            "risk_level": risk_level.value,
            "risk_score": risk_score,
            "issues": issues,
            "recommendations": recommendations,
            "summary": f"AI-Юрист: Риск-скор {risk_score}/100. {'Критический случай!' if risk_score >= 70 else 'Требует внимания' if risk_score >= 50 else 'В пределах нормы'}",
            "complaint_type": complaint_type,
            "defendant_analysis": {
                "rating": defendant_rating,
                "points": defendant_points,
                "previous_complaints": defendant_complaints,
                "trust_level": "low" if defendant_rating < 3.0 or defendant_complaints > 2 else "medium"
            },
            "auto_action": "block" if risk_score >= 70 else "penalty" if risk_score >= 50 else "review"
        }
        
        return conclusion
    
    def analyze_forum_post(self, post_data: dict) -> dict:
        """
        🤖 Анализ поста на форуме AI-Юристом.
        
        Проверяет:
        - Обоснованность обвинений
        - Наличие оскорблений
        - Достоверность информации
        - Риск клеветы
        """
        
        title = post_data.get("title", "")
        content = post_data.get("content", "")
        post_type = post_data.get("post_type", "warning")
        
        risk_score = 0
        issues = []
        recommendations = []
        
        text = (title + " " + content).lower()
        
        # Проверка на оскорбления
        offensive_words = ["дурак", "идиот", "мошенник", "вор", "обманщик"]
        offensive_count = sum(1 for word in offensive_words if word in text)
        
        if offensive_count > 2:
            risk_score += 30
            issues.append("⚠️ Обнаружены оскорбления в тексте")
            recommendations.append("Рекомендуется модерация текста")
        
        # Проверка на клевету (слишком общие обвинения без фактов)
        vague_phrases = ["всегда так", "всегда обманывает", "никогда не платит"]
        if any(phrase in text for phrase in vague_phrases):
            risk_score += 20
            issues.append("⚠️ Общие обвинения без конкретных фактов")
            recommendations.append("Рекомендуется запросить конкретные доказательства")
        
        # Проверка наличия доказательств
        has_evidence = any(word in text for word in ["фото", "скриншот", "документ", "чек", "квитанция"])
        if not has_evidence and post_type == "warning":
            risk_score += 15
            issues.append("⚠️ Нет упоминания доказательств")
            recommendations.append("Рекомендуется запросить доказательства")
        
        # Проверка на спам/повторы
        if len(content) < 50:
            risk_score += 10
            issues.append("⚠️ Слишком короткий пост")
        
        # Определение уровня риска
        if risk_score >= 50:
            risk_level = RiskLevel.HIGH
            recommendations.append("🚨 Высокий риск: Требуется модерация перед публикацией")
        elif risk_score >= 30:
            risk_level = RiskLevel.MEDIUM
            recommendations.append("⚠️ Средний риск: Рекомендуется проверка модератором")
        else:
            risk_level = RiskLevel.LOW
            recommendations.append("✓ Низкий риск: Пост может быть опубликован")
        
        return {
            "risk_level": risk_level.value,
            "risk_score": risk_score,
            "issues": issues,
            "recommendations": recommendations,
            "summary": f"AI-Юрист: Риск-скор {risk_score}/100. {'Требуется модерация' if risk_score >= 30 else 'Можно публиковать'}",
            "needs_moderation": risk_score >= 30,
            "can_publish": risk_score < 30
        }


# Singleton instance
ai_lawyer_llm = AILawyerLLM()

