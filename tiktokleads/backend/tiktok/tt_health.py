import logging

from backend.config.settings import settings
from backend.storage import database

logger = logging.getLogger(__name__)

_consecutive_errors: int = 0
_last_error: str | None = None


def record_error(message: str) -> None:
    global _consecutive_errors, _last_error
    _consecutive_errors += 1
    _last_error = message
    logger.warning("TikTok error recorded (%d consecutive): %s", _consecutive_errors, message)


def record_success() -> None:
    global _consecutive_errors
    _consecutive_errors = max(0, _consecutive_errors - 1)


def is_blocked() -> bool:
    return _consecutive_errors >= 5


async def get_health() -> dict:
    today_stats = await database.get_today_stats()
    requests_today = today_stats["requests"]

    from backend.tiktok.tt_rate_limiter import limiter
    requests_this_hour = limiter.count_this_hour()

    if is_blocked():
        status = "blocked"
    elif requests_today >= settings.max_daily:
        status = "rate_limited"
    else:
        status = "ok"

    return {
        "status": status,
        "requests_today": requests_today,
        "requests_this_hour": requests_this_hour,
        "consecutive_errors": _consecutive_errors,
        "last_error": _last_error,
        "proxy_configured": bool(settings.proxy_url),
        "headless_mode": settings.headless,
        "limits": {
            "max_daily": settings.max_daily,
            "max_per_hour": settings.max_req_hour,
        },
    }
