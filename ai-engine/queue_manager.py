"""
ai-engine / queue_manager.py
Redis-очередь AI-инференса с приоритетами: high > medium > low.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

PRIORITY_QUEUES = {
    "high":   "ai_inference_queue:high",
    "medium": "ai_inference_queue:medium",
    "low":    "ai_inference_queue:low",
}

CONTEXT_PRIORITY = {
    "fraud_score":      "high",
    "route_price":      "medium",
    "suggest_carriers": "medium",
    "route_forecast":   "low",
    "why_not_win":      "low",
    "market_anomaly":   "low",
    "summarize":        "low",
    "explain":          "low",
}

RESULT_TTL = {
    "fraud_score":      600,
    "route_price":      600,
    "suggest_carriers": 600,
    "route_forecast":   1800,
    "why_not_win":      600,
    "market_anomaly":   300,
    "default":          600,
}


@dataclass
class InferenceJob:
    job_id: str
    context: str
    prompt: str
    response_data: dict          # детерминированные данные (features, stats)
    priority: str = "medium"
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: bytes | str) -> "InferenceJob":
        return cls(**json.loads(raw))


class InferenceQueue:
    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url)
        return self._redis

    # ── Enqueue / Dequeue ─────────────────────────────────────────────────────

    async def enqueue(self, job: InferenceJob) -> str:
        r = await self._get_redis()
        await r.rpush(PRIORITY_QUEUES.get(job.priority, PRIORITY_QUEUES["medium"]), job.to_json())
        await r.setex(f"ai_job_status:{job.job_id}", 3600, "queued")
        log.info("enqueue job=%s ctx=%s pri=%s", job.job_id, job.context, job.priority)
        return job.job_id

    async def dequeue(self, timeout: int = 2) -> InferenceJob | None:
        """BLPOP с приоритетом: high → medium → low."""
        r = await self._get_redis()
        result = await r.blpop(
            [PRIORITY_QUEUES["high"], PRIORITY_QUEUES["medium"], PRIORITY_QUEUES["low"]],
            timeout=timeout,
        )
        if result is None:
            return None
        _, raw = result
        return InferenceJob.from_json(raw)

    # ── Results ───────────────────────────────────────────────────────────────

    async def set_result(self, job_id: str, context: str, data: dict) -> None:
        r = await self._get_redis()
        ttl = RESULT_TTL.get(context, RESULT_TTL["default"])
        payload = json.dumps({"status": "done", "data": data, "job_id": job_id})
        await r.setex(f"ai_result:{job_id}", ttl, payload)
        await r.setex(f"ai_job_status:{job_id}", ttl, "done")

    async def set_error(self, job_id: str, error: str) -> None:
        r = await self._get_redis()
        payload = json.dumps({"status": "error", "error": error, "job_id": job_id})
        await r.setex(f"ai_result:{job_id}", 300, payload)
        await r.setex(f"ai_job_status:{job_id}", 300, "error")

    async def get_result(self, job_id: str) -> dict | None:
        r = await self._get_redis()
        raw = await r.get(f"ai_result:{job_id}")
        return json.loads(raw) if raw else None

    async def get_status(self, job_id: str) -> str | None:
        r = await self._get_redis()
        s = await r.get(f"ai_job_status:{job_id}")
        return s.decode() if s else None

    # ── Metrics ───────────────────────────────────────────────────────────────

    async def queue_depths(self) -> dict:
        r = await self._get_redis()
        high   = await r.llen(PRIORITY_QUEUES["high"])
        medium = await r.llen(PRIORITY_QUEUES["medium"])
        low    = await r.llen(PRIORITY_QUEUES["low"])
        return {"high": high, "medium": medium, "low": low, "total": high + medium + low}

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
