from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from app.core.config import settings
from app.db.database import init_db

from app.api.routes import (
    auth,
    loads,
    bids,
    messages,
    ai,
    lawyer,
    logist,
    antifraud,
    documents,
    chatbot,
    rating,
    complaints,
    forum,
    test_data,
    telegram,
    bot_api,
    cargos,
    applications,
    deals_sync,
    documents_sync,
    moderation,
    vehicles,
)
from app.admin.routes import router as admin_router
from app.api.routes import complaints_ai
from app.api.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="GouTruckMe API",
    description="""
🚚 **GouTruckMe** — Биржа грузоперевозок нового поколения

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
app.include_router(ai.router, prefix="/ai", tags=["🤖 AI Services"])
if settings.DEBUG:
    app.include_router(test_data.router, prefix="/test-data", tags=["🧪 Test Data"])
app.include_router(deals_sync.router, prefix="/api", tags=["💼 Deals Sync"])
app.include_router(documents_sync.router, prefix="/api", tags=["📄 Documents Sync"])
app.include_router(moderation.router, prefix="/api", tags=["🛡️ Moderation"])
app.include_router(vehicles.router, prefix="/api", tags=["🚛 Vehicles"])


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
    """Главная страница — веб-интерфейс GouTruckMe"""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "ok", "project": "GouTruckMe", "docs": "/docs"}


@app.get("/api")
def api_info():
    """API информация"""
    return {
        "status": "ok", 
        "project": "GouTruckMe",
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
