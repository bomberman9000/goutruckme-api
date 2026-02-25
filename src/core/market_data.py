"""Рыночные цены из @umnayalogistika (январь 2026)"""

MARKET_PRICES = [
    # Тент (20 тонн, без НДС)
    {"from": "Екатеринбург", "to": "Симферополь", "price": 269543, "type": "тент"},
    {"from": "Набережные Челны", "to": "Москва", "price": 83207, "type": "тент"},
    {"from": "Санкт-Петербург", "to": "Мурманск", "price": 138476, "type": "тент"},
    {"from": "Москва", "to": "Нижний Новгород", "price": 48981, "type": "тент"},
    {"from": "Екатеринбург", "to": "Новосибирск", "price": 181529, "type": "тент"},
    {"from": "Пермь", "to": "Екатеринбург", "price": 51531, "type": "тент"},
    {"from": "Челябинск", "to": "Москва", "price": 93876, "type": "тент"},
    {"from": "Ростов-на-Дону", "to": "Симферополь", "price": 112206, "type": "тент"},
    {"from": "Москва", "to": "Ростов-на-Дону", "price": 140615, "type": "тент"},
    {"from": "Новосибирск", "to": "Челябинск", "price": 89703, "type": "тент"},
    {"from": "Москва", "to": "Симферополь", "price": 236554, "type": "тент"},
    {"from": "Казань", "to": "Ростов-на-Дону", "price": 103804, "type": "тент"},
    {"from": "Казань", "to": "Новосибирск", "price": 173359, "type": "тент"},
    {"from": "Санкт-Петербург", "to": "Москва", "price": 50845, "type": "тент"},
    {"from": "Москва", "to": "Санкт-Петербург", "price": 50845, "type": "тент"},
    {"from": "Санкт-Петербург", "to": "Симферополь", "price": 363525, "type": "тент"},
    {"from": "Ставрополь", "to": "Москва", "price": 81782, "type": "тент"},

    # Реф
    {"from": "Москва", "to": "Красноярск", "price": 556591, "type": "реф"},
    {"from": "Нижний Новгород", "to": "Самара", "price": 81360, "type": "реф"},
]

async def seed_market_prices(session):
    """Заполнить базу рыночными ценами"""
    from datetime import datetime
    from sqlalchemy import select
    from src.core.models import MarketPrice

    for item in MARKET_PRICES:
        existing = await session.scalar(
            select(MarketPrice).where(
                MarketPrice.from_city == item["from"],
                MarketPrice.to_city == item["to"],
                MarketPrice.cargo_type == item["type"],
            )
        )
        if existing:
            existing.price = item["price"]
            existing.updated_at = datetime.utcnow()
        else:
            session.add(
                MarketPrice(
                    from_city=item["from"],
                    to_city=item["to"],
                    price=item["price"],
                    cargo_type=item["type"],
                    weight=20.0,
                    source="umnayalogistika",
                )
            )
    await session.commit()
