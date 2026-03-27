from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.core.services.ai_service import AIUnavailableError, ai_service
from src.services.ai_kimi import kimi_service
from src.services.ai_limits import (
    check_and_increment,
    get_ai_history,
    get_remaining,
    is_premium_user,
    log_ai_request,
    FREE_DAILY_LIMIT,
)


router = APIRouter(tags=["ai"])


class AskRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    system_prompt: str | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)


class AskResponse(BaseModel):
    text: str
    model: str
    source: str


def check_local_available() -> bool:
    return ai_service.check_local_available()


@router.post("/ai/ask", response_model=AskResponse)
def ask_ai(request: AskRequest) -> AskResponse:
    try:
        result = ai_service.ask(
            prompt=request.prompt,
            model_override=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            system_prompt=request.system_prompt,
        )
        return AskResponse(**result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict()) from exc


# ---------------------------------------------------------------------------
# Kimi (OpenRouter) — специализированные режимы
# ---------------------------------------------------------------------------

class KimiRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    user_id: int | None = None  # optional; enables per-user daily rate limiting


async def _check_limit(user_id: int | None) -> None:
    """Raise 429 if user exceeded daily AI limit."""
    if user_id is None:
        return
    premium = await is_premium_user(user_id)
    allowed, remaining = await check_and_increment(user_id, is_premium=premium)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "AI_LIMIT_EXCEEDED",
                "message": f"Дневной лимит AI-запросов исчерпан (5/день). Остаток: {remaining}.",
                "remaining": remaining,
            },
        )


@router.post("/ai/logist")
async def ai_logist(request: KimiRequest):
    """Логист-режим: разбор заявки на перевозку."""
    await _check_limit(request.user_id)
    result = await kimi_service.logist_mode(request.text)
    if request.user_id:
        await log_ai_request(request.user_id, "logist", request.text, result)
    return result


@router.post("/ai/antifraud")
async def ai_antifraud(request: KimiRequest):
    """Антифрод-режим: оценка риска заявки."""
    await _check_limit(request.user_id)
    result = await kimi_service.antifraud_mode(request.text)
    if request.user_id:
        await log_ai_request(request.user_id, "antifraud", request.text, result)
    return result


@router.post("/ai/docs")
async def ai_docs(request: KimiRequest):
    """Документы-режим: генерация логистического документа."""
    await _check_limit(request.user_id)
    result = await kimi_service.docs_mode(request.text)
    if request.user_id:
        await log_ai_request(request.user_id, "docs", request.text, result)
    return result


@router.post("/ai/docs/pdf")
async def ai_docs_pdf(request: KimiRequest):
    """Генерирует документ через Kimi и возвращает готовый PDF."""
    await _check_limit(request.user_id)
    result = await kimi_service.docs_mode(request.text)
    if request.user_id:
        await log_ai_request(request.user_id, "docs_pdf", request.text, result)

    doc_text = result.get("text") or ""
    doc_type = result.get("type", "документ")
    if not doc_text:
        raise HTTPException(status_code=422, detail="AI не смог сгенерировать документ")

    try:
        from src.core.documents import generate_application_pdf
        pdf_bytes = generate_application_pdf(doc_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка генерации PDF: {e}") from e

    safe_type = doc_type.replace(" ", "_").replace("/", "-")
    filename = f"gotruckme_{safe_type}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ai/status")
async def ai_status(user_id: int | None = None, cargo_id: int | None = None):
    """Статус AI: лимиты пользователя, антифрод по грузу, конфигурация."""
    payload: dict = {
        "kimi": {
            "configured": bool(os.getenv("OPENROUTER_API_KEY", "")),
            "model": os.getenv("OPENROUTER_MODEL", "moonshotai/kimi-k2"),
        }
    }

    if user_id is not None:
        premium = await is_premium_user(user_id)
        remaining = await get_remaining(user_id)
        history = await get_ai_history(user_id)
        payload["limits"] = {
            "user_id": user_id,
            "remaining_today": remaining,
            "daily_limit": FREE_DAILY_LIMIT,
            "is_premium": premium,
        }
        payload["history"] = history

    if cargo_id is not None:
        from src.services.cargo_antifraud import get_antifraud_result
        af = await get_antifraud_result(cargo_id)
        payload["antifraud"] = af or {"status": "not_checked", "cargo_id": cargo_id}

    return payload
