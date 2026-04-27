import asyncio
import functools
import logging

import curl_cffi.requests as curl_requests

from backend.config.settings import Settings
from backend.scraper.ig_proxy_manager import ig_proxy_manager
from backend.scraper.ig_rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

IG_APP_ID = Settings.IG_APP_ID

BASE_HEADERS = {
    "x-ig-app-id": IG_APP_ID,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.instagram.com/",
    "Origin": "https://www.instagram.com",
}

_rate_limiter = RateLimiter(mode="unauth")

if Settings.IG_PROXY_LIST:
    ig_proxy_manager.init(Settings.IG_PROXY_LIST)


async def ig_get(url: str, max_retries: int = Settings.IG_MAX_RETRIES) -> dict:
    """Unauthenticated GET to Instagram with proxy rotation, rate limiting and retries."""
    loop = asyncio.get_running_loop()

    for attempt in range(max_retries):
        proxy = ig_proxy_manager.get_next()
        proxies = {"https": proxy, "http": proxy} if proxy else None

        try:
            await _rate_limiter.check_and_wait()

            # Run blocking curl call in thread pool — keeps asyncio event loop free
            fn = functools.partial(
                curl_requests.get,
                url,
                headers=BASE_HEADERS,
                proxies=proxies,
                impersonate="chrome131",
                timeout=15,
            )
            response = await loop.run_in_executor(None, fn)

            if response.status_code == 401:
                logger.warning(
                    "HTTP 401 on attempt %d via %s",
                    attempt + 1,
                    proxy[:35] if proxy else "direct",
                )
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                continue

            if response.status_code == 429:
                logger.warning("429 on attempt %d — backing off", attempt + 1)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await _rate_limiter.on_rate_limited()
                continue

            if response.status_code != 200:
                logger.warning("HTTP %d on attempt %d for %s", response.status_code, attempt + 1, url)
                continue

            data = response.json()

            if data.get("require_login") or data.get("status") == "fail":
                logger.warning("require_login/fail on attempt %d", attempt + 1)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await _rate_limiter.on_rate_limited()
                continue

            _rate_limiter.reset_backoff()
            if proxy:
                ig_proxy_manager.report_success(proxy)
            return data

        except Exception as e:
            logger.error("ig_get attempt %d failed: %s", attempt + 1, e)
            if proxy:
                ig_proxy_manager.report_error(proxy)

    return {"error": "max_retries_exceeded"}
