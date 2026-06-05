import asyncio
import random
import time

from backend.config.settings import Settings


class RateLimiter:
    def __init__(self):
        self._last_request_time: float = 0.0
        self._backoff = Settings.TIKTOKLEADS_BACKOFF_INITIAL
        self._lock = asyncio.Lock()
        self._error_lock = asyncio.Lock()
        self._cooldown_until: float = 0.0

    async def wait_turn(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                await asyncio.sleep(self._cooldown_until - now)
            delay = random.uniform(Settings.TIKTOKLEADS_DELAY_MIN, Settings.TIKTOKLEADS_DELAY_MAX)
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_time = time.monotonic()

    async def wait_cooldown(self):
        """Espera hasta que expire el cooldown global (p. ej. tras captcha/blocked). Sin bloquear `wait_turn`."""
        while True:
            wait_s = 0.0
            async with self._lock:
                now = time.monotonic()
                if now >= self._cooldown_until:
                    return
                wait_s = self._cooldown_until - now
            await asyncio.sleep(wait_s)

    async def on_retryable_error(self, error_kind: str = "retryable"):
        async with self._error_lock:
            if error_kind in {"blocked", "captcha"}:
                self._cooldown_until = time.monotonic() + max(20, self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(
                self._backoff * Settings.TIKTOKLEADS_BACKOFF_MULTIPLIER,
                Settings.TIKTOKLEADS_BACKOFF_MAX,
            )

    def reset_backoff(self):
        self._backoff = Settings.TIKTOKLEADS_BACKOFF_INITIAL


class EnrichThrottle:
    """
    Token bucket (tasa media de arranque de enrich) + semáforo (máximo en vuelo).
    Comparte cooldown con RateLimiter vía wait_cooldown.
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        concurrency: int,
        *,
        bucket_capacity: float | None = None,
        refill_rate: float | None = None,
    ):
        self._rate_limiter = rate_limiter
        c = max(1, int(concurrency))
        self._sem = asyncio.Semaphore(c)
        if bucket_capacity is not None:
            cap = float(bucket_capacity)
        else:
            cap = max(1.0, float(c) * 1.2)
        self._capacity = max(1.0, cap)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        if refill_rate is not None:
            self._refill_rate = float(refill_rate)
        else:
            mid = (Settings.TIKTOKLEADS_DELAY_MIN + Settings.TIKTOKLEADS_DELAY_MAX) / 2.0
            self._refill_rate = (1.0 / mid) if mid > 0 else 0.5
        self._bucket_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, rate_limiter: RateLimiter) -> "EnrichThrottle":
        return cls(
            rate_limiter,
            Settings.TIKTOKLEADS_ENRICH_CONCURRENCY,
            bucket_capacity=Settings.TIKTOKLEADS_ENRICH_BUCKET_BURST,
        )

    async def acquire_start(self):
        await self._rate_limiter.wait_cooldown()
        while True:
            sleep_for = 0.0
            async with self._bucket_lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    break
                need = 1.0 - self._tokens
                sleep_for = need / self._refill_rate if self._refill_rate > 0 else 0.5
            await asyncio.sleep(sleep_for)
        await self._sem.acquire()

    def release_slot(self):
        self._sem.release()
