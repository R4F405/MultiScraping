import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

from backend.config.settings import settings
from backend.storage import database


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after_seconds: int = 0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class _HourlyWindow:
    """Tracks requests in a rolling 60-minute window."""

    def __init__(self, max_per_hour: int):
        self._max = max_per_hour
        self._timestamps: list[float] = []

    def count_this_hour(self) -> int:
        cutoff = time.monotonic() - 3600
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        return len(self._timestamps)

    def record(self) -> None:
        self._timestamps.append(time.monotonic())

    def seconds_until_slot(self) -> int:
        if self.count_this_hour() < self._max:
            return 0
        oldest = self._timestamps[0]
        return max(0, int(oldest + 3600 - time.monotonic()) + 1)


class TikTokLimiter:
    """Unified rate limiter for all TikTok requests."""

    def __init__(self) -> None:
        self._hourly = _HourlyWindow(settings.max_req_hour)
        self._lock = asyncio.Lock()

    async def check_limits(self) -> None:
        stats = await database.get_today_stats()
        used_daily = stats["requests"]
        if used_daily >= settings.max_daily:
            raise RateLimitExceeded(
                f"Daily limit reached ({used_daily}/{settings.max_daily})",
                retry_after_seconds=_seconds_until_midnight(),
            )
        wait_secs = self._hourly.seconds_until_slot()
        if wait_secs > 0:
            raise RateLimitExceeded(
                f"Hourly limit reached ({settings.max_req_hour}/h)",
                retry_after_seconds=wait_secs,
            )

    async def wait(self) -> None:
        """Check limits, record request, and apply random delay."""
        async with self._lock:
            await self.check_limits()
            self._hourly.record()
        delay = random.uniform(settings.delay_min, settings.delay_max)
        await asyncio.sleep(delay)
        await database.increment_daily_stat()

    def count_this_hour(self) -> int:
        return self._hourly.count_this_hour()


def _seconds_until_midnight() -> int:
    now = datetime.now(timezone.utc)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((next_midnight - now).total_seconds())


# Singleton
limiter = TikTokLimiter()
