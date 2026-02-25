from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import (
    CompanyTrustStats,
    Complaint,
    Deal,
    DealSync,
    Load,
    ModerationReview,
    Truck,
    User,
    UserRole,
)


def _make_user(db, *, role: UserRole, suffix: str, org_name: str | None = None) -> User:
    user = User(
        phone=f"+7987{suffix}",
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=org_name or f"Trust {suffix}",
        company=org_name or f"Trust {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_company_trust_defaults_for_new_company():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    company = _make_user(db, role=UserRole.carrier, suffix=suffix)

    try:
        response = client.get(f"/api/companies/{company.id}/trust")
        assert response.status_code == 200
        payload = response.json()

        assert payload["company_id"] == company.id
        assert payload["trust_score"] == 50
        assert payload["stars"] == 3
        assert "insufficient_data" in payload.get("flags", [])
    finally:
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == company.id).delete()
        db.query(User).filter(User.id == company.id).delete()
        db.commit()
        db.close()


def test_company_trust_counts_good_history():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:7]
    shipper = _make_user(db, role=UserRole.client, suffix=f"1{suffix}")
    carrier = _make_user(db, role=UserRole.carrier, suffix=f"2{suffix}")

    load = Load(user_id=shipper.id, from_city="Самара", to_city="Москва", price=120000, weight=10)
    db.add(load)
    db.commit()
    db.refresh(load)

    deal = Deal(
        cargo_id=load.id,
        shipper_id=shipper.id,
        carrier_id=carrier.id,
        status="CONTRACTED",
        created_at=datetime.utcnow(),
    )
    db.add(deal)
    db.commit()

    try:
        response = client.get(f"/api/companies/{carrier.id}/trust")
        assert response.status_code == 200
        payload = response.json()

        assert payload["signals"]["deals_total"] >= 1
        assert payload["signals"]["deals_success"] >= 1
        assert payload["trust_score"] >= 50
        assert "insufficient_data" not in payload.get("flags", [])
    finally:
        db.query(CompanyTrustStats).filter(
            CompanyTrustStats.company_id.in_([shipper.id, carrier.id])
        ).delete(synchronize_session=False)
        db.query(Deal).filter(Deal.id == deal.id).delete()
        db.query(Load).filter(Load.id == load.id).delete()
        db.query(User).filter(User.id.in_([shipper.id, carrier.id])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_company_trust_penalizes_disputes_and_high_risk_flags():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:7]
    complainant = _make_user(db, role=UserRole.forwarder, suffix=f"3{suffix}")
    defendant = _make_user(db, role=UserRole.carrier, suffix=f"4{suffix}")

    complaint = Complaint(
        complainant_id=complainant.id,
        defendant_id=defendant.id,
        title="Не выполнил условия",
        description="Срыв сроков",
        status="resolved",
    )
    db.add(complaint)
    db.commit()

    deal_sync = DealSync(
        local_id=f"deal_sync_{uuid4().hex[:8]}",
        payload={
            "shipper_id": complainant.id,
            "carrier_id": defendant.id,
            "status": "IN_PROGRESS",
        },
    )
    db.add(deal_sync)
    db.commit()
    db.refresh(deal_sync)

    review = ModerationReview(
        entity_type="deal",
        entity_id=deal_sync.id,
        status="done",
        risk_level="high",
        flags=["prepay_100"],
        comment="Подозрение на мошенничество",
    )
    db.add(review)
    db.commit()

    try:
        response = client.get(f"/api/companies/{defendant.id}/trust")
        assert response.status_code == 200
        payload = response.json()

        assert payload["signals"]["disputes_confirmed"] >= 1
        assert payload["signals"]["flags_high"] >= 1
        assert payload["trust_score"] < 50
        assert "disputes_confirmed" in payload.get("flags", [])
        assert "high_risk_flags" in payload.get("flags", [])
    finally:
        db.query(CompanyTrustStats).filter(
            CompanyTrustStats.company_id.in_([complainant.id, defendant.id])
        ).delete(synchronize_session=False)
        db.query(ModerationReview).filter(ModerationReview.id == review.id).delete()
        db.query(DealSync).filter(DealSync.id == deal_sync.id).delete()
        db.query(Complaint).filter(Complaint.id == complaint.id).delete()
        db.query(User).filter(User.id.in_([complainant.id, defendant.id])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_trust_recalc_endpoint_admin_only():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:7]
    admin = _make_user(db, role=UserRole.admin, suffix=f"5{suffix}")
    carrier = _make_user(db, role=UserRole.carrier, suffix=f"6{suffix}")

    admin_token = create_token({"id": admin.id, "phone": admin.phone})
    carrier_token = create_token({"id": carrier.id, "phone": carrier.phone})

    try:
        denied = client.post(
            f"/api/trust/recalc/{carrier.id}",
            headers={"Authorization": f"Bearer {carrier_token}"},
        )
        assert denied.status_code == 403

        allowed = client.post(
            f"/api/trust/recalc/{carrier.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert allowed.status_code == 200
        payload = allowed.json()
        assert payload["company_id"] == carrier.id
        assert 0 <= payload["trust_score"] <= 100
    finally:
        db.query(CompanyTrustStats).filter(
            CompanyTrustStats.company_id.in_([admin.id, carrier.id])
        ).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_([admin.id, carrier.id])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_logist_find_trucks_includes_trust_fields():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    carrier = _make_user(db, role=UserRole.carrier, suffix=suffix)
    truck = Truck(user_id=carrier.id, type="20т", capacity=20, region="самара", status="free")
    db.add(truck)
    db.commit()
    db.refresh(truck)

    try:
        response = client.post(
            "/logist/find-trucks",
            json={
                "from_city": "Самара",
                "to_city": "Москва",
                "weight": 10,
                "volume": 20,
                "price": 100000,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_found"] >= 1
        top = payload["top_3"][0]
        assert "carrier_trust_score" in top
        assert "client_trust_score" in top
        assert "trust_stars" in top
    finally:
        db.query(Truck).filter(Truck.id == truck.id).delete()
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == carrier.id).delete()
        db.query(User).filter(User.id == carrier.id).delete()
        db.commit()
        db.close()
