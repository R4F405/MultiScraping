import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    db_path: str = os.getenv("DB_PATH", "./data/instaleads.db")
    session_file: str = os.getenv("SESSION_FILE", "./data/session.json")
    session_key: str = os.getenv("SESSION_KEY", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Unauthenticated rate limits (Mode A)
    max_unauth_daily: int = int(os.getenv("MAX_UNAUTH_DAILY", "150"))
    delay_unauth_min: float = float(os.getenv("DELAY_UNAUTH_MIN", "4"))
    delay_unauth_max: float = float(os.getenv("DELAY_UNAUTH_MAX", "9"))
    max_concurrent_unauth: int = int(os.getenv("MAX_CONCURRENT_UNAUTH", "3"))

    # Authenticated rate limits (Mode B)
    max_auth_daily: int = int(os.getenv("MAX_AUTH_DAILY", "150"))
    max_auth_hourly: int = int(os.getenv("MAX_AUTH_HOURLY", "35"))
    delay_auth_min: float = float(os.getenv("DELAY_AUTH_MIN", "2"))
    delay_auth_max: float = float(os.getenv("DELAY_AUTH_MAX", "5"))
    followers_auto_resume_enabled: bool = os.getenv("FOLLOWERS_AUTO_RESUME_ENABLED", "1") in {
        "1", "true", "True", "yes", "on"
    }
    followers_max_resumes_per_day: int = int(os.getenv("FOLLOWERS_MAX_RESUMES_PER_DAY", "48"))
    enrichment_max_fetches_per_hour: int = int(os.getenv("ENRICHMENT_MAX_FETCHES_PER_HOUR", "200"))
    enrichment_http_timeout_sec: float = float(os.getenv("ENRICHMENT_HTTP_TIMEOUT_SEC", "10"))
    enrichment_follow_contact_pages: bool = os.getenv("ENRICHMENT_FOLLOW_CONTACT_PAGES", "1") in {
        "1", "true", "True", "yes", "on"
    }
    enrichment_max_subpages: int = int(os.getenv("ENRICHMENT_MAX_SUBPAGES", "3"))

    # Retry configuration
    retry_max_attempts: int = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
    retry_base_delay: float = float(os.getenv("RETRY_BASE_DELAY", "5.0"))
    retry_max_delay: float = float(os.getenv("RETRY_MAX_DELAY", "120.0"))

    # Multi-account pool sessions directory
    sessions_dir: str = os.getenv("SESSIONS_DIR", "./data/sessions")

    # Cross-platform email search (opt-in, off by default)
    cross_platform_enabled: bool = os.getenv("CROSS_PLATFORM_ENABLED", "0") in {
        "1", "true", "True", "yes", "on"
    }
    hunter_api_key: str = os.getenv("HUNTER_API_KEY", "")
    snov_client_id: str = os.getenv("SNOV_CLIENT_ID", "")
    snov_client_secret: str = os.getenv("SNOV_CLIENT_SECRET", "")

    # Proxy configuration (for future residential proxy support)
    proxy_url: str = os.getenv("IG_PROXY_URL", "")

    port: int = int(os.getenv("PORT", "8002"))


settings = Settings()
