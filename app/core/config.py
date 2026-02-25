from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os
import secrets
import warnings
from dotenv import load_dotenv

# КРИТИЧНО: Загрузить .env файл ДО импорта переменных
load_dotenv()


class Settings(BaseSettings):
    """Application settings."""
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")
    
    # Application
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "ГрузПоток")
    APP_NAME: str = os.getenv("APP_NAME", os.getenv("PROJECT_NAME", "ГрузПоток"))
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./gruzpotok.db")
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    # Защита API синхронизации сделок (X-Client-Key). Пусто = не проверять (dev).
    CLIENT_SYNC_KEY: str = os.getenv("CLIENT_SYNC_KEY", "")
    # Внутренний токен для межсервисного обмена (tg-bot <-> gruzpotok-api)
    INTERNAL_TOKEN: str = os.getenv("INTERNAL_TOKEN", os.getenv("INTERNAL_WEBHOOK_TOKEN", ""))
    TG_BOT_URL: str = os.getenv(
        "TG_BOT_URL",
        os.getenv("TG_BOT_INTERNAL_URL", os.getenv("BOT_WEBHOOK_URL", "http://tg-bot:8001")),
    )
    TG_BOT_INTERNAL_URL: str = os.getenv("TG_BOT_INTERNAL_URL", os.getenv("BOT_WEBHOOK_URL", "http://tg-bot:8001"))
    TG_BOT_INTERNAL_EVENT_PATH: str = os.getenv("TG_BOT_INTERNAL_EVENT_PATH", "/internal/event")
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://144.31.64.130:8000")
    LOGIN_TOKEN_TTL_SECONDS: int = int(os.getenv("LOGIN_TOKEN_TTL_SECONDS", "300"))
    MAGIC_LINK_TTL_SECONDS: int = int(os.getenv("MAGIC_LINK_TTL_SECONDS", "60"))
    SYNC_WARMUP_TTL_SEC: int = int(os.getenv("SYNC_WARMUP_TTL_SEC", "600"))
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Безопасный fallback: если ключ не задан или задан небезопасный дефолт,
        # генерируем случайный ключ для текущего процесса.
        if not self.SECRET_KEY or self.SECRET_KEY == "supersecretkey123":
            generated_secret = secrets.token_urlsafe(64)
            os.environ["SECRET_KEY"] = generated_secret
            object.__setattr__(self, "SECRET_KEY", generated_secret)
            warnings.warn(
                "⚠️ SECRET_KEY не задана (или небезопасна). "
                "Сгенерирован временный ключ процесса; укажите SECRET_KEY в .env.",
                UserWarning,
            )
    
    # CORS (парсим из строки через запятую)
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://localhost:8080")
    
    @property
    def cors_origins_list(self) -> list[str]:
        """Преобразует CORS_ORIGINS из строки в список."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]
    
    # ========== AI/LLM CONFIGURATION ==========
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    # Yandex GPT
    YANDEX_GPT_KEY: str = os.getenv("YANDEX_GPT_KEY", "")
    YANDEX_FOLDER_ID: str = os.getenv("YANDEX_FOLDER_ID", "")
    
    # GigaChat
    GIGACHAT_KEY: str = os.getenv("GIGACHAT_KEY", "")
    
    # AI Settings
    AI_USE_LLM: bool = os.getenv("AI_USE_LLM", "false").lower() == "true"
    AI_FALLBACK_TO_LOCAL: bool = os.getenv("AI_FALLBACK_TO_LOCAL", "true").lower() == "true"
    AI_TIMEOUT_SECONDS: int = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))

    # Antifraud rules tuning
    MIN_RATE_PER_KM: int = int(os.getenv("MIN_RATE_PER_KM", "35"))
    MAX_RATE_PER_KM: int = int(os.getenv("MAX_RATE_PER_KM", "200"))
    LOW_TRUST_SCORE_THRESHOLD: int = int(os.getenv("LOW_TRUST_SCORE_THRESHOLD", "40"))
    AI_ANTIFRAUD_ENABLE_LLM: bool = os.getenv("AI_ANTIFRAUD_ENABLE_LLM", "false").lower() == "true"
    AI_ANTIFRAUD_LLM_MODEL: str = os.getenv("AI_ANTIFRAUD_LLM_MODEL", "")
    ROUTE_RATE_CACHE_TTL_SEC: int = int(os.getenv("ROUTE_RATE_CACHE_TTL_SEC", "600"))
    ROUTE_RATE_FALLBACK_MIN: int = int(os.getenv("ROUTE_RATE_FALLBACK_MIN", "35"))
    ROUTE_RATE_FALLBACK_MAX: int = int(os.getenv("ROUTE_RATE_FALLBACK_MAX", "200"))
    ROUTE_RATE_TIER_MAP_JSON: str = os.getenv(
        "ROUTE_RATE_TIER_MAP_JSON",
        '{"short":{"max_km":300,"min":40,"max":220},"mid":{"max_km":1200,"min":35,"max":200},"long":{"max_km":100000,"min":30,"max":180}}',
    )
    ANTIFRAUD_STRICT_MODE: bool = os.getenv("ANTIFRAUD_STRICT_MODE", "true").lower() == "true"
    ANTIFRAUD_DOCS_ENABLE: bool = os.getenv("ANTIFRAUD_DOCS_ENABLE", "true").lower() == "true"

    # Moderation LLM (optional; if set, used in addition to rules)
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # Local free moderation LLM (optional)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1")

    # Telegram alerts for HIGH risk
    ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
