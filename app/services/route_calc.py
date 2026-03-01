from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from sqlalchemy.orm import Session

from app.models.models import City
from app.services.geo import normalize_city_name


ROAD_FACTOR_DEFAULT = 1.2

# Fallback для MVP, пока в справочнике не у всех городов есть lat/lon.
CITY_COORDS_FALLBACK: dict[str, tuple[float, float]] = {
    "москва": (55.7558, 37.6176),
    "санкт петербург": (59.9386, 30.3141),
    "самара": (53.1959, 50.1000),
    "казань": (55.7963, 49.1088),
    "уфа": (54.7388, 55.9721),
    "пермь": (58.0105, 56.2502),
    "екатеринбург": (56.8389, 60.6057),
    "новосибирск": (55.0084, 82.9357),
    "краснодар": (45.0355, 38.9753),
    "ростов на дону": (47.2357, 39.7015),
    "челябинск": (55.1644, 61.4368),
    "омск": (54.9914, 73.3645),
    "воронеж": (51.6720, 39.1843),
    "нижний новгород": (56.3269, 44.0059),
    "тольятти": (53.5078, 49.4204),
    "саратов": (51.5336, 46.0343),
    "тверь": (56.8596, 35.9119),
    "рязань": (54.6292, 39.7364),
    "тула": (54.1921, 37.6175),
    "калуга": (54.5138, 36.2612),
    "владимир": (56.1291, 40.4066),
}


@dataclass
class CityPoint:
    city_id: int
    city_name: str
    lat: float
    lon: float
    source: str


def _haversine_km(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    radius_km = 6371.0
    d_lat = radians(to_lat - from_lat)
    d_lon = radians(to_lon - from_lon)
    a = sin(d_lat / 2) ** 2 + cos(radians(from_lat)) * cos(radians(to_lat)) * sin(d_lon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return radius_km * c


def resolve_city_point(db: Session, city_id: int) -> CityPoint | None:
    city = db.query(City).filter(City.id == int(city_id)).first()
    if not city:
        return None

    lat = float(city.lat) if city.lat is not None else None
    lon = float(city.lon) if city.lon is not None else None
    if lat is not None and lon is not None:
        return CityPoint(city_id=int(city.id), city_name=city.name, lat=lat, lon=lon, source="cities")

    fallback = CITY_COORDS_FALLBACK.get(normalize_city_name(city.name))
    if fallback is None:
        return None

    # Сохраняем fallback-координаты в справочник, чтобы следующие запросы были без fallback.
    try:
        city.lat = float(fallback[0])
        city.lon = float(fallback[1])
        db.add(city)
        db.commit()
    except Exception:
        db.rollback()

    return CityPoint(
        city_id=int(city.id),
        city_name=city.name,
        lat=float(fallback[0]),
        lon=float(fallback[1]),
        source="cities",
    )


def calculate_route_distance_km(
    *,
    from_point: CityPoint,
    to_point: CityPoint,
    road_factor: float = ROAD_FACTOR_DEFAULT,
) -> int:
    raw_distance = _haversine_km(from_point.lat, from_point.lon, to_point.lat, to_point.lon)
    if raw_distance <= 0:
        return 0
    adjusted = raw_distance * max(1.0, float(road_factor))
    return max(1, int(round(adjusted)))
