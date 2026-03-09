"""
ai-engine / main.py  v2
FastAPI сервис — AI логистика.
Bot → ai-engine → extractors (DB sync) → InferenceQueue → worker → LLM (Ollama)
"""
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from extractors import (
    extract_forecast_features,
    extract_fraud_features,
    extract_market_anomalies,
    extract_route_price_features,
    extract_suggest_carriers,
    extract_why_not_win_features,
)
from llm import LLMClient
from queue_manager import CONTEXT_PRIORITY, InferenceJob, InferenceQueue

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
OLLAMA_URL   = os.environ.get("OLLAMA_URL",   "http://10.0.0.2:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:30b")
REDIS_URL    = os.environ.get("REDIS_URL",    "redis://localhost:6379")
API_TOKEN    = os.environ.get("AI_ENGINE_TOKEN", "")

llm            = LLMClient(OLLAMA_URL, OLLAMA_MODEL, REDIS_URL)
inference_queue = InferenceQueue(REDIS_URL)
_redis_direct: aioredis.Redis | None = None
_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_direct
    _redis_direct = aioredis.from_url(REDIS_URL)
    log.info("ai-engine v2 started | model=%s", OLLAMA_MODEL)
    yield
    if llm._redis:
        await llm._redis.aclose()
    await inference_queue.close()
    if _redis_direct:
        await _redis_direct.aclose()


app = FastAPI(title="GoTruck AI Engine", version="2.0.0", lifespan=lifespan)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _check_token(token: str | None):
    if API_TOKEN and token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─── Models ───────────────────────────────────────────────────────────────────

class RoutePriceRequest(BaseModel):
    from_city: str
    to_city: str
    token: str | None = None
    wait: bool = True

class FraudScoreRequest(BaseModel):
    load_id: int
    token: str | None = None
    wait: bool = True

class RouteRequest(BaseModel):
    from_city: str
    to_city: str
    token: str | None = None
    wait: bool = True

class LoadRequest(BaseModel):
    load_id: int
    token: str | None = None
    wait: bool = True

class TokenRequest(BaseModel):
    token: str | None = None


# ─── Queue helper ─────────────────────────────────────────────────────────────

FALLBACKS = {
    "fraud_score":     "⚠️ AI недоступен. Используй алгоритмический score выше.",
    "route_price":     "⚠️ AI недоступен. Используй P10–P90 диапазон выше.",
    "route_forecast":  "⚠️ AI недоступен. Используй тренд и DOW-данные выше.",
    "why_not_win":     "⚠️ AI недоступен. Следуй алгоритмическим рекомендациям.",
    "market_anomaly":  "⚠️ AI недоступен. Проверь аномалии вручную.",
    "suggest_carriers":"⚠️ AI недоступен. Контактируй перевозчиков по match score.",
    "default":         "⚠️ AI временно недоступен.",
}

LLM_CACHE_TTL = {
    "fraud_score":      120,
    "route_price":      600,
    "suggest_carriers": 180,
    "route_forecast":   1800,
    "why_not_win":      120,
    "market_anomaly":   300,
}


async def _get_llm_cache(context: str, prompt: str) -> str | None:
    """Проверяем LLM-кэш до постановки в очередь."""
    try:
        h = hashlib.md5(prompt.encode()).hexdigest()[:16]
        raw = await _redis_direct.get(f"ai_engine:{context}:{h}")
        return raw.decode() if raw else None
    except Exception:
        return None


async def _enqueue_and_resolve(
    context: str,
    prompt: str,
    response_data: dict,
    wait: bool = True,
    wait_timeout: int = 90,
) -> dict:
    """
    Enqueue задачу → если wait=True, поллим result и возвращаем полный ответ.
    Если wait=False — сразу возвращаем {job_id, status}.
    """
    # Кэш LLM — если хит, не ставим в очередь вовсе
    cached_text = await _get_llm_cache(context, prompt)
    if cached_text:
        return {**response_data, "explanation": cached_text, "model": OLLAMA_MODEL, "cached": True}

    priority = CONTEXT_PRIORITY.get(context, "medium")
    job_id = str(uuid.uuid4())
    job = InferenceJob(
        job_id=job_id,
        context=context,
        prompt=prompt,
        response_data=response_data,
        priority=priority,
    )
    await inference_queue.enqueue(job)

    if not wait:
        return {"job_id": job_id, "status": "queued", "context": context}

    # Polling до готовности
    deadline = time.monotonic() + wait_timeout
    while time.monotonic() < deadline:
        result = await inference_queue.get_result(job_id)
        if result:
            if result["status"] == "done":
                return result["data"]
            if result["status"] == "error":
                # Возвращаем детерм. данные с fallback
                return {**response_data, "explanation": FALLBACKS.get(context, FALLBACKS["default"]),
                        "model": OLLAMA_MODEL, "cached": False}
        await __import__("asyncio").sleep(0.3)

    # Timeout — детерм. данные + fallback
    log.warning("Job %s timed out after %.0fs", job_id, wait_timeout)
    return {
        **response_data,
        "explanation": "⏳ AI обрабатывает запрос. Попробуй через минуту.",
        "model": OLLAMA_MODEL,
        "cached": False,
        "job_id": job_id,
    }


# ─── System endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    st = await llm.status()
    depths = await inference_queue.queue_depths()
    return {
        "status": "ok",
        "uptime_sec": int(time.time() - _start_time),
        "model": OLLAMA_MODEL,
        "ollama": "ok" if st["ok"] else "unavailable",
        "ollama_ms": round(st.get("ms", 0), 1),
        "queue": depths,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def metrics():
    m = await llm.get_metrics()
    gpu = await llm.gpu_info()
    depths = await inference_queue.queue_depths()

    # Метрики воркера из Redis
    worker_stats = {}
    try:
        jobs = await _redis_direct.llen("ai_metrics:jobs_processed")
        errors = await _redis_direct.llen("ai_metrics:worker_errors")
        latencies = [float(x) for x in await _redis_direct.lrange("ai_metrics:latency_ms", 0, 99)]
        worker_stats = {
            "jobs_processed": jobs,
            "worker_errors": errors,
            "avg_inference_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        }
    except Exception:
        pass

    return {
        "metrics": m,
        "worker": worker_stats,
        "queue": depths,
        "gpu": gpu,
        "model": OLLAMA_MODEL,
    }


@app.get("/queue/status")
async def queue_status():
    depths = await inference_queue.queue_depths()
    return {"queue": depths, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/result/{job_id}")
async def get_result(job_id: str, token: str | None = None):
    _check_token(token)
    status = await inference_queue.get_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    if status in ("queued", "processing"):
        depths = await inference_queue.queue_depths()
        return {"job_id": job_id, "status": status, "queue": depths}

    result = await inference_queue.get_result(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Result expired")
    return result


# ─── AI endpoints ─────────────────────────────────────────────────────────────

@app.post("/fraud_score")
async def fraud_score(req: FraudScoreRequest):
    _check_token(req.token)
    try:
        features = await extract_fraud_features(DATABASE_URL, req.load_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if features.get("not_found"):
        raise HTTPException(status_code=404, detail=f"Load {req.load_id} not found")

    score = features["score"]
    risk    = "high" if score >= 7 else "medium" if score >= 4 else "low"
    verdict = "block" if score >= 7 else "review" if score >= 4 else "approve"

    response_data = {
        "score": score,
        "risk": risk,
        "verdict": verdict,
        "features": {
            "price_vs_market_pct": features.get("price_vs_market_pct"),
            "account_age_days": features.get("account_age_days"),
            "phone_spam": features.get("phone_loads_7d", 0) > 5,
            "phone_loads_7d": features.get("phone_loads_7d"),
            "is_verified": features.get("is_verified"),
            "no_inn": not bool(features["load"].get("inn")),
            "risk_flags": features["risk_flags"],
        },
    }

    if not features["risk_flags"]:
        return {**response_data, "explanation": "", "model": OLLAMA_MODEL, "cached": False}

    ld = features["load"]
    flags_str = "; ".join([f"{f['code']}({f['detail']})" for f in features["risk_flags"]])
    prompt = (
        f"Антифрод-аналитик GoTruck. Груз #{req.load_id}: "
        f"{ld.get('from_city')} → {ld.get('to_city')}, "
        f"цена {float(ld.get('price') or 0):,.0f}₽.\n"
        f"Риск-факторы: {flags_str}\n"
        f"Вердикт: {verdict} (score {score}/10).\n"
        f"Объясни кратко: 1) главная причина, 2) что проверить. 2-3 предложения."
    )
    return await _enqueue_and_resolve("fraud_score", prompt, response_data, req.wait)


@app.post("/route_price")
async def route_price(req: RoutePriceRequest):
    _check_token(req.token)
    try:
        features = await extract_route_price_features(DATABASE_URL, req.from_city, req.to_city)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if features.get("count", 0) == 0:
        raise HTTPException(status_code=404, detail="No data for this route")

    rate_str = f"{features['avg_rate_per_km']:.0f} ₽/км" if features.get("avg_rate_per_km") else "?"
    top_body = ", ".join([f"{b}({c})" for b, c in features.get("top_body", [])])

    response_data = {
        "from_city": req.from_city,
        "to_city": req.to_city,
        "features": {
            "count_30d": features["count"],
            "count_7d": features["count_7d"],
            "avg": round(features["avg"]),
            "median": round(features["median"]),
            "p10": round(features["p10"]),
            "p25": round(features["p25"]),
            "p75": round(features["p75"]),
            "p90": round(features["p90"]),
            "min": round(features["min"]),
            "max": round(features["max"]),
            "avg_rate_per_km": round(features["avg_rate_per_km"]) if features.get("avg_rate_per_km") else None,
            "distance_km": features.get("distance_km"),
            "top_body_types": features.get("top_body", []),
        },
    }

    prompt = (
        f"Логист-аналитик GoTruck. Маршрут {req.from_city}→{req.to_city} (30д, {features['count']} грузов):\n"
        f"Средняя: {features['avg']:,.0f}₽, медиана: {features['median']:,.0f}₽, "
        f"P10–P90: {features['p10']:,.0f}–{features['p90']:,.0f}₽, за км: {rate_str}\n"
        f"Топ кузова: {top_body}\n\n"
        f"Ответь: 1) справедливая ставка, 2) риск маршрута (low/medium/high) + причина, "
        f"3) вероятность быстрой загрузки (%), 4) совет грузоотправителю. "
        f"Конкретные числа. Кратко."
    )
    return await _enqueue_and_resolve("route_price", prompt, response_data, req.wait)


@app.post("/route_forecast")
async def route_forecast(req: RouteRequest):
    _check_token(req.token)
    try:
        features = await extract_forecast_features(DATABASE_URL, req.from_city, req.to_city)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if features.get("total_days_data", 0) < 3:
        raise HTTPException(status_code=404, detail="Insufficient data for forecast")

    dow_data = features["dow_data"]
    dow_str  = ", ".join([f"{v['name']}: {v['avg']:,.0f}₽" for v in dow_data.values()])
    trend    = features.get("weekly_trend_pct")

    response_data = {
        "from_city": req.from_city,
        "to_city": req.to_city,
        "features": {
            "dow_prices":        {str(k): round(v["avg"]) for k, v in dow_data.items()},
            "today_dow":         features["today_dow"],
            "tomorrow_dow":      features["tomorrow_dow"],
            "current_week_avg":  round(features["current_week_avg"])  if features.get("current_week_avg")  else None,
            "prev_week_avg":     round(features["prev_week_avg"])     if features.get("prev_week_avg")     else None,
            "weekly_trend_pct":  trend,
        },
    }

    trend_line = f"Недельный тренд: {trend:+.1f}%" if trend else ""
    prompt = (
        f"AI-аналитик логистики. Маршрут {req.from_city}→{req.to_city}:\n"
        f"Ставки по дням: {dow_str}\n"
        f"{trend_line}\n\n"
        f"Прогноз:\n"
        f"1. СЕГОДНЯ: диапазон ₽\n"
        f"2. ЗАВТРА: диапазон ₽\n"
        f"3. ВЕРОЯТНОСТЬ РОСТА на этой неделе (%)\n"
        f"4. ЛУЧШИЙ ДЕНЬ для отправки + почему\n"
        f"Конкретные числа. Без воды."
    )
    return await _enqueue_and_resolve("route_forecast", prompt, response_data, req.wait)


@app.post("/why_not_win")
async def why_not_win(req: LoadRequest):
    _check_token(req.token)
    try:
        features = await extract_why_not_win_features(DATABASE_URL, req.load_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if features.get("not_found"):
        raise HTTPException(status_code=404, detail=f"Load {req.load_id} not found")

    ld = features["load"]
    price = float(ld.get("price") or 0)

    response_data = {
        "load_id": req.load_id,
        "features": {
            "market_avg":        round(features["market_avg"])  if features.get("market_avg")  else None,
            "market_p25":        round(features["market_p25"])  if features.get("market_p25")  else None,
            "market_p75":        round(features["market_p75"])  if features.get("market_p75")  else None,
            "competitors_active": features["competitors_active"],
            "age_hours":         features.get("age_hours"),
            "issues":            features["issues"],
            "recommendations":   features["recommendations"],
            "issue_count":       features["issue_count"],
        },
    }

    mkt = f"{features['market_avg']:,.0f}" if features.get("market_avg") else "?"
    prompt = (
        f"AI-консультант GoTruck по конверсии. Груз #{req.load_id}: "
        f"{ld.get('from_city')}→{ld.get('to_city')}, {price:,.0f}₽.\n"
        f"Рынок: {mkt}₽\n"
        f"Проблемы: {'; '.join(features['issues']) or 'нет'}\n\n"
        f"План из 3 конкретных действий чтобы груз взяли в 24ч. "
        f"Конкретные числа. Нумерованный список."
    )
    return await _enqueue_and_resolve("why_not_win", prompt, response_data, req.wait)


@app.get("/market_anomaly")
async def market_anomaly(token: str | None = None, wait: bool = True):
    _check_token(token)
    try:
        features = await extract_market_anomalies(DATABASE_URL)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    anomalies = []
    for r in features.get("dumping", []):
        if abs(r["pct"]) > 25:
            anomalies.append(f"{r['from_city']}→{r['to_city']}: демпинг {r['pct']:.0f}%")
    ratio = features.get("account_spike_ratio", 1)
    if ratio > 3:
        anomalies.append(f"Всплеск аккаунтов: x{ratio:.1f} нормы")
    for p in features.get("spam_phones", []):
        anomalies.append(f"Спам: {p['phone']} ({p['cnt']} грузов/час)")

    severity = "high" if len(anomalies) >= 3 else "medium" if anomalies else "low"

    response_data = {
        "severity": severity,
        "anomalies_count": len(anomalies),
        "anomalies": anomalies,
        "features": features,
    }

    if not anomalies:
        return {**response_data, "explanation": "", "model": OLLAMA_MODEL, "cached": False}

    prompt = (
        f"Аналитик безопасности GoTruck. Аномалии рынка:\n"
        + "\n".join(f"- {a}" for a in anomalies)
        + "\n\nКоординированная атака или случайность? Что делать прямо сейчас? 3 пункта."
    )
    return await _enqueue_and_resolve("market_anomaly", prompt, response_data, wait)


@app.post("/suggest_carriers")
async def suggest_carriers(req: LoadRequest):
    _check_token(req.token)
    try:
        data = await extract_suggest_carriers(DATABASE_URL, req.load_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if data.get("not_found"):
        raise HTTPException(status_code=404, detail=f"Load {req.load_id} not found")

    carriers = data["carriers"]
    ld = data["load"]

    carrier_list = [
        {
            "id": c["id"],
            "name": c.get("company") or c.get("fullname") or f"ID{c['id']}",
            "phone": c.get("phone"),
            "city": c.get("city"),
            "match_score": c["match_score"],
            "rating": float(c["rating"]) if c.get("rating") else None,
            "successful_deals": c.get("successful_deals"),
            "verified": c.get("verified"),
            "telegram_id": c.get("telegram_id"),
            "source": c["source"],
            "route_loads": c.get("route_loads"),
        }
        for c in carriers[:8]
    ]

    response_data = {"load_id": req.load_id, "carriers": carrier_list}

    if not carriers:
        return {**response_data, "explanation": "", "model": OLLAMA_MODEL, "cached": False}

    top3 = carriers[:3]
    cstr = "\n".join([
        f"- {c.get('company') or c.get('fullname') or 'ID'+str(c['id'])}: "
        f"match {c['match_score']}%, rating {c.get('rating') or '?'}, "
        f"{c.get('successful_deals') or 0} deals"
        for c in top3
    ])
    price = float(ld.get("price") or 0)
    prompt = (
        f"AI-диспетчер GoTruck. Груз #{req.load_id}: "
        f"{ld.get('from_city')}→{ld.get('to_city')}, {price:,.0f}₽.\n"
        f"Топ перевозчики:\n{cstr}\n\n"
        f"1) Кого звонить первым, 2) вероятность закрытия (%), 3) риски. Кратко."
    )
    return await _enqueue_and_resolve("suggest_carriers", prompt, response_data, req.wait)



# ─── NLU Context Memory ───────────────────────────────────────────────────────

_CTX_TTL      = 600   # 10 мин неактивности → сброс сессии
_CTX_MESSAGES = 3     # хранить N последних сообщений


def _ctx_key(user_id: int) -> str:
    return f"nlu_context:{user_id}"


async def _load_ctx(user_id: int | None) -> dict:
    if not user_id:
        return {}
    try:
        r = await inference_queue._get_redis()
        raw = await r.get(_ctx_key(user_id))
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


async def _save_ctx(user_id: int | None, ctx: dict) -> None:
    if not user_id:
        return
    try:
        r = await inference_queue._get_redis()
        await r.setex(_ctx_key(user_id), _CTX_TTL, json.dumps(ctx, ensure_ascii=False))
    except Exception:
        pass


def _merge_intent(prev: dict, new: dict) -> dict:
    """Мержим новый интент с предыдущим. Новые непустые поля перезаписывают старые."""
    result = {k: v for k, v in prev.items() if k != "messages"}
    for key in ("intent", "from_city", "to_city", "weight_t",
                "cargo_type", "body_type", "date"):
        val = new.get(key)
        if val is not None:
            result[key] = val
    if new.get("confidence") is not None:
        result["confidence"] = new["confidence"]
    msgs = list(prev.get("messages", []))
    if new.get("_text"):
        msgs = (msgs + [new["_text"]])[-_CTX_MESSAGES:]
    result["messages"] = msgs
    return result


# ─── NLU / Smart Entry ────────────────────────────────────────────────────────

class ParseIntentRequest(BaseModel):
    text: str
    user_id: int | None = None
    token: str | None = None

class AskLogistRequest(BaseModel):
    question: str
    context: str | None = None   # опциональный контекст: "Москва→Питер, 10т"
    user_id: int | None = None
    token: str | None = None


@app.post("/parse_intent")
async def parse_intent(req: ParseIntentRequest):
    """NLU: текст + контекст сессии → структурированный интент."""
    _check_token(req.token)

    ctx = await _load_ctx(req.user_id)
    prev_intent = {k: v for k, v in ctx.items() if k != "messages"}
    history     = ctx.get("messages", [])

    history_str = ""
    if history:
        history_str = (
            "Предыдущие сообщения пользователя в этой сессии:\n"
            + "\n".join(f"  - {m}" for m in history)
            + "\n\n"
        )

    prev_str = ""
    if prev_intent:
        known = {k: v for k, v in prev_intent.items() if v and k != "confidence"}
        if known:
            prev_str = f"Уже известно из контекста: {json.dumps(known, ensure_ascii=False)}\n\n"

    prompt = (
        "Ты парсер запросов логистической платформы GoTruck (Россия). "
        "Из текста извлеки структурированные данные.\n\n"
        + history_str
        + prev_str
        + f'Текст пользователя: "{req.text}"\n\n'
        "Верни ТОЛЬКО валидный JSON без markdown:\n"
        "{\n"
        '  \"intent\": \"find_transport\" | \"place_cargo\" | \"get_price\" | \"ask_question\" | \"unknown\",\n'
        '  \"from_city\": \"город или null — если уже известен из контекста, повтори его\",\n'
        '  \"to_city\": \"город или null\",\n'
        '  \"weight_t\": число или null,\n'
        '  \"cargo_type\": \"тип груза или null\",\n'
        '  \"body_type\": \"тент/реф/манипулятор/рефрижератор/контейнер/газель или null\",\n'
        '  \"date\": \"today/tomorrow/YYYY-MM-DD или null\",\n'
        '  \"confidence\": 0.0-1.0\n'
        "}\n\n"
        "Примеры:\n"
        '- \"перевезти запчасти из Челнов в Самару 5 тонн завтра\" → find_transport\n'
        '- \"сколько стоит Москва-Питер\" → get_price\n'
        '- \"почему так дорого до Тюмени\" → ask_question\n'
        '- \"хочу разместить груз\" → place_cargo\n'
        "ТОЛЬКО JSON. Ничего больше."
    )

    raw, _ = await llm.chat(prompt, context="parse_intent", timeout=30, use_cache=False)

    import re as _re
    match = _re.search(r'\{[\s\S]*\}', raw)
    if not match:
        return {**prev_intent, "intent": prev_intent.get("intent", "unknown"),
                "confidence": 0.0, "context_used": bool(prev_intent)}

    try:
        new_data = json.loads(match.group())
        new_data["_text"] = req.text
        merged = _merge_intent(ctx, new_data)
        await _save_ctx(req.user_id, merged)
        out = {k: v for k, v in merged.items() if k not in ("messages", "_text")}
        out["context_used"] = bool(prev_intent)
        return out
    except json.JSONDecodeError:
        return {**prev_intent, "intent": "unknown", "confidence": 0.0, "context_used": bool(prev_intent)}


    try:
        data = json.loads(match.group())
        return data
    except json.JSONDecodeError:
        return {"intent": "unknown", "confidence": 0.0, "raw_llm": raw[:200]}


@app.post("/ask_logist")
async def ask_logist(req: AskLogistRequest):
    """
    AI-логист: свободный вопрос → экспертный ответ с данными рынка.
    """
    _check_token(req.token)

    context_str = f"\nКонтекст пользователя: {req.context}" if req.context else ""

    prompt = (
        f"Ты AI-логист платформы GoTruck (Россия). Отвечай как эксперт-практик: "
        f"конкретно, с цифрами, без воды.{context_str}\n\n"
        f"Вопрос: {req.question}\n\n"
        f"Ответ (3-5 предложений, конкретика, если есть — цифры и рекомендация):"
    )

    # Сохраняем вопрос в контекст пользователя
    if req.user_id:
        ctx = await _load_ctx(req.user_id)
        ctx.setdefault("messages", [])
        ctx["messages"] = (ctx["messages"] + [req.question])[-_CTX_MESSAGES:]
        await _save_ctx(req.user_id, ctx)
    answer, from_cache = await llm.chat(prompt, context="ask_logist", timeout=90, use_cache=True)

    return {
        "answer": answer,
        "model": OLLAMA_MODEL,
        "cached": from_cache,
    }
