from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass

import redis.asyncio as redis
import vk_api

from src.core.config import settings
from src.parser_bot.extractor import parse_cargo_message
from src.parser_bot.stream import RedisLogisticsStream


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | vk-parser | %(message)s",
)
logger = logging.getLogger("vk-parser")


@dataclass(slots=True)
class VKPost:
    group_id: int
    post_id: int
    text: str
    post_ts: int

    @property
    def url(self) -> str:
        return f"https://vk.com/wall-{self.group_id}_{self.post_id}"


def _keyword_list() -> list[str]:
    return [item.strip().lower() for item in (settings.parser_keywords or "").split(",") if item.strip()]


def _group_ids() -> list[int]:
    results: list[int] = []
    raw_items = [item.strip() for item in (settings.vk_group_ids or "").split(",") if item.strip()]
    for raw in raw_items:
        value = raw.lower()
        if value.startswith("club"):
            value = value[4:]
        elif value.startswith("public"):
            value = value[6:]
        if value.startswith("-"):
            value = value[1:]
        try:
            group_id = int(value)
        except ValueError:
            logger.warning("skip invalid VK group id=%r", raw)
            continue
        if group_id > 0 and group_id not in results:
            results.append(group_id)
    return results


def _post_message_id(group_id: int, post_id: int) -> int:
    key = f"vk:{group_id}:{post_id}".encode("utf-8")
    return int(hashlib.sha1(key).hexdigest()[:15], 16)


def _dedupe_key(group_id: int, post_id: int) -> str:
    return f"vk:seen:{group_id}:{post_id}"


def _extract_copy_history_text(post: dict) -> str:
    pieces: list[str] = []
    for item in post.get("copy_history") or []:
        text = str(item.get("text") or "").strip()
        if text:
            pieces.append(text)
    return "\n".join(pieces)


def _normalize_post(post: dict, *, group_id: int) -> VKPost | None:
    post_id = int(post.get("id") or 0)
    if post_id <= 0:
        return None
    if int(post.get("marked_as_ads") or 0):
        return None

    text_parts = [
        str(post.get("text") or "").strip(),
        _extract_copy_history_text(post).strip(),
    ]
    text = "\n".join(part for part in text_parts if part).strip()
    if not text:
        return None

    post_ts = int(post.get("date") or time.time())
    return VKPost(group_id=group_id, post_id=post_id, text=text, post_ts=post_ts)


def _looks_like_cargo_post(text: str, *, keywords: list[str]) -> bool:
    parsed = parse_cargo_message(text, keywords=keywords)
    if not parsed:
        return False
    return bool(parsed.from_city and parsed.to_city)


def _fetch_group_posts_sync(group_id: int, *, count: int) -> list[dict]:
    session = vk_api.VkApi(token=settings.vk_access_token)
    api = session.get_api()
    response = api.wall.get(
        owner_id=-group_id,
        count=max(1, int(count)),
        filter="owner",
        extended=0,
        v=settings.vk_api_version,
    )
    return list(response.get("items") or [])


async def _fetch_group_posts(group_id: int) -> list[VKPost]:
    raw_posts = await asyncio.to_thread(
        _fetch_group_posts_sync,
        group_id,
        count=settings.vk_fetch_count,
    )
    posts: list[VKPost] = []
    for raw in raw_posts:
        post = _normalize_post(raw, group_id=group_id)
        if post is not None:
            posts.append(post)
    return posts


async def _enqueue_post(
    redis_client: redis.Redis,
    stream: RedisLogisticsStream,
    *,
    post: VKPost,
) -> bool:
    was_new = await redis_client.set(
        _dedupe_key(post.group_id, post.post_id),
        "1",
        ex=max(3600, int(settings.vk_dedupe_ttl_sec)),
        nx=True,
    )
    if not was_new:
        return False

    entry_id = await stream.add_raw_message(
        raw_text=post.text[:4000],
        chat_id=f"vk:club{post.group_id}",
        message_id=_post_message_id(post.group_id, post.post_id),
        source=settings.vk_source_name or "vk-parser-bot",
        external_url=post.url,
        received_at=post.post_ts,
    )
    logger.info("stream enqueue id=%s group=%s post=%s", entry_id, post.group_id, post.post_id)
    return True


async def _run_once() -> int:
    keywords = _keyword_list()
    if not keywords:
        logger.error("PARSER_KEYWORDS is empty")
        return 0
    if not settings.vk_access_token:
        logger.error("VK_ACCESS_TOKEN is empty")
        return 0

    group_ids = _group_ids()
    if not group_ids:
        logger.error("VK_GROUP_IDS is empty")
        return 0

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    stream = RedisLogisticsStream(
        redis_client,
        stream_name=settings.parser_stream_name,
        maxlen=settings.parser_stream_maxlen,
    )

    total_enqueued = 0
    try:
        for group_id in group_ids:
            try:
                posts = await _fetch_group_posts(group_id)
            except Exception as exc:
                logger.warning("vk fetch failed group=%s error=%s", group_id, str(exc)[:200])
                continue

            logger.info("fetched group=%s count=%s", group_id, len(posts))
            for post in posts:
                if not _looks_like_cargo_post(post.text, keywords=keywords):
                    continue
                if await _enqueue_post(redis_client, stream, post=post):
                    total_enqueued += 1
    finally:
        await redis_client.aclose()

    logger.info("cycle complete enqueued=%s", total_enqueued)
    return total_enqueued


async def run_forever() -> None:
    if not settings.vk_enabled:
        logger.info("VK parser disabled (VK_ENABLED=false). Exit.")
        return

    base_interval = max(15, int(settings.vk_poll_interval_sec))
    while True:
        try:
            await _run_once()
        except Exception as exc:
            logger.exception("vk cycle failed error=%s", str(exc)[:200])

        sleep_seconds = random.uniform(max(10.0, base_interval * 0.85), max(20.0, base_interval * 1.15))
        logger.info("sleep %.1fs before next cycle", sleep_seconds)
        await asyncio.sleep(sleep_seconds)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
