"""
Админка: карточка заявки с историей аудита и фильтром по группам action.
GET /admin/applications/{app_id} — фильтрация ev_sent, ev_signed, ev_error.
"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.models.models import AuditEvent, Deal, DealSync, Document, ModerationReview, User
from app.admin.audit_filter import selected_groups, actions_from_groups
from app.moderation.service import get_review, list_reviews, run_deal_review_background, set_review_pending


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    role = getattr(current_user.role, "value", current_user.role)
    normalized = str(role or "").strip().lower()
    if normalized != "admin" and not normalized.endswith("admin"):
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return current_user


router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin_user)])
_BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_BASE / "templates"))


@router.get("/applications", response_class=HTMLResponse)
def application_list(
    request: Request,
    db: Session = Depends(get_db),
):
    """Список заявок (документов)."""
    docs = db.query(Document).order_by(Document.created_at.desc()).limit(500).all()
    return templates.TemplateResponse(
        "application_list.html",
        {"request": request, "applications": docs},
    )


@router.get("/applications/{app_id}", response_class=HTMLResponse)
def application_detail(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Карточка заявки: данные + история аудита (application + deal) с фильтром по группам."""
    app = db.query(Document).filter(Document.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    deal = db.query(Deal).filter(Deal.id == app.deal_id).first()
    deal_id = app.deal_id

    sel = selected_groups(request)
    actions_allowed = actions_from_groups(sel)

    def query_audit(entity_type: str, entity_id: int, limit: int = 100):
        q = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == entity_id,
            )
        )
        if actions_allowed is not None:
            cond = AuditEvent.action.in_(actions_allowed)
            if sel.get("error"):
                # Ошибки: по action ИЛИ по meta_json (ключ error).
                # SQLite: LIKE без JSON1 (json_extract требует расширение JSON1).
                # Postgres: точная проверка по jsonb.
                dialect = db.get_bind().dialect.name
                if dialect == "sqlite":
                    cond = or_(
                        cond,
                        text("meta_json IS NOT NULL AND meta_json LIKE '%\"error\":%'"),
                    )
                else:
                    cond = or_(
                        cond,
                        text("(meta_json::jsonb)->'error' IS NOT NULL"),
                    )
            q = q.filter(cond)
        return q.order_by(AuditEvent.created_at.desc()).limit(limit).all()

    audit_app = query_audit("application", app_id)
    audit_deal = []
    if deal_id is not None and deal is not None:
        audit_deal = query_audit("deal", deal_id)

    # Объединённая лента: по created_at desc, лимит 200
    all_events = sorted(
        audit_app + audit_deal,
        key=lambda e: e.created_at or datetime.min,
        reverse=True,
    )[:200]

    # Строка query для ссылок (PDF, «к списку», return после POST)
    qp = request.query_params
    audit_filter_query = "&".join([f"{k}={v}" for k, v in qp.items()]) if qp else ""

    return templates.TemplateResponse(
        "application_detail.html",
        {
            "request": request,
            "app": app,
            "deal": deal,
            "audit_events": all_events,
            "audit_filter": sel,
            "audit_filter_query": audit_filter_query,
        },
    )


@router.get("/applications/{app_id}/pdf")
def application_pdf(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Скачать PDF; редирект обратно на карточку с теми же query-параметрами."""
    doc = db.query(Document).filter(Document.id == app_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if not doc.pdf_path:
        raise HTTPException(status_code=404, detail="PDF не сгенерирован")
    path = Path(doc.pdf_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF файл не найден")

    qp = request.query_params
    audit_filter_query = "&".join([f"{k}={v}" for k, v in qp.items()]) if qp else ""
    return_file = FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename=f"{doc.doc_type}_{doc.id}.pdf",
    )
    # Если нужен редирект после просмотра — фронт может открыть PDF в новой вкладке,
    # текущая остаётся на карточке. Отдаём файл напрямую.
    return return_file


@router.get("/deals", response_class=HTMLResponse)
def deals_sync_list(
    request: Request,
    db: Session = Depends(get_db),
    risk_level: str = None,
):
    """Список синхронизированных сделок (deal_sync) с бейджем AI Risk."""
    deals = db.query(DealSync).order_by(DealSync.updated_at.desc()).limit(500).all()
    review_map = {}
    for r in db.query(ModerationReview).filter(ModerationReview.entity_type == "deal").all():
        review_map[r.entity_id] = r
    if risk_level:
        deals = [d for d in deals if review_map.get(d.id) and review_map[d.id].risk_level == risk_level]
    return templates.TemplateResponse(
        "deals_sync_list.html",
        {"request": request, "deals": deals, "review_map": review_map, "risk_filter": risk_level},
    )


@router.get("/deals/{server_id}", response_class=HTMLResponse)
def deal_sync_detail(
    server_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Карточка сделки (deal_sync): данные + модерация + кнопка «Перезапустить модерацию»."""
    deal = db.query(DealSync).filter(DealSync.id == server_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")
    review = get_review(db, "deal", server_id)
    return templates.TemplateResponse(
        "deal_sync_detail.html",
        {"request": request, "deal": deal, "review": review},
    )


@router.post("/deals/{server_id}/run-moderation", response_class=RedirectResponse)
def deal_sync_run_moderation(
    server_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Запустить модерацию сделки (фоново) и вернуться на карточку."""
    deal = db.query(DealSync).filter(DealSync.id == server_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Сделка не найдена")
    set_review_pending(db, "deal", server_id)
    background_tasks.add_task(run_deal_review_background, server_id)
    return RedirectResponse(url=f"/admin/deals/{server_id}", status_code=303)


@router.get("/moderation", response_class=HTMLResponse)
def moderation_list(
    request: Request,
    db: Session = Depends(get_db),
    risk_level: str = None,
    entity_type: str = None,
    q: str = None,
):
    """Страница модерации: таблица отзывов с фильтрами."""
    reviews = list_reviews(db, risk_level=risk_level, entity_type=entity_type, search=q, limit=200)
    return templates.TemplateResponse(
        "moderation_list.html",
        {
            "request": request,
            "reviews": reviews,
            "risk_filter": risk_level,
            "entity_filter": entity_type,
            "search_q": q,
        },
    )


@router.post("/applications/{app_id}/send")
async def send_to_side(
    app_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Отправить стороне (заглушка); редирект на карточку с теми же query-параметрами."""
    form = await request.form()
    return_qs = form.get("return_qs") or request.query_params.get("return_qs") or ""
    url = f"/admin/applications/{app_id}"
    if return_qs:
        url = f"{url}?{return_qs}"
    return RedirectResponse(url=url, status_code=303)
