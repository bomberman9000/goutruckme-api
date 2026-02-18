"""Утилиты для записи событий аудита (audit_events)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import json

from sqlalchemy.orm import Session

from app.models.models import AuditEvent


def audit_log(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    actor_role: str = "system",
    actor_user_id: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Записать событие аудита.

    actor_* пишем и в отдельные поля, и дублируем в meta_json,
    чтобы было удобно и фильтровать, и смотреть «сырые» данные.
    """
    meta_payload: Dict[str, Any] = meta or {}
    # Сохраняем actor_* внутрь meta для консистентности
    if actor_role is not None:
        meta_payload.setdefault("actor_role", actor_role)
    if actor_user_id is not None:
        meta_payload.setdefault("actor_user_id", actor_user_id)

    ev = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_role=actor_role,
        actor_user_id=actor_user_id,
        meta_json=json.dumps(meta_payload, ensure_ascii=False),
        created_at=datetime.now(timezone.utc),
    )
    db.add(ev)
