from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from src.core.config import settings
from src.core.schemas.sync import SharedSyncEvent


logger = logging.getLogger(__name__)


def make_search_id() -> str:
    return uuid.uuid4().hex


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _internal_headers() -> dict[str, str]:
    token = (settings.internal_token or "").strip() or (settings.internal_api_token or "").strip()
    if not token:
        return {}
    return {"X-Internal-Token": token}


async def publish_sync_event(event: SharedSyncEvent) -> bool:
    url = _join_url(settings.gruzpotok_api_internal_url, settings.gruzpotok_sync_path)
    if not url:
        return False

    timeout = max(1, int(settings.internal_http_timeout))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=_internal_headers(), json=event.model_dump(mode="json"))
            response.raise_for_status()
        logger.info("sync.publish event_type=%s event_id=%s", event.event_type, event.event_id)
        return True
    except Exception as exc:
        logger.warning("sync.publish.failed event_type=%s error=%s", event.event_type, str(exc)[:200])
        return False


async def verify_gruzpotok_login_token(token: str, telegram_user_id: int) -> dict[str, Any]:
    path = settings.gruzpotok_verify_login_path
    url = _join_url(settings.gruzpotok_api_internal_url, path)
    if not url:
        return {"ok": False, "error": "gruzpotok_internal_url_not_configured"}

    timeout = max(1, int(settings.internal_http_timeout))
    payload = {"token": token, "telegram_user_id": telegram_user_id}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=_internal_headers(), json=payload)
            response.raise_for_status()
            data = response.json() if response.content else {}
    except Exception as exc:
        return {"ok": False, "error": f"verify_failed:{str(exc)[:120]}"}

    if not isinstance(data, dict):
        return {"ok": False, "error": "invalid_verify_payload"}

    data.setdefault("ok", False)
    return data


async def create_gruzpotok_login_link(
    *,
    telegram_user_id: int,
    search_id: str | None = None,
    redirect_path: str | None = None,
) -> str | None:
    path = settings.gruzpotok_create_magic_link_path or settings.gruzpotok_create_login_path
    url = _join_url(settings.gruzpotok_api_internal_url, path)
    if not url:
        return None

    payload: dict[str, Any] = {"telegram_user_id": telegram_user_id}
    if search_id:
        payload["search_id"] = search_id
    if redirect_path:
        payload["redirect_path"] = redirect_path

    timeout = max(1, int(settings.internal_http_timeout))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=_internal_headers(), json=payload)
            response.raise_for_status()
            data = response.json() if response.content else {}
    except Exception as exc:
        logger.warning("sync.login_link.failed error=%s", str(exc)[:200])
        return None

    if not isinstance(data, dict):
        return None

    for key in ("web_url", "login_url", "url", "redirect_url"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    token = data.get("token")
    if isinstance(token, str) and token.strip():
        base_url = (settings.gruzpotok_public_url or settings.webapp_url or "").strip().rstrip("/")
        if base_url:
            return f"{base_url}/auth/magic?token={token.strip()}"
    return None
