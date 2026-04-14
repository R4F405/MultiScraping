import logging

from backend.config.settings import settings
from backend.instagram import ig_session
from backend.storage import database

logger = logging.getLogger(__name__)

# Tracks consecutive error counts for blockage detection
_consecutive_errors: int = 0
_last_error: str | None = None


def record_error(message: str) -> None:
    global _consecutive_errors, _last_error
    _consecutive_errors += 1
    _last_error = message


def record_success() -> None:
    global _consecutive_errors
    _consecutive_errors = max(0, _consecutive_errors - 1)


def is_blocked() -> bool:
    return _consecutive_errors >= 5


async def get_health() -> dict:
    today_stats = await database.get_today_stats()
    unauth_today = today_stats["unauth_requests"]
    auth_today = today_stats["auth_requests"]

    session_active = ig_session.is_logged_in()

    if is_blocked():
        status = "blocked"
    elif not session_active and auth_today == 0 and unauth_today == 0:
        status = "ok"
    elif unauth_today >= settings.max_unauth_daily or auth_today >= settings.max_auth_daily:
        status = "rate_limited"
    else:
        status = "ok"

    from backend.instagram.ig_rate_limiter import auth_limiter
    auth_this_hour = auth_limiter._hourly.count_this_hour()

    import os
    google_cse_configured = bool(os.getenv("GOOGLE_API_KEY", "").strip() and os.getenv("GOOGLE_CSE_ID", "").strip())

    return {
        "status": status,
        "session_active": session_active,
        "unauth_today": unauth_today,
        "auth_today": auth_today,
        "auth_this_hour": auth_this_hour,
        "consecutive_errors": _consecutive_errors,
        "last_error": _last_error,
        "proxy_configured": bool(settings.proxy_url),
        "discovery_strategies": {
            "google_cse": google_cse_configured,
            "hashtag_api": session_active,
            "location_api": session_active,
            "hashtag_fallback": True,
        },
        "limits": {
            "max_unauth_daily": settings.max_unauth_daily,
            "max_auth_daily": settings.max_auth_daily,
            "max_auth_hourly": settings.max_auth_hourly,
        },
    }


async def get_diagnose() -> dict:
    health = await get_health()
    return {
        "blocked": is_blocked(),
        "rate_limited": health["status"] == "rate_limited",
        "last_error": _last_error,
        "consecutive_errors": _consecutive_errors,
        "session_active": health["session_active"],
    }
