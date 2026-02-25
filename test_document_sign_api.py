from __future__ import annotations

import os
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.security import create_token, hash_password
from app.db.database import SessionLocal, init_db
from app.models.models import Deal, Document, DocumentSignSession, Load, User, UserRole


TEST_SIGNATURE_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2MlS4AAAAASUVORK5CYII="
)


def _make_user(db, *, role: UserRole, suffix: str) -> User:
    user = User(
        phone=f"+7900{suffix}",
        password_hash=hash_password("pass123"),
        role=role,
        organization_name=f"Company {suffix}",
        company=f"Company {suffix}",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_deal_document(db, *, suffix: str) -> tuple[User, User, Document]:
    shipper = _make_user(db, role=UserRole.client, suffix=f"{suffix}1")
    carrier = _make_user(db, role=UserRole.carrier, suffix=f"{suffix}2")

    load = Load(
        user_id=shipper.id,
        from_city="Москва",
        to_city="Казань",
        weight=12.5,
        volume=45.0,
        price=120000,
        status="open",
    )
    db.add(load)
    db.commit()
    db.refresh(load)

    deal = Deal(
        cargo_id=load.id,
        shipper_id=shipper.id,
        carrier_id=carrier.id,
        status="IN_PROGRESS",
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)

    document = Document(
        deal_id=deal.id,
        company_id_from=shipper.id,
        company_id_to=carrier.id,
        doc_type="deal_request",
        status="draft",
        payload_json={
            "route": "Москва -> Казань",
            "rate_rub": 120000,
            "cargo": "Оборудование",
            "eta_days": 2,
        },
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    return shipper, carrier, document


def _extract_token_from_url(sign_url: str) -> str:
    return sign_url.rstrip("/").split("/")[-1]


def test_document_sign_finalize_requires_otp():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    shipper, carrier, document = _create_deal_document(db, suffix=suffix)
    token = create_token({"id": shipper.id, "phone": shipper.phone})

    try:
        sign_link_resp = client.post(
            f"/api/docs/{document.id}/sign-link",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert sign_link_resp.status_code == 200
        sign_token = _extract_token_from_url(sign_link_resp.json()["sign_url"])

        save_signature_resp = client.post(
            f"/api/public/sign/{sign_token}/signature",
            json={"signature_base64_png": TEST_SIGNATURE_DATA_URI, "meta": {"width": 200, "height": 80}},
        )
        assert save_signature_resp.status_code == 403

        finalize_resp = client.post(f"/api/public/sign/{sign_token}/finalize")
        assert finalize_resp.status_code == 403
    finally:
        db.query(DocumentSignSession).filter(DocumentSignSession.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.query(Deal).filter(Deal.id == document.deal_id).delete()
        db.query(Load).filter(Load.id > 0, Load.user_id.in_([shipper.id, carrier.id])).delete()
        db.query(User).filter(User.id.in_([shipper.id, carrier.id])).delete()
        db.commit()
        db.close()


def test_document_sign_verify_and_finalize_flow():
    init_db()
    client = TestClient(app)
    db = SessionLocal()

    suffix = str(int(uuid4().int % 1_000_000)).zfill(6)[:6]
    shipper, carrier, document = _create_deal_document(db, suffix=suffix)
    auth_token = create_token({"id": shipper.id, "phone": shipper.phone})
    os.environ["OTP_DEBUG_RETURN"] = "true"

    try:
        sign_link_resp = client.post(
            f"/api/docs/{document.id}/sign-link",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert sign_link_resp.status_code == 200
        sign_token = _extract_token_from_url(sign_link_resp.json()["sign_url"])

        document_resp = client.get(f"/api/public/sign/{sign_token}/document")
        assert document_resp.status_code == 200
        assert document_resp.json()["document"]["id"] == document.id

        otp_send_resp = client.post(
            f"/api/public/sign/{sign_token}/otp/send",
            json={"phone": carrier.phone},
        )
        assert otp_send_resp.status_code == 200
        otp_payload = otp_send_resp.json()
        assert otp_payload["ok"] is True
        otp_value = otp_payload.get("otp_debug")
        assert otp_value and len(str(otp_value)) == 6

        wrong_verify_resp = client.post(
            f"/api/public/sign/{sign_token}/otp/verify",
            json={"otp": "000000"},
        )
        assert wrong_verify_resp.status_code == 400

        verify_resp = client.post(
            f"/api/public/sign/{sign_token}/otp/verify",
            json={"otp": str(otp_value)},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["ok"] is True

        save_signature_resp = client.post(
            f"/api/public/sign/{sign_token}/signature",
            json={"signature_base64_png": TEST_SIGNATURE_DATA_URI, "meta": {"width": 200, "height": 80}},
        )
        assert save_signature_resp.status_code == 200
        assert save_signature_resp.json()["ok"] is True

        finalize_resp = client.post(f"/api/public/sign/{sign_token}/finalize")
        assert finalize_resp.status_code == 200
        finalize_payload = finalize_resp.json()
        assert finalize_payload["ok"] is True
        assert "/api/public/sign/" in finalize_payload["pdf_url"]

        pdf_resp = client.get(finalize_payload["pdf_url"])
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers.get("content-type", "").startswith("application/pdf")
        assert pdf_resp.content.startswith(b"%PDF")
    finally:
        os.environ.pop("OTP_DEBUG_RETURN", None)
        db.query(DocumentSignSession).filter(DocumentSignSession.document_id == document.id).delete()
        db.query(Document).filter(Document.id == document.id).delete()
        db.query(Deal).filter(Deal.id == document.deal_id).delete()
        db.query(Load).filter(Load.id > 0, Load.user_id.in_([shipper.id, carrier.id])).delete()
        db.query(User).filter(User.id.in_([shipper.id, carrier.id])).delete()
        db.commit()
        db.close()
