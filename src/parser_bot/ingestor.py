from __future__ import annotations

import asyncio
import logging
import re
import time

import redis.asyncio as redis
from telethon import TelegramClient, events, utils
from telethon.errors import AuthKeyDuplicatedError, FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import User

from src.core.config import settings
from src.parser_bot.stream import RedisLogisticsStream


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | parser-ingestor | %(message)s",
)
logger = logging.getLogger("parser-ingestor")


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

    return resolved


async def _build_source_name(event: events.NewMessage.Event) -> str:
    chat = getattr(event, "chat", None)
    if chat is None:
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None

    username = (getattr(chat, "username", None) or "").strip().lower() if chat else ""
    if username:
        return f"tg:{username}"

    title = (getattr(chat, "title", None) or "").strip().lower() if chat else ""
    if title:
        slug = re.sub(r"[^a-z0-9а-яё]+", "-", title).strip("-")
        if slug:
            return f"tg:{slug[:50]}"

    if event.chat_id:
        return f"tg:chat_{event.chat_id}"

    return settings.parser_source_name


async def _run_once(stream: RedisLogisticsStream, chat_ids: list[int | str]) -> None:
    client = TelegramClient(_build_session(), settings.parser_tg_api_id, settings.parser_tg_api_hash)
    await client.start()
    resolved_chat_ids = await _resolve_chat_filters(client, chat_ids)
    if not resolved_chat_ids:
        logger.error("No valid parser chats resolved from config=%s", ",".join(str(c) for c in chat_ids))
        await client.disconnect()
        return
    watched_chat_ids = set(resolved_chat_ids)

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in watched_chat_ids:
            return

        text = (event.raw_text or "").strip()
        if not text:
            return

        try:
            entry_id = await stream.add_raw_message(
                raw_text=text[:4000],
                chat_id=str(event.chat_id or "unknown"),
                message_id=int(event.id),
                source=await _build_source_name(event),
                received_at=int(time.time()),
            )
            logger.debug("stream enqueue id=%s chat=%s message=%s", entry_id, event.chat_id, event.id)
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
    await client.run_until_disconnected()


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
