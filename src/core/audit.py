"""Аудит-лог действий."""
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import AuditEvent


def log_audit_event(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Логирует событие в аудит."""
    log = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        meta_json=json.dumps(meta, ensure_ascii=False) if meta else None,
    )
    session.add(log)
