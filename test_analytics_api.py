from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import Deal, Load, User, UserRole


def _make_user(db, *, role: UserRole, suffix: str) -> User:
    user = User(
        phone=f"+7997{suffix}",
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=f"Analytics {suffix}",
        company=f"Analytics {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_analytics_endpoints_empty_scope_are_safe():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    user = _make_user(db, role=UserRole.forwarder, suffix=suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    try:
        overview = client.get("/api/analytics/overview?range=week", headers=headers)
        assert overview.status_code == 200
        overview_data = overview.json()
        assert overview_data["range"] == "week"
        assert overview_data["kpis"]["cargos_new"] == 0
        assert overview_data["kpis"]["responses_new"] == 0

        money = client.get("/api/analytics/money?range=week", headers=headers)
        assert money.status_code == 200
        money_data = money.json()
        assert money_data["rate_per_km_avg"] is None
        assert money_data["top_clients"] == []

        risk = client.get("/api/analytics/risk?range=week", headers=headers)
        assert risk.status_code == 200
        risk_data = risk.json()
        assert risk_data["counts"] == {"low": 0, "medium": 0, "high": 0}
        assert risk_data["items"] == []

        quality = client.get("/api/analytics/data_quality?range=week", headers=headers)
        assert quality.status_code == 200
        quality_data = quality.json()
        assert quality_data["duplicate_companies"] == 0
        assert quality_data["duplicate_documents"] == 0
    finally:
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_analytics_overview_respects_carrier_scope():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    shipper = _make_user(db, role=UserRole.client, suffix=f"1{suffix}")
    carrier = _make_user(db, role=UserRole.carrier, suffix=f"2{suffix}")
    other_shipper = _make_user(db, role=UserRole.client, suffix=f"3{suffix}")
    other_carrier = _make_user(db, role=UserRole.carrier, suffix=f"4{suffix}")

    load_1 = Load(user_id=shipper.id, from_city="Самара", to_city="Москва", price=10000)
    load_2 = Load(user_id=other_shipper.id, from_city="Уфа", to_city="Казань", price=12000)
    db.add_all([load_1, load_2])
    db.commit()
    db.refresh(load_1)
    db.refresh(load_2)

    deal_1 = Deal(
        cargo_id=load_1.id,
        shipper_id=shipper.id,
        carrier_id=carrier.id,
        status="IN_PROGRESS",
        carrier_message="Готов взять",
    )
    deal_2 = Deal(
        cargo_id=load_2.id,
        shipper_id=other_shipper.id,
        carrier_id=other_carrier.id,
        status="IN_PROGRESS",
        carrier_message="Другая сделка",
    )
    db.add_all([deal_1, deal_2])
    db.commit()

    token = create_token({"id": carrier.id, "phone": carrier.phone})
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = client.get("/api/analytics/overview?range=week", headers=headers)
        assert response.status_code == 200
        data = response.json()

        assert data["kpis"]["deals_created"] == 1
        assert data["kpis"]["responses_new"] == 1
        assert data["kpis"]["cargos_new"] == 1
    finally:
        db.query(Deal).filter(Deal.id.in_([deal_1.id, deal_2.id])).delete(synchronize_session=False)
        db.query(Load).filter(Load.id.in_([load_1.id, load_2.id])).delete(synchronize_session=False)
        db.query(User).filter(
            User.id.in_([shipper.id, carrier.id, other_shipper.id, other_carrier.id])
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
