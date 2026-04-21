import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Session
    IG_USERNAME: str = os.getenv("IG_USERNAME", "")
    IG_SESSION_KEY: str = os.getenv("IG_SESSION_KEY", "")

    # Rate limiting — unauthenticated (Modo A)
    IG_LIMIT_DAILY_UNAUTHENTICATED: int = int(os.getenv("IG_LIMIT_DAILY_UNAUTHENTICATED", "150"))
    IG_DELAY_UNAUTH_MIN: float = float(os.getenv("IG_DELAY_UNAUTH_MIN", "4.0"))
    IG_DELAY_UNAUTH_MAX: float = float(os.getenv("IG_DELAY_UNAUTH_MAX", "9.0"))

    # Rate limiting — authenticated (Modo B)
    IG_LIMIT_DAILY_AUTHENTICATED: int = int(os.getenv("IG_LIMIT_DAILY_AUTHENTICATED", "80"))
    IG_LIMIT_HOURLY_AUTHENTICATED: int = int(os.getenv("IG_LIMIT_HOURLY_AUTHENTICATED", "20"))
    IG_DELAY_AUTH_MIN: float = float(os.getenv("IG_DELAY_AUTH_MIN", "4.0"))
    IG_DELAY_AUTH_MAX: float = float(os.getenv("IG_DELAY_AUTH_MAX", "10.0"))

    # Backoff
    IG_BACKOFF_INITIAL: int = int(os.getenv("IG_BACKOFF_INITIAL", "60"))
    IG_BACKOFF_MULTIPLIER: int = int(os.getenv("IG_BACKOFF_MULTIPLIER", "2"))
    IG_BACKOFF_MAX: int = int(os.getenv("IG_BACKOFF_MAX", "3600"))

    # General
    IG_APP_ID: str = os.getenv("IG_APP_ID", "936619743392459")
    IG_CONCURRENCY: int = int(os.getenv("IG_CONCURRENCY", "3"))
    IG_MAX_RETRIES: int = int(os.getenv("IG_MAX_RETRIES", "3"))
    IG_HEALTH_CHECK_INTERVAL: int = int(os.getenv("IG_HEALTH_CHECK_INTERVAL", "3600"))
    IG_HEALTH_TEST_ACCOUNT: str = os.getenv("IG_HEALTH_TEST_ACCOUNT", "instagram")

    # DB
    DB_PATH: str = os.path.join(os.path.dirname(__file__), "..", "..", "data", "instaleads.db")

    # email_finder.py compatibility (copied from mapleads)
    email_scraper_use_playwright: bool = False
    email_scraper_force_direct: bool = True


# Lowercase alias so email_finder.py (copied from mapleads) can import it as `settings`
settings = Settings()
