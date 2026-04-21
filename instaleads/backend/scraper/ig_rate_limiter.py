import asyncio
import logging
import random
import time

from backend.config.settings import Settings
from backend.storage import database as db

logger = logging.getLogger(__name__)


class DailyLimitReached(Exception):
    pass


class RateLimiter:
    def __init__(self, mode: str):
        """mode: 'unauth' | 'auth'"""
        self.mode = mode
        self._backoff = Settings.IG_BACKOFF_INITIAL
        self._last_request_time: float = 0.0

    async def check_and_wait(self):
        """Call before every Instagram request. Blocks if needed."""
        count = await db.get_daily_count(self.mode)
        limit = (
            Settings.IG_LIMIT_DAILY_UNAUTHENTICATED
            if self.mode == "unauth"
            else Settings.IG_LIMIT_DAILY_AUTHENTICATED
        )
        if count >= limit:
            raise DailyLimitReached(f"Daily limit reached ({count}/{limit}) for mode={self.mode}")

        if self.mode == "unauth":
            delay = random.uniform(Settings.IG_DELAY_UNAUTH_MIN, Settings.IG_DELAY_UNAUTH_MAX)
        else:
            delay = random.uniform(Settings.IG_DELAY_AUTH_MIN, Settings.IG_DELAY_AUTH_MAX)

        elapsed = time.monotonic() - self._last_request_time
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        self._last_request_time = time.monotonic()
        await db.increment_daily_count(self.mode)

    async def on_rate_limited(self):
        """Call when receiving 429 or require_login response."""
        logger.warning("Rate limit detected (mode=%s) — backing off %ss", self.mode, self._backoff)
        await asyncio.sleep(self._backoff)
        self._backoff = min(
            self._backoff * Settings.IG_BACKOFF_MULTIPLIER,
            Settings.IG_BACKOFF_MAX,
        )

    def reset_backoff(self):
        self._backoff = Settings.IG_BACKOFF_INITIAL
