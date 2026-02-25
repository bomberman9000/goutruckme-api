from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any

from app.models.models import Document, DocumentSignSession


_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MEDIA_ROOT = os.path.join(_APP_ROOT, "media", "document_signing")
_SIGNED_DIR = os.path.join(_MEDIA_ROOT, "signed")
_SIGNATURES_DIR = os.path.join(_MEDIA_ROOT, "signatures")


def ensure_signing_storage() -> None:
    os.makedirs(_SIGNED_DIR, exist_ok=True)
    os.makedirs(_SIGNATURES_DIR, exist_ok=True)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compare_sha256(plain: str, expected_hash: str | None) -> bool:
    if not expected_hash:
        return False
    computed = sha256_hex(plain)
    return hmac.compare_digest(computed, expected_hash)


def generate_sign_token() -> str:
    return secrets.token_urlsafe(36)


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def normalize_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        raise ValueError("Введите номер телефона")

    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"

    if len(digits) == 10:
        return f"+7{digits}"

    raise ValueError("Телефон должен быть в формате +7XXXXXXXXXX")


def mask_phone(phone: str | None) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if len(digits) < 4:
        return "***"
    if len(digits) == 11:
        return f"+{digits[0]}*** *** **{digits[-2:]}"
    return f"*** *** **{digits[-2:]}"


def parse_data_uri_png(signature_base64_png: str) -> bytes:
    raw = str(signature_base64_png or "").strip()
    prefix = "data:image/png;base64,"
    if not raw.startswith(prefix):
        raise ValueError("Ожидается PNG в формате data:image/png;base64,...")

    encoded = raw[len(prefix):]
    if not encoded:
        raise ValueError("Подпись пустая")

    try:
        binary = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Не удалось декодировать PNG подпись") from exc

    if len(binary) < 32:
        raise ValueError("PNG подпись слишком короткая")
    if len(binary) > 3 * 1024 * 1024:
        raise ValueError("PNG подпись слишком большая")
    if not binary.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Некорректный PNG файл")
    return binary


def save_signature_png(document_id: int, session_id: int, binary_png: bytes) -> str:
    ensure_signing_storage()
    filename = f"doc_{document_id}_session_{session_id}_{secrets.token_hex(6)}.png"
    abs_path = os.path.join(_SIGNATURES_DIR, filename)
    with open(abs_path, "wb") as f:
        f.write(binary_png)
    return os.path.relpath(abs_path, _APP_ROOT)


def abs_from_app_rel_path(path_value: str | None) -> str | None:
    if not path_value:
        return None
    if os.path.isabs(path_value):
        return path_value
    return os.path.join(_APP_ROOT, path_value)


def build_document_fingerprint(document: Document, session: DocumentSignSession) -> str:
    payload = {
        "document_id": int(document.id),
        "deal_id": int(document.deal_id) if document.deal_id is not None else None,
        "doc_type": document.doc_type,
        "status": document.status,
        "company_id_from": document.company_id_from,
        "company_id_to": document.company_id_to,
        "payload_json": document.payload_json if isinstance(document.payload_json, dict) else {},
        "session_id": int(session.id),
        "phone": session.phone,
        "signed_at": session.signed_at.isoformat() if session.signed_at else None,
        "signature_png_path": session.signature_png_path,
    }
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _flatten_payload(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for nested_key, nested_value in value.items():
                lines.append(f"  - {nested_key}: {nested_value}")
        else:
            lines.append(f"{key}: {value}")
    return lines


def _resolve_pdf_font_name() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        if not os.path.isfile(candidate):
            continue
        try:
            pdfmetrics.registerFont(TTFont("GTMUnicode", candidate))
            return "GTMUnicode"
        except Exception:
            continue
    return "Helvetica"


def generate_signed_pdf(document: Document, session: DocumentSignSession, ip_value: str | None) -> tuple[str, str]:
    ensure_signing_storage()

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    now = datetime.utcnow()
    signed_filename = f"doc_{document.id}_signed_{session.id}_{now.strftime('%Y%m%d%H%M%S')}.pdf"
    abs_pdf_path = os.path.join(_SIGNED_DIR, signed_filename)

    payload = document.payload_json if isinstance(document.payload_json, dict) else {}
    fingerprint = build_document_fingerprint(document, session)
    masked = mask_phone(session.phone)

    c = canvas.Canvas(abs_pdf_path, pagesize=A4)
    width, height = A4
    font_name = _resolve_pdf_font_name()

    c.setFont(font_name, 14)
    c.drawString(40, height - 50, "Договор-заявка (подписанный экземпляр)")

    c.setFont(font_name, 10)
    y = height - 80
    c.drawString(40, y, f"Document ID: {document.id}")
    y -= 16
    c.drawString(40, y, f"Тип: {document.doc_type}")
    y -= 16
    c.drawString(40, y, f"Компания-инициатор: {document.company_id_from or '-'}")
    y -= 16
    c.drawString(40, y, f"Контрагент: {document.company_id_to or '-'}")
    y -= 24

    c.setFont(font_name, 11)
    c.drawString(40, y, "Данные заявки:")
    y -= 16
    c.setFont(font_name, 9)
    payload_lines = _flatten_payload(payload) if payload else ["(данные не заполнены)"]
    for line in payload_lines[:28]:
        c.drawString(40, y, str(line)[:120])
        y -= 13
        if y < 170:
            break

    stamp_lines = [
        "Подписано посредством SMS-OTP",
        f"Телефон: {masked}",
        f"Дата/время UTC: {now.isoformat()}",
        f"IP: {ip_value or '-'}",
    ]
    c.setFont(font_name, 10)
    stamp_y = 125
    c.rect(34, stamp_y - 8, 340, 68)
    for idx, line in enumerate(stamp_lines):
        c.drawString(42, stamp_y + 44 - idx * 14, line)

    signature_abs = abs_from_app_rel_path(session.signature_png_path)
    if signature_abs and os.path.isfile(signature_abs):
        signature_image = ImageReader(signature_abs)
        c.drawImage(signature_image, 390, 92, width=160, height=80, preserveAspectRatio=True, mask="auto")
        c.setFont(font_name, 8)
        c.drawString(390, 84, "Рисованная подпись")

    c.showPage()
    c.setFont(font_name, 11)
    c.drawString(40, height - 50, "Контрольный hash документа")
    c.setFont("Courier", 10)
    c.drawString(40, height - 74, fingerprint[:32])
    c.drawString(40, height - 88, fingerprint[32:])
    c.save()

    rel_pdf_path = os.path.relpath(abs_pdf_path, _APP_ROOT)
    return rel_pdf_path, fingerprint
