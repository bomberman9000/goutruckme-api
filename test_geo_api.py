from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import City, Load, User, UserRole
from app.services.geo import normalize_city_name


def _make_user(db, suffix: str) -> User:
    user = User(
        phone=f"+7912{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.forwarder,
        organization_name=f"Geo Owner {suffix}",
        company=f"Geo Owner {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_city_search_sa_returns_samara_in_top_results():
    init_db()
    client = TestClient(app)

    response = client.get("/api/geo/cities", params={"q": "са", "limit": 10})
    assert response.status_code == 200
    rows = response.json()
    assert isinstance(rows, list)
    assert rows, "ожидаем хотя бы один город в выдаче"

    top_names = [str(item.get("name") or "") for item in rows[:3]]
    assert "Самара" in top_names


def test_city_search_handles_yo_and_e_normalization():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    marker = uuid4().hex[:8]
    custom_name = f"Сём{marker}"
    custom_city = City(
        name=custom_name,
        name_norm=normalize_city_name(custom_name),
        region="Тестовый регион",
        country="RU",
    )
    db.add(custom_city)
    db.commit()
    db.refresh(custom_city)

    try:
        by_e = client.get("/api/geo/cities", params={"q": f"сем{marker}", "limit": 10})
        by_yo = client.get("/api/geo/cities", params={"q": f"сём{marker}", "limit": 10})

        assert by_e.status_code == 200
        assert by_yo.status_code == 200

        names_e = [str(item.get("name") or "") for item in by_e.json()]
        names_yo = [str(item.get("name") or "") for item in by_yo.json()]

        assert custom_name in names_e
        assert custom_name in names_yo
    finally:
        db.query(City).filter(City.id == custom_city.id).delete()
        db.commit()
        db.close()


def test_create_load_accepts_city_ids_and_stores_them():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    try:
        from_resp = client.get("/api/geo/cities", params={"q": "сам", "limit": 5})
        to_resp = client.get("/api/geo/cities", params={"q": "каз", "limit": 5})
        assert from_resp.status_code == 200
        assert to_resp.status_code == 200

        from_city = next((item for item in from_resp.json() if item.get("name") == "Самара"), from_resp.json()[0])
        to_city = next((item for item in to_resp.json() if item.get("name") == "Казань"), to_resp.json()[0])

        response = client.post(
            "/loads/create",
            params={
                "from_city": "Самара (ручной ввод)",
                "to_city": "Казань (ручной ввод)",
                "from_city_id": int(from_city["id"]),
                "to_city_id": int(to_city["id"]),
                "price": 25000,
                "weight": 2,
                "volume": 10,
                "truck_type": "Тент",
                "loading_date": "2026-02-25",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        payload = response.json()
        load_id = int(payload["load_id"])

        load = db.query(Load).filter(Load.id == load_id).first()
        assert load is not None
        assert load.from_city_id == int(from_city["id"])
        assert load.to_city_id == int(to_city["id"])
        assert load.from_city == "Самара"
        assert load.to_city == "Казань"
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()
