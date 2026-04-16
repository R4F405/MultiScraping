import logging
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    db_path: str
    log_level: str
    port: int
    headless: bool
    proxy_url: str
    proxy_urls: list[str]
    session_state_path: str
    warmup_enabled: bool
    warmup_url: str

    max_req_hour: int
    max_daily: int
    delay_min: float
    delay_max: float
    max_concurrent_workers: int

    retry_max_attempts: int
    retry_base_delay: float
    retry_max_delay: float

    def __init__(self) -> None:
        self.db_path = os.getenv("DB_PATH", "./data/tiktokleads.db")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.port = int(os.getenv("PORT", "8004"))
        self.headless = os.getenv("TIKTOK_HEADLESS", "true").lower() not in ("false", "0", "no")
        proxy_raw = os.getenv("TIKTOK_PROXY_URL", "")
        self.proxy_urls = [p.strip() for p in proxy_raw.split(",") if p.strip()]
        self.proxy_url = self.proxy_urls[0] if self.proxy_urls else ""
        self.session_state_path = os.getenv("TIKTOK_SESSION_STATE_PATH", "./data/tiktok_session_state.json")
        self.warmup_enabled = os.getenv("TIKTOK_WARMUP_ENABLED", "true").lower() not in ("false", "0", "no")
        self.warmup_url = os.getenv("TIKTOK_WARMUP_URL", "https://www.tiktok.com/")

        self.max_req_hour = int(os.getenv("MAX_REQ_HOUR", "40"))
        self.max_daily = int(os.getenv("MAX_DAILY", "200"))
        self.delay_min = float(os.getenv("DELAY_MIN", "3.0"))
        self.delay_max = float(os.getenv("DELAY_MAX", "8.0"))
        self.max_concurrent_workers = int(os.getenv("MAX_CONCURRENT_WORKERS", "1"))

        self.retry_max_attempts = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))
        self.retry_base_delay = float(os.getenv("RETRY_BASE_DELAY", "5.0"))
        self.retry_max_delay = float(os.getenv("RETRY_MAX_DELAY", "60.0"))

        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        )


settings = Settings()
