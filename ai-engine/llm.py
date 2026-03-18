"""
ai-engine / llm.py
Ollama client с SLA-защитой, Redis-кэшем и метриками.
LLM только интерпретирует — не считает.
"""
import asyncio
import hashlib
import json
import logging
import time

import aiohttp
import redis.asyncio as aioredis

log = logging.getLogger(__name__)

_FALLBACKS = {
    "fraud_score":    "⚠️ AI недоступен. Используй алгоритмический score выше.",
    "route_price":    "⚠️ AI недоступен. Используй P10–P90 диапазон выше.",
    "route_forecast": "⚠️ AI недоступен. Используй тренд и DOW-данные выше.",
    "why_not_win":    "⚠️ AI недоступен. Следуй алгоритмическим рекомендациям выше.",
    "market_anomaly": "⚠️ AI недоступен. Проверь аномалии вручную.",
    "suggest_carriers":"⚠️ AI недоступен. Контактируй топ-перевозчиков по match score.",
    "ask_logist":      "⚠️ AI-логист временно недоступен. Попробуй позже.",
    "default":        "⚠️ AI временно недоступен.",
}

_LATENCY_THRESHOLD_MS = 5000
_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
_last_latency_ms: float = 0.0

CACHE_TTL = {
    "route_price":    600,   # 10 мин
    "route_forecast": 1800,  # 30 мин
    "market_anomaly": 300,   # 5 мин
    "fraud_score":    120,   # 2 мин (данные меняются)
    "suggest_carriers": 180,
    "ask_logist":     300,
    "parse_intent":   0,
    "why_not_win":    120,
}


class LLMClient:
    def __init__(self, ollama_url: str, model: str, redis_url: str):
        self.ollama_url = ollama_url
        self.model = model
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url)
        return self._redis

    def _cache_key(self, context: str, prompt_hash: str) -> str:
        return f"ai_engine:{context}:{prompt_hash}"

    def _prompt_hash(self, prompt: str) -> str:
        return hashlib.md5(prompt.encode()).hexdigest()[:16]

    async def _track_metric(self, name: str, value: float = 1) -> None:
        try:
            r = await self._get_redis()
            await r.lpush(f"ai_metrics:{name}", value)
            await r.ltrim(f"ai_metrics:{name}", 0, 999)   # хранить 1000 последних
        except Exception:
            pass

    async def status(self) -> dict:
        try:
            t0 = time.monotonic()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                r = await s.get(f"{self.ollama_url}/api/tags")
                data = await r.json()
            ms = (time.monotonic() - t0) * 1000
            models = [m["name"] for m in data.get("models", [])]
            return {"ok": True, "ms": ms, "models": models}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def gpu_info(self) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                r = await s.get(f"{self.ollama_url}/api/ps")
                data = await r.json()
            running = data.get("models", [])
            return {
                "ok": True,
                "running": [(m["name"], m.get("size_vram", 0)) for m in running],
                "vram_total": sum(m.get("size_vram", 0) for m in running),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def chat(
        self,
        prompt: str,
        context: str = "default",
        timeout: int = 60,
        use_cache: bool = True,
    ) -> tuple[str, bool]:
        """
        Returns (response_text, from_cache).
        """
        global _last_latency_ms

        # ── Кэш ──────────────────────────────────────────────────────────────
        cache_ttl = CACHE_TTL.get(context, 0)
        cache_key = self._cache_key(context, self._prompt_hash(prompt))

        if use_cache and cache_ttl > 0:
            try:
                r = await self._get_redis()
                cached = await r.get(cache_key)
                if cached:
                    await self._track_metric("cache_hits")
                    return cached.decode(), True
            except Exception:
                pass

        # ── Проверяем доступность ─────────────────────────────────────────────
        st = await self.status()
        if not st["ok"]:
            await self._track_metric("fallbacks")
            return _FALLBACKS.get(context, _FALLBACKS["default"]), False

        # ── Короткий режим если latency высокий ──────────────────────────────
        if _last_latency_ms > _LATENCY_THRESHOLD_MS:
            prompt = prompt + "\n\nОтвечай КРАТКО — максимум 3 предложения."
            timeout = min(timeout, 20)
            await self._track_metric("short_mode")

        # ── Очередь ───────────────────────────────────────────────────────────
        queue_depth = _queue.qsize()
        await self._track_metric("queue_depth", queue_depth)
        try:
            _queue.put_nowait(1)
        except asyncio.QueueFull:
            await self._track_metric("queue_overflow")
            return "⏳ AI перегружен. Попробуй через 30 секунд.", False

        try:
            t0 = time.monotonic()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
                resp = await s.post(
                    f"{self.ollama_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False},
                )
                data = await resp.json()

            ms = (time.monotonic() - t0) * 1000
            _last_latency_ms = ms

            result = data.get("response", "").strip()
            tokens = data.get("eval_count", 0)

            await self._track_metric("latency_ms", ms)
            await self._track_metric("tokens", tokens)

            # Сохраняем в кэш
            if use_cache and cache_ttl > 0 and result:
                try:
                    r = await self._get_redis()
                    await r.setex(cache_key, cache_ttl, result)
                except Exception:
                    pass

            return result, False

        except Exception as e:
            await self._track_metric("errors")
            log.error("LLM error: %s", e)
            return _FALLBACKS.get(context, _FALLBACKS["default"]), False
        finally:
            try:
                _queue.get_nowait()
            except asyncio.QueueEmpty:
                pass

    async def get_metrics(self) -> dict:
        """Метрики из Redis."""
        try:
            r = await self._get_redis()
            latencies = [float(x) for x in await r.lrange("ai_metrics:latency_ms", 0, 99)]
            tokens = [float(x) for x in await r.lrange("ai_metrics:tokens", 0, 99)]
            errors = await r.llen("ai_metrics:errors")
            fallbacks = await r.llen("ai_metrics:fallbacks")
            cache_hits = await r.llen("ai_metrics:cache_hits")
            queue_d = [float(x) for x in await r.lrange("ai_metrics:queue_depth", 0, 9)]

            return {
                "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
                "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if latencies else 0,
                "total_requests": len(latencies),
                "total_errors": errors,
                "total_fallbacks": fallbacks,
                "cache_hits": cache_hits,
                "avg_tokens": round(sum(tokens) / len(tokens), 0) if tokens else 0,
                "queue_depth_avg": round(sum(queue_d) / len(queue_d), 1) if queue_d else 0,
                "current_queue": _queue.qsize(),
                "last_latency_ms": round(_last_latency_ms, 1),
            }
        except Exception as e:
            return {"error": str(e)}
