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
    # TLS-intercepting proxies) and optional custom CA bundle path. The web
    # User-Agent's Chrome version is derived from this profile so TLS and UA
    # always agree.
    IG_IMPERSONATE: str = os.getenv("IG_IMPERSONATE", "chrome146")
    IG_CA_BUNDLE: str = os.getenv("IG_CA_BUNDLE", "")

    # Mobile (Android app) private API — i.instagram.com. Since Sept 2026 this
    # is the only surface that still lists followers and returns contact info
    # (public_email / public_phone_number). Defaults mirror instagrapi 3.0.14
    # (2026-09-24); override from .env when Instagram rotates them.
    IG_MOBILE_APP_ID: str = os.getenv("IG_MOBILE_APP_ID", "567067343352427")
    IG_MOBILE_APP_VERSION: str = os.getenv("IG_MOBILE_APP_VERSION", "448.0.0.0.20")
    IG_MOBILE_VERSION_CODE: str = os.getenv("IG_MOBILE_VERSION_CODE", "1065560286")
    IG_MOBILE_BLOKS_VERSION_ID: str = os.getenv(
        "IG_MOBILE_BLOKS_VERSION_ID",
        "0bc46a03e177bfc9bc8d611918815acf248fa9c77754d807d6a5951dc9ce9432",
    )
    IG_MOBILE_LOCALE: str = os.getenv("IG_MOBILE_LOCALE", "es_ES")
    IG_MOBILE_TIMEZONE_OFFSET: int = int(os.getenv("IG_MOBILE_TIMEZONE_OFFSET", "7200"))
    # TLS profile for mobile requests. Empty = curl's own TLS (the Android app
    # is not a browser, so a Chrome fingerprint would not match its UA).
    IG_MOBILE_IMPERSONATE: str = os.getenv("IG_MOBILE_IMPERSONATE", "")
    # Private GraphQL "FollowersList" doc id — fallback when the v1 endpoint
    # answers with should_limit_list_of_followers.
    IG_FOLLOWERS_DOC_ID: str = os.getenv("IG_FOLLOWERS_DOC_ID", "284797047911918316998205836755")

    # Followers mode (Modo B) — requires an authenticated session.
    # Instagram returns followers in pages; the scraper paginates via the
    # max_id cursor of the mobile API to go well beyond the ~50 shown in the
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

    # Fase 2 (email/phone enrichment) — one profile lookup per follower. This
    # is the request Instagram rate-limits hardest per account, so it gets its
    # own pacing and daily cap (counted across all jobs). 0 = no cap.
    IG_ENRICH_DELAY_MIN: float = float(os.getenv("IG_ENRICH_DELAY_MIN", "8.0"))
    IG_ENRICH_DELAY_MAX: float = float(os.getenv("IG_ENRICH_DELAY_MAX", "15.0"))
    IG_LIMIT_DAILY_PROFILES: int = int(os.getenv("IG_LIMIT_DAILY_PROFILES", "500"))
    IG_ENRICH_REST_EVERY: int = int(os.getenv("IG_ENRICH_REST_EVERY", "100"))
    IG_ENRICH_REST_SECONDS: float = float(os.getenv("IG_ENRICH_REST_SECONDS", "90.0"))

    # DB
    DB_PATH: str = os.path.join(os.path.dirname(__file__), "..", "..", "data", "instaleads.db")

    # email_finder.py compatibility (copied from mapleads)
    email_scraper_use_playwright: bool = False
    email_scraper_force_direct: bool = True


# Lowercase alias so email_finder.py (copied from mapleads) can import it as `settings`
settings = Settings()
