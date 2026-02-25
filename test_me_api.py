from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import CompanyTrustStats, DealSync, ModerationReview, User, UserRole


def _make_user(db, *, role: UserRole, suffix: str) -> User:
    user = User(
        phone=f"+7955{suffix}",
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=f"Profile {suffix}",
        company=f"Profile {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_get_me_returns_user_company_and_trust():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    user = _make_user(db, role=UserRole.forwarder, suffix=suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    try:
        response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["user"]["id"] == user.id
        assert payload["company"]["id"] == user.id
        assert "trust_score" in payload["trust"]
        assert "components" in payload["trust"]
    finally:
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_get_me_without_token_returns_401():
    init_db()
    client = TestClient(app)

    response = client.get("/api/me")
    assert response.status_code == 401


def test_get_me_supports_cookie_token():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    user = _make_user(db, role=UserRole.forwarder, suffix=suffix)
    token = create_token({"id": user.id, "phone": user.phone})

    try:
        response = client.get("/api/me", cookies={"authToken": token})
        assert response.status_code == 200
        assert response.json()["user"]["id"] == user.id
    finally:
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_patch_me_company_updates_profile_fields():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    user = _make_user(db, role=UserRole.carrier, suffix=suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    unique_inn = f"9{abs(hash(suffix)) % (10**9):09d}"
    unique_ogrn = f"1{abs(hash(f'{suffix}_ogrn')) % (10**12):012d}"

    try:
        patch_response = client.patch(
            "/api/me/company",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "ООО Тест Логистик",
                "inn": unique_inn,
                "ogrn": unique_ogrn,
                "city": "Москва",
                "phone": user.phone,
                "contact_person": "Иванов Иван",
                "website": "https://example.test",
                "edo_enabled": True,
            },
        )
        assert patch_response.status_code == 200

        payload = patch_response.json()
        assert payload["company"]["name"] == "ООО Тест Логистик"
        assert payload["company"]["inn"] == unique_inn
        assert payload["company"]["ogrn"] == unique_ogrn
        assert payload["company"]["city"] == "Москва"
        assert payload["company"]["contact_person"] == "Иванов Иван"
        assert payload["company"]["website"] == "https://example.test"
        assert payload["company"]["edo_enabled"] is True
        assert payload["trust_recalc"] in {"recalculated", "needs_recalc"}

        db.refresh(user)
        assert user.organization_name == "ООО Тест Логистик"
        assert user.inn == unique_inn
        assert user.ogrn == unique_ogrn
        assert user.city == "Москва"
        assert user.contact_person == "Иванов Иван"
        assert user.website == "https://example.test"
        assert user.edo_enabled is True
    finally:
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == user.id).delete()
        db.query(User).filter(User.id == user.id).delete()
        db.commit()
        db.close()


def test_public_company_profile_hides_private_fields_and_has_stats():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    company = _make_user(db, role=UserRole.carrier, suffix=suffix)
    company.email = "owner-private@example.test"
    company.city = "Самара"
    db.add(company)
    db.commit()

    deal_sync = DealSync(
        local_id=f"deal_profile_{uuid4().hex[:8]}",
        payload={"carrier_id": company.id, "status": "IN_PROGRESS"},
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
        comment="Проверка публичного профиля",
    )
    db.add(review)
    db.commit()

    try:
        response = client.get(f"/api/companies/{company.id}/profile")
        assert response.status_code == 200

        payload = response.json()
        assert payload["company"]["id"] == company.id
        assert payload["company"].get("email") is None
        assert "stats" in payload
        assert "risk_summary" in payload
        assert "recent_activity" in payload
        assert isinstance(payload["recent_activity"].get("timeline"), list)
    finally:
        db.query(CompanyTrustStats).filter(CompanyTrustStats.company_id == company.id).delete()
        db.query(ModerationReview).filter(ModerationReview.id == review.id).delete()
        db.query(DealSync).filter(DealSync.id == deal_sync.id).delete()
        db.query(User).filter(User.id == company.id).delete()
        db.commit()
        db.close()
