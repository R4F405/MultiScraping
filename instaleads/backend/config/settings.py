import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Proxies — lista de URLs http://user:pass@host:port/ separadas por coma
    IG_PROXY_LIST: list[str] = [
        p.strip() for p in os.getenv("IG_PROXY_LIST", "").split(",") if p.strip()
    ]
    IG_PROXY_ERROR_COOLDOWN: int = int(os.getenv("IG_PROXY_ERROR_COOLDOWN", "300"))
    # Anti-ban: an authenticated session should stick to ONE proxy instead of
    # rotating. A single logged-in account hopping across 10 IPs/countries in
    # seconds is a stronger ban signal than a stable IP. Disable to fall back
    # to round-robin for authenticated requests too.
    IG_SESSION_PINNED_PROXY: bool = os.getenv(
        "IG_SESSION_PINNED_PROXY", "1"
    ).strip().lower() not in ("0", "false", "off", "no", "")

    # Rate limiting — unauthenticated (Modo A)
    # Con proxies activos el límite sube automáticamente (10 IPs × 50 req = 500)
    IG_LIMIT_DAILY_UNAUTHENTICATED: int = int(os.getenv("IG_LIMIT_DAILY_UNAUTHENTICATED", "99999"))
    IG_DELAY_UNAUTH_MIN: float = float(os.getenv("IG_DELAY_UNAUTH_MIN", "4.0"))
    IG_DELAY_UNAUTH_MAX: float = float(os.getenv("IG_DELAY_UNAUTH_MAX", "9.0"))

    # Backoff
    IG_BACKOFF_INITIAL: int = int(os.getenv("IG_BACKOFF_INITIAL", "60"))
    IG_BACKOFF_MULTIPLIER: int = int(os.getenv("IG_BACKOFF_MULTIPLIER", "2"))
    IG_BACKOFF_MAX: int = int(os.getenv("IG_BACKOFF_MAX", "3600"))

    # General
    IG_APP_ID: str = os.getenv("IG_APP_ID", "936619743392459")
    IG_CONCURRENCY: int = int(os.getenv("IG_CONCURRENCY", "3"))
    IG_MAX_RETRIES: int = int(os.getenv("IG_MAX_RETRIES", "3"))
    IG_HEALTH_CHECK_INTERVAL: int = int(os.getenv("IG_HEALTH_CHECK_INTERVAL", "3600"))
    IG_HEALTH_TEST_ACCOUNT: str = os.getenv("IG_HEALTH_TEST_ACCOUNT", "natgeo")

    # Transport — curl_cffi TLS fingerprint profile ("none" to disable behind
    # TLS-intercepting proxies) and optional custom CA bundle path.
    IG_IMPERSONATE: str = os.getenv("IG_IMPERSONATE", "chrome131")
    IG_CA_BUNDLE: str = os.getenv("IG_CA_BUNDLE", "")

    # Followers mode (Modo B) — requires an authenticated session.
    # Instagram returns followers in pages (~50–100 each); the scraper
    # paginates via the max_id cursor to go well beyond the ~50 shown in the
    # desktop web modal. These control page size and inter-page pacing.
    IG_FOLLOWERS_PAGE_SIZE: int = int(os.getenv("IG_FOLLOWERS_PAGE_SIZE", "50"))
    IG_FOLLOWERS_DELAY_MIN: float = float(os.getenv("IG_FOLLOWERS_DELAY_MIN", "2.0"))
    IG_FOLLOWERS_DELAY_MAX: float = float(os.getenv("IG_FOLLOWERS_DELAY_MAX", "5.0"))
    # Hard cap on followers collected per job (safety valve for huge accounts).
    IG_FOLLOWERS_MAX_PER_JOB: int = int(os.getenv("IG_FOLLOWERS_MAX_PER_JOB", "5000"))
    # Anti-ban: per-day cap on followers fetched by the authenticated session
    # (counted across all jobs). Keeps the account under a believable volume.
    # 0 = disabled. Default ~1500/day is a conservative, human-plausible ceiling.
    IG_LIMIT_DAILY_FOLLOWERS: int = int(os.getenv("IG_LIMIT_DAILY_FOLLOWERS", "1500"))
    # Anti-ban: every N followers, pause for a longer rest to break the steady
    # request cadence a bot would show. 0 = disabled.
    IG_FOLLOWERS_REST_EVERY: int = int(os.getenv("IG_FOLLOWERS_REST_EVERY", "500"))
    IG_FOLLOWERS_REST_SECONDS: float = float(os.getenv("IG_FOLLOWERS_REST_SECONDS", "45.0"))

    # DB
    DB_PATH: str = os.path.join(os.path.dirname(__file__), "..", "..", "data", "instaleads.db")

    # email_finder.py compatibility (copied from mapleads)
    email_scraper_use_playwright: bool = False
    email_scraper_force_direct: bool = True


# Lowercase alias so email_finder.py (copied from mapleads) can import it as `settings`
settings = Settings()
