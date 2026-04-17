import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    db_path: str = os.getenv("DB_PATH", "./data/instaleads.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    instagram_maintenance_message: str = os.getenv("INSTAGRAM_MAINTENANCE_MESSAGE", "").strip()
    port: int = int(os.getenv("PORT", "8002"))
    session_file: str = os.getenv("SESSION_FILE", "./data/instaleads.session")

    # Campaign limits
    max_daily_unauth: int = int(os.getenv("MAX_UNAUTH_DAILY", "400"))
    max_daily_auth: int = int(os.getenv("MAX_AUTH_DAILY", "1200"))
    max_hourly_auth: int = int(os.getenv("MAX_AUTH_HOURLY", "250"))
    max_concurrent_unauth: int = int(os.getenv("MAX_CONCURRENT_UNAUTH", "4"))
    max_concurrent_auth: int = int(os.getenv("MAX_CONCURRENT_AUTH", "8"))
    delay_unauth_min: float = float(os.getenv("DELAY_UNAUTH_MIN", "1.4"))
    delay_unauth_max: float = float(os.getenv("DELAY_UNAUTH_MAX", "3.4"))
    delay_auth_min: float = float(os.getenv("DELAY_AUTH_MIN", "0.8"))
    delay_auth_max: float = float(os.getenv("DELAY_AUTH_MAX", "2.2"))
    retry_max_attempts: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
    retry_base_delay: float = float(os.getenv("RETRY_BASE_DELAY", "1.5"))
    retry_max_delay: float = float(os.getenv("RETRY_MAX_DELAY", "15"))

    # Discovery
    discovery_provider: str = os.getenv("DISCOVERY_PROVIDER", "internal")
    discovery_min_coverage_ratio: float = float(os.getenv("DISCOVERY_MIN_COVERAGE_RATIO", "0.45"))
    discovery_login_escalation_ratio: float = float(os.getenv("DISCOVERY_LOGIN_ESCALATION_RATIO", "0.35"))

    # Enrichment
    enrichment_http_timeout_sec: float = float(os.getenv("ENRICHMENT_HTTP_TIMEOUT_SEC", "8"))
    enrichment_follow_contact_pages: bool = os.getenv("ENRICHMENT_FOLLOW_CONTACT_PAGES", "1") in {"1", "true", "True"}
    enrichment_max_subpages: int = int(os.getenv("ENRICHMENT_MAX_SUBPAGES", "3"))

    # Proxy
    ig_proxy_url: str = os.getenv("IG_PROXY_URL", "")
    proxy_list: list[str] = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
    proxy_open_threshold: float = float(os.getenv("PROXY_OPEN_THRESHOLD", "35"))
    proxy_half_open_threshold: float = float(os.getenv("PROXY_HALF_OPEN_THRESHOLD", "60"))
    proxy_cooldown_seconds: int = int(os.getenv("PROXY_COOLDOWN_SECONDS", "180"))

    # Feature toggles
    live_smoke_enabled: bool = os.getenv("LIVE_SMOKE_ENABLED", "0") in {"1", "true", "True"}


settings = Settings()
