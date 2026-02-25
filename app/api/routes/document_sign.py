from __future__ import annotations

from datetime import datetime, timedelta
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import Deal, Document, DocumentSignSession, User
from app.services.document_signing import (
    abs_from_app_rel_path,
    compare_sha256,
    generate_otp,
    generate_sign_token,
    generate_signed_pdf,
    normalize_phone,
    parse_data_uri_png,
    save_signature_png,
    sha256_hex,
)
from app.services.sms_provider import send_otp


router = APIRouter()

SIGN_TTL_HOURS = 48
OTP_COOLDOWN_SEC = 60
OTP_MAX_ATTEMPTS = 5


class OtpSendRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=32)


class OtpVerifyRequest(BaseModel):
    otp: str = Field(..., min_length=4, max_length=8)


class SignatureRequest(BaseModel):
    signature_base64_png: str = Field(..., min_length=32)
    meta: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.utcnow()


def _extract_client_ip(request: Request) -> str | None:
    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return None


def _extract_user_agent(request: Request) -> str | None:
    value = (request.headers.get("user-agent") or "").strip()
    return value[:500] if value else None


def _set_first_touch(session: DocumentSignSession, request: Request) -> None:
    if not session.ip_first:
        session.ip_first = _extract_client_ip(request)
    if not session.user_agent_first:
        session.user_agent_first = _extract_user_agent(request)


def _resolve_document_ownership(db: Session, document: Document, current_user: User) -> None:
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    if document.company_id_from is None or document.company_id_to is None:
        deal = db.query(Deal).filter(Deal.id == document.deal_id).first() if document.deal_id else None
        if deal:
            document.company_id_from = deal.shipper_id
            document.company_id_to = deal.carrier_id
            db.add(document)
            db.commit()
            db.refresh(document)

    if document.company_id_from != current_user.id:
        raise HTTPException(status_code=403, detail="Только инициатор может отправить документ на подпись")


def _resolve_session_by_token(db: Session, token: str) -> DocumentSignSession:
    token_hash = sha256_hex(token)
    session = db.query(DocumentSignSession).filter(DocumentSignSession.token_hash == token_hash).first()
    if not session:
        raise HTTPException(status_code=404, detail="Ссылка не найдена")

    if session.expires_at and _now() > session.expires_at and not session.signed_at:
        document = db.query(Document).filter(Document.id == session.document_id).first()
        if document and document.status != "signed":
            document.status = "expired"
            db.add(document)
            db.commit()
        raise HTTPException(status_code=410, detail="Ссылка истекла, запросите новую")

    return session


def _serialize_company(user: User | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": int(user.id),
        "name": user.organization_name or user.company or user.fullname or f"Компания #{user.id}",
        "city": user.city,
    }


def _serialize_document_for_public(document: Document, db: Session) -> dict[str, Any]:
    company_from = db.query(User).filter(User.id == document.company_id_from).first() if document.company_id_from else None
    company_to = db.query(User).filter(User.id == document.company_id_to).first() if document.company_id_to else None

    payload = document.payload_json if isinstance(document.payload_json, dict) else {}
    safe_payload = {
        key: value
        for key, value in payload.items()
        if str(key) not in {"owner_email", "internal_notes", "private_notes"}
    }

    return {
        "id": int(document.id),
        "doc_type": document.doc_type,
        "status": document.status,
        "company_from": _serialize_company(company_from),
        "company_to": _serialize_company(company_to),
        "payload": safe_payload,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "updated_at": document.updated_at.isoformat() if document.updated_at else None,
    }


@router.post("/docs/{doc_id}/sign-link")
def create_sign_link(
    doc_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == doc_id).first()
    _resolve_document_ownership(db, document, current_user)
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    if document.status == "signed":
        raise HTTPException(status_code=409, detail="Документ уже подписан")

    token = generate_sign_token()
    expires_at = _now() + timedelta(hours=SIGN_TTL_HOURS)

    session = DocumentSignSession(
        document_id=document.id,
        token_hash=sha256_hex(token),
        phone=current_user.phone,
        expires_at=expires_at,
        sms_verified=False,
        otp_attempts=0,
    )
    _set_first_touch(session, request)
    db.add(session)

    document.status = "sent"
    db.add(document)
    db.commit()
    db.refresh(session)

    base_url = str(request.base_url).rstrip("/")
    return {
        "sign_url": f"{base_url}/sign/{token}",
        "expires_at": expires_at.isoformat(),
    }


@router.get("/public/sign/{token}/document")
def get_public_sign_document(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)
    document = db.query(Document).filter(Document.id == session.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    _set_first_touch(session, request)
    db.add(session)
    db.commit()

    return {
        "document": _serialize_document_for_public(document, db),
        "session": {
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "sms_verified": bool(session.sms_verified),
            "signed_at": session.signed_at.isoformat() if session.signed_at else None,
            "has_signature": bool(session.signature_png_path),
        },
    }


@router.post("/public/sign/{token}/otp/send")
def send_sign_otp(
    token: str,
    body: OtpSendRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)
    document = db.query(Document).filter(Document.id == session.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document.status == "signed" or session.signed_at:
        raise HTTPException(status_code=409, detail="Документ уже подписан")

    now = _now()
    if session.otp_sent_at:
        elapsed = (now - session.otp_sent_at).total_seconds()
        if elapsed < OTP_COOLDOWN_SEC:
            cooldown_left = int(max(1, OTP_COOLDOWN_SEC - elapsed))
            raise HTTPException(status_code=429, detail=f"Повторная отправка через {cooldown_left} сек.")

    try:
        normalized_phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    otp_code = generate_otp()
    session.phone = normalized_phone
    session.otp_hash = sha256_hex(otp_code)
    session.otp_sent_at = now
    session.otp_attempts = 0
    session.sms_verified = False
    _set_first_touch(session, request)

    send_result = send_otp(normalized_phone, otp_code)

    db.add(session)
    db.commit()

    payload: dict[str, Any] = {
        "ok": True,
        "cooldown_sec": OTP_COOLDOWN_SEC,
        "provider": send_result.provider,
    }

    debug_enabled = (os.getenv("OTP_DEBUG_RETURN", "false") or "false").strip().lower() in {"1", "true", "yes"}
    if debug_enabled:
        payload["otp_debug"] = otp_code
    return payload


@router.post("/public/sign/{token}/otp/verify")
def verify_sign_otp(
    token: str,
    body: OtpVerifyRequest,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)

    if session.sms_verified:
        return {"ok": True}

    if not session.otp_hash:
        raise HTTPException(status_code=400, detail="Сначала запросите OTP-код")

    if session.otp_attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Превышено число попыток OTP")

    otp_value = str(body.otp or "").strip()
    if not re.fullmatch(r"\d{6}", otp_value):
        raise HTTPException(status_code=422, detail="Код должен состоять из 6 цифр")

    if not compare_sha256(otp_value, session.otp_hash):
        session.otp_attempts = int(session.otp_attempts or 0) + 1
        db.add(session)
        db.commit()
        remaining = max(0, OTP_MAX_ATTEMPTS - int(session.otp_attempts or 0))
        raise HTTPException(status_code=400, detail=f"Неверный код. Осталось попыток: {remaining}")

    session.sms_verified = True
    db.add(session)
    db.commit()
    return {"ok": True}


@router.post("/public/sign/{token}/signature")
def upload_signature(
    token: str,
    body: SignatureRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)
    if not session.sms_verified:
        raise HTTPException(status_code=403, detail="Сначала подтвердите OTP-код")

    document = db.query(Document).filter(Document.id == session.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document.status == "signed" or session.signed_at:
        raise HTTPException(status_code=409, detail="Документ уже подписан")

    try:
        signature_binary = parse_data_uri_png(body.signature_base64_png)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rel_path = save_signature_png(document.id, session.id, signature_binary)
    meta = body.meta if isinstance(body.meta, dict) else {}
    meta["bytes"] = len(signature_binary)

    session.signature_png_path = rel_path
    session.signature_meta_json = meta
    _set_first_touch(session, request)
    db.add(session)
    db.commit()

    return {"ok": True}


@router.post("/public/sign/{token}/finalize")
def finalize_signature(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)
    document = db.query(Document).filter(Document.id == session.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")

    if not session.sms_verified:
        raise HTTPException(status_code=403, detail="Подтвердите OTP перед завершением")
    if not session.signature_png_path:
        raise HTTPException(status_code=400, detail="Сначала загрузите рисованную подпись")

    if document.status == "signed" and document.pdf_signed_path:
        existing_pdf = abs_from_app_rel_path(document.pdf_signed_path)
        if existing_pdf and os.path.isfile(existing_pdf):
            return {
                "ok": True,
                "pdf_url": f"/api/public/sign/{token}/pdf",
            }

    _set_first_touch(session, request)
    signed_at = _now()
    session.signed_at = signed_at
    db.add(session)

    ip_value = session.ip_first or _extract_client_ip(request)
    signed_pdf_rel, fingerprint = generate_signed_pdf(document, session, ip_value)

    if not document.pdf_draft_path and document.pdf_path:
        document.pdf_draft_path = document.pdf_path
    document.pdf_signed_path = signed_pdf_rel
    document.status = "signed"
    document.signed_at = signed_at
    document.updated_at = signed_at
    db.add(document)

    db.commit()

    return {
        "ok": True,
        "pdf_url": f"/api/public/sign/{token}/pdf",
        "document_hash": fingerprint,
    }


@router.get("/public/sign/{token}/pdf")
def get_signed_pdf(
    token: str,
    db: Session = Depends(get_db),
):
    session = _resolve_session_by_token(db, token)
    document = db.query(Document).filter(Document.id == session.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Документ не найден")
    if document.status != "signed":
        raise HTTPException(status_code=409, detail="Документ еще не подписан")

    abs_path = abs_from_app_rel_path(document.pdf_signed_path)
    if not abs_path or not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Подписанный PDF не найден")

    return FileResponse(
        abs_path,
        media_type="application/pdf",
        filename=f"document_{document.id}_signed.pdf",
    )
