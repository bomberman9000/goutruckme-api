"""Team management API — multi-account for logistics companies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.auth.telegram_tma import TelegramTMAUser, get_required_tma_user
from src.core.database import async_session
from src.core.models import TeamMember

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])

VALID_ROLES = {"admin", "manager", "carrier"}


class AddMemberRequest(BaseModel):
    company_inn: str
    user_id: int
    role: str = "carrier"
    name: str | None = None


class MemberResponse(BaseModel):
    id: int
    company_inn: str
    user_id: int
    role: str
    name: str | None
    is_active: bool


class TeamListResponse(BaseModel):
    members: list[MemberResponse] = Field(default_factory=list)


@router.post("/members", response_model=MemberResponse)
async def add_member(
    body: AddMemberRequest,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {VALID_ROLES}")

    async with async_session() as session:
        caller = await session.scalar(
            select(TeamMember).where(
                TeamMember.company_inn == body.company_inn,
                TeamMember.user_id == tma_user.user_id,
                TeamMember.role == "admin",
            )
        )
        if not caller:
            raise HTTPException(status_code=403, detail="only admins can add members")

        existing = await session.scalar(
            select(TeamMember).where(
                TeamMember.company_inn == body.company_inn,
                TeamMember.user_id == body.user_id,
            )
        )
        if existing:
            existing.role = body.role
            existing.name = body.name or existing.name
            existing.is_active = True
            await session.commit()
            await session.refresh(existing)
            m = existing
        else:
            m = TeamMember(
                company_inn=body.company_inn,
                user_id=body.user_id,
                role=body.role,
                name=body.name,
            )
            session.add(m)
            await session.commit()
            await session.refresh(m)

    return MemberResponse(
        id=m.id, company_inn=m.company_inn, user_id=m.user_id,
        role=m.role, name=m.name, is_active=m.is_active,
    )


@router.get("/members", response_model=TeamListResponse)
async def list_members(
    company_inn: str,
    tma_user: TelegramTMAUser = Depends(get_required_tma_user),
):
    async with async_session() as session:
        rows = (
            await session.execute(
                select(TeamMember).where(
                    TeamMember.company_inn == company_inn,
                    TeamMember.is_active.is_(True),
                ).order_by(TeamMember.role, TeamMember.id)
            )
        ).scalars().all()

    return TeamListResponse(
        members=[
            MemberResponse(
                id=m.id, company_inn=m.company_inn, user_id=m.user_id,
                role=m.role, name=m.name, is_active=m.is_active,
            )
            for m in rows
        ]
    )
