from __future__ import annotations

import asyncio
import json
import logging
import re
import statistics
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import settings
from src.core.database import async_session
from src.core.models import ParserIngestEvent
from src.parser_bot.extractor import (
    ParsedCargo,
    build_content_dedupe_key,
    build_dedupe_key,
    contains_invalid_geo_token,
    evaluate_hot_deal,
    parse_cargo_message,
    parse_cargo_message_llm,
)
from src.parser_bot.stream import RedisLogisticsStream, StreamMessage
from src.antifraud.scoring import ScoreResult, get_score
from src.core.services.geo_service import get_geo_service


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | parser-worker | %(message)s",
)
logger = logging.getLogger("parser-worker")

_CARGO_INTENT_RE = re.compile(
    r"(?:\bгруз\s+готов\b|\bгруз\s+бор\b|\bюк\s+бор\b|\byuk\s+bor\b|\bмашина\s+(?:керак|нужна|нужен)\b|\bmashina\s+kerak\b|\bрастаможка\b|\bчерез\s+паром\b)",
    re.IGNORECASE,
)


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


def _internal_headers() -> dict[str, str]:
    token = (settings.internal_token or "").strip() or (settings.internal_api_token or "").strip()
    return {"X-Internal-Token": token} if token else {}


def _parse_keywords(raw: str) -> list[str]:
    return [item.strip().lower() for item in (raw or "").split(",") if item.strip()]


def _build_sync_payload(
    parsed: ParsedCargo,
    message: StreamMessage,
    *,
    trust: ScoreResult | None,
) -> dict[str, Any]:
    source_name = (message.source or settings.parser_source_name).strip() or settings.parser_source_name

    order: dict[str, Any] = {
        "from_city": parsed.from_city,
        "to_city": parsed.to_city,
        "price_rub": parsed.rate_rub or 1,
        "weight_t": parsed.weight_t or 0.0,
        "status": "active",
    }
    if settings.parser_default_user_id:
        order["user_id"] = int(settings.parser_default_user_id)
    if parsed.body_type:
        order["body_type"] = parsed.body_type
    if parsed.inn:
        order["inn"] = parsed.inn
    if parsed.load_date:
        order["load_date"] = parsed.load_date
    if parsed.load_time:
        order["load_time"] = parsed.load_time
    if parsed.cargo_description:
        order["cargo_description"] = parsed.cargo_description
    if parsed.payment_terms:
        order["payment_terms"] = parsed.payment_terms
    if parsed.is_direct_customer is not None:
        order["is_direct_customer"] = parsed.is_direct_customer
    if parsed.dimensions:
        order["dimensions"] = parsed.dimensions
    if parsed.is_hot_deal:
        order["is_hot_deal"] = True
    if parsed.phone:
        order["phone"] = parsed.phone
    if parsed.suggested_response:
        order["suggested_response"] = parsed.suggested_response
    order["source"] = source_name

    metadata = {
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "stream_entry_id": message.entry_id,
        "received_at": message.received_at,
        "phone": parsed.phone,
        "inn": parsed.inn,
        "body_type": parsed.body_type,
        "matched_keywords": parsed.matched_keywords,
        "raw_text": parsed.raw_text[:2000],
        "load_date": parsed.load_date,
        "load_time": parsed.load_time,
        "cargo_description": parsed.cargo_description,
        "payment_terms": parsed.payment_terms,
        "is_direct_customer": parsed.is_direct_customer,
        "dimensions": parsed.dimensions,
        "is_hot_deal": parsed.is_hot_deal,
        "phone_blacklisted": parsed.phone_blacklisted,
    }
    if trust:
        metadata["trust_score"] = trust.score
        metadata["trust_verdict"] = trust.verdict
        metadata["trust_comment"] = trust.comment
        metadata["trust_provider"] = trust.provider

    return {
        "event_id": f"parser-{uuid.uuid4().hex}",
        "event_type": "order.created",
        "source": source_name,
        "user_id": int(settings.parser_default_user_id) if settings.parser_default_user_id else None,
        "metadata": metadata,
        "order": order,
    }


def _normalize_source_name(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("@"):
        raw = raw[1:]
    if raw.startswith("tg:"):
        return raw
    return f"tg:{raw}"


async def _fill_rate_from_reference(parsed: ParsedCargo) -> bool:
    if parsed.rate_rub:
        return True

    source_name = _normalize_source_name(settings.parser_price_source_chat)
    if not source_name:
        return False

    lookback_days = max(1, int(settings.parser_price_reference_days))
    min_samples = max(1, int(settings.parser_price_reference_min_samples))
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    async with async_session() as session:
        rates_stmt = (
            select(ParserIngestEvent.rate_rub)
            .where(
                ParserIngestEvent.source == source_name,
                ParserIngestEvent.status == "synced",
                ParserIngestEvent.is_spam.is_(False),
                ParserIngestEvent.from_city == parsed.from_city,
                ParserIngestEvent.to_city == parsed.to_city,
                ParserIngestEvent.rate_rub.isnot(None),
                ParserIngestEvent.from_lat.isnot(None),
                ParserIngestEvent.to_lat.isnot(None),
                ParserIngestEvent.created_at >= cutoff,
            )
            .order_by(ParserIngestEvent.created_at.desc())
            .limit(120)
        )
        rates = [int(v) for v in (await session.execute(rates_stmt)).scalars().all() if v]

        if len(rates) < min_samples:
            reverse_stmt = (
                select(ParserIngestEvent.rate_rub)
                .where(
                    ParserIngestEvent.source == source_name,
                    ParserIngestEvent.status == "synced",
                    ParserIngestEvent.is_spam.is_(False),
                    ParserIngestEvent.from_city == parsed.to_city,
                    ParserIngestEvent.to_city == parsed.from_city,
                    ParserIngestEvent.rate_rub.isnot(None),
                    ParserIngestEvent.from_lat.isnot(None),
                    ParserIngestEvent.to_lat.isnot(None),
                    ParserIngestEvent.created_at >= cutoff,
                )
                .order_by(ParserIngestEvent.created_at.desc())
                .limit(120)
            )
            reverse_rates = [int(v) for v in (await session.execute(reverse_stmt)).scalars().all() if v]
            rates.extend(reverse_rates)

    if len(rates) < min_samples:
        return False

    parsed.rate_rub = int(round(statistics.median(rates)))
    logger.info(
        "rate fallback from source=%s route=%s->%s rate=%s samples=%s",
        source_name,
        parsed.from_city,
        parsed.to_city,
        parsed.rate_rub,
        len(rates),
    )
    return True


def _fill_rate_by_distance(parsed: ParsedCargo) -> bool:
    if parsed.rate_rub:
        return True
    try:
        if parsed.route_distance_km:
            distance_km = float(parsed.route_distance_km)
        else:
            from src.core.geo import city_coords, haversine_km

            fc = city_coords(parsed.from_city)
            tc = city_coords(parsed.to_city)
            if not fc or not tc:
                return False
            distance_km = haversine_km(fc[0], fc[1], tc[0], tc[1])
        if distance_km < 10:
            return False

        weight = parsed.weight_t if parsed.weight_t and parsed.weight_t > 0 else 20.0
        avg_rate_per_km = 35 + min(weight, 20.0) * 0.5
        parsed.rate_rub = int(distance_km * avg_rate_per_km)
        logger.info(
            "rate estimated by distance route=%s->%s rate=%s",
            parsed.from_city,
            parsed.to_city,
            parsed.rate_rub,
        )
        return True
    except Exception:
        return False


def _is_spam(trust: ScoreResult | None) -> bool:
    if trust is None:
        return False
    return int(trust.score) < int(settings.parser_score_min_trust)


def _has_min_signal(parsed: ParsedCargo) -> bool:
    has_structured_signal = any(
        value not in (None, "", 0, 0.0)
        for value in (
            parsed.rate_rub,
            parsed.weight_t,
            parsed.phone,
            parsed.inn,
            parsed.body_type,
            parsed.dimensions,
        )
    )
    if has_structured_signal:
        return True
    return bool(_CARGO_INTENT_RE.search(parsed.raw_text or ""))


def _is_unrealistic_rate(parsed: ParsedCargo) -> bool:
    rate = parsed.rate_rub
    if not isinstance(rate, int) or rate <= 0:
        return False

    # Messages with tiny absolute prices are almost always bad parses.
    if rate < 5000:
        return True

    try:
        if parsed.route_distance_km:
            distance_km = float(parsed.route_distance_km)
        else:
            from src.core.geo import city_coords, haversine_km

            fc = city_coords(parsed.from_city)
            tc = city_coords(parsed.to_city)
            if not fc or not tc:
                return False

            distance_km = haversine_km(fc[0], fc[1], tc[0], tc[1])
        if distance_km < 50:
            return False

        rate_per_km = rate / distance_km
        if distance_km >= 100 and rate_per_km < 8:
            return True
        if distance_km >= 500 and rate < 10000:
            return True
    except Exception:
        return False

    return False


def _rate_review_reason(parsed: ParsedCargo) -> str | None:
    rate = parsed.rate_rub
    if not isinstance(rate, int) or rate <= 0:
        return None

    if rate > int(settings.parser_max_rate_rub):
        return "rate_above_cap"

    try:
        if parsed.route_distance_km:
            distance_km = float(parsed.route_distance_km)
        else:
            from src.core.geo import city_coords, haversine_km

            fc = city_coords(parsed.from_city)
            tc = city_coords(parsed.to_city)
            if not fc or not tc:
                return None

            distance_km = haversine_km(fc[0], fc[1], tc[0], tc[1])
        if distance_km < 10:
            return None

        rate_per_km = rate / distance_km
        if rate_per_km > float(settings.parser_max_rate_per_km):
            return "rate_per_km_above_cap"
    except Exception:
        return None

    return None


async def _maybe_recheck_with_llm(
    text: str,
    *,
    keywords: list[str],
    parsed: ParsedCargo,
) -> ParsedCargo:
    if not settings.parser_rate_recheck_with_llm:
        return parsed
    if not (settings.groq_api_key or settings.openai_api_key):
        return parsed

    try:
        candidate = await parse_cargo_message_llm(text, keywords=keywords)
    except Exception as exc:
        logger.warning("LLM rate recheck failed error=%s", str(exc)[:200])
        return parsed

    if not candidate:
        return parsed
    if candidate.from_city != parsed.from_city or candidate.to_city != parsed.to_city:
        return parsed
    if candidate.rate_rub is None:
        return parsed

    candidate.is_hot_deal = evaluate_hot_deal(candidate)
    logger.info(
        "LLM rate recheck adjusted route=%s->%s old_rate=%s new_rate=%s",
        parsed.from_city,
        parsed.to_city,
        parsed.rate_rub,
        candidate.rate_rub,
    )
    return candidate


async def _save_ingest_event(
    *,
    message: StreamMessage,
    parsed: ParsedCargo | None,
    trust: ScoreResult | None,
    is_spam: bool,
    status: str,
    parse_method: str | None = None,
    error: str | None = None,
) -> None:
    details: dict[str, Any] = {
        "matched_keywords": parsed.matched_keywords if parsed else [],
        "received_at": message.received_at or int(time.time()),
        "retry_count": message.retry_count,
    }
    if parse_method:
        details["parse_method"] = parse_method
    if trust:
        details["trust"] = {
            "score": trust.score,
            "verdict": trust.verdict,
            "provider": trust.provider,
            "details": trust.details,
        }
    if parsed and parsed.route_distance_km:
        details["distance_km"] = int(parsed.route_distance_km)

    from_coords = None
    to_coords = None
    if parsed:
        if parsed.from_lat is not None and parsed.from_lon is not None:
            from_coords = (parsed.from_lat, parsed.from_lon)
        else:
            from src.core.geo import city_coords
            from_coords = city_coords(parsed.from_city)
        if parsed.to_lat is not None and parsed.to_lon is not None:
            to_coords = (parsed.to_lat, parsed.to_lon)
        else:
            from src.core.geo import city_coords
            to_coords = city_coords(parsed.to_city)

    event = ParserIngestEvent(
        stream_entry_id=message.entry_id,
        chat_id=message.chat_id,
        message_id=message.message_id,
        source=message.source or settings.parser_source_name,
        from_city=parsed.from_city if parsed else None,
        to_city=parsed.to_city if parsed else None,
        body_type=parsed.body_type if parsed else None,
        phone=parsed.phone if parsed else None,
        inn=parsed.inn if parsed else None,
        rate_rub=parsed.rate_rub if parsed else None,
        weight_t=parsed.weight_t if parsed else None,
        load_date=parsed.load_date if parsed else None,
        load_time=parsed.load_time if parsed else None,
        cargo_description=parsed.cargo_description if parsed else None,
        payment_terms=parsed.payment_terms if parsed else None,
        is_direct_customer=parsed.is_direct_customer if parsed else None,
        dimensions=parsed.dimensions if parsed else None,
        is_hot_deal=parsed.is_hot_deal if parsed else False,
        suggested_response=parsed.suggested_response if parsed else None,
        phone_blacklisted=parsed.phone_blacklisted if parsed else False,
        from_lat=from_coords[0] if from_coords else None,
        from_lon=from_coords[1] if from_coords else None,
        to_lat=to_coords[0] if to_coords else None,
        to_lon=to_coords[1] if to_coords else None,
        trust_score=trust.score if trust else None,
        trust_verdict=trust.verdict if trust else None,
        trust_comment=trust.comment if trust else None,
        provider=trust.provider if trust else None,
        is_spam=is_spam,
        status=status,
        error=(error or "")[:255] or None,
        raw_text=message.raw_text[:4000],
        details_json=json.dumps(details, ensure_ascii=False),
    )

    try:
        async with async_session() as session:
            session.add(event)
            await session.commit()
    except SQLAlchemyError as exc:
        logger.warning("ingest_event save skipped id=%s error=%s", message.entry_id, str(exc)[:160])


async def _push_to_api(http_client: httpx.AsyncClient, sync_url: str, payload: dict[str, Any]) -> None:
    response = await http_client.post(sync_url, headers=_internal_headers(), json=payload)
    response.raise_for_status()


async def _process_message(
    *,
    stream: RedisLogisticsStream,
    http_client: httpx.AsyncClient,
    sync_url: str,
    keywords: list[str],
    group_name: str,
    dedupe_ttl: int,
    max_retries: int,
    message: StreamMessage,
) -> None:
    should_ack = True
    parsed: ParsedCargo | None = None
    parse_method: str | None = None
    trust: ScoreResult | None = None
    is_spam = False

    try:
        text = (message.raw_text or "").strip()
        if not text:
            await _save_ingest_event(
                message=message,
                parsed=None,
                trust=None,
                is_spam=False,
                status="empty",
                parse_method=parse_method,
            )
            return

        fallback_id = f"{message.chat_id}:{message.message_id or message.entry_id}"
        route_geo = None

        # Fast path: try regex extraction first and validate the route before
        # spending an LLM call. This keeps the hot path cheap for the majority
        # of structured chat messages.
        parsed = parse_cargo_message(text, keywords=keywords)
        if parsed:
            parse_method = "regex_fast"
            parsed.is_hot_deal = evaluate_hot_deal(parsed)
            route_geo = await get_geo_service().resolve_route(
                parsed.from_city,
                parsed.to_city,
            )
            if route_geo:
                logger.debug(
                    "regex route accepted id=%s route=%s->%s",
                    message.entry_id,
                    parsed.from_city,
                    parsed.to_city,
                )
            else:
                logger.info(
                    "regex route rejected by geo id=%s route=%s->%s",
                    message.entry_id,
                    parsed.from_city,
                    parsed.to_city,
                )

        # Slow path: only fall back to the LLM when regex failed completely or
        # produced a route that geo validation could not confirm.
        llm_enabled = bool(settings.parser_use_llm)
        should_try_llm = llm_enabled and (
            parsed is None
            or route_geo is None
            or contains_invalid_geo_token(text)
        )
        if should_try_llm:
            if contains_invalid_geo_token(text):
                logger.info(
                    "invalid geo token detected, retrying with LLM id=%s",
                    message.entry_id,
                )
            elif parsed is None:
                logger.info("regex parse miss, retrying with LLM id=%s", message.entry_id)
            else:
                logger.info(
                    "regex route not confirmed, retrying with LLM id=%s",
                    message.entry_id,
                )

            llm_candidate = await parse_cargo_message_llm(text, keywords=keywords)
            if llm_candidate:
                llm_candidate.is_hot_deal = evaluate_hot_deal(llm_candidate)
                llm_route_geo = await get_geo_service().resolve_route(
                    llm_candidate.from_city,
                    llm_candidate.to_city,
                )
                if llm_route_geo:
                    parsed = llm_candidate
                    parse_method = "llm_fallback"
                    route_geo = llm_route_geo

        if not parsed:
            await _save_ingest_event(
                message=message,
                parsed=None,
                trust=None,
                is_spam=False,
                status="ignored",
                parse_method=parse_method,
            )
            return

        if not route_geo:
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="ignored",
                parse_method=parse_method,
                error="invalid_cities",
            )
            logger.info(
                "ignored invalid cities id=%s route=%s->%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
            )
            return

        parsed.from_city = route_geo.origin.name
        parsed.to_city = route_geo.destination.name
        parsed.from_lat = route_geo.origin.lat
        parsed.from_lon = route_geo.origin.lon
        parsed.to_lat = route_geo.destination.lat
        parsed.to_lon = route_geo.destination.lon
        parsed.route_distance_km = route_geo.distance_km

        if not _has_min_signal(parsed):
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="ignored",
                parse_method=parse_method,
            )
            return

        if not parsed.rate_rub:
            await _fill_rate_from_reference(parsed)
            if not parsed.rate_rub:
                _fill_rate_by_distance(parsed)

        if _is_unrealistic_rate(parsed):
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="ignored",
                parse_method=parse_method,
            )
            logger.info(
                "ignored unrealistic rate id=%s route=%s->%s rate=%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
                parsed.rate_rub,
            )
            return

        review_reason = _rate_review_reason(parsed)
        if review_reason:
            rechecked = await _maybe_recheck_with_llm(text, keywords=keywords, parsed=parsed)
            if rechecked is not parsed:
                parsed = rechecked
                review_reason = _rate_review_reason(parsed)

        if review_reason:
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="manual_review",
                parse_method=parse_method,
                error=review_reason,
            )
            logger.info(
                "sent to manual review id=%s route=%s->%s rate=%s reason=%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
                parsed.rate_rub,
                review_reason,
            )
            return

        dedupe_key = build_dedupe_key(parsed, chat_id=message.chat_id, fallback_id=fallback_id)
        is_new = await stream.redis.set(dedupe_key, "1", ex=dedupe_ttl, nx=True)
        if not is_new:
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="duplicate",
                parse_method=parse_method,
            )
            return

        content_key = build_content_dedupe_key(parsed)
        is_unique = await stream.redis.set(content_key, "1", ex=dedupe_ttl, nx=True)
        if not is_unique:
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=None,
                is_spam=False,
                status="duplicate",
                parse_method=parse_method,
            )
            logger.info(
                "content-dedupe hit id=%s route=%s->%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
            )
            return

        from src.core.services.responses import build_default_response
        from src.core.services.phone_blacklist import is_phone_blacklisted

        parsed.suggested_response = build_default_response(
            from_city=parsed.from_city,
            to_city=parsed.to_city,
            body_type=parsed.body_type,
            weight_t=parsed.weight_t,
            load_date=parsed.load_date,
        )

        parsed.phone_blacklisted = await is_phone_blacklisted(parsed.phone)
        if parsed.phone_blacklisted:
            logger.warning(
                "blacklisted phone id=%s phone=%s route=%s->%s",
                message.entry_id,
                parsed.phone,
                parsed.from_city,
                parsed.to_city,
            )

        if parsed.inn:
            trust = await get_score(parsed.inn)
            logger.info(
                "INN enrichment id=%s inn=%s score=%s verdict=%s",
                message.entry_id,
                parsed.inn,
                trust.score,
                trust.verdict,
            )

        is_spam = _is_spam(trust)
        if is_spam:
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=trust,
                is_spam=True,
                status="spam_filtered",
                parse_method=parse_method,
            )
            logger.info(
                "filtered by score id=%s route=%s->%s score=%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
                trust.score if trust else "n/a",
            )
            return

        sync_payload = _build_sync_payload(parsed, message, trust=trust)

        try:
            await _push_to_api(http_client, sync_url, sync_payload)
            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=trust,
                is_spam=False,
                status="synced",
                parse_method=parse_method,
            )
            logger.info(
                "synced id=%s route=%s->%s inn=%s score=%s",
                message.entry_id,
                parsed.from_city,
                parsed.to_city,
                parsed.inn or "n/a",
                trust.score if trust else "n/a",
            )
        except Exception as exc:
            await stream.redis.delete(dedupe_key)
            if message.retry_count < max_retries:
                await stream.add_raw_message(
                    raw_text=message.raw_text,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    source=message.source,
                    received_at=message.received_at or int(time.time()),
                    retry_count=message.retry_count + 1,
                )
                status = "retry_queued"
            else:
                status = "sync_failed"

            await _save_ingest_event(
                message=message,
                parsed=parsed,
                trust=trust,
                is_spam=False,
                status=status,
                parse_method=parse_method,
                error=str(exc),
            )
            logger.warning(
                "sync failed id=%s retry=%s status=%s error=%s",
                message.entry_id,
                message.retry_count,
                status,
                str(exc)[:180],
            )
    except Exception as exc:
        should_ack = False
        logger.exception("worker crashed for id=%s error=%s", message.entry_id, str(exc)[:200])
    finally:
        if should_ack:
            await stream.ack(group_name=group_name, entry_id=message.entry_id)


async def run() -> None:
    if not settings.parser_enabled:
        logger.info("Parser worker disabled (PARSER_ENABLED=false). Exit.")
        return

    keywords = _parse_keywords(settings.parser_keywords)
    if not keywords:
        logger.error("PARSER_KEYWORDS is empty")
        return

    group_name = settings.parser_stream_group
    worker_name = settings.parser_worker_name or "worker-1"
    batch = max(1, int(settings.parser_stream_batch))
    block_ms = max(100, int(settings.parser_stream_block_ms))
    claim_idle_ms = max(1000, int(settings.parser_stream_claim_idle_ms))
    dedupe_ttl = max(60, int(settings.parser_dedupe_ttl_sec))
    max_retries = max(0, int(settings.parser_worker_max_retries))

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    stream = RedisLogisticsStream(
        redis_client,
        stream_name=settings.parser_stream_name,
        maxlen=settings.parser_stream_maxlen,
    )
    sync_url = _join_url(settings.gruzpotok_api_internal_url, settings.gruzpotok_sync_path)
    http_client = httpx.AsyncClient(timeout=max(3, int(settings.parser_http_timeout)))

    await stream.ensure_group(group_name)
    logger.info(
        "worker started stream=%s group=%s worker=%s batch=%s block_ms=%s claim_idle_ms=%s",
        settings.parser_stream_name,
        group_name,
        worker_name,
        batch,
        block_ms,
        claim_idle_ms,
    )

    try:
        while True:
            messages = await stream.read_group(
                group_name=group_name,
                consumer_name=worker_name,
                count=batch,
                block_ms=block_ms,
            )

            if not messages:
                messages = await stream.claim_stale(
                    group_name=group_name,
                    consumer_name=worker_name,
                    min_idle_ms=claim_idle_ms,
                    count=batch,
                )

            if not messages:
                continue

            for message in messages:
                await _process_message(
                    stream=stream,
                    http_client=http_client,
                    sync_url=sync_url,
                    keywords=keywords,
                    group_name=group_name,
                    dedupe_ttl=dedupe_ttl,
                    max_retries=max_retries,
                    message=message,
                )
    finally:
        await http_client.aclose()
        await redis_client.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
