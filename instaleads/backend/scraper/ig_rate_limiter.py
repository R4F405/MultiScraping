import asyncio
import logging
import random
import time

from backend.config.settings import Settings
from backend.storage import database as db

logger = logging.getLogger(__name__)


class DailyLimitReached(Exception):
    pass


# mode → (daily-limit attr, delay-min attr, delay-max attr) on Settings.
# None disables that check: the "auth" limiter only provides backoff because
# the followers iterator already paces its pages and enforces its own cap.
_MODES: dict[str, tuple[str | None, str | None, str | None]] = {
    "unauth": ("IG_LIMIT_DAILY_UNAUTHENTICATED", "IG_DELAY_UNAUTH_MIN", "IG_DELAY_UNAUTH_MAX"),
    "enrich": ("IG_LIMIT_DAILY_PROFILES", "IG_ENRICH_DELAY_MIN", "IG_ENRICH_DELAY_MAX"),
    "auth": (None, None, None),
}


class RateLimiter:
    def __init__(self, mode: str):
        """mode: 'unauth' (guest/web), 'enrich' (per-profile lookups) or 'auth'."""
        self.mode = mode
        self._backoff = Settings.IG_BACKOFF_INITIAL
        self._last_request_time: float = 0.0
        self._lock = asyncio.Lock()

    async def check_and_wait(self):
        """Call before every Instagram request. Blocks if needed.

        The lock ensures concurrent callers are serialized so the inter-request
        delay is respected even when multiple coroutines fire simultaneously.
        """
        async with self._lock:
            if self.mode not in _MODES:
                raise ValueError(f"Unsupported rate limiter mode: {self.mode}")
            limit_attr, min_attr, max_attr = _MODES[self.mode]

            if limit_attr:
                count = await db.get_daily_count(self.mode)
                limit = getattr(Settings, limit_attr)
                if limit and count >= limit:
                    raise DailyLimitReached(f"Daily limit reached ({count}/{limit}) for mode={self.mode}")

            if min_attr and max_attr:
                delay = random.uniform(getattr(Settings, min_attr), getattr(Settings, max_attr))
                elapsed = time.monotonic() - self._last_request_time
                if elapsed < delay:
                    await asyncio.sleep(delay - elapsed)

            self._last_request_time = time.monotonic()
            if limit_attr:
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
