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
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)
    
    # Application
    APP_NAME: str = "goutruckme"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    
    # API
    API_V1_PREFIX: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./goutruckme.db")
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    # Защита API синхронизации сделок (X-Client-Key). Пусто = не проверять (dev).
    CLIENT_SYNC_KEY: str = os.getenv("CLIENT_SYNC_KEY", "")
    
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

    # Moderation LLM (optional; if set, used in addition to rules)
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

    # Telegram alerts for HIGH risk
    ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
