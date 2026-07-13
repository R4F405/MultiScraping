import asyncio
import functools
import logging

import curl_cffi.requests as curl_requests

from backend.config.settings import Settings
from backend.scraper.ig_proxy_manager import ig_proxy_manager
from backend.scraper.ig_rate_limiter import RateLimiter
from backend.scraper.ig_session import get_session

logger = logging.getLogger(__name__)

IG_APP_ID = Settings.IG_APP_ID


class IgAuthError(RuntimeError):
    """Raised when Instagram rejects the request for lack of a valid session."""


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


def _curl_extra_kwargs() -> dict:
    """
    Transport tweaks for curl_cffi.

    IG_IMPERSONATE selects the browser TLS fingerprint profile (default
    chrome131); "none"/"off" disables it (needed behind TLS-intercepting
    egress proxies that reject impersonated ClientHellos). IG_CA_BUNDLE
    points curl at a custom CA bundle for such proxies.
    """
    kwargs: dict = {}
    imp = (Settings.IG_IMPERSONATE or "").strip()
    if imp and imp.lower() not in ("none", "off", "0", "false"):
        kwargs["impersonate"] = imp
    if Settings.IG_CA_BUNDLE:
        kwargs["verify"] = Settings.IG_CA_BUNDLE
    return kwargs


async def ig_get(url: str, max_retries: int = Settings.IG_MAX_RETRIES) -> dict:
    """
    Unauthenticated/guest GET to Instagram with proxy rotation, rate limiting
    and retries.

    When a session is configured (see :mod:`ig_session`) it is attached
    automatically — Instagram now throttles logged-out requests to
    near-uselessness, so an authenticated session dramatically improves the
    success rate even for "public" endpoints.
    """
    session = get_session()
    return await _ig_request(url, session=session, max_retries=max_retries, require_auth=False)


async def ig_get_authenticated(url: str, max_retries: int = Settings.IG_MAX_RETRIES) -> dict:
    """
    Authenticated GET against Instagram's private web API.

    Raises :class:`IgAuthError` when no session is configured or Instagram
    rejects the session (login_required), so callers can surface a clear
    "configure IG_SESSIONID" message instead of silently returning nothing.
    """
    session = get_session()
    if session is None or not session.authenticated:
        raise IgAuthError(
            "No Instagram session configured. Set IG_SESSIONID (or IG_SESSION_FILE) "
            "to scrape followers or private-API endpoints."
        )
    return await _ig_request(url, session=session, max_retries=max_retries, require_auth=True)


async def _ig_request(url: str, *, session, max_retries: int, require_auth: bool) -> dict:
    loop = asyncio.get_running_loop()

    headers = dict(BASE_HEADERS)
    cookies = None
    if session is not None and session.authenticated:
        headers.update(session.headers())
        cookies = session.cookies()

    last_status: int | None = None

    # Anti-ban: an authenticated session sticks to one pinned proxy (stable IP
    # per account) instead of rotating; guest requests keep round-robin.
    pin_proxy = (
        require_auth
        and Settings.IG_SESSION_PINNED_PROXY
        and session is not None
        and session.authenticated
    )
    session_key = (getattr(session, "ds_user_id", None) or "session") if pin_proxy else ""

    for attempt in range(max_retries):
        proxy = ig_proxy_manager.get_pinned(session_key) if pin_proxy else ig_proxy_manager.get_next()
        proxies = {"https": proxy, "http": proxy} if proxy else None

        try:
            await _rate_limiter.check_and_wait()

            fn = functools.partial(
                curl_requests.get,
                url,
                headers=headers,
                cookies=cookies,
                proxies=proxies,
                timeout=15,
                **_curl_extra_kwargs(),
            )
            response = await loop.run_in_executor(None, fn)
            last_status = response.status_code

            if response.status_code == 401:
                logger.warning(
                    "HTTP 401 on attempt %d via %s",
                    attempt + 1,
                    proxy[:35] if proxy else "direct",
                )
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                if require_auth:
                    # 401 with a session means the session is invalid/expired.
                    raise IgAuthError("Instagram returned 401 — session invalid or expired")
                continue

            if response.status_code == 429:
                logger.warning("429 on attempt %d — backing off", attempt + 1)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await _rate_limiter.on_rate_limited()
                continue

            if response.status_code == 404:
                return {"error": "not_found", "status_code": 404}

            if response.status_code != 200:
                logger.warning("HTTP %d on attempt %d for %s", response.status_code, attempt + 1, url)
                continue

            data = response.json()

            if data.get("require_login") or data.get("message") == "login_required":
                logger.warning("require_login on attempt %d", attempt + 1)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                if require_auth:
                    raise IgAuthError("Instagram returned login_required — session invalid or expired")
                await _rate_limiter.on_rate_limited()
                continue

            if data.get("status") == "fail":
                logger.warning("status=fail on attempt %d: %s", attempt + 1, data.get("message"))
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await _rate_limiter.on_rate_limited()
                continue

            _rate_limiter.reset_backoff()
            if proxy:
                ig_proxy_manager.report_success(proxy)
            return data

        except IgAuthError:
            raise
        except Exception as e:
            logger.error("ig_get attempt %d failed: %s", attempt + 1, e)
            if proxy:
                ig_proxy_manager.report_error(proxy)

    return {"error": "max_retries_exceeded", "status_code": last_status}
