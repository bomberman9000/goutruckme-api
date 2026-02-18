from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import User, UserRole, Vehicle


def test_vehicle_create_list_and_archive():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    carrier = User(
        phone=f"+7998{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.carrier,
        organization_name="Vehicle Test Carrier",
        company="Vehicle Test Carrier",
        trust_level="new",
        verified=False,
        rating=4.3,
    )
    db.add(carrier)
    db.commit()
    db.refresh(carrier)

    token = create_token({"id": carrier.id, "phone": carrier.phone, "name": carrier.organization_name})

    try:
        payload = {
            "body_type": "тент",
            "capacity_tons": 20,
            "volume_m3": 92,
            "location_city": "Самара",
            "location_region": "РФ",
            "available_from": "2026-02-19",
            "rate_per_km": 95,
        }

        create_response = client.post(
            "/api/vehicles",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["body_type"] == "тент"
        assert created["carrier_id"] == carrier.id
        assert created["status"] == "active"
        assert created["ai"]["risk_level"] in {"low", "medium", "high"}

        vehicle_id = created["id"]

        list_response = client.get("/api/vehicles?city=Самара&body_type=тент&min_capacity_tons=10")
        assert list_response.status_code == 200
        vehicles = list_response.json()
        assert any(v["id"] == vehicle_id for v in vehicles)

        detail_response = client.get(f"/api/vehicles/{vehicle_id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["id"] == vehicle_id

        archive_response = client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "archived"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert archive_response.status_code == 200
        assert archive_response.json()["status"] == "archived"
    finally:
        db.query(Vehicle).filter(Vehicle.carrier_id == carrier.id).delete()
        db.delete(carrier)
        db.commit()
        db.close()
