"""Снапшоты реквизитов сторон для заявок (company, bank, contact, driver)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import User, CompanyDetails


def _party_payload(
    user: User | None, company: CompanyDetails | None
) -> dict:
    """Вложенная структура для шаблонов заявок (forwarder/client/carrier)."""
    return {
        "company": {
            "name": company.company_name if company else None,
            "inn": company.inn if company else None,
            "kpp": company.kpp if company else None,
            "ogrn": company.ogrn if company else None,
            "legal_address": company.legal_address if company else None,
            "ati_code": None,
        },
        "bank": {
            "name": company.bank_name if company else None,
            "bik": company.bank_bik if company else None,
            "account": company.bank_account if company else None,
            "corr_account": company.bank_corr_account if company else None,
        },
        "contact": {
            "name": company.contact_person if company else None,
            "phone": company.contact_phone if company else None,
            "email": company.contact_email if company else None,
        },
        "driver": {
            "name": company.driver_name if company else None,
            "passport": company.driver_passport if company else None,
            "phone": company.driver_phone if company else None,
            "vehicle": company.vehicle_info if company else None,
        },
    }


async def make_party_snapshot(
    user_id: int, role: str, session: AsyncSession
) -> dict:
    """Снапшот реквизитов пользователя для шаблонов заявок."""
    user = await session.scalar(select(User).where(User.id == user_id))
    company = await session.scalar(
        select(CompanyDetails).where(CompanyDetails.user_id == user_id)
    )
    payload = _party_payload(user, company)
    payload["role"] = role
    payload["user_id"] = user_id
    if user:
        payload["full_name"] = user.full_name
        payload["phone"] = user.phone
    return payload
