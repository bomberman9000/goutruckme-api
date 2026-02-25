from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
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
    yield


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
    version="1.0.0"
)

# Сжимаем крупные ответы (index.html, списки, аналитика), чтобы UI открывался быстрее.
app.add_middleware(
    GZipMiddleware,
    minimum_size=1024,
)

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

# Монтируем статику
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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


@app.get("/{spa_path:path}")
def spa_fallback(spa_path: str):
    target = _spa_redirect_target(spa_path)
    if target is not None:
        return RedirectResponse(url=target, status_code=307)
    raise HTTPException(status_code=404, detail="Not Found")
