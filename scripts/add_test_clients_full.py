#!/usr/bin/env python3
"""
Скрипт для добавления тестовых клиентов из полной таблицы
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal, init_db
from app.models.models import User, UserRole
from app.core.security import hash_password
from datetime import datetime
import re
import random

# Полные данные клиентов (вставьте сюда всю таблицу)
CLIENTS_RAW = """ис	Р/сч: KZ06998NTB0000347863, БИН 161040000777, БИК TSESKZKA, Кбе 17	Республика Казахстан, г. Атырау, ул. Сатыбалдиева, д.58				Андрей 	Андрея	Устава		8771 177 77 80		
ООО "Алеан"	р/сч: 40702810967170003735, кор.счет: 30101810800000000651, БИК 047102651	628417 ХМАО - Югра, Тюменская обл., г. Сургут, ул. Студенчиская, д. 9/1, офис 1	8602191798	860201001	1128602008127	Турдалиев Кабаер Соаталиевич	Турдалиева Кабаера Соаталиевича	Устава		89028590289		
ООО "Шафран"	карта 	185011, г. Петрозаводск, ул.Балтийская (Кукковка Р-Н), д. 5, КВАРТИРА 9, 185011	1001339863	100101001	1191001002499	Гачкин Иван Сергеевич	Гачкин Иван Сергеевич	Устава		7 (911) 400-23-77		
ИП Ефремов Артур Артурович	"Р/с 40802810100780000134
Банк КБ «КУБАНЬ КРЕДИТ» ООО г.Краснодар
К/с 301 018 102 000 000 00 722
БИК 040349722"	Юридический адрес: 354003, Россия, Краснодарский край, г. Сочи, с. Верхний Юрт, пер. Табачный, 20	231905440364			Ефремов Артур Артурович	Ефремов Артур Артурович	Устава	89990707070@mail.ru 	7-999-70-70-70	353250, Краснодарский край, Северский район, ст-ца Новодмитриевская, ул. Горького, 1	
ИП Бозиев Аслан Канукович 	КАРТА 	республика Кабардино-Балкарская, город Нальчик	71500175679		304072107000308	Бозиев Аслан Канукович	Бозиев Аслан Канукович	Устава	"aslanboz@mail.ru"	89287104367		
ООО  "МЕДИА ХОЛДИНГ"	Р/сч: 40702810620450000349 в банке Операционный офис «Алтайский» ТКБ БАНК ПАО БИК: 044525388 Корр/сч: 30101810800000000388	656064, Алтайский край, город Барнаул, Павловский тракт, дом 74, пом. н5	2224179573	222201001	1162225066827	Пушкаренко Мария Сергеевна	Пушкаренко Мария Сергеевна	Устава		901 642-09-75	"656066, Алтайский край, г. Барнаул, а/я 1555"
ООО ПК «Атлант Строй»	"р/с 40702810902500034466 в Точка ПАО Банка «ФК
Открытие» г.Москва
к/с 30110845250001000
БиК 044525999"	"160011, Вологодская обл., г. Вологда,
ул. Герцена, д.56, оф.3"	3525440409	352501001	1193525011460	Чайков Дмитрий Сергеевич,	Чайков Дмитрий Сергеевич,	устава	prima010@yandex.ru	(812) 45-45-013	"160011, Вологодская обл., г. Вологда,
ул. Герцена, д.56, оф.3"
"""

def parse_phone(phone_str):
    """Извлечь и нормализовать телефон"""
    if not phone_str or phone_str.strip() == '':
        return None
    # Удаляем все кроме цифр
    digits = re.sub(r'\D', '', phone_str)
    if len(digits) >= 10:
        # Форматируем
        if digits.startswith('8'):
            digits = '7' + digits[1:]
        elif not digits.startswith('7'):
            digits = '7' + digits
        if len(digits) == 11:
            return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return phone_str.strip()[:20]  # Ограничиваем длину

def parse_inn(inn_str):
    """Извлечь ИНН"""
    if not inn_str:
        return None
    inn = re.sub(r'\D', '', inn_str.strip())
    if len(inn) in [10, 12] and inn.isdigit():
        return inn
    return None

def determine_org_type(name):
    """Определить тип организации"""
    if not name:
        return 'ООО'
    name_lower = name.lower()
    if 'ип' in name_lower or 'индивидуальный предприниматель' in name_lower:
        return 'ИП'
    elif 'физ' in name_lower or 'физ лицо' in name_lower or 'частное лицо' in name_lower:
        return 'ФИЗ'
    elif 'ооо' in name_lower or 'общество' in name_lower or 'пао' in name_lower or 'ао' in name_lower:
        return 'ООО'
    else:
        return 'ООО'

def extract_bank_account(payment_str):
    """Извлечь расчетный счет из строки реквизитов"""
    if not payment_str:
        return None
    # Ищем паттерн р/с, р/сч, расчетный счет
    patterns = [
        r'р/с[ч]?[:\s]*(\d{20})',
        r'расчетный\s+счет[:\s]*(\d{20})',
        r'р\.с[ч]?[:\s]*(\d{20})',
        r'407\d{17}',
    ]
    for pattern in patterns:
        match = re.search(pattern, payment_str, re.IGNORECASE)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None

def parse_client_data(line):
    """Парсинг строки с данными клиента"""
    if not line or not line.strip():
        return None
    
    # Разделяем по табуляции
    parts = [p.strip() for p in line.split('\t')]
    
    if len(parts) < 3:
        return None
    
    name = parts[0].strip()
    if not name or name.lower() in ['инн', 'наименование', '']:
        return None
    
    payment_details = parts[1] if len(parts) > 1 else ''
    address = parts[2] if len(parts) > 2 else ''
    inn_str = parts[3] if len(parts) > 3 else ''
    kpp = parts[4] if len(parts) > 4 else None
    ogrn = parts[5] if len(parts) > 5 else None
    director = parts[6] if len(parts) > 6 else None
    email = parts[8] if len(parts) > 8 else None
    phone_str = parts[9] if len(parts) > 9 else ''
    
    # Парсим ИНН
    inn = parse_inn(inn_str)
    
    # Если ИНН не найден, ищем в других полях
    if not inn:
        for part in parts[3:]:
            inn = parse_inn(part)
            if inn:
                break
    
    # Если всё ещё нет ИНН, пропускаем
    if not inn:
        return None
    
    # Определяем тип организации
    org_type = determine_org_type(name)
    
    # Парсим телефон
    phone = parse_phone(phone_str)
    if not phone:
        # Генерируем случайный для теста
        phone = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
    
    # Извлекаем банковский счет
    bank_account = extract_bank_account(payment_details)
    
    return {
        'organization_type': org_type,
        'inn': inn,
        'organization_name': name[:200],  # Ограничиваем длину
        'phone': phone,
        'address': address[:500] if address else None,
        'kpp': kpp[:20] if kpp and kpp.strip() else None,
        'ogrn': ogrn[:20] if ogrn and ogrn.strip() else None,
        'director': director[:200] if director and director.strip() else None,
        'email': email[:100] if email and email.strip() else None,
        'bank_account': bank_account,
        'payment_details': payment_details[:500] if payment_details else None,
        'password': 'test123'
    }

def add_clients_to_db():
    """Добавить клиентов в базу данных"""
    init_db()
    db = SessionLocal()
    
    added = 0
    skipped = 0
    errors = 0
    
    lines = CLIENTS_RAW.strip().split('\n')
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        
        try:
            client_data = parse_client_data(line)
            if not client_data:
                skipped += 1
                continue
            
            # Проверяем, существует ли уже пользователь с таким ИНН
            existing = db.query(User).filter(User.inn == client_data['inn']).first()
            if existing:
                skipped += 1
                continue
            
            # Проверяем телефон
            phone_exists = db.query(User).filter(User.phone == client_data['phone']).first()
            if phone_exists:
                # Генерируем уникальный телефон
                client_data['phone'] = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
            
            # Создаём пользователя
            new_user = User(
                organization_type=client_data['organization_type'],
                inn=client_data['inn'],
                organization_name=client_data['organization_name'],
                phone=client_data['phone'],
                password_hash=hash_password(client_data['password']),
                role=UserRole.shipper,
                company=client_data['organization_name'],
                fullname=client_data.get('director') or client_data['organization_name'],
                bank_account=client_data.get('bank_account'),
                verified=True,
                trust_level='trusted',
                payment_confirmed=True
            )
            
            db.add(new_user)
            db.commit()
            print(f"✅ {added+1}. {client_data['organization_name']} (ИНН: {client_data['inn']}, тел: {client_data['phone']})")
            added += 1
            
        except Exception as e:
            db.rollback()
            print(f"❌ Ошибка в строке {i}: {e}")
            errors += 1
            skipped += 1
    
    db.close()
    
    print(f"\n📊 Итого:")
    print(f"   ✅ Добавлено: {added}")
    print(f"   ⏭️  Пропущено: {skipped}")
    print(f"   ❌ Ошибок: {errors}")
    print(f"\n💡 Стандартный пароль для всех: test123")
    print(f"💡 Все пользователи автоматически верифицированы и подтверждены")

if __name__ == '__main__':
    print("🚀 Добавление тестовых клиентов...\n")
    add_clients_to_db()
    print("\n✅ Готово!")


