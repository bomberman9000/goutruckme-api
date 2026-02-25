from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import Load, User, UserRole


def _make_user(db, suffix: str) -> User:
    user = User(
        phone=f"+7941{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.forwarder,
        organization_name=f"Route Owner {suffix}",
        company=f"Route Owner {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _pick_city_id(client: TestClient, query: str, preferred_name: str) -> int:
    resp = client.get("/api/geo/cities", params={"q": query, "limit": 10})
    assert resp.status_code == 200
    rows = resp.json()
    assert rows
    for row in rows:
        if str(row.get("name") or "") == preferred_name:
            return int(row["id"])
    return int(rows[0]["id"])


def test_route_calc_returns_distance_for_selected_cities():
    init_db()
    client = TestClient(app)

    from_city_id = _pick_city_id(client, "сам", "Самара")
    to_city_id = _pick_city_id(client, "каз", "Казань")

    response = client.post(
        "/api/route/calc",
        json={
            "from_city_id": from_city_id,
            "to_city_id": to_city_id,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert int(payload["distance_km"]) > 0
    assert payload["source"] in {"cities", "cities_fallback", "geocode", "osrm"}


def test_route_calc_requires_city_ids():
    init_db()
    client = TestClient(app)

    response = client.post("/api/route/calc", json={})
    assert response.status_code == 422


def test_create_load_persists_distance_and_rate_values():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    from_city_id = _pick_city_id(client, "сам", "Самара")
    to_city_id = _pick_city_id(client, "каз", "Казань")

    try:
        response = client.post(
            "/loads/create",
            params={
                "from_city": "Самара",
                "to_city": "Казань",
                "from_city_id": from_city_id,
                "to_city_id": to_city_id,
                "price": 25000,
                "total_price": 25000,
                "distance_km": 812,
                "rate_per_km": 30.8,
                "truck_type": "Тент",
                "loading_date": "2026-02-27",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        load_id = int(response.json()["load_id"])

        load = db.query(Load).filter(Load.id == load_id).first()
        assert load is not None
        assert int(load.distance_km or 0) == 812
        assert float(load.total_price or 0) == 25000
        assert float(load.rate_per_km or 0) == 30.8
        assert float(load.price or 0) == 25000
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_create_load_converts_ampm_time_to_24h():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    from_city_id = _pick_city_id(client, "сам", "Самара")
    to_city_id = _pick_city_id(client, "каз", "Казань")

    try:
        response = client.post(
            "/loads/create",
            params={
                "from_city": "Самара",
                "to_city": "Казань",
                "from_city_id": from_city_id,
                "to_city_id": to_city_id,
                "price": 12000,
                "loading_date": "2026-02-27",
                "loading_time": "09:30 PM",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        load_id = int(response.json()["load_id"])

        load = db.query(Load).filter(Load.id == load_id).first()
        assert load is not None
        assert load.loading_time == "21:30"
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_create_load_accepts_dot_space_and_compact_time_formats():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    from_city_id = _pick_city_id(client, "сам", "Самара")
    to_city_id = _pick_city_id(client, "каз", "Казань")

    try:
        cases = {
            "10.00": "10:00",
            "10 00": "10:00",
            "1000": "10:00",
        }
        for raw_time, expected in cases.items():
            response = client.post(
                "/loads/create",
                params={
                    "from_city": "Самара",
                    "to_city": "Казань",
                    "from_city_id": from_city_id,
                    "to_city_id": to_city_id,
                    "price": 12000,
                    "loading_date": "2026-02-27",
                    "loading_time": raw_time,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            load_id = int(response.json()["load_id"])
            load = db.query(Load).filter(Load.id == load_id).first()
            assert load is not None
            assert load.loading_time == expected
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_create_load_rejects_invalid_time_format():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    from_city_id = _pick_city_id(client, "сам", "Самара")
    to_city_id = _pick_city_id(client, "каз", "Казань")

    try:
        response = client.post(
            "/loads/create",
            params={
                "from_city": "Самара",
                "to_city": "Казань",
                "from_city_id": from_city_id,
                "to_city_id": to_city_id,
                "price": 12000,
                "loading_date": "2026-02-27",
                "loading_time": "9:3",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422
        assert "Некорректное время" in str(response.json().get("detail") or "")
    finally:
        db.query(Load).filter(Load.user_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()
