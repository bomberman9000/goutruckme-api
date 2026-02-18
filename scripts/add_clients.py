#!/usr/bin/env python3
"""
Скрипт для добавления тестовых клиентов
"""

import sys
import os
import re
import random

# Добавляем путь к проекту
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app.db.database import SessionLocal, init_db
from app.models.models import User, UserRole
from app.core.security import hash_password

# Данные клиентов (первые несколько для теста)
CLIENTS = [
    {
        'name': 'ООО "Алеан"',
        'inn': '8602191798',
        'org_type': 'ООО',
        'phone': '+7 902 859-02-89',
        'address': '628417 ХМАО - Югра, Тюменская обл., г. Сургут, ул. Студенчиская, д. 9/1, офис 1',
        'director': 'Турдалиев Кабаер Соаталиевич',
        'ogrn': '1128602008127'
    },
    {
        'name': 'ООО "Шафран"',
        'inn': '1001339863',
        'org_type': 'ООО',
        'phone': '+7 911 400-23-77',
        'address': '185011, г. Петрозаводск, ул.Балтийская (Кукковка Р-Н), д. 5, КВАРТИРА 9',
        'director': 'Гачкин Иван Сергеевич',
        'ogrn': '1191001002499'
    },
    {
        'name': 'ИП Ефремов Артур Артурович',
        'inn': '231905440364',
        'org_type': 'ИП',
        'phone': '+7 999 707-07-70',
        'address': '354003, Россия, Краснодарский край, г. Сочи, с. Верхний Юрт, пер. Табачный, 20',
        'director': 'Ефремов Артур Артурович',
        'email': '89990707070@mail.ru'
    },
    {
        'name': 'ИП Бозиев Аслан Канукович',
        'inn': '71500175679',
        'org_type': 'ИП',
        'phone': '+7 928 710-43-67',
        'address': 'республика Кабардино-Балкарская, город Нальчик',
        'director': 'Бозиев Аслан Канукович',
        'email': 'aslanboz@mail.ru',
        'ogrn': '304072107000308'
    },
    {
        'name': 'ООО "МЕДИА ХОЛДИНГ"',
        'inn': '2224179573',
        'org_type': 'ООО',
        'phone': '+7 901 642-09-75',
        'address': '656064, Алтайский край, город Барнаул, Павловский тракт, дом 74, пом. н5',
        'director': 'Пушкаренко Мария Сергеевна',
        'ogrn': '1162225066827'
    },
    {
        'name': 'ООО ПК «Атлант Строй»',
        'inn': '3525440409',
        'org_type': 'ООО',
        'phone': '+7 812 454-50-13',
        'address': '160011, Вологодская обл., г. Вологда, ул. Герцена, д.56, оф.3',
        'director': 'Чайков Дмитрий Сергеевич',
        'email': 'prima010@yandex.ru',
        'ogrn': '1193525011460'
    },
    {
        'name': 'ООО "Фермы Ясногорья"',
        'inn': '5036097531',
        'org_type': 'ООО',
        'phone': '+7 495 221-75-91',
        'address': '142103, РФ, Московская область, г. Подольск, Рощинский пр., 3А',
        'director': 'Дубина Николай Николаевич',
        'email': 'info@yasnogorfarms.ru',
        'ogrn': '1095074003177'
    },
    {
        'name': 'ООО «ЗАСК СПб»',
        'inn': '7805228265',
        'org_type': 'ООО',
        'phone': '+7 904 618-82-61',
        'address': '188301, Ленинградская область, Гатчинский район, Промзона Корпиково, д.2, офис 5-204',
        'director': 'Терещенко Константин Федорович',
        'email': 'mts@petrokon-spb.ru',
        'ogrn': '1027802769543'
    },
    {
        'name': 'ООО «ТИЛ-ГРУПП»',
        'inn': '7805658606',
        'org_type': 'ООО',
        'phone': '+7 812 454-54-69',
        'address': '198332, г. Санкт-Петербург, Брестский бульвар, д. 8, литера А, помещение 9-Н, Ч/О 512',
        'director': 'Пархоменко Владимир Владимирович',
        'email': 'info@til-group.ru',
        'ogrn': '1147847309345'
    },
    {
        'name': 'ООО "БЕТОНПРОФИ"',
        'inn': '4253043958',
        'org_type': 'ООО',
        'phone': '+7 903 908-31-67',
        'address': '654043, Россия, Кемеровская обл, г. Новокузнецк,шоссе Космическое, дом 11. помещение 2',
        'director': 'Лукин Сергей Викторович',
        'email': 'beton-nkz@mail.ru',
        'ogrn': '1184205024090'
    },
]

def add_clients():
    """Добавить клиентов в базу данных"""
    print("🚀 Инициализация базы данных...")
    init_db()
    
    db = SessionLocal()
    added = 0
    skipped = 0
    
    print(f"\n📋 Обработка {len(CLIENTS)} клиентов...\n")
    
    for client in CLIENTS:
        try:
            # Проверяем, существует ли уже пользователь с таким ИНН
            existing = db.query(User).filter(User.inn == client['inn']).first()
            if existing:
                print(f"⏭️  Пропущен {client['name']}: уже существует (ИНН {client['inn']})")
                skipped += 1
                continue
            
            # Проверяем телефон
            phone_exists = db.query(User).filter(User.phone == client['phone']).first()
            if phone_exists:
                # Генерируем уникальный телефон
                client['phone'] = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
            
            # Создаём пользователя
            new_user = User(
                organization_type=client['org_type'],
                inn=client['inn'],
                organization_name=client['name'],
                phone=client['phone'],
                password_hash=hash_password('test123'),
                role=UserRole.shipper,
                company=client['name'],
                fullname=client.get('director') or client['name'],
                verified=True,
                trust_level='trusted',
                payment_confirmed=True
            )
            
            db.add(new_user)
            db.commit()
            print(f"✅ {added+1}. {client['name']}")
            print(f"   ИНН: {client['inn']}, тел: {client['phone']}")
            if client.get('director'):
                print(f"   Директор: {client['director']}")
            added += 1
            
        except Exception as e:
            db.rollback()
            print(f"❌ Ошибка при добавлении {client['name']}: {e}")
            skipped += 1
    
    db.close()
    
    print(f"\n📊 Итого:")
    print(f"   ✅ Добавлено: {added}")
    print(f"   ⏭️  Пропущено: {skipped}")
    print(f"\n💡 Стандартный пароль для всех: test123")
    print(f"💡 Все пользователи автоматически верифицированы")

if __name__ == '__main__':
    try:
        add_clients()
        print("\n✅ Готово!")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()


