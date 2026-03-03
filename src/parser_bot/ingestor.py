from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis
from telethon import TelegramClient, events, utils
from telethon.errors import AuthKeyDuplicatedError, FloodWaitError
from telethon.sessions import StringSession
from telethon.tl import functions
from telethon.tl.types import PeerChannel, User

from src.core.config import settings
from src.parser_bot.extractor import split_cargo_message_blocks
from src.parser_bot.stream import RedisLogisticsStream


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | parser-ingestor | %(message)s",
)
logger = logging.getLogger("parser-ingestor")


def _heartbeat_key() -> str:
    return (settings.parser_heartbeat_key or "").strip() or "parser:heartbeat"


async def _touch_heartbeat(redis_client: redis.Redis) -> None:
    ttl = max(60, int(settings.parser_heartbeat_ttl_sec))
    now_ts = int(time.time())
    await redis_client.set(_heartbeat_key(), str(now_ts), ex=ttl)


async def _self_health_check(redis_client: redis.Redis, *, started_monotonic: float) -> None:
    grace_sec = max(60, int(settings.parser_self_kill_grace_sec))
    stale_after_sec = max(grace_sec, int(settings.parser_self_kill_after_sec))

    while True:
        await asyncio.sleep(60)

        if (time.monotonic() - started_monotonic) < grace_sec:
            continue

        raw = await redis_client.get(_heartbeat_key())
        if raw is None:
            logger.error(
                "parser heartbeat missing after grace=%ss. Exiting for clean restart.",
                grace_sec,
            )
            os._exit(1)

        try:
            age_sec = max(0.0, time.time() - float(raw))
        except (TypeError, ValueError):
            logger.error("parser heartbeat is invalid (%r). Exiting for clean restart.", raw)
            os._exit(1)

        if age_sec > stale_after_sec:
            logger.error(
                "parser heartbeat stale age=%.1fs threshold=%ss. Exiting for clean restart.",
                age_sec,
                stale_after_sec,
            )
            os._exit(1)


def _parse_chat_ids(raw: str) -> list[int | str]:
    values: list[int | str] = []
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        if item.startswith("@"):
            values.append(item[1:])
            continue
        try:
            values.append(int(item))
        except ValueError:
            values.append(item)
    return values


def _build_session() -> StringSession | str:
    if settings.parser_tg_string_session.strip():
        return StringSession(settings.parser_tg_string_session.strip())
    return settings.parser_tg_session_name


async def _resolve_chat_filters(
    client: TelegramClient,
    chat_ids: list[int | str],
) -> list[int]:
    resolved: list[int] = []
    for item in chat_ids:
        if isinstance(item, int):
            resolved.append(item)
            logger.info("watch target=%s peer_id=%s type=int", item, item)
            continue

        try:
            entity = await client.get_entity(item)
        except Exception as exc:
            logger.warning("skip unresolved target=%s error=%s", item, str(exc)[:200])
            continue

        if isinstance(entity, User):
            logger.warning(
                "skip non-chat target=%s resolved_user=%s",
                item,
                getattr(entity, "username", None) or getattr(entity, "id", None),
            )
            continue

        peer_id = int(utils.get_peer_id(entity))
        resolved.append(peer_id)
        logger.info(
            "watch target=%s peer_id=%s title=%s username=%s type=%s",
            item,
            peer_id,
            getattr(entity, "title", None) or getattr(entity, "first_name", None),
            getattr(entity, "username", None),
            type(entity).__name__,
        )

        # If the configured channel has a linked discussion chat, watch it too.
        # Many logistics channels post into the broadcast channel, while the real
        # live traffic happens in the linked discussion group.
        try:
            full = await client(functions.channels.GetFullChannelRequest(channel=entity))
            linked_chat_id = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
        except Exception:
            linked_chat_id = None

        if not linked_chat_id:
            continue

        try:
            linked_entity = await client.get_entity(PeerChannel(linked_chat_id))
        except Exception as exc:
            logger.warning("skip linked chat target=%s error=%s", item, str(exc)[:200])
            continue

        linked_peer_id = int(utils.get_peer_id(linked_entity))
        if linked_peer_id in resolved:
            continue

        resolved.append(linked_peer_id)
        logger.info(
            "watch linked target=%s peer_id=%s title=%s username=%s type=%s",
            item,
            linked_peer_id,
            getattr(linked_entity, "title", None) or getattr(linked_entity, "first_name", None),
            getattr(linked_entity, "username", None),
            type(linked_entity).__name__,
        )

    return resolved


def _build_source_name_from_chat(chat_id: int | None, chat: object | None) -> str:
    username = (getattr(chat, "username", None) or "").strip().lower() if chat else ""
    if username:
        return f"tg:{username}"

    title = (getattr(chat, "title", None) or "").strip().lower() if chat else ""
    if title:
        slug = re.sub(r"[^a-z0-9а-яё]+", "-", title).strip("-")
        if slug:
            return f"tg:{slug[:50]}"

    if chat_id:
        return f"tg:chat_{chat_id}"

    return settings.parser_source_name


async def _build_source_name(event: events.NewMessage.Event) -> str:
    chat = getattr(event, "chat", None)
    if chat is None:
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None
    return _build_source_name_from_chat(event.chat_id, chat)


def _event_peer_id(event: events.NewMessage.Event) -> int | None:
    candidates = [
        getattr(event, "chat_id", None),
        getattr(event, "peer_id", None),
        getattr(getattr(event, "message", None), "peer_id", None),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if isinstance(candidate, int):
                return int(candidate)
            return int(utils.get_peer_id(candidate))
        except Exception:
            continue
    return None


async def _enqueue_message(
    stream: RedisLogisticsStream,
    *,
    raw_text: str,
    chat_id: int | str,
    message_id: int,
    source: str,
) -> int:
    blocks = split_cargo_message_blocks(raw_text)
    total = 0

    for idx, block in enumerate(blocks):
        entry_id = await stream.add_raw_message(
            raw_text=block[:4000],
            chat_id=str(chat_id or "unknown"),
            message_id=int(message_id),
            source=source,
            received_at=int(time.time()),
        )
        total += 1
        logger.info(
            "stream enqueue id=%s chat=%s message=%s part=%s/%s",
            entry_id,
            chat_id,
            message_id,
            idx + 1,
            len(blocks),
        )

    if total:
        await _touch_heartbeat(stream.redis)

    return total


async def _startup_backfill(
    client: TelegramClient,
    stream: RedisLogisticsStream,
    watched_chat_ids: set[int],
) -> None:
    limit = max(0, settings.parser_startup_backfill_limit)
    minutes = max(0, settings.parser_startup_backfill_minutes)
    if limit == 0 or minutes == 0:
        return

    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    total = 0

    for peer_id in watched_chat_ids:
        try:
            entity = await client.get_entity(peer_id)
        except Exception as exc:
            logger.warning("skip startup backfill chat=%s error=%s", peer_id, str(exc)[:200])
            continue

        source = _build_source_name_from_chat(peer_id, entity)
        queued = 0

        async for message in client.iter_messages(entity, limit=limit):
            if not message or not (message.message or "").strip():
                continue
            if message.date is None or message.date < since:
                continue
            queued_now = await _enqueue_message(
                stream,
                raw_text=message.message,
                chat_id=peer_id,
                message_id=int(message.id),
                source=source,
            )
            queued += queued_now
            total += queued_now

        logger.info(
            "startup backfill chat=%s source=%s queued=%s limit=%s window=%sm",
            peer_id,
            source,
            queued,
            limit,
            minutes,
        )

    if total:
        logger.info("startup backfill complete queued_total=%s", total)


async def _run_once(stream: RedisLogisticsStream, chat_ids: list[int | str]) -> None:
    client = TelegramClient(_build_session(), settings.parser_tg_api_id, settings.parser_tg_api_hash)
    health_task: asyncio.Task[None] | None = None
    await client.start()
    resolved_chat_ids = await _resolve_chat_filters(client, chat_ids)
    if not resolved_chat_ids:
        logger.error("No valid parser chats resolved from config=%s", ",".join(str(c) for c in chat_ids))
        await client.disconnect()
        return
    watched_chat_ids = set(resolved_chat_ids)
    await _touch_heartbeat(stream.redis)
    health_task = asyncio.create_task(
        _self_health_check(stream.redis, started_monotonic=time.monotonic())
    )
    await _startup_backfill(client, stream, watched_chat_ids)

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        peer_id = _event_peer_id(event)
        if peer_id not in watched_chat_ids:
            return

        raw_message = getattr(getattr(event, "message", None), "message", None)
        text = (raw_message or event.raw_text or "").strip()
        if not text:
            return

        try:
            await _enqueue_message(
                stream,
                raw_text=text,
                chat_id=peer_id or event.chat_id or "unknown",
                message_id=int(event.id),
                source=await _build_source_name(event),
            )
        except Exception as exc:
            logger.warning(
                "stream enqueue failed chat=%s message=%s error=%s",
                event.chat_id,
                event.id,
                str(exc)[:200],
            )

    logger.info(
        "Ingestor started: configured=%s resolved=%s stream=%s",
        ",".join(str(c) for c in chat_ids),
        ",".join(str(c) for c in resolved_chat_ids),
        settings.parser_stream_name,
    )
    try:
        await client.run_until_disconnected()
    finally:
        if health_task is not None:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
        with contextlib.suppress(Exception):
            await client.disconnect()


async def run() -> None:
    if not settings.parser_enabled:
        logger.info("Parser ingestor disabled (PARSER_ENABLED=false). Exit.")
        return
    if not settings.parser_tg_api_id or not settings.parser_tg_api_hash:
        logger.error("PARSER_TG_API_ID / PARSER_TG_API_HASH are required")
        return

    chat_ids = _parse_chat_ids(settings.parser_chat_ids)
    if not chat_ids:
        logger.error("PARSER_CHAT_IDS is empty")
        return

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    stream = RedisLogisticsStream(
        redis_client,
        stream_name=settings.parser_stream_name,
        maxlen=settings.parser_stream_maxlen,
    )

    try:
        while True:
            try:
                await _run_once(stream, chat_ids)
                await asyncio.sleep(1)
            except FloodWaitError as exc:
                wait_seconds = max(1, int(getattr(exc, "seconds", 30)))
                logger.warning("FloodWaitError: sleep %ss", wait_seconds)
                await asyncio.sleep(wait_seconds)
            except AuthKeyDuplicatedError:
                logger.error("AuthKeyDuplicatedError: session invalidated. Stop parser ingestor.")
                return
            except Exception as exc:
                logger.exception("ingestor crashed: %s", str(exc)[:200])
                await asyncio.sleep(5)
    finally:
        await redis_client.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
