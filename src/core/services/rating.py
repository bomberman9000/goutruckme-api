from datetime import datetime
from sqlalchemy import select, func, or_
from src.core.models import CompanyDetails, Cargo, CargoStatus, Claim, ClaimStatus


async def recalculate_rating(session, company_id: int):
    """Пересчитывает рейтинг компании"""
    company = await session.scalar(
        select(CompanyDetails).where(CompanyDetails.id == company_id)
    )
    if not company:
        return

    # Опыт > 1 года
    if company.registered_at:
        days = (datetime.utcnow() - company.registered_at).days
        company.rating_experience = 1 if days > 365 else 0

    # Завершённые сделки
    deals = await session.scalar(
        select(func.count())
        .select_from(Cargo)
        .where(
            or_(
                Cargo.owner_id == company.user_id,
                Cargo.carrier_id == company.user_id,
            ),
            Cargo.status == CargoStatus.COMPLETED,
        )
    )
    deals = deals or 0
    if deals >= 50:
        company.rating_deals_completed = 2
    elif deals >= 10:
        company.rating_deals_completed = 1
    else:
        company.rating_deals_completed = 0

    # Претензии
    open_claims = await session.scalar(
        select(func.count())
        .select_from(Claim)
        .where(
            Claim.to_company_id == company_id,
            Claim.status == ClaimStatus.OPEN,
        )
    )
    resolved_claims = await session.scalar(
        select(func.count())
        .select_from(Claim)
        .where(
            Claim.to_company_id == company_id,
            Claim.status == ClaimStatus.RESOLVED,
        )
    )

    open_claims = open_claims or 0
    resolved_claims = resolved_claims or 0
    if open_claims == 0 and resolved_claims == 0:
        company.rating_no_claims = 1
    elif open_claims > 0:
        company.rating_no_claims = -1 * min(2, open_claims)
    else:
        company.rating_no_claims = 0

    await session.commit()
