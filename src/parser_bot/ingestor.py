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
from telethon.errors import AuthKeyDuplicatedError, FloodWaitError, UserAlreadyParticipantError, ChannelPrivateError
from telethon.sessions import StringSession
from telethon.tl import functions, types
from telethon.tl.types import PeerChannel, User, Channel, Chat

from src.core.config import settings
from src.parser_bot.extractor import split_cargo_message_blocks
from src.parser_bot.stream import RedisLogisticsStream


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | parser-ingestor | %(message)s",
)
logger = logging.getLogger("parser-ingestor")


REDIS_DISCOVERED_KEY = "parser:discovered_channels"  # Redis SET с peer_id
DISCOVERY_INTERVAL_SEC = 6 * 3600  # раз в 6 часов
DISCOVERY_KEYWORDS = [
    "грузы по России", "фрахт тент реф", "грузы РФ тент",
    "биржа грузов Россия", "перевозки тент борт реф",
    "грузоперевозки Москва", "грузоперевозки Урал",
    "грузоперевозки Сибирь", "грузоперевозки Краснодар",
    "тент реф борт груз", "груз Москва Екатеринбург",
]
CARGO_TITLE_HINTS = re.compile(
    r"груз|логист|фрахт|перевоз|транспорт|ати|ati|cargo|freight|экспедит|тент|фура",
    re.I,
)


async def _auto_discover_channels(
    client: TelegramClient,
    redis_client: redis.Redis,
    watched_chat_ids: set[int],
) -> int:
    """Ищет каналы с грузами, вступает, добавляет в watched_chat_ids."""
    found = 0
    for keyword in DISCOVERY_KEYWORDS:
        try:
            result = await client(functions.contacts.SearchRequest(q=keyword, limit=20))
            chats = getattr(result, "chats", []) or []
            for chat in chats:
                if not isinstance(chat, (Channel, Chat)):
                    continue
                title = getattr(chat, "title", "") or ""
                if not CARGO_TITLE_HINTS.search(title):
                    continue
                peer_id = int(utils.get_peer_id(chat))
                if peer_id in watched_chat_ids:
                    continue
                # Проверяем что не приватный
                if getattr(chat, "access_hash", None) is None:
                    continue
                try:
                    await client(functions.channels.JoinChannelRequest(channel=chat))
                    logger.info("auto-joined channel title=%r peer_id=%s", title, peer_id)
                except (UserAlreadyParticipantError, ChannelPrivateError):
                    # Уже в канале — всё равно добавляем в watch
                    pass
                except FloodWaitError as e:
                    wait = max(1, int(getattr(e, "seconds", 60)))
                    logger.info("join flood wait %ss, sleeping", wait)
                    await asyncio.sleep(wait)
                    continue
                except Exception as e:
                    logger.warning("auto-join failed title=%r error=%s", title, str(e)[:100])
                    continue
                watched_chat_ids.add(peer_id)
                await redis_client.sadd(REDIS_DISCOVERED_KEY, peer_id)
                found += 1
            await asyncio.sleep(2)  # не спамим
        except Exception as e:
            logger.warning("discovery keyword=%r error=%s", keyword, str(e)[:100])
    logger.info("auto-discovery done: found=%s total_watched=%s", found, len(watched_chat_ids))
    return found


async def _load_discovered_channels(
    client: TelegramClient,
    redis_client: redis.Redis,
    watched_chat_ids: set[int],
) -> None:
    """Загружает ранее найденные каналы из Redis при старте."""
    saved = await redis_client.smembers(REDIS_DISCOVERED_KEY)
    for raw in saved:
        try:
            peer_id = int(raw)
            if peer_id not in watched_chat_ids:
                watched_chat_ids.add(peer_id)
                logger.info("loaded discovered channel peer_id=%s from redis", peer_id)
        except Exception:
            pass


async def _discovery_loop(
    client: TelegramClient,
    redis_client: redis.Redis,
    watched_chat_ids: set[int],
) -> None:
    """Фоновый цикл автодискавери каналов."""
    while True:
        await asyncio.sleep(60)  # первый запуск через минуту после старта
        try:
            await _auto_discover_channels(client, redis_client, watched_chat_ids)
        except Exception as e:
            logger.warning("discovery loop error: %s", e)
        await asyncio.sleep(DISCOVERY_INTERVAL_SEC)


def _heartbeat_key() -> str:
    return (settings.parser_heartbeat_key or "").strip() or "parser:heartbeat"


async def _update_heartbeat(redis_client: redis.Redis) -> None:
    ttl = max(60, int(settings.parser_heartbeat_ttl_sec))
    await redis_client.set(_heartbeat_key(), int(time.time()), ex=ttl)


async def _health_monitor(redis_client: redis.Redis, *, started_monotonic: float) -> None:
    grace_sec = max(60, int(settings.parser_self_kill_grace_sec))
    stale_after_sec = max(grace_sec, int(settings.parser_self_kill_after_sec))

    await asyncio.sleep(grace_sec)

    while True:
        await asyncio.sleep(60)

        # Avoid false positives right after restart.
        if (time.monotonic() - started_monotonic) < grace_sec:
            continue

        last_beat = await redis_client.get(_heartbeat_key())
        if not last_beat:
            logger.critical(
                "parser heartbeat missing after %ss grace. Exiting for clean restart.",
                grace_sec,
            )
            os._exit(1)

        try:
            age_sec = time.time() - int(last_beat)
        except (TypeError, ValueError):
            logger.critical("parser heartbeat is invalid (%r). Exiting for clean restart.", last_beat)
            os._exit(1)

        if age_sec > stale_after_sec:
            logger.critical(
                "parser heartbeat stale for %.1fs (threshold=%ss). Exiting for clean restart.",
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
        await _update_heartbeat(stream.redis)

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
    # Загружаем ранее найденные каналы из Redis
    await _load_discovered_channels(client, stream.redis, watched_chat_ids)
    await _update_heartbeat(stream.redis)
    health_task = asyncio.create_task(
        _health_monitor(stream.redis, started_monotonic=time.monotonic())
    )
    # Запускаем фоновый autodiscovery
    discovery_task = asyncio.create_task(
        _discovery_loop(client, stream.redis, watched_chat_ids)
    )
    await _startup_backfill(client, stream, watched_chat_ids)

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in watched_chat_ids:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        try:
            await _enqueue_message(
                stream,
                raw_text=text,
                chat_id=event.chat_id or "unknown",
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
        discovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await discovery_task
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
