from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from src.core.config import settings
from src.core.logger import logger
from src.core.redis import get_redis, close_redis
from src.core.database import init_db
from src.core.scheduler import setup_scheduler, scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.bot.bot import bot
    from src.bot.handlers.verification import router as verification_router
    from src.bot.handlers.start import router as start_router
    from src.bot.handlers.feedback import router as feedback_router
    from src.bot.handlers.admin import router as admin_router
    from src.bot.handlers.errors import router as errors_router
    from src.bot.handlers.reminder import router as reminder_router
    from src.bot.handlers.payments import router as payments_router
    from src.bot.handlers.referral import router as referral_router
    from src.bot.handlers.cargo import router as cargo_router
    from src.bot.handlers.search import router as search_router
    from src.bot.handlers.rating import router as rating_router
    from src.bot.handlers.profile import router as profile_router
    from src.bot.handlers.analytics import router as analytics_router
    from src.bot.handlers.chat import router as chat_router
    from src.bot.handlers.antifraud import router as antifraud_router
    from src.bot.handlers.geolocation import router as geolocation_router
    from src.bot.handlers.inline import router as inline_router
    from src.bot.handlers.claims import router as claims_router
    from src.bot.handlers.legal import router as legal_router
    from src.bot.middlewares.logging import LoggingMiddleware
    from src.bot.middlewares.watchdog import WatchdogMiddleware
    from src.core.services.watchdog import watchdog_loop

    logger.info("Starting bot...")
    
    await init_db()
    logger.info("Database initialized")

    try:
        from src.core.database import async_session
        from src.core.market_data import seed_market_prices
        from src.core.scheduler import archive_old_cargos_job
        async with async_session() as session:
            await seed_market_prices(session)
        logger.info("Market prices seeded")
    except Exception as e:
        logger.warning("Market prices seed failed: %s", e)

    try:
        await archive_old_cargos_job()
    except Exception as e:
        logger.warning("Archive old cargos failed: %s", e)
    
    redis = await get_redis()
    await redis.ping()
    logger.info("Redis connected")

    from aiogram import Dispatcher
    from aiogram.fsm.storage.redis import RedisStorage

    dp = Dispatcher(storage=RedisStorage(redis=redis))
    logger.info("FSM storage: Redis")

    setup_scheduler()

    import logging as _logging
    _logging.getLogger("aiogram").setLevel(_logging.DEBUG)

    async def debug_updates(handler, event, data):
        kind = type(event).__name__
        uid = getattr(event, "message_id", None) or getattr(event, "id", None)
        print(f"[DEBUG] RAW UPDATE: {kind} (id={uid})", flush=True)
        logger.info("RAW UPDATE: %s (id=%s)", kind, uid)
        return await handler(event, data)

    dp.message.outer_middleware(debug_updates)
    dp.callback_query.outer_middleware(debug_updates)
    dp.inline_query.outer_middleware(debug_updates)

    dp.message.middleware(WatchdogMiddleware())
    dp.callback_query.middleware(WatchdogMiddleware())
    dp.message.middleware(LoggingMiddleware())
    dp.callback_query.middleware(LoggingMiddleware())
    from src.bot.handlers.feed_commands import router as feed_commands_router
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(feed_commands_router)
    dp.include_router(cargo_router)
    dp.include_router(search_router)
    dp.include_router(inline_router)
    dp.include_router(rating_router)
    dp.include_router(profile_router)
    dp.include_router(analytics_router)
    dp.include_router(chat_router)
    dp.include_router(antifraud_router)
    dp.include_router(geolocation_router)
    dp.include_router(claims_router)
    dp.include_router(legal_router)
    dp.include_router(verification_router)
    dp.include_router(feedback_router)
    dp.include_router(errors_router)
    dp.include_router(reminder_router)
    dp.include_router(payments_router)
    dp.include_router(referral_router)

    async def _run_polling():
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook deleted, starting polling...")
            await dp.start_polling(
                bot,
                allowed_updates=["message", "callback_query", "inline_query", "pre_checkout_query"],
                handle_signals=False,
            )
        except BaseException as e:
            logger.error("Polling crashed: %s", e, exc_info=True)

    polling_task = asyncio.create_task(_run_polling())
    asyncio.create_task(watchdog_loop())
    logger.info("Bot polling started")
    logger.info("Watchdog started")
    yield
    logger.info("Shutting down...")
    scheduler.shutdown()
    polling_task.cancel()
    await bot.session.close()
    await close_redis()

app = FastAPI(title="Logistics Bot API", lifespan=lifespan)

TWA_ASSETS_DIR = Path("frontend/twa/dist/assets")
app.mount(
    "/webapp/assets",
    StaticFiles(directory=TWA_ASSETS_DIR, check_dir=False),
    name="twa-assets",
)

# Admin panel
from src.admin.routes import router as admin_panel_router
from src.webapp.routes import router as webapp_router
from src.api.ai import router as ai_router
from src.api.feed import router as feed_router
from src.api.antifraud import router as antifraud_api_router
from src.api.antifraud_admin import router as antifraud_admin_api_router
from src.api.internal import router as internal_api_router
from src.api.export import router as export_router
from src.api.analytics import router as analytics_router
from src.api.ws_feed import router as ws_feed_router
from src.api.favorites import router as favorites_router
from src.api.bridge import router as bridge_router
from src.api.company import router as company_router
from src.api.fleet import router as fleet_router
from src.api.cargos import router as cargos_router
from src.api.admin_stats import router as admin_stats_router
from src.api.docs_gen import router as docs_gen_router
from src.api.finance import router as finance_router
from src.api.teams import router as teams_router
from src.api.currency import router as currency_router
from src.api.subscriptions import router as subscriptions_router
from src.api.escrow import router as escrow_router
from src.api.geo import router as geo_router
from src.api.match import router as match_router
from src.core.ai_diag import explain_health
from src.core.services.watchdog import watchdog

app.include_router(admin_panel_router)
app.include_router(webapp_router)
app.include_router(ai_router)
app.include_router(feed_router)
app.include_router(export_router)
app.include_router(analytics_router)
app.include_router(ws_feed_router)
app.include_router(favorites_router)
app.include_router(bridge_router)
app.include_router(company_router)
app.include_router(fleet_router)
app.include_router(cargos_router)
app.include_router(admin_stats_router)
app.include_router(docs_gen_router)
app.include_router(finance_router)
app.include_router(teams_router)
app.include_router(currency_router)
app.include_router(subscriptions_router)
app.include_router(escrow_router)
app.include_router(geo_router)
app.include_router(match_router)
app.include_router(antifraud_api_router)
app.include_router(antifraud_admin_api_router)
app.include_router(internal_api_router)


@app.get("/health")
async def health_check():
    """Health check для внешнего мониторинга (Docker, uptime)."""
    health = await watchdog.check_health()
    is_healthy = all(
        "❌" not in str(v) for v in health["checks"].values()
    )
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": health["timestamp"],
        "checks": health["checks"],
        "metrics": health.get("metrics", {}),
    }


@app.get("/health/detailed")
async def health_detailed():
    """Детальный health check."""
    return await watchdog.check_health()


@app.get("/health/ai")
@app.get("/health_ai")
async def health_ai():
    """Health check with human-readable diagnosis."""
    health = await watchdog.check_health()
    return {
        "status": "ok",
        "timestamp": health["timestamp"],
        "diagnosis": explain_health(health),
        "health": health,
    }


@app.get("/api/health")
async def health():
    redis = await get_redis()
    return {"status": "ok", "redis": await redis.ping()}

@app.get("/api/stats")
async def api_stats():
    from src.core.database import async_session
    from src.core.models import User, Cargo, Report
    from sqlalchemy import select, func
    
    async with async_session() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        cargos = await session.scalar(select(func.count()).select_from(Cargo))
        reports = await session.scalar(select(func.count()).select_from(Report))
    
    return {"users": users, "cargos": cargos, "reports": reports}

@app.get("/api/cargos")
async def api_cargos(from_city: str = None, to_city: str = None):
    from src.core.database import async_session
    from src.core.models import Cargo, CargoStatus
    from sqlalchemy import select
    
    async with async_session() as session:
        query = select(Cargo).where(Cargo.status == CargoStatus.NEW)
        if from_city:
            query = query.where(Cargo.from_city.ilike(f"%{from_city}%"))
        if to_city:
            query = query.where(Cargo.to_city.ilike(f"%{to_city}%"))
        result = await session.execute(query.limit(50))
        cargos = result.scalars().all()
    
    return [{"id": c.id, "from": c.from_city, "to": c.to_city, "weight": c.weight, "price": c.price} for c in cargos]

@app.get("/")
async def root(request: Request):
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return RedirectResponse(url="/webapp", status_code=302)
    return {"message": "Logistics Bot API", "admin": "/admin", "webapp": "/webapp"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=settings.debug)
