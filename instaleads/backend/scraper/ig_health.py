from datetime import datetime, timedelta

from backend.config.settings import Settings
from backend.storage import database as db

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

    result = {
        "status": "ok",
        "unauth_mode": unauth_status,
        "message": unauth_message,
        "proxy_count": proxies_count,
        "fix_guide": None,
        "checked_at": datetime.now().isoformat(),
    }

    await db.insert_health_log("ok", unauth_status == "ok", unauth_message)
    _health_cache = result
    _health_cache_at = datetime.now()
    return result
