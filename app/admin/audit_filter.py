"""
Фильтрация событий аудита по группам action (GET /admin/applications/{app_id}).
Чекбоксы ev_sent, ev_signed, ev_error → actions_allowed (OR по группам).
"""
from fastapi import Request

SENT_ACTIONS = {
    "sent", "sent_to_client", "sent_to_carrier", "sent_to_forwarder",
}
SIGNED_ACTIONS = {
    "signed", "signed_by_client", "signed_by_forwarder", "signed_by_carrier",
}
ERROR_ACTIONS = {"error", "pdf_error", "send_error"}


def _bool_q(v: str | None) -> bool:
    return v in ("1", "true", "yes", "on")


def selected_groups(request: Request) -> dict:
    """Query ev_sent, ev_signed, ev_error → dict sent/signed/error."""
    qp = request.query_params
    return {
        "sent": _bool_q(qp.get("ev_sent")),
        "signed": _bool_q(qp.get("ev_signed")),
        "error": _bool_q(qp.get("ev_error")),
    }


def actions_from_groups(sel: dict) -> set[str] | None:
    """Выбранные группы → set(action) для WHERE; None = показывать всё."""
    actions = set()
    if sel["sent"]:
        actions |= SENT_ACTIONS
    if sel["signed"]:
        actions |= SIGNED_ACTIONS
    if sel["error"]:
        actions |= ERROR_ACTIONS
    return actions if actions else None
