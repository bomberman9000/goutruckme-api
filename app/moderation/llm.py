"""
Optional local LLM adapter for moderation.
Uses Ollama if OLLAMA_BASE_URL is configured, otherwise returns None.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.moderation.flags import normalize_flags


_ALLOWED_RISK = {"low", "medium", "high"}
_OLLAMA_TEMPERATURE = 0.15
_OLLAMA_NUM_PREDICT = 400

_SYSTEM_PROMPT = """
Ты модератор логистической биржи. Твоя задача — оценить риск мошенничества/проблем по сделке или документу.
Верни ТОЛЬКО валидный JSON без пояснений.

Требования:
- risk_level: "low" | "medium" | "high"
- flags: массив строк (snake_case), без пробелов
- comment: коротко 1–2 предложения на русском
- recommended_action: коротко 1 предложение на русском (что делать оператору)
- confidence: число 0..1
- model_used: строка (укажи название модели, если знаешь, иначе "llm")

Используй flags только из списка:
high_price_outlier, low_price_outlier, prepay_100, cash_only, no_documents, no_contract, urgent_pressure, contact_mismatch, new_company, low_trust_counterparty, route_inconsistent, weight_volume_inconsistent, body_type_mismatch, doc_empty_or_missing, doc_duplicate_hash, doc_type_mismatch, suspicious_words, insufficient_data

Если данных недостаточно — ставь risk_level="medium", flags=["insufficient_data"], comment="Недостаточно данных для уверенной оценки.", recommended_action="Запросить недостающие сведения и проверить контрагента.", confidence=0.35

Оценивай по признакам:
- предоплата 100%, “срочно”, “только наличка”, “без документов”, “без договора”
- ставка сильно выше/ниже рынка
- подозрительные формулировки, уклонение от конкретики
- несостыковки в маршруте/весе/датах/типе кузова
- новый контрагент без истории + агрессивные условия
- документ пустой/битый/повторяющийся/не соответствует сделке

OUTPUT FORMAT (строго):
{
  "risk_level": "...",
  "flags": ["..."],
  "comment": "...",
  "recommended_action": "...",
  "confidence": 0.0,
  "model_used": "llm"
}
""".strip()


def _build_user_prompt(entity_type: str, entity_id: int, payload: Any) -> str:
    payload_json = json.dumps(
        payload if payload is not None else {},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    return (
        "Контекст проверки:\n"
        f'entity_type: {entity_type or "deal"}\n'
        f"entity_id: {int(entity_id or 0)}\n\n"
        "Данные (JSON):\n"
        f"{payload_json}\n\n"
        "Верни только JSON по формату."
    )


def _extract_first_json(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None

    candidate = text.strip()
    # Strip fenced markdown if model returned ```json ... ```
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if "\n" in candidate:
            candidate = candidate.split("\n", 1)[1]

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    snippet = candidate[start : end + 1]
    try:
        parsed = json.loads(snippet)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        return None
    return None


def _normalize_result(parsed: dict[str, Any], model: str) -> Optional[dict[str, Any]]:
    risk_level = str(parsed.get("risk_level") or "").strip().lower()
    if risk_level not in _ALLOWED_RISK:
        return None

    raw_flags = parsed.get("flags")
    if isinstance(raw_flags, list):
        flags = [str(item).strip() for item in raw_flags if str(item or "").strip()]
    else:
        flags = []
    flags = normalize_flags(flags)

    comment = str(parsed.get("comment") or "").strip()
    recommended_action = str(parsed.get("recommended_action") or "").strip()
    confidence_raw = parsed.get("confidence")
    confidence: float | None = None
    try:
        if confidence_raw is not None:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
    except Exception:
        confidence = None
    if confidence is None:
        confidence = 0.35 if "insufficient_data" in flags else 0.5

    model_used = str(parsed.get("model_used") or "").strip() or model or "llm"

    return {
        "risk_level": risk_level,
        "flags": flags,
        "comment": comment,
        "recommended_action": recommended_action,
        "confidence": round(confidence, 3),
        "model_used": model_used,
    }


def _chat_ollama(base_url: str, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
    chat_url = f"{base_url.rstrip('/')}/api/chat"
    generate_url = f"{base_url.rstrip('/')}/api/generate"

    with httpx.Client(timeout=20.0) as client:
        try:
            chat_payload = {
                "model": model,
                "stream": False,
                "options": {
                    "temperature": _OLLAMA_TEMPERATURE,
                    "num_predict": _OLLAMA_NUM_PREDICT,
                },
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {"role": "user", "content": user_prompt},
                ],
            }
            response = client.post(chat_url, json=chat_payload)
            response.raise_for_status()
            data = response.json()
            message = data.get("message") or {}
            content = str(message.get("content") or "").strip()
            if content:
                return content
        except Exception:
            # Fallback to /api/generate for older Ollama setups.
            pass

        try:
            generate_payload = {
                "model": model,
                "stream": False,
                "options": {
                    "temperature": _OLLAMA_TEMPERATURE,
                    "num_predict": _OLLAMA_NUM_PREDICT,
                },
                "prompt": f"{system_prompt}\n\n{user_prompt}",
            }
            response = client.post(generate_url, json=generate_payload)
            response.raise_for_status()
            data = response.json()
            content = str(data.get("response") or "").strip()
            if content:
                return content
        except Exception:
            return None

    return None


def llm_analyze(text: str) -> Optional[dict[str, Any]]:
    """
    Returns normalized moderation result or None if Ollama is disabled/unavailable.
    """
    settings = get_settings()
    base_url = (getattr(settings, "OLLAMA_BASE_URL", "") or os.getenv("OLLAMA_BASE_URL", "")).strip()
    if not base_url:
        return None

    model = (getattr(settings, "OLLAMA_MODEL", "") or os.getenv("OLLAMA_MODEL", "llama3.1")).strip() or "llama3.1"

    content = _chat_ollama(
        base_url=base_url,
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=text[:7000],
    )
    if not content:
        return None

    parsed = _extract_first_json(content)
    if not parsed:
        return None

    return _normalize_result(parsed, model)


def llm_analyze_review(entity_type: str, entity_id: int, payload: Any) -> Optional[dict[str, Any]]:
    """
    Structured moderation entrypoint that uses strict JSON prompt template.
    """
    user_prompt = _build_user_prompt(entity_type=entity_type, entity_id=entity_id, payload=payload)
    return llm_analyze(user_prompt)
