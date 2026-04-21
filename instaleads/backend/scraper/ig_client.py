import logging

import curl_cffi.requests as curl_requests

from backend.config.settings import Settings
from backend.scraper.ig_rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Single source of truth for the Instagram internal app ID
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


async def ig_get(url: str, max_retries: int = Settings.IG_MAX_RETRIES) -> dict:
    """Authenticated-free GET to Instagram. Handles rate limiting and retries."""
    for attempt in range(max_retries):
        try:
            await _rate_limiter.check_and_wait()

            response = curl_requests.get(
                url,
                headers=BASE_HEADERS,
                impersonate="chrome131",
                timeout=15,
            )

            if response.status_code == 429:
                logger.warning("429 received on attempt %d — backing off", attempt + 1)
                await _rate_limiter.on_rate_limited()
                continue

            if response.status_code != 200:
                logger.warning("HTTP %d on attempt %d for %s", response.status_code, attempt + 1, url)
                continue

            data = response.json()

            if data.get("require_login") or data.get("status") == "fail":
                logger.warning("require_login/fail response on attempt %d", attempt + 1)
                await _rate_limiter.on_rate_limited()
                continue

            _rate_limiter.reset_backoff()
            return data

        except Exception as e:
            logger.error("ig_get attempt %d failed: %s", attempt + 1, e)

    return {"error": "max_retries_exceeded"}
