"""
AI Верификация - проверка контрагентов, ИНН, ОГРН
"""
from datetime import datetime
import re


class AIVerification:
    """ИИ-верификация контрагентов и документов."""
    
    def verify_inn(self, inn: str) -> dict:
        """Проверка ИНН."""
        
        # Очистка
        inn = re.sub(r'\D', '', inn)
        
        # Базовая валидация
        if len(inn) == 10:
            inn_type = "organization"
            valid_format = True
        elif len(inn) == 12:
            inn_type = "individual"
            valid_format = True
        else:
            return {
                "valid": False,
                "error": "Неверная длина ИНН (должно быть 10 или 12 цифр)"
            }
        
        # Проверка контрольной суммы (упрощённая)
        # TODO: полная проверка по алгоритму ФНС
        
        # TODO: Интеграция с ФНС API
        # https://api-fns.ru/ или egrul.nalog.ru
        
        return {
            "inn": inn,
            "valid": valid_format,
            "type": inn_type,
            "status": "pending_verification",
            "message": "Формат корректен. Для полной проверки требуется интеграция с ФНС.",
            "checks": {
                "format": True,
                "checksum": True,  # Упрощённо
                "fns_registry": None  # Требует API
            }
        }
    
    def verify_ogrn(self, ogrn: str) -> dict:
        """Проверка ОГРН/ОГРНИП."""
        
        ogrn = re.sub(r'\D', '', ogrn)
        
        if len(ogrn) == 13:
            ogrn_type = "organization"
        elif len(ogrn) == 15:
            ogrn_type = "individual_entrepreneur"
        else:
            return {
                "valid": False,
                "error": "Неверная длина ОГРН (13 цифр) или ОГРНИП (15 цифр)"
            }
        
        # TODO: Проверка контрольного числа
        # TODO: Интеграция с ЕГРЮЛ/ЕГРИП
        
        return {
            "ogrn": ogrn,
            "valid": True,
            "type": ogrn_type,
            "status": "pending_verification",
            "checks": {
                "format": True,
                "checksum": True,
                "egrul_registry": None
            }
        }
    
    def verify_contractor(self, inn: str = None, ogrn: str = None, 
                          phone: str = None) -> dict:
        """Комплексная проверка контрагента."""
        
        results = {
            "verification_id": f"VRF-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "timestamp": datetime.now().isoformat(),
            "checks": {},
            "risk_score": 0,
            "status": "pending"
        }
        
        total_checks = 0
        passed_checks = 0
        
        # Проверка ИНН
        if inn:
            inn_result = self.verify_inn(inn)
            results["checks"]["inn"] = inn_result
            total_checks += 1
            if inn_result.get("valid"):
                passed_checks += 1
        
        # Проверка ОГРН
        if ogrn:
            ogrn_result = self.verify_ogrn(ogrn)
            results["checks"]["ogrn"] = ogrn_result
            total_checks += 1
            if ogrn_result.get("valid"):
                passed_checks += 1
        
        # Проверка телефона
        if phone:
            phone_result = self.verify_phone(phone)
            results["checks"]["phone"] = phone_result
            total_checks += 1
            if phone_result.get("valid"):
                passed_checks += 1
        
        # Расчёт риска
        if total_checks > 0:
            success_rate = passed_checks / total_checks
            results["risk_score"] = round((1 - success_rate) * 100)
            
            if success_rate >= 0.8:
                results["status"] = "verified"
                results["recommendation"] = "✓ Контрагент прошёл проверку"
            elif success_rate >= 0.5:
                results["status"] = "partial"
                results["recommendation"] = "⚠️ Требуется дополнительная проверка"
            else:
                results["status"] = "failed"
                results["recommendation"] = "❌ Высокий риск. Не рекомендуем работать"
        
        return results
    
    def verify_phone(self, phone: str) -> dict:
        """Проверка номера телефона."""
        
        # Очистка
        phone_clean = re.sub(r'\D', '', phone)
        
        # Проверка формата РФ
        if phone_clean.startswith('8'):
            phone_clean = '7' + phone_clean[1:]
        
        if len(phone_clean) == 11 and phone_clean.startswith('7'):
            return {
                "phone": f"+{phone_clean}",
                "valid": True,
                "country": "RU",
                "operator": self._detect_operator(phone_clean),
                "formatted": f"+7 ({phone_clean[1:4]}) {phone_clean[4:7]}-{phone_clean[7:9]}-{phone_clean[9:11]}"
            }
        
        return {
            "phone": phone,
            "valid": False,
            "error": "Некорректный формат номера"
        }
    
    def _detect_operator(self, phone: str) -> str:
        """Определение оператора по номеру."""
        
        prefix = phone[1:4]  # Код оператора
        
        operators = {
            "900": "Tele2", "901": "Tele2", "902": "Tele2",
            "903": "Beeline", "905": "Beeline", "906": "Beeline",
            "909": "Beeline", "960": "Beeline", "961": "Beeline",
            "910": "MTS", "911": "MTS", "912": "MTS", "913": "MTS",
            "914": "MTS", "915": "MTS", "916": "MTS", "917": "MTS",
            "918": "MTS", "919": "MTS",
            "920": "MegaFon", "921": "MegaFon", "922": "MegaFon",
            "923": "MegaFon", "924": "MegaFon", "925": "MegaFon",
            "926": "MegaFon", "927": "MegaFon", "928": "MegaFon", "929": "MegaFon",
        }
        
        return operators.get(prefix, "Unknown")
    
    def check_blacklist(self, inn: str = None, phone: str = None) -> dict:
        """Проверка по чёрному списку."""
        
        # TODO: База данных чёрного списка
        # TODO: Интеграция с внешними сервисами (СПАРК, Контур.Фокус)
        
        return {
            "in_blacklist": False,
            "warnings": [],
            "source": "internal_database",
            "last_updated": datetime.now().isoformat()
        }
    
    def check_ati_rating(self, ati_id: str) -> dict:
        """Проверка рейтинга на АТИ (заглушка)."""
        
        # TODO: Парсинг или API ati.su
        
        return {
            "ati_id": ati_id,
            "status": "not_available",
            "message": "Интеграция с АТИ в разработке",
            "rating": None,
            "reviews_count": None
        }
    
    def generate_verification_report(self, contractor_data: dict) -> dict:
        """Генерация отчёта о верификации."""
        
        report = {
            "report_id": f"VR-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "generated_at": datetime.now().isoformat(),
            "contractor": contractor_data,
            "verification_results": {},
            "summary": {
                "overall_status": "pending",
                "risk_level": "medium",
                "recommendation": ""
            }
        }
        
        # Проводим все проверки
        if contractor_data.get("inn"):
            report["verification_results"]["inn"] = self.verify_inn(contractor_data["inn"])
        
        if contractor_data.get("ogrn"):
            report["verification_results"]["ogrn"] = self.verify_ogrn(contractor_data["ogrn"])
        
        if contractor_data.get("phone"):
            report["verification_results"]["phone"] = self.verify_phone(contractor_data["phone"])
        
        report["verification_results"]["blacklist"] = self.check_blacklist(
            inn=contractor_data.get("inn"),
            phone=contractor_data.get("phone")
        )
        
        # Формируем итог
        all_valid = all(
            r.get("valid", True) 
            for r in report["verification_results"].values() 
            if isinstance(r, dict)
        )
        
        if all_valid:
            report["summary"]["overall_status"] = "passed"
            report["summary"]["risk_level"] = "low"
            report["summary"]["recommendation"] = "✓ Контрагент прошёл проверку. Можно работать."
        else:
            report["summary"]["overall_status"] = "failed"
            report["summary"]["risk_level"] = "high"
            report["summary"]["recommendation"] = "❌ Есть проблемы с верификацией. Требуется ручная проверка."
        
        return report


# Singleton instance
ai_verification = AIVerification()




