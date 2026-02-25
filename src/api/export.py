"""Export feed data as CSV (Excel-compatible) or PDF."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import ParserIngestEvent

router = APIRouter(tags=["export"])

_CSV_COLUMNS = [
    ("Дата", "load_date"),
    ("Время", "load_time"),
    ("Откуда", "from_city"),
    ("Куда", "to_city"),
    ("Кузов", "body_type"),
    ("Вес (т)", "weight_t"),
    ("Ставка (₽)", "rate_rub"),
    ("Описание", "cargo_description"),
    ("Оплата", "payment_terms"),
    ("Прямой", "is_direct_customer"),
    ("Габариты", "dimensions"),
    ("🔥", "is_hot_deal"),
    ("Телефон", "phone"),
    ("ИНН", "inn"),
    ("Надёжность", "trust_score"),
    ("Вердикт", "trust_verdict"),
    ("Создано", "created_at"),
]


async def _fetch_events(
    *,
    from_city: str | None,
    to_city: str | None,
    body_type: str | None,
    load_date: str | None,
    limit: int,
) -> list[ParserIngestEvent]:
    stmt = (
        select(ParserIngestEvent)
        .where(
            ParserIngestEvent.is_spam.is_(False),
            ParserIngestEvent.status == "synced",
        )
    )
    if from_city:
        stmt = stmt.where(ParserIngestEvent.from_city.ilike(f"%{from_city.strip()}%"))
    if to_city:
        stmt = stmt.where(ParserIngestEvent.to_city.ilike(f"%{to_city.strip()}%"))
    if body_type:
        stmt = stmt.where(ParserIngestEvent.body_type.ilike(f"%{body_type.strip()}%"))
    if load_date:
        stmt = stmt.where(ParserIngestEvent.load_date == load_date.strip())
    stmt = stmt.order_by(ParserIngestEvent.id.desc()).limit(limit)

    async with async_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


def _cell(event: ParserIngestEvent, attr: str) -> str:
    val = getattr(event, attr, None)
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Да" if val else "Нет"
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M")
    return str(val)


@router.get("/api/v1/export")
async def export_feed(
    fmt: str = Query(default="csv", pattern="^(csv|pdf)$"),
    from_city: str | None = Query(default=None),
    to_city: str | None = Query(default=None),
    body_type: str | None = Query(default=None),
    load_date: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    events = await _fetch_events(
        from_city=from_city,
        to_city=to_city,
        body_type=body_type,
        load_date=load_date,
        limit=limit,
    )

    if fmt == "pdf":
        return _build_pdf_response(events)
    return _build_csv_response(events)


def _build_csv_response(events: list[ParserIngestEvent]) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([col[0] for col in _CSV_COLUMNS])
    for ev in events:
        writer.writerow([_cell(ev, col[1]) for col in _CSV_COLUMNS])

    buf.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="gruzpotok_export_{ts}.csv"'
        },
    )


def _build_pdf_response(events: list[ParserIngestEvent]) -> StreamingResponse:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(
        f"GruzPotok Export — {datetime.utcnow().strftime('%d.%m.%Y %H:%M')} UTC",
        styles["Title"],
    ))
    elements.append(Spacer(1, 4 * mm))

    pdf_cols = [
        ("Дата", "load_date"),
        ("Откуда", "from_city"),
        ("Куда", "to_city"),
        ("Кузов", "body_type"),
        ("Вес", "weight_t"),
        ("Ставка", "rate_rub"),
        ("Оплата", "payment_terms"),
        ("🔥", "is_hot_deal"),
        ("Телефон", "phone"),
        ("Балл", "trust_score"),
    ]

    data = [[c[0] for c in pdf_cols]]
    for ev in events:
        data.append([_cell(ev, c[1]) for c in pdf_cols])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4A90D9")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(table)

    doc.build(elements)
    buf.seek(0)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="gruzpotok_export_{ts}.pdf"'
        },
    )
