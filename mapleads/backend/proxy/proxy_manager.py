import asyncio
import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timedelta

from backend.config.settings import settings
from backend.proxy.proxy_stats import ProxyStats

logger = logging.getLogger(__name__)

_DAILY_STATE_FILE = "./data/proxy_daily_state.json"

# Estimated proxy requests consumed per company scraped:
# ~2 for Maps (place HTML + preview JSON) + ~2 for email (homepage + contact avg)
_REQUESTS_PER_COMPANY = 4

# Context var so wait_for_available knows which job is waiting (no parameter threading needed)
_current_job_ctx: ContextVar[str | None] = ContextVar("current_job", default=None)


def set_current_job(job_id: str | None) -> None:
    """Call at the start of a scrape job to enable proxy-wait tracking in the UI."""
    _current_job_ctx.set(job_id)


class ProxyManager:
    """
    Manages proxy rotation with per-proxy rate limiting, cooldown, circuit
    breaker, and a daily hard cap.

    Rules (from .env):
    - Each proxy gets MAX_REQUESTS_PER_PROXY_BEFORE_COOLDOWN requests, then
      rests PROXY_COOLDOWN_SECONDS before resuming.
    - If a proxy's error rate exceeds ERROR_RATE_THRESHOLD it gets a longer
      HIGH_ERROR_COOLDOWN_SECONDS cooldown (circuit breaker).
    - MAX_REQUESTS_PER_DAY is a global daily hard cap across all proxies.

    Usage in scrapers (always use wait_for_available, never get_next directly):

        proxy = await proxy_manager.wait_for_available()
        if not proxy:
            return None  # system paused
        try:
            ...
            await proxy_manager.report_success(proxy)
        except BlockedError:
            await proxy_manager.report_error(proxy)
    """

    def __init__(self) -> None:
        self._stats: dict[str, ProxyStats] = {}
        self._daily_count: int = 0
        self._daily_reset_date: str = datetime.now().strftime("%Y-%m-%d")
        self._lock = asyncio.Lock()
        self._initialized = False
        # job_id → seconds until next proxy (populated while job is waiting for a proxy)
        self._waiting_jobs: dict[str, int] = {}

    def _build_proxy_url(self) -> str:
        return (
            f"http://{settings.proxy_user}:{settings.proxy_pass}"
            f"@{settings.proxy_host}:{settings.proxy_port}"
        )

    def _load_daily_state(self) -> None:
        """Restore daily counter from disk so it survives server restarts."""
        try:
            if os.path.exists(_DAILY_STATE_FILE):
                with open(_DAILY_STATE_FILE) as f:
                    data = json.load(f)
                today = datetime.now().strftime("%Y-%m-%d")
                if data.get("date") == today:
                    self._daily_count = data.get("count", 0)
                    self._daily_reset_date = today
                    logger.info("ProxyManager: restored daily counter from disk — %d requests today", self._daily_count)
        except Exception as exc:
            logger.warning("ProxyManager: could not load daily state: %s", exc)

    def _save_daily_state(self) -> None:
        """Persist daily counter to disk."""
        try:
            os.makedirs(os.path.dirname(_DAILY_STATE_FILE), exist_ok=True)
            with open(_DAILY_STATE_FILE, "w") as f:
                json.dump({"date": self._daily_reset_date, "count": self._daily_count}, f)
        except Exception as exc:
            logger.warning("ProxyManager: could not save daily state: %s", exc)

    def _ensure_initialized(self) -> None:
        """Lazy init — build proxy list on first use (settings loaded by then)."""
        if self._initialized:
            return
        self._initialized = True
        self._load_daily_state()

        # PROXY_LIST takes priority over host/port (needed for Webshare static proxies)
        if settings.proxy_list:
            for url in settings.proxy_list:
                self._stats[url] = ProxyStats(url=url)
            logger.info("ProxyManager: initialized with %d proxies from PROXY_LIST", len(self._stats))
            return

        if not settings.proxy_user or not settings.proxy_pass:
            # Development mode: a single None-equivalent sentinel
            logger.debug("ProxyManager: no credentials — running without proxy")
            return

        # Fallback: single endpoint built from host/port (Webshare rotating plan)
        url = self._build_proxy_url()
        self._stats[url] = ProxyStats(url=url)
        logger.info("ProxyManager: initialized with 1 proxy endpoint (%s:%d)", settings.proxy_host, settings.proxy_port)

    def _reset_daily_counter_if_needed(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_count = 0
            self._daily_reset_date = today
            logger.info("ProxyManager: daily request counter reset")

    async def get_next(self) -> str | None:
        """
        Return the best available proxy URL, or None if:
        - No credentials configured (dev mode — caller uses None = direct connection)
        - All proxies are in cooldown
        - Daily limit reached
        """
        self._ensure_initialized()

        if not self._stats:
            logger.debug("ProxyManager.get_next(): dev mode (no proxies configured)")
            return None  # dev mode

        async with self._lock:
            self._reset_daily_counter_if_needed()

            if self._daily_count >= settings.max_requests_per_day:
                logger.warning("ProxyManager: daily limit reached (%d). Pausing.", settings.max_requests_per_day)
                return None

            available = [s for s in self._stats.values() if s.is_available]
            if not available:
                wait = min(s.seconds_until_available for s in self._stats.values())
                logger.info("ProxyManager: all proxies in cooldown — next available in %ds", wait)
                return None

            # Pick the proxy with the fewest total requests (distribute load evenly)
            chosen = min(available, key=lambda s: s.total_requests)
            chosen.total_requests += 1
            chosen.requests_since_last_cooldown += 1
            self._daily_count += 1
            self._save_daily_state()

            logger.debug(
                "ProxyManager.get_next(): returning proxy (total_req=%d, daily_count=%d/%d)",
                chosen.total_requests,
                self._daily_count,
                settings.max_requests_per_day,
            )

            limit = settings.max_requests_per_proxy_before_cooldown
            if chosen.requests_since_last_cooldown >= limit:
                chosen.requests_since_last_cooldown = 0
                chosen.cooldown_until = datetime.now() + timedelta(seconds=settings.proxy_cooldown_seconds)
                logger.debug(
                    "ProxyManager: proxy entering %ds cooldown after %d requests",
                    settings.proxy_cooldown_seconds,
                    limit,
                )

            return chosen.url

    async def report_error(self, proxy_url: str) -> None:
        """
        Report a failed/blocked request on this proxy.
        Triggers circuit breaker if error rate exceeds threshold.
        """
        self._ensure_initialized()
        if not proxy_url or proxy_url not in self._stats:
            return

        async with self._lock:
            stat = self._stats[proxy_url]
            stat.total_errors += 1

            if (
                stat.total_requests > 10
                and stat.error_rate > settings.error_rate_threshold
            ):
                stat.cooldown_until = datetime.now() + timedelta(
                    seconds=settings.high_error_cooldown_seconds
                )
                logger.warning(
                    "ProxyManager: circuit breaker triggered (error rate %.0f%%). "
                    "Proxy cooling down for %ds.",
                    stat.error_rate * 100,
                    settings.high_error_cooldown_seconds,
                )

    async def report_success(self, proxy_url: str) -> None:
        """Record a successful request (reserved for future sliding-window logic)."""
        pass

    async def wait_for_available(self, timeout_seconds: int | None = None) -> str | None:
        """
        Block until a proxy is available. By default waits indefinitely, pausing
        the scraping job until a proxy exits cooldown (smart pause/resume).

        Returns None only when:
        - Dev mode (no proxies configured) → direct connection used by caller
        - Daily limit reached → caller should skip this request
        - timeout_seconds exceeded (when explicitly set)

        The current job ID (from _current_job_ctx) is tracked in _waiting_jobs
        so the UI can show a "waiting for proxy" state.
        """
        self._ensure_initialized()

        # Dev mode: caller uses direct connection
        if not self._stats:
            return None

        job_id = _current_job_ctx.get()
        start = datetime.now()
        first_wait = True

        while True:
            proxy = await self.get_next()

            if proxy is not None:
                if job_id:
                    self._waiting_jobs.pop(job_id, None)
                return proxy

            # Daily limit reached → no point waiting, skip this request
            if self._daily_count >= settings.max_requests_per_day:
                logger.warning("ProxyManager: daily limit reached, aborting wait.")
                if job_id:
                    self._waiting_jobs.pop(job_id, None)
                return None

            # All proxies in cooldown → calculate how long to wait
            cooldown_remaining = [
                s.seconds_until_available
                for s in self._stats.values()
                if s.cooldown_until is not None
            ]
            wait_secs = min(cooldown_remaining) if cooldown_remaining else 30

            if first_wait:
                logger.info(
                    "ProxyManager: all proxies in cooldown (~%ds). Job '%s' paused.",
                    wait_secs,
                    job_id or "unknown",
                )
                first_wait = False

            if job_id:
                self._waiting_jobs[job_id] = wait_secs

            # Optional hard timeout (for callers that don't want unlimited wait)
            if timeout_seconds is not None:
                elapsed = (datetime.now() - start).total_seconds()
                if elapsed >= timeout_seconds:
                    logger.error("ProxyManager: no proxy available after %ds. Aborting.", timeout_seconds)
                    if job_id:
                        self._waiting_jobs.pop(job_id, None)
                    return None

            await asyncio.sleep(min(5, max(1, wait_secs)))

            # Refresh remaining wait estimate
            if job_id:
                still_cooling = [
                    s.seconds_until_available
                    for s in self._stats.values()
                    if not s.is_available and s.cooldown_until is not None
                ]
                if still_cooling:
                    self._waiting_jobs[job_id] = min(still_cooling)
                else:
                    self._waiting_jobs.pop(job_id, None)

    def get_job_wait_seconds(self, job_id: str) -> int:
        """Return estimated seconds until next proxy for a waiting job, or 0."""
        return self._waiting_jobs.get(job_id, 0)

    def estimate_capacity(self) -> dict:
        """
        Estimate how many companies can be scraped before proxies need to cooldown.

        Uses _REQUESTS_PER_COMPANY (~4) as the cost per company:
        - ~2 for Maps (place HTML + preview JSON)
        - ~2 for email (homepage + contact page average)
        """
        self._ensure_initialized()

        if not self._stats:
            return {
                "companies_before_wait": 9999,
                "requests_available_now": 9999,
                "all_in_cooldown": False,
                "next_available_seconds": 0,
                "daily_remaining": 9999,
                "requests_per_company_estimate": _REQUESTS_PER_COMPANY,
                "cooldown_seconds": settings.proxy_cooldown_seconds,
                "dev_mode": True,
            }

        self._reset_daily_counter_if_needed()
        available = [s for s in self._stats.values() if s.is_available]
        in_cooldown = [s for s in self._stats.values() if not s.is_available]

        # Requests left before available proxies individually hit their cooldown threshold
        requests_before_cooldown = sum(
            max(0, settings.max_requests_per_proxy_before_cooldown - s.requests_since_last_cooldown)
            for s in available
        )

        daily_remaining = max(0, settings.max_requests_per_day - self._daily_count)
        requests_available = min(requests_before_cooldown, daily_remaining)
        companies_before_wait = max(0, requests_available // _REQUESTS_PER_COMPANY)

        next_available_seconds = 0
        if not available and in_cooldown:
            next_available_seconds = min(s.seconds_until_available for s in in_cooldown)

        return {
            "companies_before_wait": companies_before_wait,
            "requests_available_now": requests_available,
            "all_in_cooldown": len(available) == 0,
            "next_available_seconds": next_available_seconds,
            "daily_remaining": daily_remaining,
            "requests_per_company_estimate": _REQUESTS_PER_COMPANY,
            "cooldown_seconds": settings.proxy_cooldown_seconds,
            "dev_mode": False,
        }

    def get_status(self) -> dict:
        """Return current system status for the /api/proxy/status endpoint."""
        self._ensure_initialized()
        available = sum(1 for s in self._stats.values() if s.is_available)
        remaining = max(0, settings.max_requests_per_day - self._daily_count)

        return {
            "total_proxies": len(self._stats),
            "available_now": available,
            "in_cooldown": len(self._stats) - available,
            "daily_requests_used": self._daily_count,
            "daily_requests_limit": settings.max_requests_per_day,
            "daily_requests_remaining": remaining,
            "proxies": [
                {
                    "id": i + 1,
                    "available": s.is_available,
                    "total_requests": s.total_requests,
                    "error_rate": f"{s.error_rate:.0%}",
                    "cooldown_remaining_seconds": s.seconds_until_available,
                }
                for i, s in enumerate(self._stats.values())
            ],
        }


# Singleton — one shared instance across the entire app
proxy_manager = ProxyManager()
