"""
ai-engine / worker.py
Inference worker: BLPOP из приоритетных очередей → Ollama → result:{job_id}.
LLM только интерпретирует — не считает.
"""
import asyncio
import hashlib
import logging
import os
import time

import aiohttp
import redis.asyncio as aioredis

from queue_manager import CONTEXT_PRIORITY, InferenceJob, InferenceQueue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("ai-worker")

OLLAMA_URL   = os.environ.get("OLLAMA_URL",   "http://10.0.0.2:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:30b")
REDIS_URL    = os.environ.get("REDIS_URL",    "redis://localhost:6379")
WORKER_NAME  = os.environ.get("WORKER_NAME",  "worker-1")
WORKERS_N    = int(os.environ.get("WORKERS_N", "2"))  # параллельных воркеров

LLM_CACHE_TTL = {
    "fraud_score":      120,
    "route_price":      600,
    "suggest_carriers": 180,
    "route_forecast":   1800,
    "why_not_win":      120,
    "market_anomaly":   300,
    "default":          300,
}

_jobs_processed = 0
_worker_errors  = 0
_total_ms       = 0.0
_start_time     = time.time()


# ─── Ollama ───────────────────────────────────────────────────────────────────

async def call_ollama(prompt: str, timeout: int = 90) -> tuple[str, int]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
        r = await s.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        )
        data = await r.json()
    return data.get("response", "").strip(), data.get("eval_count", 0)


# ─── Metrics ──────────────────────────────────────────────────────────────────

async def track(redis: aioredis.Redis, name: str, value: float = 1) -> None:
    try:
        await redis.lpush(f"ai_metrics:{name}", value)
        await redis.ltrim(f"ai_metrics:{name}", 0, 999)
    except Exception:
        pass


# ─── Cache ────────────────────────────────────────────────────────────────────

def _cache_key(context: str, prompt: str) -> str:
    h = hashlib.md5(prompt.encode()).hexdigest()[:16]
    return f"ai_engine:{context}:{h}"


async def get_cached(redis: aioredis.Redis, context: str, prompt: str) -> str | None:
    try:
        raw = await redis.get(_cache_key(context, prompt))
        return raw.decode() if raw else None
    except Exception:
        return None


async def set_cache(redis: aioredis.Redis, context: str, prompt: str, text: str) -> None:
    try:
        ttl = LLM_CACHE_TTL.get(context, LLM_CACHE_TTL["default"])
        await redis.setex(_cache_key(context, prompt), ttl, text)
    except Exception:
        pass


# ─── Worker loop ──────────────────────────────────────────────────────────────

async def process_job(job: InferenceJob, queue: InferenceQueue) -> None:
    global _jobs_processed, _worker_errors, _total_ms

    r = await queue._get_redis()
    log.info("[%s] start job=%s ctx=%s", WORKER_NAME, job.job_id, job.context)

    depths = await queue.queue_depths()
    await track(r, "queue_depth", depths["total"])

    t0 = time.monotonic()
    try:
        # Проверяем LLM-кэш перед вызовом
        cached_text = await get_cached(r, job.context, job.prompt)
        if cached_text:
            await track(r, "cache_hits")
            result = {**job.response_data, "explanation": cached_text, "model": OLLAMA_MODEL, "cached": True}
            await queue.set_result(job.job_id, job.context, result)
            log.info("[%s] cache hit job=%s", WORKER_NAME, job.job_id)
            return

        response, tokens = await call_ollama(job.prompt)
        ms = (time.monotonic() - t0) * 1000
        _total_ms += ms
        _jobs_processed += 1

        await track(r, "latency_ms", ms)
        await track(r, "tokens", tokens)
        await track(r, "jobs_processed")

        # Сохраняем LLM-ответ в кэш
        if response:
            await set_cache(r, job.context, job.prompt, response)

        result = {**job.response_data, "explanation": response, "model": OLLAMA_MODEL, "cached": False}
        await queue.set_result(job.job_id, job.context, result)
        log.info("[%s] done job=%s ms=%.0f tokens=%d", WORKER_NAME, job.job_id, ms, tokens)

    except Exception as e:
        _worker_errors += 1
        await track(r, "worker_errors")
        log.error("[%s] error job=%s: %s", WORKER_NAME, job.job_id, e)
        await queue.set_error(job.job_id, str(e)[:200])


async def worker_loop(queue: InferenceQueue, name: str) -> None:
    log.info("Worker [%s] started", name)
    while True:
        try:
            job = await queue.dequeue(timeout=2)
            if job is None:
                continue
            await process_job(job, queue)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("[%s] loop error: %s", name, e)
            await asyncio.sleep(1)


async def main() -> None:
    queue = InferenceQueue(REDIS_URL)
    log.info(
        "AI Engine Workers x%d | model=%s | ollama=%s | redis=%s",
        WORKERS_N, OLLAMA_MODEL, OLLAMA_URL, REDIS_URL,
    )
    tasks = [
        asyncio.create_task(worker_loop(queue, f"{WORKER_NAME}-{i}"))
        for i in range(1, WORKERS_N + 1)
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(main())
