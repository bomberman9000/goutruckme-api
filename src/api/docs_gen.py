"""Trip document PDF generator — auto-fill from cargo + company data."""

from __future__ import annotations

import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import ParserIngestEvent

router = APIRouter(tags=["docs"])


class TripDocRequest(BaseModel):
    carrier_name: str
    carrier_inn: str | None = None
    carrier_phone: str | None = None
    vehicle_plate: str | None = None
    notes: str | None = None


@router.post("/api/v1/docs/generate-trip/{feed_id}")
async def generate_trip_doc(
    feed_id: int,
    body: TripDocRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    """Generate a trip request PDF from cargo + carrier data."""
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, feed_id)
        if not event:
            raise HTTPException(status_code=404, detail="cargo not found")

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("ZAJAVKA NA PEREVOZKU GRUZA", styles["Title"]))
    elements.append(Paragraph(
        f"#{feed_id} ot {datetime.utcnow().strftime('%d.%m.%Y')}",
        styles["Normal"],
    ))
    elements.append(Spacer(1, 8 * mm))

    data = [
        ["Marshrut", f"{event.from_city or '?'} — {event.to_city or '?'}"],
        ["Data zagruzki", f"{event.load_date or '—'} {event.load_time or ''}"],
        ["Tip kuzova", event.body_type or "—"],
        ["Ves (t)", str(event.weight_t or "—")],
        ["Stavka", f"{event.rate_rub or 0:,} rub"],
        ["Oplata", event.payment_terms or "—"],
        ["Opisanie gruza", event.cargo_description or "—"],
        ["Gabarityi", event.dimensions or "—"],
    ]

    data.append(["", ""])
    data.append(["PEREVOZCHIK", ""])
    data.append(["Nazvanie", body.carrier_name])
    data.append(["INN", body.carrier_inn or "—"])
    data.append(["Telefon", body.carrier_phone or "—"])
    data.append(["Nomer TC", body.vehicle_plate or "—"])

    if event.phone:
        data.append(["", ""])
        data.append(["DISPATCHER", ""])
        data.append(["Telefon", event.phone])
        if event.inn:
            data.append(["INN", event.inn])

    if body.notes:
        data.append(["", ""])
        data.append(["Primechaniya", body.notes])

    table = Table(data, colWidths=[55 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(table)

    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph(
        f"Sformirovano: GruzPotok | {datetime.utcnow().strftime('%d.%m.%Y %H:%M')} UTC",
        styles["Normal"],
    ))

    doc.build(elements)
    buf.seek(0)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="trip_{feed_id}_{ts}.pdf"'
        },
    )
