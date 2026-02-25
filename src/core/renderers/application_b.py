"""
Рендер текста заявки B (Экспедитор ⇄ Перевозчик) из Jinja2-шаблона.
Все реквизиты — из parties (forwarder, carrier); при отсутствии поля — «—».
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.models import Cargo


def _deal_context(cargo: Cargo | object, app_id: int | None = None) -> dict:
    """Собирает контекст deal для шаблона из Cargo или объекта с теми же атрибутами. Поля без маппинга — «—»."""
    load_date = getattr(cargo, "load_date", None)
    load_date_str = load_date.strftime("%d.%m.%Y") if load_date else "—"
    if getattr(cargo, "load_time", None):
        load_date_str += f" {cargo.load_time}"
    price = getattr(cargo, "actual_price", None) if getattr(cargo, "actual_price", None) is not None else getattr(cargo, "price", None)
    weight = getattr(cargo, "weight", None)
    weight_kg = int(weight * 1000) if weight is not None else None
    return {
        "app_id": app_id,
        "number_a": None,
        "number_b": None,
        "load_date": load_date_str,
        "load_address": getattr(cargo, "load_address", None) or getattr(cargo, "from_city", None) or "—",
        "load_contact_name": getattr(cargo, "load_contact_name", None) or "—",
        "load_contact_phone": getattr(cargo, "load_contact_phone", None) or "",
        "unload_address": getattr(cargo, "unload_address", None) or getattr(cargo, "to_city", None) or "—",
        "unload_contact_name": getattr(cargo, "unload_contact_name", None) or "—",
        "unload_contact_phone": getattr(cargo, "unload_contact_phone", None) or "",
        "unload_date": getattr(cargo, "unload_date", None) or "—",
        "cargo_name": getattr(cargo, "cargo_type", None) or getattr(cargo, "cargo_name", None) or "—",
        "cargo_weight_kg": weight_kg,
        "cargo_packages": getattr(cargo, "cargo_packages", None),
        "cargo_volume_m3": getattr(cargo, "volume", None),
        "client_price": price,
        "carrier_price": price,
        "price": price,
        "payment_vat_text": getattr(cargo, "payment_vat_text", None) or "без НДС",
        "payment_terms": getattr(cargo, "payment_terms", None) or "—",
        "payer_company_name": getattr(cargo, "payer_company_name", None) or "—",
    }


def _empty_party() -> dict:
    """Пустая структура стороны (company, bank, contact, driver) для подстановки «—» в шаблоне."""
    return {
        "company": {"name": None, "inn": None, "kpp": None, "ogrn": None, "legal_address": None, "ati_code": None},
        "bank": {"name": None, "bik": None, "account": None, "corr_account": None},
        "contact": {"name": None, "phone": None, "email": None},
        "driver": {"name": None, "passport": None, "phone": None, "vehicle": None},
    }


def render_application_b(deal: Cargo | dict, parties: dict, *, app_id: int | None = None) -> str:
    """
    parties: {"forwarder": payload, "carrier": payload}
    payload — dict с ключами company, bank, contact, driver (все данные только из parties).
    """
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(disabled_extensions=("txt",), default_for_string=True),
    )
    env.filters["default_dash"] = lambda v: v if v not in (None, "") else "—"
    template = env.get_template("application_b.txt")

    if isinstance(deal, dict):
        deal_ctx = {**deal}
        if app_id is not None:
            deal_ctx["app_id"] = app_id
    else:
        deal_ctx = _deal_context(deal, app_id=app_id)

    forwarder = parties.get("forwarder") or _empty_party()
    carrier = parties.get("carrier") or _empty_party()

    return template.render(
        deal=deal_ctx,
        forwarder=forwarder,
        carrier=carrier,
        signatures_block="",
    )
