"""
GET /api/loads/{load_id}/matching-trucks
Топ-5 машин, подходящих под груз по маршруту + тоннажу + типу кузова.
"""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.models.models import Load, Vehicle, User

router = APIRouter()


def _city_match(city_a: str, city_b: str) -> bool:
    """Нечёткое сравнение городов (без учёта регистра, вхождение)."""
    a = (city_a or "").lower().strip()
    b = (city_b or "").lower().strip()
    return a and b and (a in b or b in a)


@router.get("/loads/{load_id}/matching-trucks")
def get_matching_trucks(load_id: int, db: Session = Depends(get_db)):
    """Подбор машин для груза — топ-5 по совместимости."""
    load = db.query(Load).filter(Load.id == load_id).first()
    if not load:
        raise HTTPException(404, "Груз не найден")

    today = date.today()

    # Базовый фильтр: активные машины, доступные сегодня
    q = db.query(Vehicle, User).join(
        User, User.id == Vehicle.carrier_id
    ).filter(
        Vehicle.status == "active",
        Vehicle.available_from <= today,
    )

    # Дата окончания: либо не указана, либо >= сегодня
    from sqlalchemy import or_
    q = q.filter(
        or_(Vehicle.available_to == None, Vehicle.available_to >= today)
    )

    # Тоннаж: машина должна вмещать груз
    weight = load.weight or load.weight_t
    if weight:
        q = q.filter(Vehicle.capacity_tons >= weight * 0.9)  # 10% допуск

    # Тип кузова
    if load.required_body_type:
        q = q.filter(
            func.lower(Vehicle.body_type) == func.lower(load.required_body_type)
        )

    vehicles = q.limit(50).all()

    # Скоринг по совместимости
    scored = []
    for v, carrier in vehicles:
        score = 0

        # Город отправления совпадает
        if _city_match(v.location_city, load.from_city):
            score += 50

        # Тоннаж точнее
        if weight and v.capacity_tons:
            ratio = weight / v.capacity_tons
            if 0.7 <= ratio <= 1.0:
                score += 30  # загрузка 70-100% — идеально
            elif ratio <= 0.7:
                score += 10

        # AI score перевозчика
        if carrier.trust_score:
            score += min(20, carrier.trust_score // 5)

        scored.append((score, v, carrier))

    # Сортировка: по скору desc, потом по trust_score
    scored.sort(key=lambda x: (-x[0], -(x[2].trust_score or 0)))

    result = []
    for score, v, carrier in scored[:5]:
        result.append({
            "vehicle_id":    v.id,
            "body_type":     v.body_type,
            "capacity_tons": v.capacity_tons,
            "volume_m3":     v.volume_m3,
            "location_city": v.location_city,
            "available_from": v.available_from.isoformat() if v.available_from else None,
            "brand":         v.brand,
            "plate_number":  v.plate_number,
            "match_score":   score,
            "carrier": {
                "id":          carrier.id,
                "name":        carrier.company or carrier.fullname or f"Перевозчик #{carrier.id}",
                "trust_score": carrier.trust_score,
                "phone":       carrier.phone,
                "gtp_code":    carrier.gtp_code,
            },
        })

    return {
        "load_id": load_id,
        "from_city": load.from_city,
        "to_city": load.to_city,
        "weight": weight,
        "trucks": result,
        "total_found": len(scored),
    }
