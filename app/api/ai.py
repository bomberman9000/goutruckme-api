from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ai.ai_service import AIUnavailableError, ai_service


router = APIRouter()


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
