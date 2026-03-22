import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from threading import Lock
from time import monotonic

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import os

from app.core.config import settings
from app.db.database import init_db

from app.api.routes import (
    auth,
    loads,
    bids,
    messages,
    ai as ai_routes,
    lawyer,
    logist,
    antifraud,
    documents,
    chatbot,
    rating,
    complaints,
    forum,
    geo,
    test_data,
    telegram,
    bot_api,
    cargos,
    applications,
    deals_sync,
    documents_sync,
    moderation,
    vehicles,
    consolidation,
    analytics,
    document_sign,
    dicts,
    shipments,
    route_calc,
    me,
    profile,
    trust,
    internal,
    inn_lookup,
    employees,
    public_stats,
    traffic_light,
    blacklist,
    verification,
    seo_pages,
    email_digest,
    referral,
    matching,
    billing,
    tenders,
    push_web,
    tms,
)
from app.api import ai as ai_hybrid_router
from app.api import antifraud as antifraud_review_router
from app.api import antifraud_admin as antifraud_admin_router
from app.ai import routes as ai_scoring_routes
from app.admin.routes import router as admin_router
from app.api.routes import complaints_ai
from app.api.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler_task = asyncio.create_task(email_digest.daily_digest_scheduler())
    yield
    scheduler_task.cancel()


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.APP_NAME} API",
    description="""
🚚 **ГрузПоток** — Биржа грузоперевозок нового поколения

## 🤖 5 ИИ-Модулей:

1. ⚖️ **AI-Юрист** — проверка заявок, договоры, риски
2. 🚛 **AI-Логист** — подбор машин, ТОП-3 предложения  
3. 🛡️ **AI-Антимошенник** — защита от мошенников
4. 📄 **AI-Документы** — УПД, ТТН, договоры, счета
5. 💬 **AI-Чатбот** — автоматический диспетчер 24/7

**Такого НЕТ ни у кого. Это убийца АТИ!** 🔥
""",
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

# Сжимаем крупные ответы (index.html, списки, аналитика), чтобы UI открывался быстрее.
app.add_middleware(
    GZipMiddleware,
    minimum_size=1024,
)

_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()


def _client_ip(request: Request) -> str:
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _is_exempt_rate_limit_path(path: str) -> bool:
    return (
        path.startswith("/internal/")
        or path.startswith("/static/")
        or path.startswith("/sign/")
        or path == "/favicon.ico"
    )


def _is_authenticated_request(request: Request) -> bool:
    return bool(request.headers.get("authorization") or request.cookies.get("auth_token"))


def _check_rate_limit(key: str, *, limit: int, window_sec: int = 60) -> bool:
    now = monotonic()
    with _rate_limit_lock:
        bucket = _rate_limit_buckets[key]
        while bucket and now - bucket[0] >= window_sec:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True




@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(self), microphone=()"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path

    if request.method == "OPTIONS" or _is_exempt_rate_limit_path(path):
        return await call_next(request)

    ip = _client_ip(request)
    if path in ("/auth/login", "/auth/send-otp", "/auth/verify-otp", "/auth/login-otp"):
        limit = 5 if "otp" in path else max(1, settings.AUTH_LOGIN_RATE_LIMIT_PER_MINUTE)
        allowed = _check_rate_limit(f"auth:{ip}", limit=limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много попыток. Подождите минуту."},
                headers={"Retry-After": "60"},
            )
    elif not _is_authenticated_request(request):
        allowed = _check_rate_limit(
            f"public:{ip}",
            limit=max(1, settings.PUBLIC_RATE_LIMIT_PER_MINUTE),
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Слишком много запросов. Попробуйте позже."},
            )

    return await call_next(request)

# ==================== ОСНОВНЫЕ РОУТЫ ====================
app.include_router(auth.router, prefix="/auth", tags=["🔐 Auth"])
app.include_router(loads.router, prefix="/loads", tags=["📦 Loads"])
app.include_router(bids.router, prefix="/bids", tags=["💰 Bids"])
app.include_router(messages.router, prefix="/messages", tags=["💬 Messages"])
app.include_router(rating.router, prefix="/rating", tags=["⭐ Рейтинг и Баллы"])
app.include_router(complaints.router, prefix="/complaints", tags=["⚠️ Претензии"])
app.include_router(forum.router, prefix="/forum", tags=["💬 Форум"])

# ==================== 5 ИИ-МОДУЛЕЙ ====================
app.include_router(lawyer.router, prefix="/lawyer", tags=["⚖️ AI-Юрист"])
app.include_router(logist.router, prefix="/logist", tags=["🚛 AI-Логист"])
app.include_router(antifraud.router, prefix="/antifraud", tags=["🛡️ AI-Антимошенник"])
app.include_router(documents.router, prefix="/documents", tags=["📄 AI-Документы"])
app.include_router(chatbot.router, prefix="/chatbot", tags=["💬 AI-Чатбот"])

# ==================== TELEGRAM & BOT API ====================
app.include_router(telegram.router, prefix="/api/telegram", tags=["Telegram"])
app.include_router(bot_api.router, prefix="/api/bot", tags=["Bot"])
app.include_router(cargos.router, prefix="/api", tags=["Cargos for Bot"])
app.include_router(applications.router, prefix="/api", tags=["Applications for Bot"])
app.include_router(complaints_ai.router, prefix="/api", tags=["AI Moderation"])
app.include_router(admin_router)

# ==================== HEALTH ====================
app.include_router(health_router, tags=["Health"])

# ==================== ДОПОЛНИТЕЛЬНО ====================
app.include_router(ai_routes.router, prefix="/ai", tags=["🤖 AI Services"])
app.include_router(ai_hybrid_router.router, tags=["🤖 AI Hybrid"])
app.include_router(antifraud_review_router.router, tags=["🛡️ Antifraud Review"])
app.include_router(antifraud_admin_router.router, tags=["🛡️ Antifraud Admin"])
app.include_router(internal.router, tags=["🔗 Internal Sync"])
if settings.DEBUG:
    app.include_router(test_data.router, prefix="/test-data", tags=["🧪 Test Data"])
app.include_router(deals_sync.router, prefix="/api", tags=["💼 Deals Sync"])
app.include_router(documents_sync.router, prefix="/api", tags=["📄 Documents Sync"])
app.include_router(moderation.router, prefix="/api", tags=["🛡️ Moderation"])
app.include_router(vehicles.router, prefix="/api", tags=["🚛 Vehicles"])
app.include_router(consolidation.router, prefix="/api", tags=["📦 Consolidation"])
app.include_router(analytics.router, prefix="/api", tags=["📊 Analytics"])
app.include_router(dicts.router, prefix="/api", tags=["📚 Dicts"])
app.include_router(me.router, prefix="/api", tags=["me"])
app.include_router(trust.router, prefix="/api", tags=["⭐ Trust"])
app.include_router(profile.router, prefix="/api", tags=["🏢 Company Profile"])
app.include_router(document_sign.router, prefix="/api", tags=["✍️ Document Sign"])
app.include_router(shipments.router, prefix="/api", tags=["📒 Shipments Registry"])
app.include_router(geo.router, prefix="/api", tags=["🌍 Geo"])
app.include_router(route_calc.router, prefix="/api", tags=["🧭 Route"])
app.include_router(ai_scoring_routes.router, prefix="/api", tags=["⚡ AI Loads"])
app.include_router(inn_lookup.router, prefix="/api", tags=["🏢 INN Lookup"])
app.include_router(employees.router, prefix="/api", tags=["👥 Employees"])
app.include_router(public_stats.router, prefix="/api", tags=["📊 Public Stats"])
app.include_router(traffic_light.router, prefix="/api", tags=["🚦 Traffic Light"])
app.include_router(blacklist.router, prefix="/api", tags=["🚫 Blacklist"])
app.include_router(verification.router, prefix="/api", tags=["✅ Verification"])
app.include_router(seo_pages.router, tags=["🔍 SEO Pages"])
app.include_router(email_digest.router, prefix="/api", tags=["📧 Email Digest"])
app.include_router(referral.router, prefix="/api", tags=["🎁 Referral"])
app.include_router(matching.router, prefix="/api", tags=["🚛 Matching"])
app.include_router(billing.router, prefix="/api", tags=["💳 Billing"])
app.include_router(tenders.router, prefix="/api", tags=["📋 Tenders"])
app.include_router(push_web.router, prefix="/api", tags=["🔔 Push"])
app.include_router(tms.router,      prefix="/api", tags=["🔌 TMS API"])


# CORS: в debug разрешаем все, в остальных режимах только явный allowlist
cors_origins = ["*"] if settings.DEBUG else settings.cors_origins_list
cors_allow_credentials = False if settings.DEBUG else True
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Путь к статическим файлам
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

# SW и manifest с корня — правильный scope + no-cache
@app.get("/sw.js", include_in_schema=False)
def serve_sw():
    import os as _os
    return FileResponse(_os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"})

@app.get("/requisites", include_in_schema=False)
def serve_requisites():
    import os as _os
    return FileResponse(_os.path.join(STATIC_DIR, "requisites.html"),
        media_type="text/html",
        headers={"Cache-Control": "public, max-age=3600"})

@app.get("/manifest.json", include_in_schema=False)
def serve_manifest():
    import os as _os
    return FileResponse(_os.path.join(STATIC_DIR, "manifest.json"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"})

# Монтируем статику
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def root():
    """Главная страница — веб-интерфейс ГрузПоток"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(
            index_path,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return {"status": "ok", "project": settings.APP_NAME, "docs": "/docs"}


@app.get("/webapp")
def webapp_root():
    """Alias для Telegram Mini App."""
    return root()


@app.get("/api")
def api_info():
    """API информация"""
    return {
        "status": "ok",
        "project": settings.APP_NAME,
        "version": "1.0.0",
        "modules": [
            "AI-Юрист",
            "AI-Логист",
            "AI-Антимошенник",
            "AI-Документы",
            "AI-Чатбот"
        ],
        "docs": "/docs"
    }


@app.get("/sign/{token}")
def sign_page(token: str):
    """Публичная страница подписания документа по токену."""
    sign_path = os.path.join(STATIC_DIR, "sign.html")
    if os.path.exists(sign_path):
        return FileResponse(
            sign_path,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    raise HTTPException(status_code=404, detail="Sign page not found")


def _spa_redirect_target(raw_path: str) -> str | None:
    path = (raw_path or "").strip().strip("/")
    if not path:
        return "/"

    # Иногда браузер/копипаст преобразует hash-роут '#/profile' в '/%23/profile'
    # или '/%23/profile.' (с пунктуацией).
    if path.startswith("%23/"):
        hash_part = path[3:].rstrip(".,;:!?")
        return f"/#/{hash_part}"

    if path.startswith("#/"):
        hash_part = path[2:].rstrip(".,;:!?")
        return f"/#/{hash_part}"

    if path in {"profile", "me"}:
        return "/#/profile"

    if path == "dashboard":
        return "/"

    if path.startswith("company/"):
        _, _, company_id = path.partition("/")
        if company_id.isdigit():
            return f"/#/company/{company_id}"

    if path == "shipments":
        return "/#/shipments"

    if path.startswith("shipments/"):
        _, _, shipment_id = path.partition("/")
        if shipment_id.isdigit():
            return f"/#/shipments/{shipment_id}"

    return None



# ── TMS API Documentation ─────────────────────────────────────────────────────
from fastapi import FastAPI as _FastAPI
from fastapi.openapi.docs import get_swagger_ui_html as _swagger_html

_tms_openapi: dict | None = None

@app.get("/tms/docs", include_in_schema=False)
async def tms_docs_page():
    """Swagger UI for TMS API (X-Api-Key auth)."""
    from fastapi.responses import HTMLResponse
    return _swagger_html(
        openapi_url="/tms/openapi.json",
        title="ГрузПоток TMS API",
        swagger_favicon_url="/static/icons/icon-192x192.png",
    )

@app.get("/tms/openapi.json", include_in_schema=False)
async def tms_openapi():
    """OpenAPI spec filtered to /tms/* routes."""
    global _tms_openapi
    if _tms_openapi is None:
        full = app.openapi()
        tms_paths = {k: v for k, v in full.get("paths", {}).items() if k.startswith("/api/tms")}
        _tms_openapi = {
            "openapi": full["openapi"],
            "info": {
                "title": "ГрузПоток TMS API",
                "version": full["info"]["version"],
                "description": (
                    "REST API для интеграции с ТМС-системами.\n\n"
                    "**Авторизация:** заголовок `X-Api-Key`.\n\n"
                    "**Лимиты:** Free — 100 запросов/день, Pro — 1000/день, Business — без ограничений.\n\n"
                    "Получить ключ: Настройки → API → Создать ключ."
                ),
            },
            "paths": tms_paths,
            "components": full.get("components", {}),
        }
    return _tms_openapi


@app.get("/{spa_path:path}")
def spa_fallback(spa_path: str):
    target = _spa_redirect_target(spa_path)
    if target is not None:
        return RedirectResponse(url=target, status_code=307)
    raise HTTPException(status_code=404, detail="Not Found")
