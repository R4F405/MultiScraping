import logging
from datetime import datetime, timedelta

from backend.config.settings import Settings
from backend.scraper.ig_session import get_authenticated_client, session_info
from backend.storage import database as db

logger = logging.getLogger(__name__)

_health_cache: dict | None = None
_health_cache_at: datetime | None = None
_HEALTH_CACHE_TTL = timedelta(minutes=2)


async def run_health_check() -> dict:
    """Service health check — does NOT call Instagram API to preserve quota."""
    global _health_cache, _health_cache_at
    if _health_cache and _health_cache_at and datetime.now() - _health_cache_at < _HEALTH_CACHE_TTL:
        return _health_cache

    used_today = await db.get_daily_count("unauth")
    daily_limit = Settings.IG_LIMIT_DAILY_UNAUTHENTICATED
    proxies_count = len(Settings.IG_PROXY_LIST)

    if used_today >= daily_limit:
        unauth_status = "rate_limited"
        unauth_message = f"Límite diario alcanzado ({used_today}/{daily_limit})"
    else:
        unauth_status = "ok"
        unauth_message = f"{used_today}/{daily_limit} requests hoy"
        if proxies_count:
            unauth_message += f" · {proxies_count} proxies activos"

    # Authenticated check (Modo B) — only if session exists
    auth_ok: bool | None = None
    info = session_info()
    if info["logged_in"]:
        try:
            cl = get_authenticated_client(Settings.IG_USERNAME, "")
            cl.get_timeline_feed()
            auth_ok = True
        except Exception as e:
            logger.warning("Health check authenticated failed: %s", e)
            auth_ok = False

    result = {
        "status": "ok",
        "unauth_mode": unauth_status,
        "auth_mode": (
            "ok" if auth_ok is True
            else "broken" if auth_ok is False
            else "no_session"
        ),
        "message": unauth_message,
        "proxy_count": proxies_count,
        "fix_guide": None,
        "checked_at": datetime.now().isoformat(),
    }

    await db.insert_health_log("ok", unauth_status == "ok", auth_ok, unauth_message)
    _health_cache = result
    _health_cache_at = datetime.now()
    return result
