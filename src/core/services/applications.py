"""
Создание заявок A/B и снапшотов сторон при выборе перевозчика по сделке.
Всё в одной транзакции. Сразу заполняется rendered_text.
PDF: генерация и путь в Application.
"""

import json
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import Application, ApplicationPartySnapshot, Cargo
from src.core.snapshots import make_party_snapshot
from src.core.audit import log_audit_event
from src.core.renderers.application_a import render_application_a
from src.core.renderers.application_b import render_application_b
from src.core.documents import generate_application_pdf


async def create_applications_for_selected_carrier(
    session: AsyncSession,
    deal_id: int,
    client_user_id: int,
    forwarder_user_id: int,
    carrier_user_id: int,
    created_by_user_id: int | None = None,
    selected_carrier_user_id: int | None = None,
) -> tuple[int | None, int]:
    """
    B создаётся всегда (forwarder + carrier есть).
    A создаётся только если client_user_id задан и client_user_id != forwarder_user_id.
    Иначе возвращает (None, app_b.id); в UI показывать «Назначьте клиента в сделке».
    """
    create_a = bool(
        client_user_id is not None
        and forwarder_user_id is not None
        and client_user_id != forwarder_user_id
    )

    async with session.begin():
        app_a = None
        if create_a:
            app_a = Application(
                deal_id=deal_id,
                type="A",
                status="draft",
                created_by_user_id=created_by_user_id,
            )
            session.add(app_a)

        app_b = Application(
            deal_id=deal_id,
            type="B",
            status="draft",
            created_by_user_id=created_by_user_id,
            selected_carrier_user_id=selected_carrier_user_id or carrier_user_id,
        )
        session.add(app_b)
        await session.flush()

        payload_forwarder = await make_party_snapshot(forwarder_user_id, "forwarder", session=session)
        payload_carrier = await make_party_snapshot(carrier_user_id, "carrier", session=session)

        snaps_b = [
            ApplicationPartySnapshot(
                application_id=app_b.id,
                role="forwarder",
                payload_json=json.dumps(payload_forwarder, ensure_ascii=False),
            ),
            ApplicationPartySnapshot(
                application_id=app_b.id,
                role="carrier",
                payload_json=json.dumps(payload_carrier, ensure_ascii=False),
            ),
        ]
        if app_a is not None:
            payload_client = await make_party_snapshot(client_user_id, "client", session=session)
            snaps_a = [
                ApplicationPartySnapshot(
                    application_id=app_a.id,
                    role="client",
                    payload_json=json.dumps(payload_client, ensure_ascii=False),
                ),
                ApplicationPartySnapshot(
                    application_id=app_a.id,
                    role="forwarder",
                    payload_json=json.dumps(payload_forwarder, ensure_ascii=False),
                ),
            ]
            session.add_all(snaps_a)
        session.add_all(snaps_b)

        deal = await session.scalar(select(Cargo).where(Cargo.id == deal_id))
        if deal is not None:
            now_rendered = datetime.now(timezone.utc)
            parties_b = {"forwarder": payload_forwarder, "carrier": payload_carrier}
            app_b.rendered_text = render_application_b(deal, parties_b, app_id=app_b.id)
            app_b.rendered_updated_at = now_rendered
            if app_a is not None:
                parties_a = {"client": payload_client, "forwarder": payload_forwarder, "carrier": payload_carrier}
                app_a.rendered_text = render_application_a(deal, parties_a, app_id=app_a.id)
                app_a.rendered_updated_at = now_rendered

        actor_id = created_by_user_id
        if app_a is not None:
            log_audit_event(
                session, "application", app_a.id, "created",
                actor_user_id=actor_id, actor_role="forwarder",
                meta={"deal_id": deal_id, "type": "A"},
            )
        log_audit_event(
            session, "application", app_b.id, "created",
            actor_user_id=actor_id, actor_role="forwarder",
            meta={"deal_id": deal_id, "type": "B"},
        )

    return (app_a.id if app_a is not None else None, app_b.id)


def _signature_block_line(label: str, dt) -> str:
    if dt is None:
        return ""
    ts = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
    return f"Подписано {label}: {ts}"


def build_signature_block(app: "Application") -> str:
    """Собирает блок подписей из signed_by_*_at. Для A — клиент и экспедитор, для B — экспедитор и перевозчик."""
    lines = ["───────────────────────────────────────", "Подписи", "───────────────────────────────────────"]
    if app.type == "A":
        if app.signed_by_client_at:
            lines.append(_signature_block_line("Клиентом", app.signed_by_client_at))
        if app.signed_by_forwarder_at:
            lines.append(_signature_block_line("Экспедитором", app.signed_by_forwarder_at))
    else:
        if app.signed_by_forwarder_at:
            lines.append(_signature_block_line("Экспедитором", app.signed_by_forwarder_at))
        if app.signed_by_carrier_at:
            lines.append(_signature_block_line("Перевозчиком", app.signed_by_carrier_at))
    return "\n".join(lines)


def update_rendered_text_with_signatures(app: "Application") -> None:
    """Дополняет app.rendered_text блоком подписей (или заменяет старый блок). Обновляет rendered_updated_at."""
    base = (app.rendered_text or "").strip()
    if "Подписано" in base or "Подписи" in base:
        for sep in ("Подписи", "Подписано Клиентом", "Подписано Экспедитором", "Подписано Перевозчиком"):
            if sep in base:
                base = base.split(sep)[0].strip()
                break
    block = build_signature_block(app)
    app.rendered_text = (base + "\n\n" + block).strip()
    app.rendered_updated_at = datetime.now(timezone.utc)


async def send_application_to_party(
    session, app_id: int, target_role: str
) -> tuple[int | None, str | None, int | None]:
    """
    Определяет target_user_id по сделке для заявки.
    target_role: "client" (для A) или "carrier" (для B).
    Возвращает (target_user_id, app_type, deal_id) или (None, None, None) если сторона не назначена.
    """
    app = await session.scalar(select(Application).where(Application.id == app_id))
    if not app:
        return (None, None, None)
    deal = await session.scalar(select(Cargo).where(Cargo.id == app.deal_id))
    if not deal:
        return (None, app.type, app.deal_id)
    if app.type == "A" and target_role == "client":
        target = deal.client_user_id
    elif app.type == "A" and target_role == "forwarder":
        target = deal.forwarder_user_id
    elif app.type == "B" and target_role == "carrier":
        target = deal.carrier_id
    elif app.type == "B" and target_role == "forwarder":
        target = deal.forwarder_user_id
    else:
        target = None
    return (target, app.type, app.deal_id)


def ensure_application_pdf(app: Application, storage_root: str) -> str:
    """
    Если pdf_path есть и PDF не устарел (pdf_updated_at >= rendered_updated_at) — возвращает путь к файлу.
    Иначе генерирует PDF из rendered_text, сохраняет, заполняет app.pdf_path и app.pdf_updated_at.
    Возвращает абсолютный путь к PDF. Коммит не делает — делает вызывающий.
    """
    if app.rendered_text is None or not app.rendered_text.strip():
        raise ValueError("Невозможно сформировать PDF — текст заявки отсутствует")
    now = datetime.now(timezone.utc)
    use_existing = (
        app.pdf_path is not None
        and app.pdf_updated_at is not None
        and app.rendered_updated_at is not None
        and app.pdf_updated_at >= app.rendered_updated_at
    )
    os.makedirs(storage_root, exist_ok=True)
    rel_path = f"{app.id}.pdf"
    abs_path = os.path.join(storage_root, rel_path)
    if use_existing and os.path.isfile(abs_path):
        return abs_path
    pdf_bytes = generate_application_pdf(app.rendered_text, app_id=app.id)
    with open(abs_path, "wb") as f:
        f.write(pdf_bytes)
    app.pdf_path = rel_path
    app.pdf_updated_at = now
    return abs_path
