from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path

import redis.asyncio as redis
from playwright.async_api import Page, async_playwright

from src.core.config import settings
from src.parser_bot.stream import RedisLogisticsStream

logger = logging.getLogger("avito-ingestor")

_DEFAULT_STATE_FILE = Path(__file__).parent.parent.parent / "avito_state.json"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'] });
window.chrome = { runtime: {} };
"""

SOURCE_CARGO = "avito:cargo"
SOURCE_TRUCKS = "avito:trucks"

_DEDUPE_PREFIX = "avito:seen:"
_DEDUPE_TTL = 86400 * 3


async def _is_seen(redis_client: redis.Redis, ad_id: str) -> bool:
    key = f"{_DEDUPE_PREFIX}{ad_id}"
    result = await redis_client.set(key, "1", ex=_DEDUPE_TTL, nx=True)
    return result is None


async def _make_context(playwright, *, headless: bool = True) -> tuple:
    browser = await playwright.firefox.launch(headless=headless)

    state_path = Path(settings.avito_state_file) if settings.avito_state_file else _DEFAULT_STATE_FILE
    if not state_path.is_absolute():
        state_path = Path(__file__).parent.parent.parent / state_path
    storage_state = str(state_path) if state_path.exists() else None
    if not storage_state:
        logger.warning("avito_state.json not found; running without auth")

    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        storage_state=storage_state,
    )
    await context.add_init_script(STEALTH_JS)
    return browser, context


async def _scrape_ads(page: Page, search_url: str, max_ads: int) -> list[dict]:
    logger.info("avito fetch url=%s", search_url)
    await page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)

    try:
        await page.wait_for_selector("[data-marker='item']", timeout=15_000)
    except Exception:
        logger.warning("avito cards not visible; anti-bot or empty page")
        return []

    items = await page.query_selector_all("[data-marker='item']")
    ads: list[dict] = []
    for item in items[:max_ads]:
        try:
            ad_id = await item.get_attribute("data-item-id") or ""
            title_el = await item.query_selector("[itemprop='name']")
            title = (await title_el.inner_text()).strip() if title_el else ""

            desc_el = await item.query_selector("[class*='description']")
            description = (await desc_el.inner_text()).strip() if desc_el else ""

            price_el = await item.query_selector("[itemprop='price']")
            price = (await price_el.get_attribute("content") or "").strip() if price_el else ""

            link_el = await item.query_selector("a[data-marker='item-title']")
            href = await link_el.get_attribute("href") if link_el else ""
            full_url = f"https://www.avito.ru{href}" if href and href.startswith("/") else href

            if not (title or description):
                continue

            ads.append(
                {
                    "id": ad_id or hashlib.md5(f"{title}{description}".encode()).hexdigest()[:12],
                    "title": title,
                    "description": description,
                    "price": price,
                    "url": full_url,
                }
            )
        except Exception as exc:
            logger.debug("avito card parse failed: %s", exc)
    logger.info("avito scraped ads=%s", len(ads))
    return ads


def _build_raw_text(ad: dict) -> str:
    parts = [ad["title"]]
    if ad["description"]:
        parts.append(ad["description"])
    if ad["price"]:
        parts.append(f"Цена: {ad['price']} руб.")
    if ad["url"]:
        parts.append(f"Ссылка: {ad['url']}")
    return "\n".join(parts)


def _stable_message_id(ad_id: str) -> int:
    digest = hashlib.sha1(ad_id.encode("utf-8")).hexdigest()[:15]
    return int(digest, 16)


async def _scrape_and_enqueue(
    stream: RedisLogisticsStream,
    redis_client: redis.Redis,
    page: Page,
    *,
    search_url: str,
    source: str,
    max_ads: int,
) -> int:
    ads = await _scrape_ads(page, search_url, max_ads)
    new_count = 0
    for ad in ads:
        if await _is_seen(redis_client, ad["id"]):
            continue
        await stream.add_raw_message(
            raw_text=_build_raw_text(ad)[:4000],
            chat_id="avito",
            message_id=_stable_message_id(str(ad["id"])),
            source=source,
            external_url=ad["url"] or None,
            received_at=int(time.time()),
        )
        new_count += 1
        logger.info("avito enqueue source=%s ad_id=%s title=%.60s", source, ad["id"], ad["title"])
    return new_count


async def _run_once(
    stream: RedisLogisticsStream,
    redis_client: redis.Redis,
    targets: list[tuple[str, str]],
    max_ads: int,
) -> int:
    total = 0
    async with async_playwright() as playwright:
        browser, context = await _make_context(playwright)
        try:
            for url, source in targets:
                page = await context.new_page()
                try:
                    total += await _scrape_and_enqueue(
                        stream,
                        redis_client,
                        page,
                        search_url=url,
                        source=source,
                        max_ads=max_ads,
                    )
                finally:
                    await page.close()
        finally:
            await context.close()
            await browser.close()
    return total


async def run() -> None:
    if not settings.avito_enabled:
        logger.info("Avito ingestor disabled (AVITO_ENABLED=false). Exit.")
        return

    targets: list[tuple[str, str]] = []
    if settings.avito_search_url:
        targets.append((settings.avito_search_url, SOURCE_CARGO))
    if settings.avito_trucks_url:
        targets.append((settings.avito_trucks_url, SOURCE_TRUCKS))
    if not targets:
        logger.error("Neither AVITO_SEARCH_URL nor AVITO_TRUCKS_URL is set")
        return

    logger.info(
        "Avito ingestor start | targets=%s | interval=%ss | max_ads=%s",
        [source for _, source in targets],
        settings.avito_interval_sec,
        settings.avito_max_ads,
    )

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    stream = RedisLogisticsStream(
        redis_client,
        stream_name=settings.parser_stream_name,
        maxlen=settings.parser_stream_maxlen,
    )

    try:
        while True:
            try:
                new = await _run_once(stream, redis_client, targets, settings.avito_max_ads)
                logger.info("avito iteration done new_ads=%s next_in=%ss", new, settings.avito_interval_sec)
            except Exception as exc:
                logger.exception("avito iteration failed: %s", str(exc)[:300])
            await asyncio.sleep(settings.avito_interval_sec)
    finally:
        await redis_client.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | avito-ingestor | %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
