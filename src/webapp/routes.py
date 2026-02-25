from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func

from src.core.config import settings
from src.core.database import async_session
from src.core.models import (
    Cargo,
    CargoStatus,
    CargoResponse,
    CompanyDetails,
    User,
    Claim,
    ClaimStatus,
)
from src.webapp.auth import validate_init_data

router = APIRouter(tags=["webapp"])
templates = Jinja2Templates(directory="src/webapp/templates")


def _get_webapp_url() -> str:
    """Base URL for WebApp links."""
    return "/webapp"


def _auth_user(init_data: str | None) -> dict:
    """Validate initData and return user dict or raise 401."""
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    user = validate_init_data(init_data)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid initData")
    return user


# --------------- HTML page ---------------

@router.get("/webapp", response_class=HTMLResponse)
async def webapp_page(request: Request):
    """Serve the WebApp SPA."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "bot_username": settings.bot_username or "",
    })


# --------------- JSON API ---------------

@router.get("/api/webapp/cargos")
async def webapp_cargos(
    from_city: str | None = None,
    to_city: str | None = None,
    min_weight: float | None = None,
    max_weight: float | None = None,
    limit: int = 30,
):
    """List active cargos with optional filters."""
    async with async_session() as session:
        query = (
            select(Cargo)
            .where(Cargo.status == CargoStatus.NEW)
            .order_by(Cargo.created_at.desc())
        )
        if from_city:
            query = query.where(
                Cargo.from_city.ilike(f"%{from_city}%")
            )
        if to_city:
            query = query.where(
                Cargo.to_city.ilike(f"%{to_city}%")
            )
        if min_weight is not None:
            query = query.where(Cargo.weight >= min_weight)
        if max_weight is not None:
            query = query.where(Cargo.weight <= max_weight)

        result = await session.execute(query.limit(min(limit, 50)))
        cargos = result.scalars().all()

        items = []
        for c in cargos:
            items.append({
                "id": c.id,
                "from_city": c.from_city,
                "to_city": c.to_city,
                "cargo_type": c.cargo_type,
                "weight": c.weight,
                "volume": c.volume,
                "price": c.price,
                "load_date": c.load_date.strftime("%d.%m.%Y"),
                "load_time": c.load_time,
                "comment": c.comment,
                "status": c.status.value,
                "created_at": c.created_at.isoformat(),
            })

    return {"cargos": items, "count": len(items)}


@router.get("/api/webapp/cargo/{cargo_id}")
async def webapp_cargo_detail(cargo_id: int):
    """Single cargo with owner company rating."""
    async with async_session() as session:
        cargo = await session.scalar(
            select(Cargo).where(Cargo.id == cargo_id)
        )
        if not cargo:
            raise HTTPException(status_code=404, detail="Not found")

        owner = await session.scalar(
            select(User).where(User.id == cargo.owner_id)
        )
        company = await session.scalar(
            select(CompanyDetails).where(
                CompanyDetails.user_id == cargo.owner_id
            )
        )

        responses_count = await session.scalar(
            select(func.count())
            .select_from(CargoResponse)
            .where(CargoResponse.cargo_id == cargo_id)
        )

        company_data = None
        if company:
            open_claims = await session.scalar(
                select(func.count())
                .select_from(Claim)
                .where(
                    Claim.to_company_id == company.id,
                    Claim.status == ClaimStatus.OPEN,
                )
            )
            company_data = {
                "id": company.id,
                "name": company.company_name,
                "inn": company.inn,
                "rating": company.total_rating,
                "open_claims": open_claims or 0,
            }

    return {
        "id": cargo.id,
        "from_city": cargo.from_city,
        "to_city": cargo.to_city,
        "cargo_type": cargo.cargo_type,
        "weight": cargo.weight,
        "volume": cargo.volume,
        "price": cargo.price,
        "load_date": cargo.load_date.strftime("%d.%m.%Y"),
        "load_time": cargo.load_time,
        "comment": cargo.comment,
        "status": cargo.status.value,
        "created_at": cargo.created_at.isoformat(),
        "owner": {
            "id": owner.id if owner else None,
            "name": owner.full_name if owner else "N/A",
        },
        "company": company_data,
        "responses_count": responses_count or 0,
    }


@router.post("/api/webapp/respond/{cargo_id}")
async def webapp_respond(
    cargo_id: int,
    x_telegram_init_data: str | None = Header(None),
):
    """Submit a response to a cargo (requires initData auth)."""
    tg_user = _auth_user(x_telegram_init_data)
    user_id = tg_user["id"]

    async with async_session() as session:
        cargo = await session.scalar(
            select(Cargo).where(Cargo.id == cargo_id)
        )
        if not cargo:
            raise HTTPException(status_code=404, detail="Cargo not found")
        if cargo.status != CargoStatus.NEW:
            raise HTTPException(
                status_code=400, detail="Cargo is not available"
            )
        if cargo.owner_id == user_id:
            raise HTTPException(
                status_code=400, detail="Cannot respond to own cargo"
            )

        existing = await session.scalar(
            select(CargoResponse).where(
                CargoResponse.cargo_id == cargo_id,
                CargoResponse.carrier_id == user_id,
            )
        )
        if existing:
            raise HTTPException(
                status_code=400, detail="Already responded"
            )

        resp = CargoResponse(
            cargo_id=cargo_id,
            carrier_id=user_id,
        )
        session.add(resp)
        await session.commit()

    return {"ok": True, "message": "Response submitted"}


@router.get("/api/webapp/profile")
async def webapp_profile(
    x_telegram_init_data: str | None = Header(None),
):
    """Current user profile with cargos and company info."""
    tg_user = _auth_user(x_telegram_init_data)
    user_id = tg_user["id"]

    async with async_session() as session:
        user = await session.scalar(
            select(User).where(User.id == user_id)
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        company = await session.scalar(
            select(CompanyDetails).where(
                CompanyDetails.user_id == user_id
            )
        )

        cargos_result = await session.execute(
            select(Cargo)
            .where(Cargo.owner_id == user_id)
            .order_by(Cargo.created_at.desc())
            .limit(10)
        )
        user_cargos = cargos_result.scalars().all()

        company_data = None
        if company:
            company_data = {
                "id": company.id,
                "name": company.company_name,
                "inn": company.inn,
                "rating": company.total_rating,
            }

    return {
        "user": {
            "id": user.id,
            "name": user.full_name,
            "username": user.username,
            "phone": user.phone,
            "is_carrier": user.is_carrier,
            "is_verified": user.is_verified,
        },
        "company": company_data,
        "cargos": [
            {
                "id": c.id,
                "from_city": c.from_city,
                "to_city": c.to_city,
                "weight": c.weight,
                "price": c.price,
                "status": c.status.value,
                "load_date": c.load_date.strftime("%d.%m.%Y"),
            }
            for c in user_cargos
        ],
    }
