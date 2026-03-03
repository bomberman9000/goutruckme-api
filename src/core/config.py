from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    bot_token: str
    bot_username: str | None = None
    redis_url: str = "redis://localhost:6379"
    database_url: str
    admin_id: int | None = None
    debug: bool = False
    
    # Admin panel
    admin_username: str = "admin"
    admin_password: str = "admin123"
    secret_key: str = "your-secret-key-change-in-production"

    # WebApp
    webapp_url: str | None = None  # e.g. https://yourdomain.com
    telegram_tma_max_age_sec: int = 86400
    geo_http_timeout_sec: int = 5
    geo_nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    geo_osrm_url: str = "https://router.project-osrm.org/route/v1/driving"
    geo_user_agent: str = "GoTruck_AI_Bot/1.0"

    # AI
    groq_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Hybrid AI router
    local_ollama_url: str = "http://localhost:11434"
    local_model: str = "llama3:8b"
    vps_ai_url: str = ""
    vps_model: str = "mistral"
    ai_timeout: int = 10

    # Antifraud v2
    min_rate_per_km: int = 35
    max_rate_per_km: int = 200
    low_trust_score_threshold: int = 40
    ai_antifraud_enable_llm: bool = False
    ai_antifraud_llm_model: str = ""
    route_rate_cache_ttl_sec: int = 600
    route_rate_fallback_min: int = 35
    route_rate_fallback_max: int = 200
    route_rate_tier_map_json: str = (
        '{"short":{"max_km":300,"min":40,"max":220},'
        '"mid":{"max_km":1200,"min":35,"max":200},'
        '"long":{"max_km":100000,"min":30,"max":180}}'
    )
    antifraud_strict_mode: bool = True
    antifraud_docs_enable: bool = True

    # Cross-service sync
    internal_token: str = ""
    internal_api_token: str = ""  # legacy alias
    internal_http_timeout: int = 10
    tg_bot_internal_url: str = "http://tg-bot:8001"
    gruzpotok_api_internal_url: str = "http://gruzpotok-api:8000"
    gruzpotok_public_url: str = "http://144.31.64.130:8000"
    gruzpotok_sync_path: str = "/internal/sync"
    gruzpotok_verify_login_path: str = "/internal/auth/verify-login-token"
    gruzpotok_create_magic_link_path: str = "/internal/auth/create-login-token"
    gruzpotok_create_login_path: str = "/internal/auth/create-login-token"  # legacy alias
    gruzpotok_confirm_link_path: str = "/api/telegram/confirm-link"

    # Parser-bot (Telegram userbot -> gruzpotok-api)
    parser_enabled: bool = False
    parser_tg_api_id: int | None = None
    parser_tg_api_hash: str = ""
    parser_tg_string_session: str = ""
    parser_tg_session_name: str = "parser-bot"
    parser_chat_ids: str = ""
    parser_keywords: str = (
        "груз,погрузка,выгрузка,тент,реф,ндс,ставка,догруз,фрахт,изотерм,борт,контейнер"
    )
    parser_dedupe_ttl_sec: int = 7200
    parser_source_name: str = "tg-parser-bot"
    parser_default_user_id: int | None = None
    parser_http_timeout: int = 10
    parser_stream_name: str = "logistics_stream"
    parser_stream_maxlen: int = 200000
    parser_stream_group: str = "parser_workers"
    parser_stream_block_ms: int = 1000
    parser_stream_batch: int = 100
    parser_stream_claim_idle_ms: int = 60000
    parser_startup_backfill_limit: int = 10
    parser_startup_backfill_minutes: int = 30
    parser_worker_name: str = "worker-1"
    parser_worker_max_retries: int = 3
    parser_worker_enable_inn_moderation: bool = False
    parser_worker_inn_timeout_sec: int = 8
    parser_manual_review_alert_threshold: int = 20

    # Parser LLM extractor
    parser_use_llm: bool = False
    parser_price_source_chat: str = ""
    parser_price_reference_days: int = 14
    parser_price_reference_min_samples: int = 2
    parser_max_rate_rub: int = 5_000_000
    parser_max_rate_per_km: int = 500
    parser_rate_recheck_with_llm: bool = False

    # Parser scoring
    parser_score_min_trust: int = 40
    parser_scoring_enable_ai: bool = False
    parser_scoring_ai_model: str = ""
    parser_scoring_timeout_sec: int = 8
    dadata_api_url: str = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
    dadata_api_token: str = ""

    # Premium (Telegram Stars)
    premium_stars_7d: int = 700
    premium_stars_30d: int = 2400
    referral_reward_days: int = 7
    referral_invited_reward_days: int = 3
    referral_ambassador_threshold: int = 10
    manual_cargo_notify_dedupe_sec: int = 300
    admin_notification_mute_sec: int = 86400

    # Escrow prototype
    escrow_enabled: bool = True
    escrow_provider: str = "mock"
    escrow_platform_fee_percent: float = 2.0
    escrow_fast_payout_fee_percent: float = 0.5
    tochka_client_id: str = ""
    tochka_client_secret: str = ""
    tochka_base_url: str = "https://enter.tochka.com/api/v2"

    @field_validator("admin_id", "parser_tg_api_id", "parser_default_user_id", mode="before")
    @classmethod
    def _empty_str_to_none_for_optional_ints(cls, value):
        if value == "":
            return None
        return value

    @field_validator("groq_api_key", "openai_api_key", mode="before")
    @classmethod
    def _empty_str_to_none_for_optional_strs(cls, value):
        if value == "":
            return None
        return value

settings = Settings()
