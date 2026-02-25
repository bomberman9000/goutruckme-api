from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from src.core.config import settings
from src.core.services.ai_service import ai_service


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScoreResult:
    inn: str | None
    score: int
    verdict: str
    comment: str
    provider: str
    details: dict[str, Any]


def _parse_registration_date(raw: Any) -> datetime | None:
    if raw is None:
        return None
    # Dadata uses milliseconds unix timestamp.
    try:
        ts = int(raw)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _heuristic_score(*, age_years: float | None, capital: int | None, is_liquidating: bool | None) -> tuple[int, str]:
    score = 55
    reasons: list[str] = []

    if age_years is None:
        reasons.append("возраст компании неизвестен")
    elif age_years < 0.5:
        score -= 25
        reasons.append("компания младше 6 месяцев")
    elif age_years < 1:
        score -= 15
        reasons.append("компания младше 1 года")
    elif age_years >= 3:
        score += 10
        reasons.append("компания старше 3 лет")

    if capital is None:
        reasons.append("капитал неизвестен")
    elif capital <= 10000:
        score -= 10
        reasons.append("низкий уставный капитал")
    elif capital >= 1_000_000:
        score += 10
        reasons.append("высокий уставный капитал")

    if is_liquidating:
        score -= 40
        reasons.append("есть признаки ликвидации/прекращения")

    score = max(0, min(100, score))
    return score, "; ".join(reasons[:3]) if reasons else "базовая проверка"


def _extract_json_block(text: str) -> dict[str, Any] | None:
    payload = (text or "").strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", payload, flags=re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


async def _fetch_company_profile(inn: str) -> tuple[dict[str, Any], str]:
    token = (settings.dadata_api_token or "").strip()
    if not token:
        # Offline deterministic fallback to keep worker fully functional without provider.
        seed = _safe_int(inn[-2:], default=42)
        age_years = round((seed % 12) / 2, 2)
        capital = 10_000 if seed % 3 else 1_000_000
        return {
            "age_years": age_years,
            "capital": capital,
            "is_liquidating": bool(seed % 11 == 0),
            "name": None,
            "source": "stub",
        }, "stub"

    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"query": inn}
    timeout = max(2, int(settings.parser_scoring_timeout_sec))

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(settings.dadata_api_url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    suggestions = data.get("suggestions") if isinstance(data, dict) else None
    if not suggestions:
        return {"age_years": None, "capital": None, "is_liquidating": None, "name": None, "source": "dadata"}, "dadata"

    item = suggestions[0] if isinstance(suggestions, list) else {}
    item_data = item.get("data") if isinstance(item, dict) else {}
    state = item_data.get("state") if isinstance(item_data, dict) else {}

    registration_date = _parse_registration_date(state.get("registration_date"))
    age_years = None
    if registration_date:
        age_years = max(0.0, round((datetime.now(UTC) - registration_date).days / 365, 2))

    return {
        "age_years": age_years,
        "capital": _safe_int(item_data.get("capital")),
        "is_liquidating": bool(
            state.get("status") in {"LIQUIDATING", "LIQUIDATED"}
            or item_data.get("state", {}).get("actuality_date") in (0,)
        ),
        "name": item.get("value"),
        "source": "dadata",
    }, "dadata"


async def _ai_adjust_score(base: ScoreResult) -> ScoreResult:
    prompt = (
        "Ты антифрод-модуль в логистике. Верни JSON: "
        '{"score":0-100,"verdict":"green|yellow|red","comment":"..."} '
        f"Данные: {json.dumps(base.details, ensure_ascii=False)}. "
        f"Текущий score={base.score}, verdict={base.verdict}."
    )

    model_override = settings.parser_scoring_ai_model.strip() or None
    try:
        ai_response = await asyncio.to_thread(
            ai_service.ask,
            prompt=prompt,
            model_override=model_override,
            max_tokens=180,
            temperature=0.1,
        )
    except Exception as exc:
        logger.warning("scoring.ai.failed inn=%s error=%s", base.inn, str(exc)[:160])
        return base

    payload = _extract_json_block(ai_response.get("text", ""))
    if not payload:
        return base

    score = _safe_int(payload.get("score"), default=base.score)
    score = max(0, min(100, score))
    verdict = str(payload.get("verdict") or base.verdict).lower().strip()
    if verdict not in {"green", "yellow", "red"}:
        verdict = base.verdict
    comment = str(payload.get("comment") or base.comment).strip()[:300]

    return ScoreResult(
        inn=base.inn,
        score=score,
        verdict=verdict,
        comment=comment or base.comment,
        provider=base.provider,
        details={**base.details, "ai_used": True, "ai_model": ai_response.get("model")},
    )


async def get_score(inn: str | None) -> ScoreResult:
    normalized = "".join(ch for ch in str(inn or "") if ch.isdigit())
    if len(normalized) not in (10, 12):
        return ScoreResult(
            inn=None,
            score=35,
            verdict="red",
            comment="ИНН не найден или некорректен",
            provider="none",
            details={"reason": "invalid_inn"},
        )

    profile, provider = await _fetch_company_profile(normalized)
    score, comment = _heuristic_score(
        age_years=profile.get("age_years"),
        capital=profile.get("capital"),
        is_liquidating=profile.get("is_liquidating"),
    )
    base = ScoreResult(
        inn=normalized,
        score=score,
        verdict="green" if score >= 70 else ("yellow" if score >= 40 else "red"),
        comment=comment,
        provider=provider,
        details=profile,
    )

    if settings.parser_scoring_enable_ai:
        return await _ai_adjust_score(base)
    return base
