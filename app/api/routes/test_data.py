"""
API для добавления тестовых данных
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db, init_db
from app.models.models import User, UserRole
from app.core.config import get_settings
from app.core.security import hash_password, get_current_user
import random

router = APIRouter()


def _ensure_test_data_access(current_user: User) -> None:
    settings = get_settings()
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Not found")
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Только для администраторов")

# Тестовые клиенты
TEST_CLIENTS = [
    {'name': 'ООО "Алеан"', 'inn': '8602191798', 'org_type': 'ООО', 'phone': '+7 902 859-02-89', 'address': '628417 ХМАО - Югра, Тюменская обл., г. Сургут, ул. Студенчиская, д. 9/1, офис 1', 'director': 'Турдалиев Кабаер Соаталиевич', 'ogrn': '1128602008127'},
    {'name': 'ООО "Шафран"', 'inn': '1001339863', 'org_type': 'ООО', 'phone': '+7 911 400-23-77', 'address': '185011, г. Петрозаводск, ул.Балтийская (Кукковка Р-Н), д. 5, КВАРТИРА 9', 'director': 'Гачкин Иван Сергеевич', 'ogrn': '1191001002499'},
    {'name': 'ИП Ефремов Артур Артурович', 'inn': '231905440364', 'org_type': 'ИП', 'phone': '+7 999 707-07-70', 'address': '354003, Россия, Краснодарский край, г. Сочи, с. Верхний Юрт, пер. Табачный, 20', 'director': 'Ефремов Артур Артурович', 'email': '89990707070@mail.ru'},
    {'name': 'ИП Бозиев Аслан Канукович', 'inn': '71500175679', 'org_type': 'ИП', 'phone': '+7 928 710-43-67', 'address': 'республика Кабардино-Балкарская, город Нальчик', 'director': 'Бозиев Аслан Канукович', 'email': 'aslanboz@mail.ru', 'ogrn': '304072107000308'},
    {'name': 'ООО "МЕДИА ХОЛДИНГ"', 'inn': '2224179573', 'org_type': 'ООО', 'phone': '+7 901 642-09-75', 'address': '656064, Алтайский край, город Барнаул, Павловский тракт, дом 74, пом. н5', 'director': 'Пушкаренко Мария Сергеевна', 'ogrn': '1162225066827'},
    {'name': 'ООО ПК «Атлант Строй»', 'inn': '3525440409', 'org_type': 'ООО', 'phone': '+7 812 454-50-13', 'address': '160011, Вологодская обл., г. Вологда, ул. Герцена, д.56, оф.3', 'director': 'Чайков Дмитрий Сергеевич', 'email': 'prima010@yandex.ru', 'ogrn': '1193525011460'},
    {'name': 'ООО "Фермы Ясногорья"', 'inn': '5036097531', 'org_type': 'ООО', 'phone': '+7 495 221-75-91', 'address': '142103, РФ, Московская область, г. Подольск, Рощинский пр., 3А', 'director': 'Дубина Николай Николаевич', 'email': 'info@yasnogorfarms.ru', 'ogrn': '1095074003177'},
    {'name': 'ООО «ЗАСК СПб»', 'inn': '7805228265', 'org_type': 'ООО', 'phone': '+7 904 618-82-61', 'address': '188301, Ленинградская область, Гатчинский район, Промзона Корпиково, д.2, офис 5-204', 'director': 'Терещенко Константин Федорович', 'email': 'mts@petrokon-spb.ru', 'ogrn': '1027802769543'},
    {'name': 'ООО «ТИЛ-ГРУПП»', 'inn': '7805658606', 'org_type': 'ООО', 'phone': '+7 812 454-54-69', 'address': '198332, г. Санкт-Петербург, Брестский бульвар, д. 8, литера А, помещение 9-Н, Ч/О 512', 'director': 'Пархоменко Владимир Владимирович', 'email': 'info@til-group.ru', 'ogrn': '1147847309345'},
    {'name': 'ООО "БЕТОНПРОФИ"', 'inn': '4253043958', 'org_type': 'ООО', 'phone': '+7 903 908-31-67', 'address': '654043, Россия, Кемеровская обл, г. Новокузнецк,шоссе Космическое, дом 11. помещение 2', 'director': 'Лукин Сергей Викторович', 'email': 'beton-nkz@mail.ru', 'ogrn': '1184205024090'},
]


@router.post("/add-clients")
def add_test_clients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Добавить тестовых клиентов в базу данных
    """
    _ensure_test_data_access(current_user)
    init_db()
    
    added = 0
    skipped = 0
    errors = []
    
    for client in TEST_CLIENTS:
        try:
            # Проверяем, существует ли уже пользователь с таким ИНН
            existing = db.query(User).filter(User.inn == client['inn']).first()
            if existing:
                skipped += 1
                continue
            
            # Проверяем телефон
            phone = client['phone']
            phone_exists = db.query(User).filter(User.phone == phone).first()
            if phone_exists:
                phone = f"+7 999 {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
            
            # Создаём пользователя
            new_user = User(
                organization_type=client['org_type'],
                inn=client['inn'],
                organization_name=client['name'],
                phone=phone,
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
            added += 1
            
        except Exception as e:
            db.rollback()
            errors.append(f"{client['name']}: {str(e)}")
            skipped += 1
    
    return {
        "status": "ok",
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "message": f"Добавлено {added} клиентов. Пароль для всех: test123"
    }


@router.get("/clients-count")
def get_clients_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Получить количество клиентов в базе"""
    _ensure_test_data_access(current_user)
    count = db.query(User).count()
    return {"count": count}
