from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import DealSync, ModerationReview, User, UserRole


def _make_user(db, *, role: UserRole, suffix: str) -> User:
    user = User(
        phone=f"+7965{suffix}",
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=f"Moderation {suffix}",
        company=f"Moderation {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_moderation_reviews_empty_and_review_lifecycle():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = uuid4().hex[:8]
    user = _make_user(db, role=UserRole.forwarder, suffix=suffix)
    token = create_token({"id": user.id, "phone": user.phone})
    headers = {"Authorization": f"Bearer {token}"}

    deal_sync = DealSync(
        local_id=f"deal_mod_{uuid4().hex[:8]}",
        payload={
            "from_city": "Самара",
            "to_city": "Москва",
            "carrier": {"name": "Test Carrier", "phone": "+79990000000"},
            "cargoSnapshot": {"price": 100000, "distance": 1000},
        },
    )
    db.add(deal_sync)
    db.commit()
    db.refresh(deal_sync)

    try:
        empty = client.get("/api/moderation/reviews?status=pending&limit=50", headers=headers)
        assert empty.status_code == 200
        assert isinstance(empty.json(), list)

        first = client.post(
            "/api/moderation/review",
            json={"entity_type": "deal", "entity_id": deal_sync.id, "force": False},
            headers=headers,
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["entity_type"] == "deal"
        assert first_payload["entity_id"] == deal_sync.id
        assert first_payload["status"] == "done"
        assert first_payload["risk_level"] in {"low", "medium", "high"}

        second = client.post(
            "/api/moderation/review",
            json={"entity_type": "deal", "entity_id": deal_sync.id, "force": False},
            headers=headers,
        )
        assert second.status_code == 200
        second_payload = second.json()
        assert second_payload["id"] == first_payload["id"]

        patch = client.patch(
            f"/api/moderation/reviews/{first_payload['id']}",
            json={"status": "pending"},
            headers=headers,
        )
        assert patch.status_code == 200
        assert patch.json()["status"] == "pending"

        done_patch = client.patch(
            f"/api/moderation/reviews/{first_payload['id']}",
            json={"status": "done"},
            headers=headers,
        )
        assert done_patch.status_code == 200
        assert done_patch.json()["status"] == "done"

        done_list = client.get("/api/moderation/reviews?status=done&entity_type=deal", headers=headers)
        assert done_list.status_code == 200
        done_rows = done_list.json()
        assert any(int(row["entity_id"]) == deal_sync.id for row in done_rows)
    finally:
        db.query(ModerationReview).filter(
            ModerationReview.entity_type == "deal", ModerationReview.entity_id == deal_sync.id
        ).delete(synchronize_session=False)
        db.query(DealSync).filter(DealSync.id == deal_sync.id).delete(synchronize_session=False)
        db.query(User).filter(User.id == user.id).delete(synchronize_session=False)
        db.commit()
        db.close()
