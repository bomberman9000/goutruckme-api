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
from src.core.models import User, Cargo, CargoStatus, Report, Rating, ChatMessage, Feedback

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="src/admin/templates")

def _ctx(request: Request, **kwargs):
    return {"request": request, "bot_username": settings.bot_username, **kwargs}

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
            "new_users": new_users,
            "new_cargos": new_cargos,
            "revenue": total_revenue
        },
        "recent_cargos": recent_cargos
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
