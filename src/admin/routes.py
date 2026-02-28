import json
import uuid

import httpx
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from datetime import datetime, timedelta
from src.admin.auth import (
    verify_password, create_access_token, get_current_admin,
    ADMIN_PASSWORD_HASH
)
from src.core.config import settings
from src.core.database import async_session
from src.core.audit import log_audit_event
from src.core.services.banking import get_bank_client
from src.core.services.notification_dispatcher import (
    is_dispatch_muted,
    mute_dispatch,
    notify_matching_carriers,
)
from src.core.services.watchdog import watchdog
from src.core.models import (
    AuditEvent,
    User,
    Cargo,
    CargoPaymentStatus,
    CargoStatus,
    EscrowDeal,
    EscrowEvent,
    EscrowStatus,
    Report,
    Rating,
    ChatMessage,
    Feedback,
    ParserIngestEvent,
    UserWallet,
)

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/admin/templates")

def _ctx(request: Request, **kwargs):
    return {"request": request, "bot_username": settings.bot_username, **kwargs}


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _internal_headers() -> dict[str, str]:
    token = (settings.internal_token or "").strip() or (settings.internal_api_token or "").strip()
    return {"X-Internal-Token": token} if token else {}


def _build_retry_sync_payload(event: ParserIngestEvent) -> dict:
    order = {
        "from_city": event.from_city,
        "to_city": event.to_city,
        "price_rub": event.rate_rub or 1,
        "weight_t": event.weight_t or 0.0,
        "status": "active",
        "source": event.source or settings.parser_source_name,
    }

    if settings.parser_default_user_id:
        order["user_id"] = int(settings.parser_default_user_id)
    if event.body_type:
        order["body_type"] = event.body_type
    if event.inn:
        order["inn"] = event.inn
    if event.load_date:
        order["load_date"] = event.load_date
    if event.load_time:
        order["load_time"] = event.load_time
    if event.cargo_description:
        order["cargo_description"] = event.cargo_description
    if event.payment_terms:
        order["payment_terms"] = event.payment_terms
    if event.is_direct_customer is not None:
        order["is_direct_customer"] = event.is_direct_customer
    if event.dimensions:
        order["dimensions"] = event.dimensions
    if event.is_hot_deal:
        order["is_hot_deal"] = True
    if event.phone:
        order["phone"] = event.phone
    if event.suggested_response:
        order["suggested_response"] = event.suggested_response

    metadata = {
        "chat_id": event.chat_id,
        "message_id": event.message_id,
        "stream_entry_id": event.stream_entry_id,
        "phone": event.phone,
        "inn": event.inn,
        "body_type": event.body_type,
        "raw_text": event.raw_text[:2000] if event.raw_text else "",
        "load_date": event.load_date,
        "load_time": event.load_time,
        "cargo_description": event.cargo_description,
        "payment_terms": event.payment_terms,
        "is_direct_customer": event.is_direct_customer,
        "dimensions": event.dimensions,
        "is_hot_deal": event.is_hot_deal,
        "phone_blacklisted": event.phone_blacklisted,
    }

    if event.trust_score is not None:
        metadata["trust_score"] = event.trust_score
        metadata["trust_verdict"] = event.trust_verdict
        metadata["trust_comment"] = event.trust_comment
        metadata["trust_provider"] = event.provider

    return {
        "event_id": f"parser-retry-{uuid.uuid4().hex}",
        "event_type": "order.created",
        "source": event.source or settings.parser_source_name,
        "user_id": int(settings.parser_default_user_id) if settings.parser_default_user_id else None,
        "metadata": metadata,
        "order": order,
    }


def _safe_meta(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _apply_parser_filters(query, *, status: str | None, source: str | None):
    if status:
        query = query.where(ParserIngestEvent.status == status)
    if source:
        query = query.where(ParserIngestEvent.source == source)
    return query


def _time_window_start(window: str | None) -> datetime | None:
    if window == "1h":
        return datetime.utcnow() - timedelta(hours=1)
    if window == "24h":
        return datetime.utcnow() - timedelta(hours=24)
    if window == "7d":
        return datetime.utcnow() - timedelta(days=7)
    return None


def _apply_time_window(query, *, window: str | None):
    since = _time_window_start(window)
    if since is not None:
        query = query.where(ParserIngestEvent.created_at >= since)
    return query


async def _retry_sync_event(client: httpx.AsyncClient, event: ParserIngestEvent) -> None:
    if not event.from_city or not event.to_city:
        event.status = "manual_review"
        event.error = "retry_missing_route"
        return

    payload = _build_retry_sync_payload(event)
    sync_url = _join_url(settings.gruzpotok_api_internal_url, settings.gruzpotok_sync_path)

    try:
        response = await client.post(sync_url, headers=_internal_headers(), json=payload)
        response.raise_for_status()
        event.status = "synced"
        event.is_spam = False
        event.error = None
    except Exception as exc:
        event.status = "sync_failed"
        event.error = str(exc)[:255]


async def _ensure_wallet(session, user_id: int) -> UserWallet:
    wallet = await session.get(UserWallet, user_id)
    if wallet:
        return wallet
    wallet = UserWallet(user_id=user_id, balance_rub=0, frozen_balance_rub=0)
    session.add(wallet)
    await session.flush()
    return wallet


def _escrow_status_label(status: EscrowStatus | str | None) -> str:
    if isinstance(status, EscrowStatus):
        return status.value
    return str(status or "")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", _ctx(request))

@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username != settings.admin_username or not verify_password(password, ADMIN_PASSWORD_HASH):
        return templates.TemplateResponse("login.html", {
            **_ctx(request),
            "error": "Неверный логин или пароль"
        })
    
    token = create_access_token({"sub": username})
    response = RedirectResponse(url="/admin", status_code=302)
    response.set_cookie("admin_token", token, httponly=True, max_age=86400)
    return response

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie("admin_token")
    return response

@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, admin: dict = Depends(get_current_admin)):
    parser_metrics = await watchdog.collect_parser_metrics()

    async with async_session() as session:
        # Stats
        users_count = await session.scalar(select(func.count()).select_from(User))
        cargos_count = await session.scalar(select(func.count()).select_from(Cargo))
        active_cargos = await session.scalar(
            select(func.count()).select_from(Cargo)
            .where(Cargo.status.in_([CargoStatus.NEW, CargoStatus.IN_PROGRESS]))
        )
        reports_count = await session.scalar(
            select(func.count()).select_from(Report).where(Report.is_reviewed == False)
        )
        manual_review_count = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent).where(ParserIngestEvent.status == "manual_review")
        )
        
        # Recent activity
        week_ago = datetime.utcnow() - timedelta(days=7)
        new_users = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= week_ago)
        )
        new_cargos = await session.scalar(
            select(func.count()).select_from(Cargo).where(Cargo.created_at >= week_ago)
        )
        
        # Revenue (completed cargos)
        revenue_result = await session.execute(
            select(func.sum(Cargo.price))
            .where(Cargo.status == CargoStatus.COMPLETED)
        )
        total_revenue = revenue_result.scalar() or 0
        
        # Recent cargos
        recent_cargos = await session.execute(
            select(Cargo).order_by(desc(Cargo.created_at)).limit(5)
        )
        recent_cargos = recent_cargos.scalars().all()
    
    return templates.TemplateResponse("dashboard.html", {
        **_ctx(request),
        "admin": admin,
        "stats": {
            "users": users_count,
            "cargos": cargos_count,
            "active_cargos": active_cargos,
            "reports": reports_count,
            "manual_review": manual_review_count,
            "new_users": new_users,
            "new_cargos": new_cargos,
            "revenue": total_revenue
        },
        "recent_cargos": recent_cargos,
        "parser_metrics": parser_metrics,
    })

@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, admin: dict = Depends(get_current_admin), page: int = 1):
    limit = 20
    offset = (page - 1) * limit
    
    async with async_session() as session:
        total = await session.scalar(select(func.count()).select_from(User))
        result = await session.execute(
            select(User).order_by(desc(User.created_at)).offset(offset).limit(limit)
        )
        users = result.scalars().all()
    
    return templates.TemplateResponse("users.html", {
        **_ctx(request),
        "admin": admin,
        "users": users,
        "page": page,
        "total_pages": (total + limit - 1) // limit,
        "total": total
    })

@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # User's cargos
        cargos = await session.execute(
            select(Cargo).where(Cargo.owner_id == user_id).order_by(desc(Cargo.created_at)).limit(10)
        )
        cargos = cargos.scalars().all()
        
        # User's ratings
        avg_rating = await session.scalar(
            select(func.avg(Rating.score)).where(Rating.to_user_id == user_id)
        )
        
        # Reports against user
        reports = await session.execute(
            select(Report).where(Report.to_user_id == user_id).order_by(desc(Report.created_at))
        )
        reports = reports.scalars().all()
    
    return templates.TemplateResponse("user_detail.html", {
        **_ctx(request),
        "admin": admin,
        "user": user,
        "cargos": cargos,
        "avg_rating": round(avg_rating, 1) if avg_rating else None,
        "reports": reports
    })

@router.post("/users/{user_id}/ban")
async def ban_user(user_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.is_banned = True
            await session.commit()
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

@router.post("/users/{user_id}/unban")
async def unban_user(user_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.is_banned = False
            await session.commit()
    return RedirectResponse(url=f"/admin/users/{user_id}", status_code=302)

@router.get("/cargos", response_class=HTMLResponse)
async def cargos_list(request: Request, admin: dict = Depends(get_current_admin), page: int = 1, status: str = None):
    limit = 20
    offset = (page - 1) * limit
    
    async with async_session() as session:
        query = select(Cargo)
        count_query = select(func.count()).select_from(Cargo)
        
        if status:
            status_enum = CargoStatus(status)
            query = query.where(Cargo.status == status_enum)
            count_query = count_query.where(Cargo.status == status_enum)
        
        total = await session.scalar(count_query)
        result = await session.execute(
            query.order_by(desc(Cargo.created_at)).offset(offset).limit(limit)
        )
        cargos = result.scalars().all()
    
    return templates.TemplateResponse("cargos.html", {
        **_ctx(request),
        "admin": admin,
        "cargos": cargos,
        "page": page,
        "total_pages": (total + limit - 1) // limit,
        "total": total,
        "current_status": status,
        "statuses": [s.value for s in CargoStatus]
    })

@router.get("/reports", response_class=HTMLResponse)
async def reports_list(request: Request, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(
            select(Report).order_by(Report.is_reviewed, desc(Report.created_at)).limit(50)
        )
        reports = result.scalars().all()
        
        # Get user names
        user_ids = set()
        for r in reports:
            user_ids.add(r.from_user_id)
            user_ids.add(r.to_user_id)
        
        users_result = await session.execute(
            select(User).where(User.id.in_(user_ids))
        )
        users = {u.id: u for u in users_result.scalars().all()}
    
    return templates.TemplateResponse("reports.html", {
        **_ctx(request),
        "admin": admin,
        "reports": reports,
        "users": users
    })


@router.get("/manual-review", response_class=HTMLResponse)
async def manual_review_queue(
    request: Request,
    admin: dict = Depends(get_current_admin),
    page: int = 1,
):
    limit = 20
    offset = (page - 1) * limit

    async with async_session() as session:
        total = await session.scalar(
            select(func.count()).select_from(ParserIngestEvent).where(ParserIngestEvent.status == "manual_review")
        )
        result = await session.execute(
            select(ParserIngestEvent)
            .where(ParserIngestEvent.status == "manual_review")
            .order_by(desc(ParserIngestEvent.created_at))
            .offset(offset)
            .limit(limit)
        )
        events = result.scalars().all()

    return templates.TemplateResponse("manual_review.html", {
        **_ctx(request),
        "admin": admin,
        "events": events,
        "page": page,
        "total_pages": (total + limit - 1) // limit if total else 1,
        "total": total,
    })


@router.get("/parser", response_class=HTMLResponse)
async def parser_events(
    request: Request,
    admin: dict = Depends(get_current_admin),
    page: int = 1,
    status: str | None = None,
    source: str | None = None,
    window: str | None = "24h",
):
    limit = 25
    offset = (page - 1) * limit
    parser_metrics = await watchdog.collect_parser_metrics()

    async with async_session() as session:
        base_filters = _apply_time_window(
            _apply_parser_filters(
                select(ParserIngestEvent),
                status=None,
                source=source,
            ),
            window=window,
        )

        counts_rows = await session.execute(
            base_filters.with_only_columns(
                ParserIngestEvent.status,
                func.count(),
            )
            .group_by(ParserIngestEvent.status)
            .order_by(ParserIngestEvent.status)
        )
        status_counts = {
            row[0] or "unknown": row[1]
            for row in counts_rows.all()
        }

        reasons_rows = await session.execute(
            base_filters
            .where(ParserIngestEvent.error.is_not(None))
            .with_only_columns(
                ParserIngestEvent.error,
                func.count(),
            )
            .group_by(ParserIngestEvent.error)
            .order_by(func.count().desc(), ParserIngestEvent.error)
            .limit(8)
        )
        top_errors = [
            {"reason": row[0], "count": row[1]}
            for row in reasons_rows.all()
            if row[0]
        ]

        sources_rows = await session.execute(
            select(ParserIngestEvent.source)
            .distinct()
            .order_by(ParserIngestEvent.source)
        )
        sources = [row[0] for row in sources_rows.all() if row[0]]

        query = _apply_time_window(
            _apply_parser_filters(
                select(ParserIngestEvent),
                status=status,
                source=source,
            ),
            window=window,
        )
        count_query = _apply_time_window(
            _apply_parser_filters(
                select(func.count()).select_from(ParserIngestEvent),
                status=status,
                source=source,
            ),
            window=window,
        )

        total = await session.scalar(count_query)
        result = await session.execute(
            query
            .order_by(desc(ParserIngestEvent.created_at))
            .offset(offset)
            .limit(limit)
        )
        events = result.scalars().all()

    return templates.TemplateResponse("parser.html", {
        **_ctx(request),
        "admin": admin,
        "events": events,
        "page": page,
        "total_pages": (total + limit - 1) // limit if total else 1,
        "total": total,
        "status_counts": status_counts,
        "current_status": status,
        "current_source": source,
        "current_window": window or "all",
        "sources": sources,
        "parser_metrics": parser_metrics,
        "top_errors": top_errors,
    })


@router.post("/parser/bulk-retry-sync")
async def bulk_retry_parser_sync(
    admin: dict = Depends(get_current_admin),
    status: str = Form("sync_failed"),
    source: str | None = Form(None),
    window: str | None = Form("24h"),
):
    allowed = {"sync_failed", "error", "retry_queued"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported status")

    async with async_session() as session:
        rows = (
            await session.execute(
                _apply_time_window(
                    _apply_parser_filters(
                        select(ParserIngestEvent).where(ParserIngestEvent.status == status),
                        status=None,
                        source=source,
                    ),
                    window=window,
                ),
            )
            .order_by(desc(ParserIngestEvent.created_at))
            .limit(100)
        ).scalars().all()

        async with httpx.AsyncClient(timeout=max(3, int(settings.parser_http_timeout))) as client:
            for event in rows:
                await _retry_sync_event(client, event)

        await session.commit()

    redirect_url = "/admin/parser"
    if status or source or window:
        params = []
        if status:
            params.append(f"status={status}")
        if source:
            params.append(f"source={source}")
        if window:
            params.append(f"window={window}")
        redirect_url = f"{redirect_url}?{'&'.join(params)}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/parser/bulk-ignore")
async def bulk_ignore_parser_events(
    admin: dict = Depends(get_current_admin),
    status: str = Form("error"),
    source: str | None = Form(None),
    window: str | None = Form("24h"),
):
    async with async_session() as session:
        rows = (
            await session.execute(
                _apply_time_window(
                    _apply_parser_filters(
                        select(ParserIngestEvent).where(ParserIngestEvent.status == status),
                        status=None,
                        source=source,
                    ),
                    window=window,
                ),
            )
            .order_by(desc(ParserIngestEvent.created_at))
            .limit(200)
        ).scalars().all()

        for event in rows:
            event.status = "ignored"

        await session.commit()

    redirect_url = "/admin/parser"
    if status or source or window:
        params = []
        if status:
            params.append(f"status={status}")
        if source:
            params.append(f"source={source}")
        if window:
            params.append(f"window={window}")
        redirect_url = f"{redirect_url}?{'&'.join(params)}"
    return RedirectResponse(url=redirect_url, status_code=302)


@router.post("/manual-review/{event_id}/publish")
async def publish_manual_review(event_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, event_id)
        if event:
            event.status = "synced"
            event.is_spam = False
            await session.commit()
    return RedirectResponse(url="/admin/manual-review", status_code=302)


@router.post("/manual-review/{event_id}/dismiss")
async def dismiss_manual_review(event_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, event_id)
        if event:
            event.status = "ignored"
            await session.commit()
    return RedirectResponse(url="/admin/manual-review", status_code=302)


@router.post("/parser/{event_id}/retry-sync")
async def retry_parser_sync(event_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        async with httpx.AsyncClient(timeout=max(3, int(settings.parser_http_timeout))) as client:
            await _retry_sync_event(client, event)

        await session.commit()

    return RedirectResponse(url="/admin/parser", status_code=302)


@router.post("/parser/{event_id}/ignore")
async def ignore_parser_event(event_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        event = await session.get(ParserIngestEvent, event_id)
        if event:
            event.status = "ignored"
            await session.commit()
    return RedirectResponse(url="/admin/parser", status_code=302)


@router.get("/escrow", response_class=HTMLResponse)
async def escrow_console(
    request: Request,
    admin: dict = Depends(get_current_admin),
    page: int = 1,
    status: str | None = None,
):
    limit = 20
    offset = (page - 1) * limit

    async with async_session() as session:
        counts_rows = await session.execute(
            select(EscrowDeal.status, func.count())
            .group_by(EscrowDeal.status)
            .order_by(EscrowDeal.status)
        )
        status_counts = {
            (row[0].value if isinstance(row[0], EscrowStatus) else str(row[0] or "unknown")): row[1]
            for row in counts_rows.all()
        }

        query = select(EscrowDeal)
        count_query = select(func.count()).select_from(EscrowDeal)
        current_status = None
        if status:
            try:
                current_status = EscrowStatus(status)
            except ValueError:
                current_status = None
            if current_status is not None:
                query = query.where(EscrowDeal.status == current_status)
                count_query = count_query.where(EscrowDeal.status == current_status)

        total = await session.scalar(count_query)
        deals = (
            await session.execute(
                query.order_by(desc(EscrowDeal.created_at)).offset(offset).limit(limit)
            )
        ).scalars().all()

        cargo_ids = [int(deal.cargo_id) for deal in deals]
        user_ids = sorted({
            int(user_id)
            for deal in deals
            for user_id in (deal.client_id, deal.carrier_id)
            if user_id
        })

        cargos = {}
        users = {}
        if cargo_ids:
            cargo_rows = (await session.execute(select(Cargo).where(Cargo.id.in_(cargo_ids)))).scalars().all()
            cargos = {int(c.id): c for c in cargo_rows}
        if user_ids:
            user_rows = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
            users = {int(u.id): u for u in user_rows}

        notification_rows = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.action.like("notification_dispatch%"))
                .order_by(desc(AuditEvent.created_at))
                .limit(20)
            )
        ).scalars().all()

        escrow_audit_rows = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.action.like("escrow_%"))
                .order_by(desc(AuditEvent.created_at))
                .limit(20)
            )
        ).scalars().all()

    muted_cargo_ids = set()
    for row in notification_rows:
        cargo_id = int(row.entity_id)
        if await is_dispatch_muted(cargo_id):
            muted_cargo_ids.add(cargo_id)

    return templates.TemplateResponse("escrow.html", {
        **_ctx(request),
        "admin": admin,
        "deals": deals,
        "cargos": cargos,
        "users": users,
        "page": page,
        "total_pages": (total + limit - 1) // limit if total else 1,
        "total": total,
        "current_status": current_status.value if current_status else None,
        "status_counts": status_counts,
        "notification_logs": [
            {
                "row": row,
                "meta": _safe_meta(row.meta_json),
                "is_muted": int(row.entity_id) in muted_cargo_ids,
            }
            for row in notification_rows
        ],
        "escrow_logs": [
            {"row": row, "meta": _safe_meta(row.meta_json)}
            for row in escrow_audit_rows
        ],
        "escrow_status_label": _escrow_status_label,
    })


@router.post("/escrow/{deal_id}/release")
async def admin_release_escrow(deal_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        deal = await session.get(EscrowDeal, deal_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        cargo = await session.get(Cargo, int(deal.cargo_id))
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if deal.status != EscrowStatus.DELIVERY_MARKED:
            return RedirectResponse(url="/admin/escrow", status_code=302)

        payout = await get_bank_client(deal.provider).release_funds(
            escrow_id=int(deal.id),
            cargo_id=int(cargo.id),
            amount_rub=int(deal.carrier_amount_rub),
            carrier_user_id=int(deal.carrier_id or cargo.carrier_id or deal.client_id),
        )

        client_wallet = await _ensure_wallet(session, int(deal.client_id))
        carrier_wallet = await _ensure_wallet(session, int(deal.carrier_id or cargo.carrier_id or deal.client_id))

        client_wallet.balance_rub = max(int(client_wallet.balance_rub) - int(deal.amount_rub), 0)
        client_wallet.frozen_balance_rub = max(int(client_wallet.frozen_balance_rub) - int(deal.amount_rub), 0)
        carrier_wallet.balance_rub += int(deal.carrier_amount_rub)

        deal.status = EscrowStatus.RELEASED
        deal.released_at = datetime.utcnow()
        cargo.payment_status = CargoPaymentStatus.RELEASED
        cargo.payment_verified_at = cargo.payment_verified_at or datetime.utcnow()

        session.add(
            EscrowEvent(
                escrow_deal_id=int(deal.id),
                event_type="admin_released",
                actor_user_id=None,
                payload_json=json.dumps(
                    {
                        "provider": payout.provider,
                        "provider_payout_id": payout.provider_payout_id,
                        "actor": admin.get("username"),
                    },
                    ensure_ascii=False,
                ),
            )
        )
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=int(cargo.id),
            action="escrow_admin_released",
            actor_role="admin",
            meta={
                "escrow_id": int(deal.id),
                "provider": payout.provider,
                "provider_payout_id": payout.provider_payout_id,
                "carrier_amount_rub": int(deal.carrier_amount_rub),
            },
        )
        await session.commit()

    return RedirectResponse(url="/admin/escrow", status_code=302)


@router.post("/escrow/{deal_id}/dispute")
async def admin_dispute_escrow(deal_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        deal = await session.get(EscrowDeal, deal_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Escrow not found")
        cargo = await session.get(Cargo, int(deal.cargo_id))
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if deal.status in {EscrowStatus.RELEASED, EscrowStatus.CANCELLED, EscrowStatus.DISPUTED}:
            return RedirectResponse(url="/admin/escrow", status_code=302)

        deal.status = EscrowStatus.DISPUTED
        cargo.payment_status = CargoPaymentStatus.DISPUTED
        session.add(
            EscrowEvent(
                escrow_deal_id=int(deal.id),
                event_type="admin_disputed",
                actor_user_id=None,
                payload_json=json.dumps({"actor": admin.get("username")}, ensure_ascii=False),
            )
        )
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=int(cargo.id),
            action="escrow_disputed",
            actor_role="admin",
            meta={"escrow_id": int(deal.id)},
        )
        await session.commit()

    return RedirectResponse(url="/admin/escrow", status_code=302)


@router.post("/notifications/{cargo_id}/retry")
async def admin_retry_notification_dispatch(cargo_id: int, admin: dict = Depends(get_current_admin)):
    sent = await notify_matching_carriers(cargo_id, force=True)

    async with async_session() as session:
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=int(cargo_id),
            action="notification_dispatch_retried",
            actor_role="admin",
            meta={"sent_count": int(sent), "actor": admin.get("username")},
        )
        await session.commit()

    return RedirectResponse(url="/admin/escrow", status_code=302)


@router.post("/notifications/{cargo_id}/mute")
async def admin_mute_notification_dispatch(cargo_id: int, admin: dict = Depends(get_current_admin)):
    await mute_dispatch(cargo_id, ttl_sec=settings.admin_notification_mute_sec)

    async with async_session() as session:
        log_audit_event(
            session,
            entity_type="cargo",
            entity_id=int(cargo_id),
            action="notification_dispatch_muted_by_admin",
            actor_role="admin",
            meta={
                "mute_sec": int(settings.admin_notification_mute_sec),
                "actor": admin.get("username"),
            },
        )
        await session.commit()

    return RedirectResponse(url="/admin/escrow", status_code=302)

@router.post("/reports/{report_id}/review")
async def review_report(report_id: int, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(select(Report).where(Report.id == report_id))
        report = result.scalar_one_or_none()
        if report:
            report.is_reviewed = True
            await session.commit()
    return RedirectResponse(url="/admin/reports", status_code=302)

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_list(request: Request, admin: dict = Depends(get_current_admin)):
    async with async_session() as session:
        result = await session.execute(
            select(Feedback).order_by(desc(Feedback.created_at)).limit(50)
        )
        feedbacks = result.scalars().all()
    
    return templates.TemplateResponse("feedback.html", {
        **_ctx(request),
        "admin": admin,
        "feedbacks": feedbacks
    })
