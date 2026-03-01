from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.models import City


_CITY_CLEAN_RE = re.compile(r"[^0-9a-zа-я\s\-]", flags=re.IGNORECASE)
_SPACE_RE = re.compile(r"[\s\-–—]+")
_CITY_PREFIX_RE = re.compile(r"^(?:г\.?|город)\s+", flags=re.IGNORECASE)
_COMPANY_PREFIX_RE = re.compile(r"^(?:ооо|ип|ук|llc)\b", flags=re.IGNORECASE)
_CITY_ALIASES = {
    "спб": "санкт петербург",
    "питер": "санкт петербург",
    "мск": "москва",
    "ташкен": "ташкент",
    "ташкенд": "ташкент",
    "тошкент": "ташкент",
    "город бишкек": "бишкек",
    "чимкент": "шымкент",
}
SUPPORTED_CITY_COUNTRIES = {"RU", "BY", "UZ", "KG", "KZ"}
INVALID_CITY_TOKENS = (
    "sanatorium",
    "beach",
    "hotel",
    "resort",
    "tuman",
    "district",
    "oblast",
    "область",
    "район",
    "полигон",
    "poligoni",
    "chiqindi",
    "belarussia",
    "belarus",
)


def normalize_city_name(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = _CITY_PREFIX_RE.sub("", text)
    text = _CITY_CLEAN_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    text = text.strip()
    return _CITY_ALIASES.get(text, text)


def is_city_like_name(value: str | None) -> bool:
    normalized = normalize_city_name(value)
    if not normalized:
        return False
    if _COMPANY_PREFIX_RE.match(normalized):
        return False
    if any(token in normalized for token in INVALID_CITY_TOKENS):
        return False
    return True


def is_supported_city(city: City | None) -> bool:
    if city is None:
        return False
    country = str(city.country or "").strip().upper()
    if country and country not in SUPPORTED_CITY_COUNTRIES:
        return False
    return is_city_like_name(city.name)


# MVP-справочник (расширяемый): крупнейшие города РФ для автокомплита.
DEFAULT_CITY_ROWS: tuple[dict, ...] = (
    {"name": "Москва", "region": "Москва", "country": "RU", "population": 13010000},
    {"name": "Санкт-Петербург", "region": "Санкт-Петербург", "country": "RU", "population": 5598000},
    {"name": "Новосибирск", "region": "Новосибирская область", "country": "RU", "population": 1634000},
    {"name": "Екатеринбург", "region": "Свердловская область", "country": "RU", "population": 1544000},
    {"name": "Казань", "region": "Республика Татарстан", "country": "RU", "population": 1314000},
    {"name": "Нижний Новгород", "region": "Нижегородская область", "country": "RU", "population": 1213000},
    {"name": "Челябинск", "region": "Челябинская область", "country": "RU", "population": 1189000},
    {"name": "Красноярск", "region": "Красноярский край", "country": "RU", "population": 1188000},
    {"name": "Самара", "region": "Самарская область", "country": "RU", "population": 1174000},
    {"name": "Уфа", "region": "Республика Башкортостан", "country": "RU", "population": 1149000},
    {"name": "Ростов-на-Дону", "region": "Ростовская область", "country": "RU", "population": 1140000},
    {"name": "Омск", "region": "Омская область", "country": "RU", "population": 1104000},
    {"name": "Краснодар", "region": "Краснодарский край", "country": "RU", "population": 1099000},
    {"name": "Воронеж", "region": "Воронежская область", "country": "RU", "population": 1058000},
    {"name": "Пермь", "region": "Пермский край", "country": "RU", "population": 1027000},
    {"name": "Волгоград", "region": "Волгоградская область", "country": "RU", "population": 1019000},
    {"name": "Саратов", "region": "Саратовская область", "country": "RU", "population": 901000},
    {"name": "Тюмень", "region": "Тюменская область", "country": "RU", "population": 861000},
    {"name": "Тольятти", "region": "Самарская область", "country": "RU", "population": 668000},
    {"name": "Ижевск", "region": "Удмуртская Республика", "country": "RU", "population": 620000},
    {"name": "Барнаул", "region": "Алтайский край", "country": "RU", "population": 617000},
    {"name": "Ульяновск", "region": "Ульяновская область", "country": "RU", "population": 613000},
    {"name": "Иркутск", "region": "Иркутская область", "country": "RU", "population": 611000},
    {"name": "Хабаровск", "region": "Хабаровский край", "country": "RU", "population": 617000},
    {"name": "Ярославль", "region": "Ярославская область", "country": "RU", "population": 599000},
    {"name": "Владивосток", "region": "Приморский край", "country": "RU", "population": 606000},
    {"name": "Махачкала", "region": "Республика Дагестан", "country": "RU", "population": 623000},
    {"name": "Томск", "region": "Томская область", "country": "RU", "population": 574000},
    {"name": "Оренбург", "region": "Оренбургская область", "country": "RU", "population": 539000},
    {"name": "Кемерово", "region": "Кемеровская область", "country": "RU", "population": 547000},
    {"name": "Новокузнецк", "region": "Кемеровская область", "country": "RU", "population": 538000},
    {"name": "Рязань", "region": "Рязанская область", "country": "RU", "population": 533000},
    {"name": "Набережные Челны", "region": "Республика Татарстан", "country": "RU", "population": 548000},
    {"name": "Астрахань", "region": "Астраханская область", "country": "RU", "population": 475000},
    {"name": "Пенза", "region": "Пензенская область", "country": "RU", "population": 507000},
    {"name": "Липецк", "region": "Липецкая область", "country": "RU", "population": 486000},
    {"name": "Киров", "region": "Кировская область", "country": "RU", "population": 475000},
    {"name": "Чебоксары", "region": "Чувашская Республика", "country": "RU", "population": 510000},
    {"name": "Тула", "region": "Тульская область", "country": "RU", "population": 468000},
    {"name": "Калининград", "region": "Калининградская область", "country": "RU", "population": 498000},
    {"name": "Балашиха", "region": "Московская область", "country": "RU", "population": 526000},
    {"name": "Курск", "region": "Курская область", "country": "RU", "population": 452000},
    {"name": "Севастополь", "region": "Севастополь", "country": "RU", "population": 561000},
    {"name": "Сочи", "region": "Краснодарский край", "country": "RU", "population": 445000},
    {"name": "Улан-Удэ", "region": "Республика Бурятия", "country": "RU", "population": 436000},
    {"name": "Ставрополь", "region": "Ставропольский край", "country": "RU", "population": 557000},
    {"name": "Тверь", "region": "Тверская область", "country": "RU", "population": 424000},
    {"name": "Магнитогорск", "region": "Челябинская область", "country": "RU", "population": 409000},
    {"name": "Иваново", "region": "Ивановская область", "country": "RU", "population": 396000},
    {"name": "Брянск", "region": "Брянская область", "country": "RU", "population": 370000},
    {"name": "Белгород", "region": "Белгородская область", "country": "RU", "population": 391000},
    {"name": "Сургут", "region": "Ханты-Мансийский АО", "country": "RU", "population": 420000},
    {"name": "Владимир", "region": "Владимирская область", "country": "RU", "population": 352000},
    {"name": "Чита", "region": "Забайкальский край", "country": "RU", "population": 353000},
    {"name": "Архангельск", "region": "Архангельская область", "country": "RU", "population": 301000},
    {"name": "Смоленск", "region": "Смоленская область", "country": "RU", "population": 321000},
    {"name": "Волжский", "region": "Волгоградская область", "country": "RU", "population": 321000},
    {"name": "Курган", "region": "Курганская область", "country": "RU", "population": 309000},
    {"name": "Орёл", "region": "Орловская область", "country": "RU", "population": 301000},
    {"name": "Саранск", "region": "Республика Мордовия", "country": "RU", "population": 318000},
    {"name": "Вологда", "region": "Вологодская область", "country": "RU", "population": 313000},
    {"name": "Череповец", "region": "Вологодская область", "country": "RU", "population": 305000},
    {"name": "Тамбов", "region": "Тамбовская область", "country": "RU", "population": 286000},
    {"name": "Мурманск", "region": "Мурманская область", "country": "RU", "population": 270000},
    {"name": "Якутск", "region": "Республика Саха (Якутия)", "country": "RU", "population": 376000},
    {"name": "Грозный", "region": "Чеченская Республика", "country": "RU", "population": 324000},
    {"name": "Калуга", "region": "Калужская область", "country": "RU", "population": 337000},
    {"name": "Петрозаводск", "region": "Республика Карелия", "country": "RU", "population": 279000},
    {"name": "Новороссийск", "region": "Краснодарский край", "country": "RU", "population": 338000},
    {"name": "Нижний Тагил", "region": "Свердловская область", "country": "RU", "population": 332000},
    {"name": "Кострома", "region": "Костромская область", "country": "RU", "population": 270000},
    {"name": "Йошкар-Ола", "region": "Республика Марий Эл", "country": "RU", "population": 280000},
    {"name": "Нальчик", "region": "Кабардино-Балкарская Республика", "country": "RU", "population": 240000},
    {"name": "Сыктывкар", "region": "Республика Коми", "country": "RU", "population": 245000},
    {"name": "Псков", "region": "Псковская область", "country": "RU", "population": 208000},
    {"name": "Абакан", "region": "Республика Хакасия", "country": "RU", "population": 186000},
    {"name": "Бийск", "region": "Алтайский край", "country": "RU", "population": 203000},
    {"name": "Прокопьевск", "region": "Кемеровская область", "country": "RU", "population": 188000},
    {"name": "Люберцы", "region": "Московская область", "country": "RU", "population": 230000},
    {"name": "Мытищи", "region": "Московская область", "country": "RU", "population": 286000},
    {"name": "Химки", "region": "Московская область", "country": "RU", "population": 257000},
    {"name": "Королёв", "region": "Московская область", "country": "RU", "population": 227000},
    {"name": "Подольск", "region": "Московская область", "country": "RU", "population": 313000},
    {"name": "Благовещенск", "region": "Амурская область", "country": "RU", "population": 241000},
    {"name": "Петропавловск-Камчатский", "region": "Камчатский край", "country": "RU", "population": 181000},
    {"name": "Южно-Сахалинск", "region": "Сахалинская область", "country": "RU", "population": 206000},
    {"name": "Норильск", "region": "Красноярский край", "country": "RU", "population": 174000},
    {"name": "Великий Новгород", "region": "Новгородская область", "country": "RU", "population": 223000},
    {"name": "Энгельс", "region": "Саратовская область", "country": "RU", "population": 226000},
    {"name": "Комсомольск-на-Амуре", "region": "Хабаровский край", "country": "RU", "population": 242000},
    {"name": "Армавир", "region": "Краснодарский край", "country": "RU", "population": 191000},
    {"name": "Копейск", "region": "Челябинская область", "country": "RU", "population": 146000},
    {"name": "Рыбинск", "region": "Ярославская область", "country": "RU", "population": 182000},
    {"name": "Таганрог", "region": "Ростовская область", "country": "RU", "population": 246000},
    {"name": "Каменск-Уральский", "region": "Свердловская область", "country": "RU", "population": 162000},
    {"name": "Альметьевск", "region": "Республика Татарстан", "country": "RU", "population": 160000},
    {"name": "Миасс", "region": "Челябинская область", "country": "RU", "population": 151000},
    {"name": "Стерлитамак", "region": "Республика Башкортостан", "country": "RU", "population": 279000},
    {"name": "Керчь", "region": "Республика Крым", "country": "RU", "population": 145000},
    {"name": "Трёхгорный", "region": "Челябинская область", "country": "RU", "population": 33000},
    {"name": "Минск", "region": "Минская область", "country": "BY", "population": 1997000, "lat": 53.9006, "lon": 27.5590},
    {"name": "Брест", "region": "Брестская область", "country": "BY", "population": 345000, "lat": 52.0976, "lon": 23.7341},
    {"name": "Гомель", "region": "Гомельская область", "country": "BY", "population": 503000, "lat": 52.4345, "lon": 30.9754},
    {"name": "Пинск", "region": "Брестская область", "country": "BY", "population": 126000, "lat": 52.1229, "lon": 26.0951},
    {"name": "Борисов", "region": "Минская область", "country": "BY", "population": 136000, "lat": 54.2279, "lon": 28.5050},
    {"name": "Ташкент", "region": "Ташкент", "country": "UZ", "population": 3024000, "lat": 41.2995, "lon": 69.2401},
    {"name": "Андижан", "region": "Андижанская область", "country": "UZ", "population": 450000, "lat": 40.7821, "lon": 72.3442},
    {"name": "Наманган", "region": "Наманганская область", "country": "UZ", "population": 626000, "lat": 40.9983, "lon": 71.6726},
    {"name": "Фергана", "region": "Ферганская область", "country": "UZ", "population": 299000, "lat": 40.3734, "lon": 71.7978},
    {"name": "Самарканд", "region": "Самаркандская область", "country": "UZ", "population": 573000, "lat": 39.6542, "lon": 66.9597},
    {"name": "Карши", "region": "Кашкадарьинская область", "country": "UZ", "population": 278000, "lat": 38.8606, "lon": 65.7891},
    {"name": "Бухара", "region": "Бухарская область", "country": "UZ", "population": 280000, "lat": 39.7747, "lon": 64.4286},
    {"name": "Навои", "region": "Навоийская область", "country": "UZ", "population": 145000, "lat": 40.1039, "lon": 65.3687},
    {"name": "Зарафшан", "region": "Навоийская область", "country": "UZ", "population": 85000, "lat": 41.5799, "lon": 64.2076},
    {"name": "Ургенч", "region": "Хорезмская область", "country": "UZ", "population": 145000, "lat": 41.5507, "lon": 60.6330},
    {"name": "Нукус", "region": "Каракалпакстан", "country": "UZ", "population": 329000, "lat": 42.4600, "lon": 59.6166},
    {"name": "Джизак", "region": "Джизакская область", "country": "UZ", "population": 186000, "lat": 40.1158, "lon": 67.8422},
    {"name": "Коканд", "region": "Ферганская область", "country": "UZ", "population": 259000, "lat": 40.5286, "lon": 70.9427},
    {"name": "Бишкек", "region": "Бишкек", "country": "KG", "population": 1074000, "lat": 42.8746, "lon": 74.5698},
    {"name": "Ош", "region": "Ошская область", "country": "KG", "population": 322000, "lat": 40.5283, "lon": 72.7985},
    {"name": "Алматы", "region": "Алматы", "country": "KZ", "population": 2275000, "lat": 43.2383, "lon": 76.9456},
    {"name": "Астана", "region": "Астана", "country": "KZ", "population": 1435000, "lat": 51.1694, "lon": 71.4491},
    {"name": "Шымкент", "region": "Шымкент", "country": "KZ", "population": 1220000, "lat": 42.3417, "lon": 69.5901},
)


def _build_city_objects(rows: Iterable[dict]) -> list[City]:
    result: list[City] = []
    for item in rows:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append(
            City(
                name=name,
                name_norm=normalize_city_name(name),
                region=(item.get("region") or None),
                country=(item.get("country") or "RU"),
                population=item.get("population"),
                lat=item.get("lat"),
                lon=item.get("lon"),
            )
        )
    return result


def seed_default_cities(db: Session) -> int:
    """Заполняет cities дефолтным каталогом, не дублируя записи."""
    existing = {row.name_norm: row for row in db.query(City).all() if row.name_norm}
    to_insert: list[City] = []
    changed = 0

    for city in _build_city_objects(DEFAULT_CITY_ROWS):
        if not city.name_norm:
            continue
        current = existing.get(city.name_norm)
        if current is None:
            to_insert.append(city)
            continue

        updated = False
        if city.region and not current.region:
            current.region = city.region
            updated = True
        if city.population and not current.population:
            current.population = city.population
            updated = True
        if city.lat is not None and current.lat is None:
            current.lat = city.lat
            updated = True
        if city.lon is not None and current.lon is None:
            current.lon = city.lon
            updated = True
        incoming_country = str(city.country or "").strip().upper()
        current_country = str(current.country or "").strip().upper()
        if incoming_country and (not current_country or current_country == "RU"):
            if current_country != incoming_country:
                current.country = incoming_country
                updated = True
        if updated:
            db.add(current)
            changed += 1

    if to_insert:
        db.bulk_save_objects(to_insert)
        changed += len(to_insert)

    if not changed:
        return 0
    db.commit()
    return changed
