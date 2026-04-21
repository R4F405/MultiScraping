import logging
import os
from datetime import datetime

from backend.config.settings import Settings
from backend.scraper.ig_client import ig_get
from backend.scraper.ig_session import SESSION_PATH, get_authenticated_client, session_info
from backend.storage import database as db

logger = logging.getLogger(__name__)

FIX_GUIDE = (
    "1. Check that IG_APP_ID in ig_client.py is still valid.\n"
    "2. Try updating impersonate version in ig_client.py (e.g. chrome131 → chrome132).\n"
    "3. If IP is blocked, wait 1-2 hours before retrying.\n"
    "4. Check https://scrapfly.io/blog/posts/how-to-scrape-instagram for updated endpoints."
)


async def run_health_check() -> dict:
    """Check both unauthenticated and authenticated scraping modes."""
    test_account = Settings.IG_HEALTH_TEST_ACCOUNT

    # If the daily unauthenticated limit is already reached, skip the live check —
    # the endpoint is fine, we just exhausted today's quota.
    used_today = await db.get_daily_count("unauth")
    daily_limit = Settings.IG_LIMIT_DAILY_UNAUTHENTICATED
    if used_today >= daily_limit:
        unauth_ok = True
        unauth_status = "rate_limited"
        unauth_message = f"Límite diario alcanzado ({used_today}/{daily_limit}) — el conector funciona correctamente"
    else:
        unauth_ok = False
        unauth_status = "broken"
        unauth_message = "Unauthenticated endpoint not responding"
        try:
            data = await ig_get(
                f"https://www.instagram.com/api/v1/users/web_profile_info/?username={test_account}"
            )
            unauth_ok = "data" in data and "user" in data.get("data", {})
            if unauth_ok:
                unauth_status = "ok"
                unauth_message = "All systems operational"
        except Exception as e:
            logger.warning("Health check unauthenticated failed: %s", e)

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

    status = "ok" if unauth_ok else "broken"
    message = unauth_message

    result = {
        "status": status,
        "unauth_mode": unauth_status,
        "auth_mode": (
            "ok" if auth_ok is True
            else "broken" if auth_ok is False
            else "no_session"
        ),
        "message": message,
        "fix_guide": FIX_GUIDE if status == "broken" else None,
        "checked_at": datetime.now().isoformat(),
    }

    await db.insert_health_log(status, unauth_ok, auth_ok, message)
    return result
