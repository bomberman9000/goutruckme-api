#!/usr/bin/env python3
"""
Скрипт для добавления тестовых клиентов из таблицы
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal, init_db
from app.models.models import User, UserRole
from app.core.security import hash_password
from datetime import datetime
import re

# Данные клиентов (из таблицы пользователя)
CLIENTS_DATA = """
ис	Р/сч: KZ06998NTB0000347863, БИН 161040000777, БИК TSESKZKA, Кбе 17	Республика Казахстан, г. Атырау, ул. Сатыбалдиева, д.58				Андрей 	Андрея	Устава		8771 177 77 80		
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
    """Извлечь телефон из строки"""
    if not phone_str:
        return None
    # Удаляем все кроме цифр
    digits = re.sub(r'\D', '', phone_str)
    if len(digits) >= 10:
        # Форматируем как +7
        if digits.startswith('8'):
            digits = '7' + digits[1:]
        elif not digits.startswith('7'):
            digits = '7' + digits
        return '+' + digits[:1] + ' ' + digits[1:4] + ' ' + digits[4:7] + '-' + digits[7:9] + '-' + digits[9:11]
    return phone_str.strip()

def parse_inn(inn_str):
    """Извлечь ИНН из строки"""
    if not inn_str:
        return None
    # Удаляем пробелы
    inn = inn_str.replace(' ', '').strip()
    # Проверяем длину (10 для ИП, 12 для ООО)
    if len(inn) in [10, 12] and inn.isdigit():
        return inn
    return None

def determine_org_type(name):
    """Определить тип организации по названию"""
    name_lower = name.lower()
    if 'ип' in name_lower or 'индивидуальный предприниматель' in name_lower:
        return 'ИП'
    elif 'ооо' in name_lower or 'общество' in name_lower:
        return 'ООО'
    elif 'физ' in name_lower or 'физ лицо' in name_lower or 'частное лицо' in name_lower:
        return 'ФИЗ'
    else:
        return 'ООО'  # По умолчанию

def parse_client_data(line):
    """Парсинг строки с данными клиента"""
    parts = line.split('\t')
    if len(parts) < 10:
        return None
    
    name = parts[0].strip()
    if not name or name == 'ИНН':
        return None
    
    payment_details = parts[1].strip() if len(parts) > 1 else ''
    address = parts[2].strip() if len(parts) > 2 else ''
    inn = parse_inn(parts[3].strip() if len(parts) > 3 else '')
    kpp = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
    ogrn = parts[5].strip() if len(parts) > 5 and parts[5].strip() else None
    director = parts[6].strip() if len(parts) > 6 else None
    email = parts[8].strip() if len(parts) > 8 else None
    phone = parse_phone(parts[9].strip() if len(parts) > 9 else '')
    
    # Определяем тип организации
    org_type = determine_org_type(name)
    
    # Для ИП и физлиц ИНН обязателен
    if org_type in ['ИП', 'ФИЗ'] and not inn:
        # Пытаемся найти ИНН в других полях
        for part in parts:
            inn_candidate = parse_inn(part)
            if inn_candidate:
                inn = inn_candidate
                break
    
    # Если нет ИНН, пропускаем
    if not inn:
        print(f"⚠️  Пропущен {name}: нет ИНН")
        return None
    
    # Генерируем телефон, если нет
    if not phone:
        # Используем случайный телефон для теста
        import random
        phone = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
    
    return {
        'organization_type': org_type,
        'inn': inn,
        'organization_name': name,
        'phone': phone,
        'address': address,
        'kpp': kpp,
        'ogrn': ogrn,
        'director': director,
        'email': email,
        'payment_details': payment_details,
        'password': 'test123'  # Стандартный пароль для теста
    }

def add_clients_to_db():
    """Добавить клиентов в базу данных"""
    init_db()
    db = SessionLocal()
    
    added = 0
    skipped = 0
    
    lines = CLIENTS_DATA.strip().split('\n')
    for line in lines:
        if not line.strip():
            continue
        
        client_data = parse_client_data(line)
        if not client_data:
            skipped += 1
            continue
        
        # Проверяем, существует ли уже пользователь с таким ИНН
        existing = db.query(User).filter(User.inn == client_data['inn']).first()
        if existing:
            print(f"⏭️  Пропущен {client_data['organization_name']}: уже существует (ИНН {client_data['inn']})")
            skipped += 1
            continue
        
        # Проверяем телефон
        phone_exists = db.query(User).filter(User.phone == client_data['phone']).first()
        if phone_exists:
            # Генерируем уникальный телефон
            import random
            base_phone = client_data['phone']
            client_data['phone'] = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
        
        try:
            new_user = User(
                organization_type=client_data['organization_type'],
                inn=client_data['inn'],
                organization_name=client_data['organization_name'],
                phone=client_data['phone'],
                password_hash=hash_password(client_data['password']),
                role=UserRole.shipper,  # По умолчанию грузовладелец
                company=client_data['organization_name'],
                fullname=client_data.get('director') or client_data['organization_name'],
                # Дополнительные поля (если есть в модели)
                verified=True,  # Автоматически верифицируем для теста
                trust_level='trusted',
                payment_confirmed=True
            )
            
            db.add(new_user)
            db.commit()
            print(f"✅ Добавлен: {client_data['organization_name']} (ИНН: {client_data['inn']}, тел: {client_data['phone']})")
            added += 1
            
        except Exception as e:
            db.rollback()
            print(f"❌ Ошибка при добавлении {client_data['organization_name']}: {e}")
            skipped += 1
    
    db.close()
    
    print(f"\n📊 Итого:")
    print(f"   ✅ Добавлено: {added}")
    print(f"   ⏭️  Пропущено: {skipped}")
    print(f"\n💡 Стандартный пароль для всех: test123")

if __name__ == '__main__':
    print("🚀 Добавление тестовых клиентов...\n")
    add_clients_to_db()
    print("\n✅ Готово!")


