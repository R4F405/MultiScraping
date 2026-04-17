"""
Application settings loaded from environment variables.

Fill in the .env file at the project root. All values have safe defaults
for local development without proxies.
"""

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Proxy credentials (for single rotating endpoint or as fallback)
    proxy_user: str
    proxy_pass: str
    proxy_host: str
    proxy_port: int
    # Explicit list of proxy URLs (overrides host/port if set)
    # Format: http://user:pass@host:port,http://user:pass@host2:port2
    proxy_list: list[str]

    # App
    log_level: str
    db_path: str

    # Per-proxy rate limiting
    max_requests_per_proxy_before_cooldown: int
    proxy_cooldown_seconds: int

    # Concurrency
    max_concurrent_requests: int
    request_delay_min: float
    request_delay_max: float

    # Circuit breaker
    error_rate_threshold: float
    high_error_cooldown_seconds: int

    # Daily hard cap
    max_requests_per_day: int

    # Dedupe window (days): skip businesses seen recently
    dedupe_days: int

    # Email DNS: if MX missing, still treat as deliverable-capable when A exists (some hosts).
    email_dns_accept_a: bool

    # Optional: render homepage with Playwright when curl finds no emails (SPAs; requires `pip install playwright`).
    email_scraper_use_playwright: bool
    # Force website email discovery without proxies (helps diagnose proxy/network issues).
    email_scraper_force_direct: bool

    # API authentication (optional — leave empty to disable, useful for local dev)
    # Set API_KEY in .env to require X-API-Key header on all API requests
    api_key: str


def _parse_proxy_list(raw: str) -> list[str]:
    """Parse comma-separated proxy URLs, stripping whitespace."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _load_settings() -> Settings:
    return Settings(
        proxy_user=os.getenv("WEBSHARE_PROXY_USER", ""),
        proxy_pass=os.getenv("WEBSHARE_PROXY_PASS", ""),
        proxy_host=os.getenv("WEBSHARE_PROXY_HOST", "proxy.webshare.io"),
        proxy_port=int(os.getenv("WEBSHARE_PROXY_PORT", "80")),
        proxy_list=_parse_proxy_list(os.getenv("PROXY_LIST", "")),
        log_level=os.getenv("LOG_LEVEL", "DEBUG"),
        db_path=os.getenv("DB_PATH", "./data/mapleads.db"),
        max_requests_per_proxy_before_cooldown=int(
            os.getenv("MAX_REQUESTS_PER_PROXY_BEFORE_COOLDOWN", "40")
        ),
        proxy_cooldown_seconds=int(os.getenv("PROXY_COOLDOWN_SECONDS", "360")),
        max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "15")),
        request_delay_min=float(os.getenv("REQUEST_DELAY_MIN_SECONDS", "0.5")),
        request_delay_max=float(os.getenv("REQUEST_DELAY_MAX_SECONDS", "1.5")),
        error_rate_threshold=float(os.getenv("ERROR_RATE_THRESHOLD", "0.30")),
        high_error_cooldown_seconds=int(os.getenv("HIGH_ERROR_COOLDOWN_SECONDS", "600")),
        max_requests_per_day=int(os.getenv("MAX_REQUESTS_PER_DAY", "10000")),
        dedupe_days=int(os.getenv("DEDUPE_DAYS", "30")),
        email_dns_accept_a=os.getenv("EMAIL_DNS_ACCEPT_A", "0").strip().lower() in ("1", "true", "yes"),
        email_scraper_use_playwright=(
            os.getenv("EMAIL_SCRAPER_USE_PLAYWRIGHT", "0").strip().lower() in ("1", "true", "yes")
        ),
        email_scraper_force_direct=(
            os.getenv("EMAIL_SCRAPER_FORCE_DIRECT", "0").strip().lower() in ("1", "true", "yes")
        ),
        api_key=os.getenv("API_KEY", ""),
    )


settings = _load_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.DEBUG),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
