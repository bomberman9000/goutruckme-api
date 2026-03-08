"""Admin action audit log service."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)

_dsn: str = ""


def configure(database_url: str) -> None:
    global _dsn
    _dsn = database_url.replace("postgresql://", "postgres://", 1)


async def log_action(
    *,
    actor_tg_id: int,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    result: str = "ok",
    metadata: dict | None = None,
) -> None:
    if not _dsn:
        return
    try:
        conn = await asyncpg.connect(_dsn, timeout=5)
        await conn.execute(
            """
            INSERT INTO admin_actions
                (actor_tg_id, action, target_type, target_id, result, metadata_json, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            actor_tg_id,
            action,
            target_type,
            target_id,
            result,
            json.dumps(metadata) if metadata else None,
            datetime.utcnow(),
        )
        await conn.close()
    except Exception as exc:
        logger.warning("admin_audit.log_action failed: %s", exc)
