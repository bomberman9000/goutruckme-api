from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

from openai import AsyncOpenAI, APIError, APITimeoutError


logger = logging.getLogger(__name__)

KIMI_DEFAULT_MODEL = "moonshotai/kimi-k2"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_TOKENS = 1000
TIMEOUT = 45.0
CACHE_TTL = 3600  # 1 hour

SYSTEM_LOGIST = (
    "Ты AI-логист. Разбирай заявки на перевозку. "
    "Вес в тоннах — достаточная информация, отдельный объём в м³ не требуется. "
    "В questions задавай вопрос ТОЛЬКО если данные реально отсутствуют: нет маршрута, нет веса/тоннажа, нет типа ТС. "
    "Не задавай вопросы о том, что уже указано в заявке. "
    "Отвечай строго JSON без лишнего текста."
)

SYSTEM_ANTIFRAUD = (
    "Ты AI-антифрод система логистики. "
    "Оцени риск заявки. Отвечай строго JSON без лишнего текста."
)

SYSTEM_DOCS = (
    "Ты помощник по документообороту в логистике. "
    "Используй ТОЛЬКО данные из запроса пользователя — не выдумывай компании, ИНН, суммы, адреса. "
    "Если данных не хватает — ставь [УКАЗАТЬ] вместо выдуманных значений. "
    "Формируй профессиональный документ с правильной структурой. "
    "Отвечай строго JSON без лишнего текста."
)

SYSTEM_PRICE = (
    "Ты эксперт по логистическому ценообразованию в России. "
    "Анализируй рыночные данные и давай точные рекомендации по ставкам. "
    "Отвечай строго JSON без лишнего текста."
)


def _get_client() -> AsyncOpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=TIMEOUT,
    )


def _cache_key(mode: str, text: str) -> str:
    h = hashlib.sha256(f"{mode}:{text}".encode()).hexdigest()
    return f"ai_kimi:{mode}:{h}"


def _fix_json_newlines(s: str) -> str:
    """Fix literal newlines inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\' and i + 1 < len(s):
            result.append(ch)
            result.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ch == '\n':
            result.append('\\n')
        elif in_string and ch == '\r':
            result.append('\\r')
        elif in_string and ch == '\t':
            result.append('\\t')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract and parse JSON from model response."""
    text = text.strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    # Strip thinking tags (kimi-k2, deepseek-r1, gemini thinking mode)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

    def _try(s: str) -> dict | None:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_fix_json_newlines(s))
        except json.JSONDecodeError:
            pass
        return None

    result = _try(text)
    if result is not None:
        return result

    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        result = _try(match.group(1).strip())
        if result is not None:
            return result

    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        result = _try(match.group(0))
        if result is not None:
            return result

    # Last resort: extract type + text via regex
    type_m = re.search(r'"type"\s*:\s*"([^"]+)"', text)
    text_m = re.search(r'"text"\s*:\s*"([\s\S]+?)(?<!\\)"\s*[},]', text)
    if text_m:
        return {
            "type": type_m.group(1) if type_m else "unknown",
            "text": text_m.group(1).replace("\\n", "\n"),
        }

    raise ValueError(f"Cannot extract JSON from response: {text[:300]}")


class KimiAIService:
    """OpenRouter Kimi AI service with Redis caching and retry."""

    def __init__(self) -> None:
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        if self._redis is None:
            try:
                from src.core.redis import get_redis
                self._redis = await get_redis()
            except Exception as e:
                logger.warning("kimi.redis.unavailable error=%s", e)
        return self._redis

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        try:
            redis = await self._get_redis()
            if redis is None:
                return None
            data = await redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning("kimi.cache.get error=%s", e)
        return None

    async def _cache_set(self, key: str, value: dict[str, Any]) -> None:
        try:
            redis = await self._get_redis()
            if redis is None:
                return
            await redis.set(key, json.dumps(value, ensure_ascii=False), ex=CACHE_TTL)
        except Exception as e:
            logger.warning("kimi.cache.set error=%s", e)

    async def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_retries: int = 2,
        max_tokens: int = MAX_TOKENS,
    ) -> str:
        """Call OpenRouter API with retry on transient errors. Falls back to Gemini/Ollama."""
        # Gemini — primary; OpenRouter — secondary; Ollama — last resort
        try:
            return await _call_gemini_fallback(system, user, max_tokens=max_tokens)
        except Exception as eg:
            logger.warning("kimi.chat gemini_primary failed: %s, trying OpenRouter", eg)
        try:
            client = _get_client()
        except RuntimeError:
            logger.warning("kimi.chat openrouter_unavailable, trying Ollama")
            return await _call_ollama_fallback(system, user)
        model = os.getenv("OPENROUTER_MODEL", KIMI_DEFAULT_MODEL)
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    "kimi.chat model=%s temperature=%.1f prompt_len=%d attempt=%d",
                    model,
                    temperature,
                    len(user),
                    attempt,
                )
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = resp.choices[0].message.content or ""
                logger.info(
                    "kimi.chat.ok model=%s tokens=%s",
                    model,
                    resp.usage.total_tokens if resp.usage else "?",
                )
                return text
            except APITimeoutError as e:
                last_exc = e
                logger.warning("kimi.chat.timeout attempt=%d error=%s", attempt, e)
            except APIError as e:
                last_exc = e
                logger.error("kimi.chat.api_error attempt=%d status=%s error=%s", attempt, getattr(e, "status_code", "?"), e)
                if attempt >= max_retries:
                    raise
            except Exception as e:
                logger.error("kimi.chat.error attempt=%d error=%s", attempt, e)
                raise

        # All retries failed, try Gemini then Ollama
        logger.warning("kimi.chat all_retries_failed, trying Gemini fallback")
        try:
            return await _call_gemini_fallback(system, user, max_tokens=max_tokens)
        except Exception as ge:
            logger.warning("kimi.chat gemini_fallback failed: %s, trying Ollama", ge)
            return await _call_ollama_fallback(system, user)

    async def logist_mode(self, text: str) -> dict[str, Any]:
        """Parse freight order into structured fields."""
        cache_key = _cache_key("logist", text)
        if cached := await self._cache_get(cache_key):
            logger.info("kimi.logist cache_hit")
            return cached

        prompt = (
            "Разбери заявку на перевозку и верни JSON:\n"
            "{\n"
            '  "route": "маршрут из A в B",\n'
            '  "cargo": "описание груза",\n'
            '  "weight": "вес",\n'
            '  "volume": "объём",\n'
            '  "vehicle": "тип ТС",\n'
            '  "date": "дата/период",\n'
            '  "risks": ["риск1"],\n'
            '  "questions": ["вопрос1"]\n'
            "}\n\n"
            f"Заявка:\n{text}"
        )

        try:
            raw = await self.chat(system=SYSTEM_LOGIST, user=prompt, temperature=0.2)
            logger.info("kimi.logist.response len=%d", len(raw))
            result = _extract_json(raw)
            await self._cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error("kimi.logist.error error=%s", e)
            return {
                "route": "",
                "cargo": text[:100],
                "weight": "",
                "volume": "",
                "vehicle": "",
                "date": "",
                "risks": ["Не удалось проанализировать заявку"],
                "questions": [],
                "error": str(e),
            }

    async def antifraud_mode(self, text: str) -> dict[str, Any]:
        """Assess fraud risk of a freight order."""
        cache_key = _cache_key("antifraud", text)
        if cached := await self._cache_get(cache_key):
            logger.info("kimi.antifraud cache_hit")
            return cached

        prompt = (
            "Оцени риск мошенничества в заявке на перевозку и верни JSON:\n"
            "{\n"
            '  "risk_score": <число 0-100>,\n'
            '  "flags": ["флаг1"],\n'
            '  "explanation": "объяснение",\n'
            '  "recommendation": "accept | caution | reject"\n'
            "}\n\n"
            f"Заявка:\n{text}"
        )

        try:
            raw = await self.chat(system=SYSTEM_ANTIFRAUD, user=prompt, temperature=0.1)
            logger.info("kimi.antifraud.response len=%d", len(raw))
            result = _extract_json(raw)
            if "risk_score" in result:
                result["risk_score"] = int(float(result["risk_score"]))
            await self._cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error("kimi.antifraud.error error=%s", e)
            return {
                "risk_score": 50,
                "flags": ["Ошибка анализа"],
                "explanation": str(e),
                "recommendation": "caution",
                "error": str(e),
            }

    async def docs_mode(self, text: str) -> dict[str, Any]:
        """Generate a logistics document (contract, request, client message)."""
        cache_key = _cache_key("docs", text)
        if cached := await self._cache_get(cache_key):
            logger.info("kimi.docs cache_hit")
            return cached

        prompt = (
            "Создай логистический документ по данным пользователя. Верни JSON:\n"
            "{\n"
            '  "type": "договор | заявка | акт | сообщение_клиенту",\n'
            '  "text": "полный текст документа"\n'
            "}\n\n"
            "Правила:\n"
            "- Используй только данные из запроса, не придумывай реквизиты\n"
            "- Пропущенные поля замени на [УКАЗАТЬ]\n"
            "- Структура: заголовок, стороны, предмет договора, условия, реквизиты, подписи\n\n"
            f"Данные от пользователя:\n{text}"
        )

        try:
            raw = await self.chat(system=SYSTEM_DOCS, user=prompt, temperature=0.4, max_tokens=4000)
            logger.info("kimi.docs.response len=%d raw_preview=%s", len(raw), repr(raw[:200]))
            # If response is truncated (no closing ```), try to close it
            if raw.strip().startswith("```") and not raw.strip().endswith("```"):
                raw = raw.strip() + "\n```"
            result = _extract_json(raw)
            await self._cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error("kimi.docs.error error=%s raw=%s", e, repr(raw[:300]) if "raw" in dir() else "N/A")
            return {
                "type": "unknown",
                "text": "",
                "error": str(e),
            }


    async def price_mode(
        self,
        from_city: str,
        to_city: str,
        weight: float,
        vehicle: str = "тент",
        distance_km: int | None = None,
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """AI price recommendation combining market data + Kimi reasoning."""
        cache_key = _cache_key(
            "price",
            f"{from_city}:{to_city}:{weight}:{vehicle}:{distance_km}",
        )
        if cached := await self._cache_get(cache_key):
            logger.info("kimi.price cache_hit")
            return cached

        # Build market context string
        market_ctx = ""
        if market_data and market_data.get("available"):
            market_ctx = (
                f"\nРыночные данные за 30 дней:\n"
                f"- Средняя ставка: {market_data.get('current_avg', '?')} ₽\n"
                f"- Тренд: {market_data.get('trend', '?')} "
                f"({market_data.get('pct_change', 0):+.1f}%)\n"
                f"- Прогноз через 7 дней: {market_data.get('predicted_avg', '?')} ₽\n"
                f"- Кол-во сделок в базе: {market_data.get('data_points', 0)}\n"
                f"- Рекомендация рынка: {market_data.get('recommendation', '')}"
            )
        else:
            market_ctx = "\nРыночные данные: нет данных по этому маршруту в базе."

        dist_ctx = f"\nРасстояние: {distance_km} км" if distance_km else ""

        prompt = (
            f"Дай рекомендацию по ставке на перевозку.\n\n"
            f"Маршрут: {from_city} → {to_city}{dist_ctx}\n"
            f"Груз: {weight} т, транспорт: {vehicle}"
            f"{market_ctx}\n\n"
            "Верни JSON:\n"
            "{\n"
            '  "recommended_price": <число ₽>,\n'
            '  "min_price": <число ₽>,\n'
            '  "max_price": <число ₽>,\n'
            '  "price_per_km": <число ₽/км или null>,\n'
            '  "explanation": "краткое объяснение",\n'
            '  "confidence": "high|medium|low",\n'
            '  "market_comment": "комментарий о состоянии рынка"\n'
            "}"
        )

        try:
            raw = await self.chat(system=SYSTEM_PRICE, user=prompt, temperature=0.15)
            logger.info("kimi.price.response len=%d", len(raw))
            result = _extract_json(raw)
            # Coerce numeric fields
            for field in ("recommended_price", "min_price", "max_price"):
                if field in result:
                    result[field] = int(float(str(result[field]).replace(" ", "").replace("₽", "")))
            result.update(
                from_city=from_city,
                to_city=to_city,
                weight=weight,
                vehicle=vehicle,
                distance_km=distance_km,
            )
            # Short cache — prices change, 30 min is enough
            await self._cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error("kimi.price.error error=%s", e)
            return {
                "from_city": from_city,
                "to_city": to_city,
                "recommended_price": None,
                "min_price": None,
                "max_price": None,
                "price_per_km": None,
                "explanation": "Не удалось рассчитать ставку",
                "confidence": "low",
                "market_comment": "",
                "error": str(e),
            }



async def _call_gemini_fallback(system: str, user: str, max_tokens: int = MAX_TOKENS) -> str:
    """Fallback to Gemini when OpenRouter is unavailable."""
    import httpx, os
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No Gemini API key for fallback")
    model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
        "generationConfig": {"temperature": 0.15, "maxOutputTokens": max_tokens}
    }
    async with httpx.AsyncClient(timeout=45.0) as c:
        r = await c.post(url, json=payload)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = next((p["text"] for p in reversed(parts) if not p.get("thought")), parts[-1]["text"])
        return text.strip()


async def _call_ollama_fallback(system: str, user: str) -> str:
    """Fallback to local Ollama."""
    import httpx, os
    base = os.getenv("LOCAL_OLLAMA_URL", "http://10.0.0.2:11434")
    model = os.getenv("LOCAL_MODEL", "qwen2.5:14b")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": 0.15, "num_predict": 1000}
    }
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{base}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()


kimi_service = KimiAIService()
