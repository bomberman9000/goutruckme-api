"""
📄 AI-ДОКУМЕНТЫ (Модуль 4)
Автоматическая генерация документов: УПД, ТТН, Договор, Счёт, Акт, Маршрутный лист

ГОСТ-ориентированное оформление с печатями и подписями
Такого НЕТ ни у кого!
"""
import os
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import json
import hashlib


class DocumentType(str, Enum):
    """Типы документов."""
    UPD = "upd"                    # Универсальный передаточный документ
    TTN = "ttn"                    # Товарно-транспортная накладная
    CONTRACT = "contract"          # Договор перевозки
    INVOICE = "invoice"            # Счёт
    ACT = "act"                    # Акт сдачи-приёмки
    WAYBILL = "waybill"            # Маршрутный/путевой лист
    CLAIM = "claim"                # Претензия
    POWER_OF_ATTORNEY = "poa"      # Доверенность


class DocumentStatus(str, Enum):
    """Статусы документа."""
    DRAFT = "draft"
    GENERATED = "generated"
    SIGNED = "signed"
    SENT = "sent"
    ARCHIVED = "archived"


@dataclass
class CompanyRequisites:
    """Реквизиты компании."""
    name: str
    inn: str
    kpp: str = ""
    ogrn: str = ""
    legal_address: str = ""
    actual_address: str = ""
    bank_name: str = ""
    bik: str = ""
    account: str = ""
    corr_account: str = ""
    director: str = ""
    phone: str = ""
    email: str = ""
    
    def to_dict(self):
        return {
            "name": self.name,
            "inn": self.inn,
            "kpp": self.kpp,
            "ogrn": self.ogrn,
            "legal_address": self.legal_address,
            "actual_address": self.actual_address,
            "bank_name": self.bank_name,
            "bik": self.bik,
            "account": self.account,
            "corr_account": self.corr_account,
            "director": self.director,
            "phone": self.phone,
            "email": self.email
        }


@dataclass
class CargoInfo:
    """Информация о грузе."""
    name: str
    weight: float
    volume: float = 0
    quantity: int = 1
    unit: str = "шт"
    price: float = 0
    packaging: str = ""
    
    def to_dict(self):
        return {
            "name": self.name,
            "weight": self.weight,
            "volume": self.volume,
            "quantity": self.quantity,
            "unit": self.unit,
            "price": self.price,
            "packaging": self.packaging
        }


class AIDocuments:
    """
    📄 AI-Документы — генератор всех документов для грузоперевозок.
    
    Возможности:
    - УПД (универсальный передаточный документ)
    - ТТН (товарно-транспортная накладная)
    - Договор перевозки
    - Счёт на оплату
    - Акт сдачи-приёмки
    - Маршрутный лист
    - Претензия
    - Доверенность
    
    Особенности:
    - ГОСТ-ориентированное оформление
    - Автоподстановка реквизитов
    - Печати и подписи
    - Генерация PDF
    - Хранение истории
    """
    
    # Хранилище документов (в реальности БД)
    documents_storage: Dict[str, Dict] = {}
    
    # Счётчик номеров документов
    doc_counters: Dict[str, int] = {
        "upd": 0,
        "ttn": 0,
        "contract": 0,
        "invoice": 0,
        "act": 0,
        "waybill": 0
    }
    
    def _generate_doc_number(self, doc_type: str) -> str:
        """Генерация номера документа."""
        self.doc_counters[doc_type] = self.doc_counters.get(doc_type, 0) + 1
        prefix = {
            "upd": "УПД",
            "ttn": "ТТН",
            "contract": "ДП",
            "invoice": "СЧ",
            "act": "АКТ",
            "waybill": "ПЛ"
        }.get(doc_type, "DOC")
        
        return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{self.doc_counters[doc_type]:04d}"
    
    def _calculate_nds(self, amount: float, rate: float = 20) -> Dict[str, float]:
        """Расчёт НДС."""
        nds_amount = amount * rate / (100 + rate)
        amount_without_nds = amount - nds_amount
        
        return {
            "amount_with_nds": round(amount, 2),
            "amount_without_nds": round(amount_without_nds, 2),
            "nds_amount": round(nds_amount, 2),
            "nds_rate": rate
        }
    
    def _number_to_words(self, number: float) -> str:
        """Сумма прописью (упрощённо)."""
        rubles = int(number)
        kopeks = int((number - rubles) * 100)
        
        # Упрощённая реализация
        units = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
        teens = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", 
                 "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
        tens = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", 
                "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
        hundreds = ["", "сто", "двести", "триста", "четыреста", "пятьсот",
                    "шестьсот", "семьсот", "восемьсот", "девятьсот"]
        
        if rubles < 10:
            words = units[rubles]
        elif rubles < 20:
            words = teens[rubles - 10]
        elif rubles < 100:
            words = tens[rubles // 10] + (" " + units[rubles % 10] if rubles % 10 else "")
        else:
            words = str(rubles)  # Для больших чисел просто цифрами
        
        ruble_word = "рублей"
        if rubles % 10 == 1 and rubles % 100 != 11:
            ruble_word = "рубль"
        elif rubles % 10 in [2, 3, 4] and rubles % 100 not in [12, 13, 14]:
            ruble_word = "рубля"
        
        return f"{words} {ruble_word} {kopeks:02d} копеек".strip()
    
    # ================== ГЕНЕРАЦИЯ ДОКУМЕНТОВ ==================
    
    def generate_upd(self, shipper: Dict, carrier: Dict, cargo: Dict, 
                     load: Dict, price: float) -> Dict[str, Any]:
        """
        📄 УПД — Универсальный передаточный документ
        
        Статус: 1 — счёт-фактура и передаточный документ
        """
        doc_number = self._generate_doc_number("upd")
        nds = self._calculate_nds(price)
        
        document = {
            "document_type": DocumentType.UPD.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            
            # Статус УПД
            "upd_status": "1",  # 1 = счёт-фактура + передаточный документ
            
            # Продавец (Грузоотправитель)
            "seller": {
                "name": shipper.get("company", shipper.get("fullname", "")),
                "inn": shipper.get("inn", ""),
                "kpp": shipper.get("kpp", ""),
                "address": shipper.get("address", ""),
            },
            
            # Покупатель (Грузополучатель)
            "buyer": {
                "name": carrier.get("company", carrier.get("fullname", "")),
                "inn": carrier.get("inn", ""),
                "kpp": carrier.get("kpp", ""),
                "address": carrier.get("address", ""),
            },
            
            # Грузоотправитель
            "consignor": shipper.get("company", shipper.get("fullname", "")),
            
            # Грузополучатель
            "consignee": load.get("to_city", ""),
            
            # Товары/услуги
            "items": [{
                "number": 1,
                "name": f"Услуги по перевозке груза: {cargo.get('name', 'Груз')}",
                "unit_code": "796",
                "unit_name": "шт",
                "quantity": 1,
                "price": nds["amount_without_nds"],
                "amount": nds["amount_without_nds"],
                "nds_rate": nds["nds_rate"],
                "nds_amount": nds["nds_amount"],
                "total": nds["amount_with_nds"]
            }],
            
            # Итого
            "totals": {
                "amount": nds["amount_without_nds"],
                "nds": nds["nds_amount"],
                "total": nds["amount_with_nds"],
                "total_words": self._number_to_words(nds["amount_with_nds"])
            },
            
            # Маршрут
            "route": {
                "from": load.get("from_city", ""),
                "to": load.get("to_city", ""),
            },
            
            # Основание
            "basis": f"Договор перевозки от {datetime.now().strftime('%d.%m.%Y')}",
            
            # Подписи
            "signatures": {
                "seller_director": shipper.get("director", shipper.get("fullname", "")),
                "seller_accountant": shipper.get("accountant", ""),
                "buyer_director": carrier.get("director", carrier.get("fullname", "")),
            },
            
            # Метаданные
            "created_at": datetime.now().isoformat(),
            "created_by": "AI-Documents ГрузПоток"
        }
        
        # Сохраняем
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "УПД",
            "document": document,
            "message": f"✅ УПД {doc_number} успешно сформирован"
        }
    
    def generate_ttn(self, shipper: Dict, carrier: Dict, cargo: Dict,
                     load: Dict, driver: Dict, vehicle: Dict) -> Dict[str, Any]:
        """
        📄 ТТН — Товарно-транспортная накладная
        
        Форма 1-Т
        """
        doc_number = self._generate_doc_number("ttn")
        
        document = {
            "document_type": DocumentType.TTN.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            "form": "1-Т",
            
            # Раздел 1: Товарный раздел
            "commodity_section": {
                # Грузоотправитель
                "consignor": {
                    "name": shipper.get("company", shipper.get("fullname", "")),
                    "address": shipper.get("address", ""),
                    "phone": shipper.get("phone", "")
                },
                
                # Грузополучатель
                "consignee": {
                    "name": load.get("consignee_name", "Грузополучатель"),
                    "address": load.get("to_city", ""),
                    "phone": load.get("consignee_phone", "")
                },
                
                # Плательщик
                "payer": {
                    "name": shipper.get("company", shipper.get("fullname", "")),
                    "address": shipper.get("address", "")
                },
                
                # Груз
                "cargo": {
                    "name": cargo.get("name", "Груз"),
                    "unit": cargo.get("unit", "шт"),
                    "quantity": cargo.get("quantity", 1),
                    "price": cargo.get("price", 0),
                    "amount": cargo.get("price", 0) * cargo.get("quantity", 1),
                    "weight_gross": cargo.get("weight", 0),
                    "weight_net": cargo.get("weight", 0),
                    "places": cargo.get("places", 1),
                    "packaging": cargo.get("packaging", "без упаковки")
                }
            },
            
            # Раздел 2: Транспортный раздел
            "transport_section": {
                # Перевозчик
                "carrier": {
                    "name": carrier.get("company", carrier.get("fullname", "")),
                    "address": carrier.get("address", ""),
                    "phone": carrier.get("phone", "")
                },
                
                # Водитель
                "driver": {
                    "name": driver.get("name", driver.get("fullname", "")),
                    "license": driver.get("license", ""),
                    "phone": driver.get("phone", "")
                },
                
                # Транспортное средство
                "vehicle": {
                    "model": vehicle.get("model", ""),
                    "plate": vehicle.get("plate", ""),
                    "trailer_plate": vehicle.get("trailer_plate", ""),
                    "type": vehicle.get("type", "")
                },
                
                # Маршрут
                "route": {
                    "loading_point": load.get("from_city", ""),
                    "loading_address": load.get("loading_address", ""),
                    "unloading_point": load.get("to_city", ""),
                    "unloading_address": load.get("unloading_address", "")
                },
                
                # Сроки
                "dates": {
                    "loading_date": load.get("loading_date", datetime.now().strftime("%d.%m.%Y")),
                    "delivery_date": load.get("delivery_date", "")
                }
            },
            
            # Стоимость перевозки
            "transportation_cost": load.get("price", 0),
            
            # Подписи
            "signatures": {
                "consignor_released": shipper.get("director", ""),
                "driver_received": driver.get("name", ""),
                "consignee_received": "",
                "carrier_delivered": ""
            },
            
            # Отметки
            "marks": {
                "loading_time": "",
                "unloading_time": "",
                "downtime": "",
                "notes": ""
            },
            
            "created_at": datetime.now().isoformat()
        }
        
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "ТТН",
            "document": document,
            "message": f"✅ ТТН {doc_number} успешно сформирована"
        }
    
    def generate_contract(self, shipper: Dict, carrier: Dict, load: Dict,
                          terms: Dict = None) -> Dict[str, Any]:
        """
        📄 Договор перевозки груза
        """
        doc_number = self._generate_doc_number("contract")
        
        if not terms:
            terms = {}
        
        price = load.get("price", 0)
        nds = self._calculate_nds(price)
        
        document = {
            "document_type": DocumentType.CONTRACT.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            
            # Стороны договора
            "parties": {
                "customer": {
                    "role": "Заказчик",
                    "name": shipper.get("company", shipper.get("fullname", "")),
                    "inn": shipper.get("inn", ""),
                    "ogrn": shipper.get("ogrn", ""),
                    "address": shipper.get("address", ""),
                    "director": shipper.get("director", shipper.get("fullname", "")),
                    "basis": "Устава"
                },
                "carrier": {
                    "role": "Перевозчик",
                    "name": carrier.get("company", carrier.get("fullname", "")),
                    "inn": carrier.get("inn", ""),
                    "ogrn": carrier.get("ogrn", ""),
                    "address": carrier.get("address", ""),
                    "director": carrier.get("director", carrier.get("fullname", "")),
                    "basis": "Устава"
                }
            },
            
            # Предмет договора
            "subject": {
                "description": "Перевозка груза автомобильным транспортом",
                "route_from": load.get("from_city", ""),
                "route_to": load.get("to_city", ""),
                "cargo_description": load.get("cargo_name", "Груз"),
                "weight": load.get("weight", 0),
                "volume": load.get("volume", 0)
            },
            
            # Стоимость и порядок расчётов
            "payment": {
                "amount": price,
                "amount_words": self._number_to_words(price),
                "nds": nds,
                "payment_terms": terms.get("payment_terms", "Оплата в течение 3 банковских дней после доставки"),
                "payment_method": terms.get("payment_method", "Безналичный расчёт")
            },
            
            # Сроки
            "dates": {
                "loading_date": load.get("loading_date", ""),
                "delivery_deadline": terms.get("delivery_deadline", ""),
                "contract_valid_until": terms.get("valid_until", (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y"))
            },
            
            # Обязанности сторон
            "obligations": {
                "customer": [
                    "Предоставить груз к перевозке в указанное время и место",
                    "Обеспечить надлежащую упаковку груза",
                    "Предоставить необходимые документы на груз",
                    "Произвести оплату в установленный срок"
                ],
                "carrier": [
                    "Подать транспортное средство в указанное время и место",
                    "Обеспечить сохранность груза при перевозке",
                    "Доставить груз в пункт назначения в установленный срок",
                    "Предоставить документы о выполнении перевозки"
                ]
            },
            
            # Ответственность сторон
            "liability": {
                "carrier_liability": "Перевозчик несёт ответственность за сохранность груза с момента принятия до момента выдачи",
                "customer_liability": "Заказчик несёт ответственность за достоверность сведений о грузе",
                "penalty": terms.get("penalty", "0.1% от суммы договора за каждый день просрочки"),
                "force_majeure": "Стороны освобождаются от ответственности при наступлении обстоятельств непреодолимой силы"
            },
            
            # Порядок разрешения споров
            "disputes": "Споры разрешаются путём переговоров, при недостижении согласия — в Арбитражном суде по месту нахождения истца",
            
            # Реквизиты сторон
            "requisites": {
                "customer": {
                    "name": shipper.get("company", shipper.get("fullname", "")),
                    "inn": shipper.get("inn", ""),
                    "kpp": shipper.get("kpp", ""),
                    "address": shipper.get("address", ""),
                    "bank": shipper.get("bank", ""),
                    "bik": shipper.get("bik", ""),
                    "account": shipper.get("account", ""),
                    "corr_account": shipper.get("corr_account", "")
                },
                "carrier": {
                    "name": carrier.get("company", carrier.get("fullname", "")),
                    "inn": carrier.get("inn", ""),
                    "kpp": carrier.get("kpp", ""),
                    "address": carrier.get("address", ""),
                    "bank": carrier.get("bank", ""),
                    "bik": carrier.get("bik", ""),
                    "account": carrier.get("account", ""),
                    "corr_account": carrier.get("corr_account", "")
                }
            },
            
            # Подписи
            "signatures": {
                "customer": {
                    "position": "Директор",
                    "name": shipper.get("director", shipper.get("fullname", "")),
                    "stamp": True
                },
                "carrier": {
                    "position": "Директор",
                    "name": carrier.get("director", carrier.get("fullname", "")),
                    "stamp": True
                }
            },
            
            "created_at": datetime.now().isoformat()
        }
        
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "Договор перевозки",
            "document": document,
            "message": f"✅ Договор {doc_number} успешно сформирован"
        }
    
    def generate_invoice(self, seller: Dict, buyer: Dict, 
                         items: List[Dict], load: Dict = None) -> Dict[str, Any]:
        """
        📄 Счёт на оплату
        """
        doc_number = self._generate_doc_number("invoice")
        
        # Расчёт итогов
        total_amount = sum(item.get("amount", 0) for item in items)
        nds = self._calculate_nds(total_amount)
        
        document = {
            "document_type": DocumentType.INVOICE.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            
            # Получатель платежа
            "seller": {
                "name": seller.get("company", seller.get("fullname", "")),
                "inn": seller.get("inn", ""),
                "kpp": seller.get("kpp", ""),
                "address": seller.get("address", ""),
                "bank": seller.get("bank", ""),
                "bik": seller.get("bik", ""),
                "account": seller.get("account", ""),
                "corr_account": seller.get("corr_account", "")
            },
            
            # Плательщик
            "buyer": {
                "name": buyer.get("company", buyer.get("fullname", "")),
                "inn": buyer.get("inn", ""),
                "kpp": buyer.get("kpp", ""),
                "address": buyer.get("address", "")
            },
            
            # Товары/услуги
            "items": [{
                "number": i + 1,
                "name": item.get("name", "Услуга"),
                "unit": item.get("unit", "шт"),
                "quantity": item.get("quantity", 1),
                "price": item.get("price", 0),
                "amount": item.get("amount", item.get("price", 0) * item.get("quantity", 1))
            } for i, item in enumerate(items)],
            
            # Итого
            "totals": {
                "amount_without_nds": nds["amount_without_nds"],
                "nds_rate": nds["nds_rate"],
                "nds_amount": nds["nds_amount"],
                "total": nds["amount_with_nds"],
                "total_words": self._number_to_words(nds["amount_with_nds"])
            },
            
            # Основание
            "basis": load.get("contract_number", f"Договор от {datetime.now().strftime('%d.%m.%Y')}") if load else "",
            
            # Срок оплаты
            "payment_due": (datetime.now() + timedelta(days=5)).strftime("%d.%m.%Y"),
            
            # Подписи
            "signatures": {
                "director": seller.get("director", seller.get("fullname", "")),
                "accountant": seller.get("accountant", "")
            },
            
            "created_at": datetime.now().isoformat()
        }
        
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "Счёт",
            "document": document,
            "message": f"✅ Счёт {doc_number} успешно сформирован"
        }
    
    def generate_act(self, customer: Dict, contractor: Dict, 
                     services: List[Dict], load: Dict = None) -> Dict[str, Any]:
        """
        📄 Акт сдачи-приёмки выполненных работ (услуг)
        """
        doc_number = self._generate_doc_number("act")
        
        total_amount = sum(s.get("amount", 0) for s in services)
        nds = self._calculate_nds(total_amount)
        
        document = {
            "document_type": DocumentType.ACT.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            
            "title": "АКТ сдачи-приёмки оказанных услуг",
            
            # Заказчик
            "customer": {
                "name": customer.get("company", customer.get("fullname", "")),
                "inn": customer.get("inn", ""),
                "address": customer.get("address", "")
            },
            
            # Исполнитель
            "contractor": {
                "name": contractor.get("company", contractor.get("fullname", "")),
                "inn": contractor.get("inn", ""),
                "address": contractor.get("address", "")
            },
            
            # Основание
            "basis": {
                "contract_number": load.get("contract_number", "") if load else "",
                "contract_date": load.get("contract_date", "") if load else ""
            },
            
            # Услуги
            "services": [{
                "number": i + 1,
                "name": s.get("name", "Услуга"),
                "unit": s.get("unit", "шт"),
                "quantity": s.get("quantity", 1),
                "price": s.get("price", 0),
                "amount": s.get("amount", s.get("price", 0) * s.get("quantity", 1))
            } for i, s in enumerate(services)],
            
            # Итого
            "totals": {
                "amount_without_nds": nds["amount_without_nds"],
                "nds_rate": nds["nds_rate"],
                "nds_amount": nds["nds_amount"],
                "total": nds["amount_with_nds"],
                "total_words": self._number_to_words(nds["amount_with_nds"])
            },
            
            # Заключение
            "conclusion": "Услуги выполнены полностью и в срок. Заказчик претензий по объёму, качеству и срокам оказания услуг не имеет.",
            
            # Подписи
            "signatures": {
                "customer": {
                    "position": "Директор",
                    "name": customer.get("director", customer.get("fullname", ""))
                },
                "contractor": {
                    "position": "Директор",
                    "name": contractor.get("director", contractor.get("fullname", ""))
                }
            },
            
            "created_at": datetime.now().isoformat()
        }
        
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "Акт",
            "document": document,
            "message": f"✅ Акт {doc_number} успешно сформирован"
        }
    
    def generate_waybill(self, carrier: Dict, driver: Dict, vehicle: Dict,
                         route: Dict, load: Dict = None) -> Dict[str, Any]:
        """
        📄 Путевой лист грузового автомобиля
        
        Форма 4-П
        """
        doc_number = self._generate_doc_number("waybill")
        
        document = {
            "document_type": DocumentType.WAYBILL.value,
            "document_number": doc_number,
            "document_date": datetime.now().strftime("%d.%m.%Y"),
            "status": DocumentStatus.GENERATED.value,
            "form": "4-П",
            
            # Организация
            "organization": {
                "name": carrier.get("company", carrier.get("fullname", "")),
                "address": carrier.get("address", ""),
                "phone": carrier.get("phone", "")
            },
            
            # Водитель
            "driver": {
                "name": driver.get("name", driver.get("fullname", "")),
                "license_number": driver.get("license", ""),
                "license_class": driver.get("license_class", "C"),
                "tab_number": driver.get("tab_number", "")
            },
            
            # Автомобиль
            "vehicle": {
                "model": vehicle.get("model", ""),
                "plate_number": vehicle.get("plate", ""),
                "garage_number": vehicle.get("garage_number", ""),
                "trailer": vehicle.get("trailer", "")
            },
            
            # Задание водителю
            "assignment": {
                "customer": load.get("customer_name", "") if load else "",
                "route": f"{route.get('from', '')} — {route.get('to', '')}",
                "cargo": load.get("cargo_name", "Груз") if load else "",
                "loading_address": route.get("loading_address", route.get("from", "")),
                "unloading_address": route.get("unloading_address", route.get("to", ""))
            },
            
            # Показания приборов
            "odometer": {
                "departure": "",
                "return": "",
                "total_km": ""
            },
            
            # Горючее
            "fuel": {
                "fuel_type": vehicle.get("fuel_type", "ДТ"),
                "balance_departure": "",
                "issued": "",
                "balance_return": "",
                "consumption_norm": vehicle.get("fuel_norm", ""),
                "consumption_actual": ""
            },
            
            # Время работы
            "time": {
                "departure_date": datetime.now().strftime("%d.%m.%Y"),
                "departure_time": "",
                "return_time": "",
                "total_hours": ""
            },
            
            # Результаты работы
            "results": {
                "trips": "",
                "tons_transported": load.get("weight", "") if load else "",
                "ton_km": ""
            },
            
            # Подписи
            "signatures": {
                "dispatcher": "",
                "mechanic": "",
                "driver": driver.get("name", ""),
                "medical_officer": ""
            },
            
            # Отметки
            "medical_check": {
                "pre_trip": {
                    "time": "",
                    "allowed": True,
                    "signature": ""
                },
                "post_trip": {
                    "time": "",
                    "signature": ""
                }
            },
            
            "technical_check": {
                "departure": {
                    "condition": "исправен",
                    "signature": ""
                },
                "return": {
                    "condition": "",
                    "signature": ""
                }
            },
            
            "created_at": datetime.now().isoformat()
        }
        
        self.documents_storage[doc_number] = document
        
        return {
            "success": True,
            "document_number": doc_number,
            "document_type": "Путевой лист",
            "document": document,
            "message": f"✅ Путевой лист {doc_number} успешно сформирован"
        }
    
    # ================== ГЕНЕРАЦИЯ ПОЛНОГО ПАКЕТА ==================
    
    def generate_full_package(self, shipper: Dict, carrier: Dict, driver: Dict,
                              vehicle: Dict, cargo: Dict, load: Dict) -> Dict[str, Any]:
        """
        📦 Генерация полного пакета документов для перевозки
        """
        
        documents = []
        
        # 1. Договор перевозки
        contract = self.generate_contract(shipper, carrier, load)
        documents.append(contract)
        
        # 2. Счёт
        invoice = self.generate_invoice(
            carrier, shipper,
            [{"name": f"Перевозка груза: {cargo.get('name', 'Груз')}", 
              "quantity": 1, "price": load.get("price", 0), "amount": load.get("price", 0)}],
            load
        )
        documents.append(invoice)
        
        # 3. ТТН
        ttn = self.generate_ttn(shipper, carrier, cargo, load, driver, vehicle)
        documents.append(ttn)
        
        # 4. Путевой лист
        waybill = self.generate_waybill(
            carrier, driver, vehicle,
            {"from": load.get("from_city", ""), "to": load.get("to_city", "")},
            load
        )
        documents.append(waybill)
        
        # 5. УПД
        upd = self.generate_upd(shipper, carrier, cargo, load, load.get("price", 0))
        documents.append(upd)
        
        # 6. Акт
        act = self.generate_act(
            shipper, carrier,
            [{"name": f"Услуги по перевозке груза {load.get('from_city', '')} — {load.get('to_city', '')}", 
              "quantity": 1, "price": load.get("price", 0), "amount": load.get("price", 0)}],
            load
        )
        documents.append(act)
        
        return {
            "success": True,
            "package_id": f"PKG-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "total_documents": len(documents),
            "documents": documents,
            "document_numbers": [d["document_number"] for d in documents],
            "message": f"✅ Полный пакет из {len(documents)} документов сформирован"
        }
    
    # ================== УПРАВЛЕНИЕ ДОКУМЕНТАМИ ==================
    
    def get_document(self, doc_number: str) -> Dict[str, Any]:
        """Получить документ по номеру."""
        doc = self.documents_storage.get(doc_number)
        if not doc:
            return {"error": "Документ не найден", "document_number": doc_number}
        return {"success": True, "document": doc}
    
    def list_documents(self, doc_type: str = None, limit: int = 50) -> Dict[str, Any]:
        """Список документов."""
        docs = list(self.documents_storage.values())
        
        if doc_type:
            docs = [d for d in docs if d.get("document_type") == doc_type]
        
        docs = sorted(docs, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]
        
        return {
            "total": len(docs),
            "documents": [
                {
                    "number": d.get("document_number"),
                    "type": d.get("document_type"),
                    "date": d.get("document_date"),
                    "status": d.get("status")
                } for d in docs
            ]
        }
    
    def get_document_html(self, doc_number: str) -> str:
        """Получить HTML-представление документа для PDF."""
        doc = self.documents_storage.get(doc_number)
        if not doc:
            return "<h1>Документ не найден</h1>"
        
        doc_type = doc.get("document_type")
        
        # Возвращаем базовый HTML (в реальности - полные шаблоны)
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{doc_type} {doc_number}</title>
    <style>
        body {{ font-family: 'Times New Roman', serif; font-size: 12pt; }}
        .header {{ text-align: center; margin-bottom: 20px; }}
        .title {{ font-size: 14pt; font-weight: bold; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td, th {{ border: 1px solid black; padding: 5px; }}
        .signature {{ margin-top: 50px; }}
        .stamp {{ color: blue; font-style: italic; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title">{doc_type.upper()} № {doc_number}</div>
        <div>от {doc.get('document_date')}</div>
    </div>
    <div>
        <!-- Содержимое документа -->
        <pre>{json.dumps(doc, ensure_ascii=False, indent=2)}</pre>
    </div>
    <div class="signature">
        <p>Подпись: _________________ / _________________ /</p>
        <p class="stamp">М.П.</p>
    </div>
</body>
</html>
"""


# Singleton instance
ai_documents = AIDocuments()



