from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import Attachment, Payment, Shipment, User

router = APIRouter()

SHIPMENT_STATUSES = {"draft", "in_progress", "done", "closed"}
PAYMENT_STATUSES = {"planned", "paid", "overdue"}
PAYMENT_DIRECTIONS = {"in", "out"}

_APP_ROOT = Path(__file__).resolve().parents[2]
_SHIPMENT_ATTACHMENTS_DIR = _APP_ROOT / "media" / "shipments" / "attachments"


def _now() -> datetime:
    return datetime.utcnow()


def _safe_float(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _money(value: float | int | None) -> float:
    return round(_safe_float(value), 2)


def _money_text(value: float | int | None) -> str:
    rounded = _money(value)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.2f}"


def _normalize_shipment_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in SHIPMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Некорректный статус перевозки")
    return status


def _normalize_payment_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status not in PAYMENT_STATUSES:
        raise HTTPException(status_code=422, detail="Некорректный статус платежа")
    return status


def _normalize_direction(value: str) -> str:
    direction = str(value or "").strip().lower()
    if direction not in PAYMENT_DIRECTIONS:
        raise HTTPException(status_code=422, detail="direction должен быть in или out")
    return direction


def _serialize_payment(payment: Payment) -> dict:
    return {
        "id": int(payment.id),
        "shipment_id": int(payment.shipment_id),
        "direction": payment.direction,
        "planned_date": payment.planned_date.isoformat() if payment.planned_date else None,
        "planned_amount": _money(payment.planned_amount),
        "actual_date": payment.actual_date.isoformat() if payment.actual_date else None,
        "actual_amount": _money(payment.actual_amount) if payment.actual_amount is not None else None,
        "status": payment.status,
        "comment": payment.comment,
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
    }


def _serialize_attachment(attachment: Attachment) -> dict:
    return {
        "id": int(attachment.id),
        "shipment_id": int(attachment.shipment_id),
        "file_name": attachment.file_name,
        "file_type": attachment.file_type,
        "download_url": f"/api/attachments/{attachment.id}/download",
        "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
    }


def _payment_summary(payments: list[Payment]) -> dict:
    client_paid_total = 0.0
    client_due_total = 0.0
    carrier_paid_total = 0.0
    carrier_due_total = 0.0

    for payment in payments:
        direction = (payment.direction or "").lower()
        amount_paid = payment.actual_amount if payment.actual_amount is not None else payment.planned_amount
        amount_planned = payment.planned_amount
        is_paid = (payment.status or "").lower() == "paid"

        if direction == "in":
            if is_paid:
                client_paid_total += _safe_float(amount_paid)
            else:
                client_due_total += _safe_float(amount_planned)
        elif direction == "out":
            if is_paid:
                carrier_paid_total += _safe_float(amount_paid)
            else:
                carrier_due_total += _safe_float(amount_planned)

    client_total = client_paid_total + client_due_total
    carrier_total = carrier_paid_total + carrier_due_total
    compact = f"Кл: {_money_text(client_paid_total)}/{_money_text(client_total)} | Пер: {_money_text(carrier_paid_total)}/{_money_text(carrier_total)}"

    return {
        "client_paid_total": _money(client_paid_total),
        "client_due_total": _money(client_due_total),
        "carrier_paid_total": _money(carrier_paid_total),
        "carrier_due_total": _money(carrier_due_total),
        "compact_status": compact,
    }


def _shipment_margin(shipment: Shipment) -> float:
    return _money(_safe_float(shipment.client_amount) - _safe_float(shipment.carrier_amount))


def _shipment_payload(shipment: Shipment, payments: list[Payment]) -> dict:
    summary = _payment_summary(payments)
    return {
        "id": int(shipment.id),
        "owner_company_id": int(shipment.owner_company_id),
        "ship_date": shipment.ship_date.isoformat() if shipment.ship_date else None,
        "client_name": shipment.client_name,
        "client_inn": shipment.client_inn,
        "from_city": shipment.from_city,
        "to_city": shipment.to_city,
        "cargo_brief": shipment.cargo_brief,
        "carrier_name": shipment.carrier_name,
        "carrier_inn": shipment.carrier_inn,
        "client_amount": _money(shipment.client_amount),
        "carrier_amount": _money(shipment.carrier_amount),
        "margin": _shipment_margin(shipment),
        "status": shipment.status,
        "created_at": shipment.created_at.isoformat() if shipment.created_at else None,
        "updated_at": shipment.updated_at.isoformat() if shipment.updated_at else None,
        "payment_summary": summary,
    }


def _refresh_payment_statuses(db: Session, owner_company_id: int) -> None:
    today = date.today()
    rows = (
        db.query(Payment, Shipment)
        .join(Shipment, Shipment.id == Payment.shipment_id)
        .filter(Shipment.owner_company_id == owner_company_id)
        .all()
    )

    changed = False
    for payment, _shipment in rows:
        current = (payment.status or "planned").lower()
        if current == "paid":
            continue
        next_status = "planned"
        if payment.planned_date and payment.planned_date < today:
            next_status = "overdue"
        if current != next_status:
            payment.status = next_status
            db.add(payment)
            changed = True
    if changed:
        db.commit()


def _get_owned_shipment(db: Session, shipment_id: int, owner_company_id: int) -> Shipment:
    shipment = (
        db.query(Shipment)
        .filter(Shipment.id == shipment_id, Shipment.owner_company_id == owner_company_id)
        .first()
    )
    if not shipment:
        raise HTTPException(status_code=404, detail="Перевозка не найдена")
    return shipment


def _get_owned_payment(db: Session, payment_id: int, owner_company_id: int) -> tuple[Payment, Shipment]:
    row = (
        db.query(Payment, Shipment)
        .join(Shipment, Shipment.id == Payment.shipment_id)
        .filter(Payment.id == payment_id, Shipment.owner_company_id == owner_company_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Платеж не найден")
    return row


def _sanitize_filename(name: str) -> str:
    value = re.sub(r"[^\w\-. ]+", "_", str(name or "").strip())
    value = value.strip(" .")
    return value[:180] or "file.bin"


def _ics_escape(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n")
    return text


class ShipmentCreate(BaseModel):
    ship_date: date = Field(default_factory=date.today)
    client_name: str = Field(..., min_length=1, max_length=255)
    client_inn: str | None = Field(default=None, max_length=20)
    from_city: str = Field(..., min_length=1, max_length=120)
    to_city: str = Field(..., min_length=1, max_length=120)
    cargo_brief: str = Field(..., min_length=1)
    carrier_name: str = Field(..., min_length=1, max_length=255)
    carrier_inn: str | None = Field(default=None, max_length=20)
    client_amount: float = Field(default=0.0)
    carrier_amount: float = Field(default=0.0)
    status: str = Field(default="draft")


class ShipmentPatch(BaseModel):
    ship_date: date | None = None
    client_name: str | None = Field(default=None, min_length=1, max_length=255)
    client_inn: str | None = Field(default=None, max_length=20)
    from_city: str | None = Field(default=None, min_length=1, max_length=120)
    to_city: str | None = Field(default=None, min_length=1, max_length=120)
    cargo_brief: str | None = Field(default=None, min_length=1)
    carrier_name: str | None = Field(default=None, min_length=1, max_length=255)
    carrier_inn: str | None = Field(default=None, max_length=20)
    client_amount: float | None = None
    carrier_amount: float | None = None
    status: str | None = None


class PaymentCreate(BaseModel):
    direction: str
    planned_date: date
    planned_amount: float = Field(..., gt=0)
    comment: str | None = None


class PaymentPatch(BaseModel):
    status: str | None = None
    actual_date: date | None = None
    actual_amount: float | None = Field(default=None, gt=0)
    planned_date: date | None = None
    planned_amount: float | None = Field(default=None, gt=0)
    comment: str | None = None


@router.get("/shipments")
def list_shipments(
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _refresh_payment_statuses(db, int(current_user.id))

    query = db.query(Shipment).filter(Shipment.owner_company_id == current_user.id)
    if status:
        normalized = _normalize_shipment_status(status)
        query = query.filter(Shipment.status == normalized)
    if date_from:
        query = query.filter(Shipment.ship_date >= date_from)
    if date_to:
        query = query.filter(Shipment.ship_date <= date_to)

    if q:
        pattern = f"%{q.strip()}%"
        if pattern != "%%":
            query = query.filter(
                or_(
                    Shipment.client_name.ilike(pattern),
                    Shipment.client_inn.ilike(pattern),
                    Shipment.carrier_name.ilike(pattern),
                    Shipment.carrier_inn.ilike(pattern),
                    Shipment.from_city.ilike(pattern),
                    Shipment.to_city.ilike(pattern),
                    Shipment.cargo_brief.ilike(pattern),
                )
            )

    shipments = query.order_by(Shipment.ship_date.desc(), Shipment.id.desc()).all()
    shipment_ids = [s.id for s in shipments]
    payments_map: dict[int, list[Payment]] = {shipment_id: [] for shipment_id in shipment_ids}
    if shipment_ids:
        payments = db.query(Payment).filter(Payment.shipment_id.in_(shipment_ids)).all()
        for payment in payments:
            payments_map.setdefault(int(payment.shipment_id), []).append(payment)

    return [_shipment_payload(shipment, payments_map.get(int(shipment.id), [])) for shipment in shipments]


@router.post("/shipments")
def create_shipment(
    body: ShipmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shipment = Shipment(
        owner_company_id=current_user.id,
        ship_date=body.ship_date,
        client_name=body.client_name.strip(),
        client_inn=(body.client_inn or "").strip() or None,
        from_city=body.from_city.strip(),
        to_city=body.to_city.strip(),
        cargo_brief=body.cargo_brief.strip(),
        carrier_name=body.carrier_name.strip(),
        carrier_inn=(body.carrier_inn or "").strip() or None,
        client_amount=_money(body.client_amount),
        carrier_amount=_money(body.carrier_amount),
        status=_normalize_shipment_status(body.status),
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)
    return _shipment_payload(shipment, [])


@router.get("/shipments/{shipment_id}")
def get_shipment(
    shipment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _refresh_payment_statuses(db, int(current_user.id))
    shipment = _get_owned_shipment(db, shipment_id, int(current_user.id))
    payments = (
        db.query(Payment)
        .filter(Payment.shipment_id == shipment.id)
        .order_by(Payment.planned_date.asc(), Payment.id.asc())
        .all()
    )
    attachments = (
        db.query(Attachment)
        .filter(Attachment.shipment_id == shipment.id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        .all()
    )

    payload = _shipment_payload(shipment, payments)
    payload["payments"] = [_serialize_payment(row) for row in payments]
    payload["attachments"] = [_serialize_attachment(row) for row in attachments]
    return payload


@router.patch("/shipments/{shipment_id}")
def patch_shipment(
    shipment_id: int,
    body: ShipmentPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shipment = _get_owned_shipment(db, shipment_id, int(current_user.id))

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        if key == "status":
            setattr(shipment, key, _normalize_shipment_status(value))
        elif key in {"client_amount", "carrier_amount"}:
            setattr(shipment, key, _money(value))
        elif key in {"client_name", "from_city", "to_city", "cargo_brief", "carrier_name"} and value is not None:
            setattr(shipment, key, str(value).strip())
        elif key in {"client_inn", "carrier_inn"}:
            setattr(shipment, key, (str(value).strip() if value else None))
        else:
            setattr(shipment, key, value)

    shipment.updated_at = _now()
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    payments = db.query(Payment).filter(Payment.shipment_id == shipment.id).all()
    return _shipment_payload(shipment, payments)


@router.post("/shipments/{shipment_id}/payments")
def create_shipment_payment(
    shipment_id: int,
    body: PaymentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shipment = _get_owned_shipment(db, shipment_id, int(current_user.id))
    direction = _normalize_direction(body.direction)

    status = "planned"
    if body.planned_date < date.today():
        status = "overdue"

    payment = Payment(
        shipment_id=shipment.id,
        direction=direction,
        planned_date=body.planned_date,
        planned_amount=_money(body.planned_amount),
        status=status,
        comment=(body.comment or "").strip() or None,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return _serialize_payment(payment)


@router.patch("/payments/{payment_id}")
def patch_payment(
    payment_id: int,
    body: PaymentPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payment, shipment = _get_owned_payment(db, payment_id, int(current_user.id))
    updates = body.model_dump(exclude_unset=True)

    for key, value in updates.items():
        if key == "status" and value is not None:
            payment.status = _normalize_payment_status(value)
        elif key in {"actual_amount", "planned_amount"} and value is not None:
            setattr(payment, key, _money(value))
        elif key == "comment":
            payment.comment = (str(value).strip() if value else None)
        else:
            setattr(payment, key, value)

    if payment.status == "paid":
        if not payment.actual_date:
            payment.actual_date = date.today()
        if payment.actual_amount is None:
            payment.actual_amount = payment.planned_amount
    else:
        if payment.planned_date and payment.planned_date < date.today():
            payment.status = "overdue"
        else:
            payment.status = "planned"

    db.add(payment)
    db.commit()
    db.refresh(payment)

    # На всякий случай держим статусы консистентными для компании.
    _refresh_payment_statuses(db, int(shipment.owner_company_id))
    return _serialize_payment(payment)


@router.get("/payments")
def list_payments(
    status: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _refresh_payment_statuses(db, int(current_user.id))
    query = (
        db.query(Payment, Shipment)
        .join(Shipment, Shipment.id == Payment.shipment_id)
        .filter(Shipment.owner_company_id == current_user.id)
    )
    if status:
        normalized = _normalize_payment_status(status)
        query = query.filter(Payment.status == normalized)

    rows = query.order_by(Payment.planned_date.asc(), Payment.id.asc()).all()
    result = []
    for payment, shipment in rows:
        payload = _serialize_payment(payment)
        payload["shipment_id"] = int(shipment.id)
        payload["shipment_route"] = f"{shipment.from_city} → {shipment.to_city}"
        payload["shipment_status"] = shipment.status
        result.append(payload)
    return result


@router.get("/payments/{payment_id}/reminder.ics")
def payment_reminder_ics(
    payment_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payment, shipment = _get_owned_payment(db, payment_id, int(current_user.id))
    if not payment.planned_date:
        raise HTTPException(status_code=422, detail="Для платежа не задана плановая дата")

    dtstart = datetime.combine(payment.planned_date, time(hour=10, minute=0))
    dtstamp = _now()
    amount = _money_text(payment.planned_amount)
    summary = _ics_escape(f"Оплата ({payment.direction}) по перевозке {shipment.id}: {amount}")
    url = f"{str(request.base_url).rstrip('/')}/#/shipments/{shipment.id}"
    description = _ics_escape(
        "Маршрут: "
        + f"{shipment.from_city} -> {shipment.to_city}\n"
        + f"Клиент: {shipment.client_name}\n"
        + f"Перевозчик: {shipment.carrier_name}\n"
        + f"Сумма: {amount}\n"
        + f"Ссылка: {url}"
    )
    uid = f"payment-{payment.id}-{uuid.uuid4().hex[:10]}@gruzpotok"

    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//GruzPotok//Shipments//RU\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{dtstamp.strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTSTART:{dtstart.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"DESCRIPTION:{description}\r\n"
        f"URL:{_ics_escape(url)}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    filename = f"payment_{payment.id}_reminder.ics"
    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/shipments/{shipment_id}/payments/{payment_id}/reminder.ics")
def shipment_payment_reminder_ics(
    shipment_id: int,
    payment_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payment, shipment = _get_owned_payment(db, payment_id, int(current_user.id))
    if int(shipment.id) != int(shipment_id):
        raise HTTPException(status_code=404, detail="Платеж не найден для указанной перевозки")
    return payment_reminder_ics(payment_id=payment_id, request=request, current_user=current_user, db=db)


@router.post("/shipments/{shipment_id}/attachments")
def upload_shipment_attachment(
    shipment_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shipment = _get_owned_shipment(db, shipment_id, int(current_user.id))
    if not file.filename:
        raise HTTPException(status_code=422, detail="Файл не выбран")

    _SHIPMENT_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(file.filename)
    ext = Path(safe_name).suffix or ".bin"
    stored_name = f"shipment_{shipment.id}_{uuid.uuid4().hex[:12]}{ext}"
    full_path = _SHIPMENT_ATTACHMENTS_DIR / stored_name
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Файл пустой")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл слишком большой (макс. 20 MB)")

    with open(full_path, "wb") as f:
        f.write(content)

    rel_path = str(full_path.relative_to(_APP_ROOT))
    attachment = Attachment(
        shipment_id=shipment.id,
        file_path=rel_path,
        file_name=safe_name,
        file_type=file.content_type,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return _serialize_attachment(attachment)


@router.get("/shipments/{shipment_id}/attachments")
def list_shipment_attachments(
    shipment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    shipment = _get_owned_shipment(db, shipment_id, int(current_user.id))
    rows = (
        db.query(Attachment)
        .filter(Attachment.shipment_id == shipment.id)
        .order_by(Attachment.created_at.desc(), Attachment.id.desc())
        .all()
    )
    return [_serialize_attachment(item) for item in rows]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(Attachment, Shipment)
        .join(Shipment, Shipment.id == Attachment.shipment_id)
        .filter(Attachment.id == attachment_id, Shipment.owner_company_id == current_user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Файл не найден")
    attachment, _shipment = row
    full_path = Path(_APP_ROOT / attachment.file_path)
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        path=str(full_path),
        filename=attachment.file_name,
        media_type=attachment.file_type or "application/octet-stream",
    )
