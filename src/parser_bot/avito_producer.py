from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
import random
import time
from urllib.parse import urljoin, urlparse

import redis.asyncio as redis
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, async_playwright

from src.core.config import settings
from src.parser_bot.extractor import parse_cargo_message
from src.parser_bot.stream import RedisLogisticsStream


logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | avito-producer | %(message)s",
)
logger = logging.getLogger("avito-producer")


@dataclass(slots=True)
class AvitoSeed:
    label: str
    url: str
    positive_markers: tuple[str, ...]


@dataclass(slots=True)
class AvitoCandidate:
    seed_label: str
    url: str
    title: str
    description: str | None
    price: str | None

    @property
    def raw_text(self) -> str:
        parts = [
            self.title.strip(),
            (self.description or "").strip(),
            (self.price or "").strip(),
        ]
        return "\n".join(part for part in parts if part)


SEEDS: tuple[AvitoSeed, ...] = (
    AvitoSeed(
        label="avito_jobs",
        url="https://www.avito.ru/rossiya/vakansii?q=%D0%B2%D0%BE%D0%B4%D0%B8%D1%82%D0%B5%D0%BB%D1%8C+%D1%81%D0%BE+%D1%81%D0%B2%D0%BE%D0%B8%D0%BC+%D0%B3%D1%80%D1%83%D0%B7%D0%BE%D0%B2%D1%8B%D0%BC+%D0%B0%D0%B2%D1%82%D0%BE",
        positive_markers=(
            "нужен перевозчик",
            "требуется перевозка",
            "маршрут",
            "рейс",
            "оплата",
            "ищем перевозчика",
            "постоянный рейс",
        ),
    ),
    AvitoSeed(
        label="avito_services",
        url="https://www.avito.ru/rossiya/predlozheniya_uslug?q=%D1%82%D1%80%D0%B5%D0%B1%D1%83%D0%B5%D1%82%D1%81%D1%8F+%D0%BF%D0%B5%D1%80%D0%B5%D0%B2%D0%BE%D0%B7%D0%BA%D0%B0",
        positive_markers=(
            "нужно перевезти",
            "требуется перевозка",
            "нужна машина",
            "переезд",
            "заплачу",
            "доставка",
        ),
    ),
)

STOP_MARKERS: tuple[str, ...] = (
    "опытный водитель",
    "грузчики",
    "своя газель",
    "перевезу ваш груз",
    "перевезу груз",
    "чистый кузов",
    "свой авто",
    "со своим авто",
    "24/7",
    "недорого",
)

COMMON_POSITIVE_MARKERS: tuple[str, ...] = (
    "требуется",
    "нужна машина",
    "нужен перевозчик",
    "ищем постоянного перевозчика",
    "оплата по безналу",
    "оплата на карту",
)

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
)


def _keyword_list() -> list[str]:
    return [item.strip().lower() for item in (settings.parser_keywords or "").split(",") if item.strip()]


def _normalized_page_url(seed_url: str, page_num: int) -> str:
    if page_num <= 1:
        return seed_url
    joiner = "&" if "?" in seed_url else "?"
    return f"{seed_url}{joiner}p={page_num}"


def _normalize_listing_url(href: str | None) -> str | None:
    raw = (href or "").strip()
    if not raw:
        return None
    url = urljoin("https://www.avito.ru", raw)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if "avito.ru" not in parsed.netloc:
        return None
    return url


def _message_id_from_url(url: str) -> int:
    return int(hashlib.sha1(url.encode("utf-8")).hexdigest()[:15], 16)


def _looks_like_customer_request(text: str, *, seed: AvitoSeed, keywords: list[str]) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False

    if any(marker in lowered for marker in STOP_MARKERS):
        return False

    if not any(marker in lowered for marker in (*COMMON_POSITIVE_MARKERS, *seed.positive_markers)):
        return False

    parsed = parse_cargo_message(text, keywords=keywords)
    if not parsed:
        return False
    return bool(parsed.from_city and parsed.to_city)


async def _page_pause() -> None:
    await asyncio.sleep(random.uniform(5.0, 15.0))


async def _first_text(scope, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            if await locator.count() == 0:
                continue
            text = (await locator.first.inner_text(timeout=1200)).strip()
            if text:
                return text
        except PlaywrightError:
            continue
    return None


async def _dismiss_overlay(page: Page) -> None:
    dismiss_selectors = (
        "button[data-marker='modal-close-button']",
        "button[aria-label='Закрыть']",
        "button:has-text('Понятно')",
        "button:has-text('Хорошо')",
    )
    for selector in dismiss_selectors:
        button = page.locator(selector)
        try:
            if await button.count() == 0:
                continue
            await button.first.click(timeout=800)
            await asyncio.sleep(0.5)
            return
        except PlaywrightError:
            continue


async def _new_context(playwright) -> tuple[Browser, BrowserContext]:
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        locale="ru-RU",
        timezone_id="Europe/Moscow",
        viewport={"width": 1440, "height": 1100},
    )
    return browser, context


async def _extract_candidates(page: Page, *, seed: AvitoSeed, keywords: list[str]) -> list[AvitoCandidate]:
    title_links = page.locator("a[data-marker='item-title'], a[itemprop='url']")
    try:
        total = min(await title_links.count(), 40)
    except PlaywrightError:
        return []

    results: list[AvitoCandidate] = []
    seen_urls: set[str] = set()
    for idx in range(total):
        link = title_links.nth(idx)
        try:
            href = _normalize_listing_url(await link.get_attribute("href"))
            title = (await link.inner_text(timeout=1200)).strip()
        except PlaywrightError:
            continue

        if not href or not title or href in seen_urls:
            continue

        card = link.locator("xpath=ancestor::*[self::article or self::li or self::div][1]")
        description = await _first_text(
            card,
            (
                "[data-marker='item-description']",
                "[itemprop='description']",
                "p",
            ),
        )
        price = await _first_text(
            card,
            (
                "[data-marker='item-price']",
                "[itemprop='price']",
                "strong",
            ),
        )
        candidate = AvitoCandidate(
            seed_label=seed.label,
            url=href,
            title=title,
            description=description,
            price=price,
        )
        if not _looks_like_customer_request(candidate.raw_text, seed=seed, keywords=keywords):
            continue

        results.append(candidate)
        seen_urls.add(href)
    return results


async def _enqueue_candidates(
    stream: RedisLogisticsStream,
    *,
    candidates: list[AvitoCandidate],
) -> int:
    enqueued = 0
    now_ts = int(time.time())
    for item in candidates:
        entry_id = await stream.add_raw_message(
            raw_text=item.raw_text[:4000],
            chat_id=f"avito:{item.seed_label}",
            message_id=_message_id_from_url(item.url),
            source="avito",
            external_url=item.url,
            received_at=now_ts,
        )
        enqueued += 1
        logger.info("stream enqueue id=%s seed=%s url=%s", entry_id, item.seed_label, item.url)
    return enqueued


async def _scrape_seed(page: Page, *, seed: AvitoSeed, keywords: list[str]) -> list[AvitoCandidate]:
    collected: list[AvitoCandidate] = []
    seen_urls: set[str] = set()
    max_pages = max(1, int(settings.avito_max_pages_per_run))

    for page_num in range(1, max_pages + 1):
        url = _normalized_page_url(seed.url, page_num)
        logger.info("fetch seed=%s page=%s", seed.label, page_num)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except PlaywrightError as exc:
            logger.warning("page load failed seed=%s page=%s error=%s", seed.label, page_num, str(exc)[:160])
            await _page_pause()
            continue

        await _dismiss_overlay(page)
        try:
            await page.mouse.wheel(0, random.randint(600, 1400))
        except PlaywrightError:
            pass

        page_candidates = await _extract_candidates(page, seed=seed, keywords=keywords)
        for candidate in page_candidates:
            if candidate.url in seen_urls:
                continue
            collected.append(candidate)
            seen_urls.add(candidate.url)

        await _page_pause()

    return collected


async def _run_once() -> None:
    keywords = _keyword_list()
    if not keywords:
        logger.warning("parser keywords empty; avito producer skipped")
        return

    redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    stream = RedisLogisticsStream(
        redis_client,
        stream_name=settings.parser_stream_name,
        maxlen=settings.parser_stream_maxlen,
    )

    total_enqueued = 0
    browser: Browser | None = None
    context: BrowserContext | None = None
    try:
        async with async_playwright() as playwright:
            browser, context = await _new_context(playwright)
            page = await context.new_page()
            all_candidates: list[AvitoCandidate] = []
            for seed in SEEDS:
                all_candidates.extend(await _scrape_seed(page, seed=seed, keywords=keywords))

            deduped: list[AvitoCandidate] = []
            seen_urls: set[str] = set()
            for candidate in all_candidates:
                if candidate.url in seen_urls:
                    continue
                deduped.append(candidate)
                seen_urls.add(candidate.url)

            total_enqueued = await _enqueue_candidates(stream, candidates=deduped)
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        await redis_client.aclose()

    logger.info("cycle complete enqueued=%s", total_enqueued)


async def run_forever() -> None:
    base_interval_sec = max(1, int(settings.avito_poll_interval_min)) * 60
    while True:
        if not settings.avito_enabled:
            logger.info(
                "Avito producer disabled (AVITO_ENABLED=false). Sleep %.1fs before re-check.",
                float(base_interval_sec),
            )
            await asyncio.sleep(float(base_interval_sec))
            continue

        try:
            await _run_once()
        except Exception as exc:
            logger.exception("avito cycle failed error=%s", str(exc)[:200])

        sleep_seconds = random.uniform(max(60.0, base_interval_sec * 0.85), max(120.0, base_interval_sec * 1.15))
        logger.info("sleep %.1fs before next cycle", sleep_seconds)
        await asyncio.sleep(sleep_seconds)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
