from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import Attachment, Payment, Shipment, User, UserRole


def _make_user(db, suffix: str) -> User:
    user = User(
        phone=f"+7910{suffix}",
        password_hash=hash_password("pass123"),
        role=UserRole.forwarder,
        organization_name=f"Shipment Co {suffix}",
        company=f"Shipment Co {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_shipments_registry_crud_payments_and_ics():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    user = _make_user(db, suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    shipment_id = None
    payment_in_id = None
    payment_out_id = None

    try:
        create_resp = client.post(
            "/api/shipments",
            headers=headers,
            json={
                "ship_date": date.today().isoformat(),
                "client_name": "ООО Клиент",
                "client_inn": "1234567890",
                "from_city": "Москва",
                "to_city": "Казань",
                "cargo_brief": "Оборудование",
                "carrier_name": "ИП Перевозчик",
                "carrier_inn": "0987654321",
                "client_amount": 120000,
                "carrier_amount": 100000,
                "status": "in_progress",
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()
        shipment_id = created["id"]
        assert created["margin"] == 20000

        payment_in_resp = client.post(
            f"/api/shipments/{shipment_id}/payments",
            headers=headers,
            json={
                "direction": "in",
                "planned_date": (date.today() + timedelta(days=2)).isoformat(),
                "planned_amount": 120000,
                "comment": "Оплата от клиента",
            },
        )
        assert payment_in_resp.status_code == 200
        payment_in = payment_in_resp.json()
        payment_in_id = payment_in["id"]
        assert payment_in["status"] == "planned"

        payment_out_resp = client.post(
            f"/api/shipments/{shipment_id}/payments",
            headers=headers,
            json={
                "direction": "out",
                "planned_date": (date.today() - timedelta(days=1)).isoformat(),
                "planned_amount": 90000,
                "comment": "Оплата перевозчику",
            },
        )
        assert payment_out_resp.status_code == 200
        payment_out = payment_out_resp.json()
        payment_out_id = payment_out["id"]
        assert payment_out["status"] == "overdue"

        overdue_resp = client.get("/api/payments?status=overdue", headers=headers)
        assert overdue_resp.status_code == 200
        overdue_items = overdue_resp.json()
        assert any(item["id"] == payment_out_id for item in overdue_items)

        paid_resp = client.patch(
            f"/api/payments/{payment_out_id}",
            headers=headers,
            json={
                "status": "paid",
                "actual_date": date.today().isoformat(),
                "actual_amount": 90000,
            },
        )
        assert paid_resp.status_code == 200
        assert paid_resp.json()["status"] == "paid"

        list_resp = client.get("/api/shipments", headers=headers)
        assert list_resp.status_code == 200
        rows = list_resp.json()
        assert any(row["id"] == shipment_id for row in rows)
        row = next(row for row in rows if row["id"] == shipment_id)
        assert "payment_summary" in row
        assert "Кл:" in row["payment_summary"]["compact_status"]
        assert "Пер:" in row["payment_summary"]["compact_status"]

        detail_resp = client.get(f"/api/shipments/{shipment_id}", headers=headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert len(detail["payments"]) >= 2

        ics_resp = client.get(f"/api/payments/{payment_in_id}/reminder.ics", headers=headers)
        assert ics_resp.status_code == 200
        assert ics_resp.headers.get("content-type", "").startswith("text/calendar")
        text = ics_resp.text
        assert "BEGIN:VEVENT" in text
        assert f"перевозке {shipment_id}" in text.lower()
        assert f"/#/shipments/{shipment_id}" in text
    finally:
        if shipment_id:
            db.query(Attachment).filter(Attachment.shipment_id == shipment_id).delete()
            db.query(Payment).filter(Payment.shipment_id == shipment_id).delete()
            db.query(Shipment).filter(Shipment.id == shipment_id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_shipments_endpoints_require_auth():
    init_db()
    client = TestClient(app)

    list_resp = client.get("/api/shipments")
    assert list_resp.status_code == 401

    overdue_resp = client.get("/api/payments?status=overdue")
    assert overdue_resp.status_code == 401
