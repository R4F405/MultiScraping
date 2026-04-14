import asyncio
import random
import time
from datetime import datetime

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


class UnauthLimiter:
    """Rate limiter for unauthenticated Mode A (dorking)."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_unauth)
        self._lock = asyncio.Lock()

    async def check_daily(self) -> None:
        stats = await database.get_today_stats()
        used = stats["unauth_requests"]
        if used >= settings.max_unauth_daily:
            raise RateLimitExceeded(
                f"Daily unauthenticated limit reached ({used}/{settings.max_unauth_daily})",
                retry_after_seconds=_seconds_until_midnight(),
            )

    async def wait(self) -> None:
        """Check limits and apply random delay. Must be called before each request."""
        async with self._lock:
            await self.check_daily()
        delay = random.uniform(settings.delay_unauth_min, settings.delay_unauth_max)
        await asyncio.sleep(delay)
        await database.increment_daily_stat("unauth")

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore


class AuthLimiter:
    """Rate limiter for authenticated Mode B (followers).

    When account_username is provided, daily tracking is per-account
    (ig_account_daily_stats table). Otherwise uses the global ig_daily_stats.
    """

    def __init__(self, account_username: str | None = None) -> None:
        self._hourly = _HourlyWindow(settings.max_auth_hourly)
        self._lock = asyncio.Lock()
        self._account_username = account_username

    async def check_limits(self) -> None:
        if self._account_username:
            stats = await database.get_account_today_stats(self._account_username)
            used_daily = stats["auth_requests"]
        else:
            stats = await database.get_today_stats()
            used_daily = stats["auth_requests"]
        if used_daily >= settings.max_auth_daily:
            raise RateLimitExceeded(
                f"Daily authenticated limit reached ({used_daily}/{settings.max_auth_daily})",
                retry_after_seconds=_seconds_until_midnight(),
            )
        wait_secs = self._hourly.seconds_until_slot()
        if wait_secs > 0:
            raise RateLimitExceeded(
                f"Hourly authenticated limit reached ({settings.max_auth_hourly}/h)",
                retry_after_seconds=wait_secs,
            )

    async def wait(self) -> None:
        """Check limits and apply random delay. Must be called before each authenticated op."""
        async with self._lock:
            await self.check_limits()
            self._hourly.record()
        delay = random.uniform(settings.delay_auth_min, settings.delay_auth_max)
        await asyncio.sleep(delay)
        if self._account_username:
            await database.increment_account_daily_stat(self._account_username)
        else:
            await database.increment_daily_stat("auth")

    async def handle_429(self, backoff_seconds: int = 60) -> None:
        """Call this when Instagram returns 429 or equivalent."""
        await asyncio.sleep(backoff_seconds)

    def count_this_hour(self) -> int:
        """Public accessor for current authenticated hourly usage."""
        return self._hourly.count_this_hour()


def _seconds_until_midnight() -> int:
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Next midnight
    from datetime import timedelta
    next_midnight = midnight + timedelta(days=1)
    return int((next_midnight - now).total_seconds())


# Singletons — imported by other modules
unauth_limiter = UnauthLimiter()
auth_limiter = AuthLimiter()
