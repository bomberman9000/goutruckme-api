"""
AI Юрист - генерация договоров и проверка документов
"""
from datetime import datetime
from app.models.models import Load, User, Bid


class AILawyer:
    """ИИ-юрист для работы с документами и договорами."""
    
    def generate_contract(self, load: Load, shipper: User, carrier: User, bid: Bid) -> dict:
        """Генерация договора на перевозку."""
        
        contract_number = f"GT-{datetime.now().strftime('%Y%m%d')}-{load.id}"
        
        contract = {
            "contract_number": contract_number,
            "date": datetime.now().isoformat(),
            "shipper": {
                "name": shipper.fullname,
                "company": shipper.company,
                "phone": shipper.phone
            },
            "carrier": {
                "name": carrier.fullname,
                "company": carrier.company,
                "phone": carrier.phone
            },
            "cargo": {
                "from_city": load.from_city,
                "to_city": load.to_city,
                "weight": load.weight,
                "volume": load.volume
            },
            "price": bid.price,
            "terms": self._generate_terms(load, bid),
            "status": "draft"
        }
        
        return contract
    
    def _generate_terms(self, load: Load, bid: Bid) -> list:
        """Генерация условий договора."""
        terms = [
            f"1. Перевозчик обязуется доставить груз из {load.from_city} в {load.to_city}.",
            f"2. Стоимость перевозки составляет {bid.price} рублей.",
            f"3. Вес груза: {load.weight or 'не указан'} тонн.",
            f"4. Объём груза: {load.volume or 'не указан'} м³.",
            "5. Оплата производится после доставки груза.",
            "6. Перевозчик несёт ответственность за сохранность груза.",
            "7. Споры решаются путём переговоров или в судебном порядке."
        ]
        return terms
    
    def generate_contract_text(self, load: Load, shipper: User, carrier: User, bid: Bid) -> str:
        """Генерация текста договора."""
        
        contract_number = f"GT-{datetime.now().strftime('%Y%m%d')}-{load.id}"
        date = datetime.now().strftime('%d.%m.%Y')
        
        text = f"""
ДОГОВОР НА ПЕРЕВОЗКУ ГРУЗА № {contract_number}

г. Москва                                                    {date}

ЗАКАЗЧИК: {shipper.fullname}
{f'Компания: {shipper.company}' if shipper.company else ''}
Телефон: {shipper.phone}

ПЕРЕВОЗЧИК: {carrier.fullname}
{f'Компания: {carrier.company}' if carrier.company else ''}
Телефон: {carrier.phone}

1. ПРЕДМЕТ ДОГОВОРА

1.1. Заказчик поручает, а Перевозчик принимает на себя обязательство по перевозке груза:
    - Маршрут: {load.from_city} → {load.to_city}
    - Вес: {load.weight or 'по факту'} т.
    - Объём: {load.volume or 'по факту'} м³

2. СТОИМОСТЬ УСЛУГ

2.1. Стоимость перевозки составляет: {bid.price} руб.
2.2. Оплата производится в течение 3 банковских дней после доставки.

3. ОБЯЗАННОСТИ СТОРОН

3.1. Перевозчик обязуется:
    - Подать транспортное средство в указанное место и время
    - Обеспечить сохранность груза
    - Доставить груз в пункт назначения

3.2. Заказчик обязуется:
    - Подготовить груз к перевозке
    - Предоставить необходимые документы
    - Произвести оплату в установленный срок

4. ОТВЕТСТВЕННОСТЬ СТОРОН

4.1. За неисполнение обязательств стороны несут ответственность согласно законодательству РФ.

5. ПОДПИСИ СТОРОН

Заказчик: _________________ / {shipper.fullname} /

Перевозчик: _________________ / {carrier.fullname} /
"""
        return text
    
    def check_documents(self, carrier: User) -> dict:
        """Проверка документов перевозчика."""
        # TODO: Интеграция с внешними сервисами проверки
        
        checks = {
            "phone_verified": carrier.phone.startswith('+7'),
            "profile_complete": bool(carrier.fullname and carrier.phone),
            "has_company": bool(carrier.company),
            "rating_ok": carrier.rating >= 4.0
        }
        
        passed = sum(checks.values())
        total = len(checks)
        
        return {
            "checks": checks,
            "passed": passed,
            "total": total,
            "verification_score": round(passed / total * 100),
            "status": "verified" if passed >= 3 else "pending" if passed >= 2 else "rejected"
        }
    
    def analyze_dispute(self, messages: list, load: Load) -> dict:
        """Анализ спора между сторонами."""
        
        # Базовый анализ переписки
        total_messages = len(messages)
        
        # TODO: NLP анализ тональности
        
        return {
            "messages_count": total_messages,
            "load_id": load.id,
            "recommendation": "mediation",  # mediation / shipper_favor / carrier_favor
            "suggested_actions": [
                "Связаться с обеими сторонами",
                "Запросить подтверждающие документы",
                "Предложить компромиссное решение"
            ]
        }


# Singleton instance
ai_lawyer = AILawyer()




