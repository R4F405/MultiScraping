import asyncio
import functools
import logging
from urllib.parse import urljoin

import curl_cffi.requests as curl_requests

from backend.config.settings import Settings
from backend.scraper import ig_mobile
from backend.scraper.ig_proxy_manager import ig_proxy_manager
from backend.scraper.ig_rate_limiter import DailyLimitReached, RateLimiter
from backend.scraper.ig_session import get_session, web_user_agent

logger = logging.getLogger(__name__)

IG_APP_ID = Settings.IG_APP_ID


class IgAuthError(RuntimeError):
    """Raised when Instagram rejects the request for lack of a valid session."""


class IgChallengeError(IgAuthError):
    """The account must pass a checkpoint/challenge before it can be used again
    (open Instagram with it in a browser/app, complete it, copy a new sessionid)."""


def _base_headers() -> dict:
    return {
        "x-ig-app-id": IG_APP_ID,
        "User-Agent": web_user_agent(),
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.instagram.com/",
        "Origin": "https://www.instagram.com",
    }


# Kept for backwards compatibility (imported by older code/tests).
BASE_HEADERS = _base_headers()

# unauth: guest/web lookups + dorking. auth: followers list pages (the
# iterator paces them itself, so only backoff applies). enrich: one profile
# lookup per follower in Fase 2 — its own delay and daily cap.
_rate_limiter = RateLimiter(mode="unauth")
_auth_rate_limiter = RateLimiter(mode="auth")
_enrich_rate_limiter = RateLimiter(mode="enrich")


def effective_proxy_list() -> list[str]:
    """Proxy URLs from the encrypted settings store (panel) if set, else env."""
    from backend.config.settings_store import store

    raw = store.get("IG_PROXY_LIST")
    if raw is not None:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return Settings.IG_PROXY_LIST


def reload_proxies() -> int:
    """Re-init the proxy manager from current config. Returns proxy count."""
    proxies = effective_proxy_list()
    ig_proxy_manager.init(proxies)
    return len(proxies)


reload_proxies()


def _curl_extra_kwargs(impersonate: str | None = None) -> dict:
    """
    Transport tweaks for curl_cffi.

    ``impersonate`` selects the browser TLS fingerprint profile (defaults to
    IG_IMPERSONATE); "none"/"off"/"" disables it (needed behind
    TLS-intercepting egress proxies that reject impersonated ClientHellos).
    IG_CA_BUNDLE points curl at a custom CA bundle for such proxies.
    """
    kwargs: dict = {}
    imp = (Settings.IG_IMPERSONATE if impersonate is None else impersonate or "").strip()
    if imp and imp.lower() not in ("none", "off", "0", "false"):
        kwargs["impersonate"] = imp
    if Settings.IG_CA_BUNDLE:
        kwargs["verify"] = Settings.IG_CA_BUNDLE
    return kwargs


async def ig_get(url: str, max_retries: int | None = None, session=None) -> dict:
    """
    Unauthenticated/guest GET to Instagram with proxy rotation, rate limiting
    and retries.

    ``session`` lets a caller pin a specific identity (e.g. a separate guest
    account for email enrichment, see :func:`ig_session.get_enrichment_session`)
    instead of the main account. When omitted, falls back to the main session
    if one is configured — Instagram now throttles logged-out requests to
    near-uselessness, so an authenticated session dramatically improves the
    success rate even for "public" endpoints.
    """
    if max_retries is None:
        max_retries = Settings.IG_MAX_RETRIES
    if session is None:
        session = get_session()
    return await _ig_request(url, session=session, max_retries=max_retries, require_auth=False)


def _require_session(session):
    session = session if session is not None else get_session()
    if session is None or not session.authenticated:
        raise IgAuthError(
            "No Instagram session configured. Set IG_SESSIONID (or IG_SESSION_FILE) "
            "to scrape followers or private-API endpoints."
        )
    return session


async def ig_get_authenticated(url: str, max_retries: int | None = None) -> dict:
    """
    Authenticated GET against Instagram's private web API.

    Raises :class:`IgAuthError` when no session is configured or Instagram
    rejects the session (login_required), so callers can surface a clear
    "configure IG_SESSIONID" message instead of silently returning nothing.
    """
    if max_retries is None:
        max_retries = Settings.IG_MAX_RETRIES
    session = _require_session(None)
    return await _ig_request(
        url, session=session, max_retries=max_retries, require_auth=True,
        limiter=_auth_rate_limiter,
    )


async def ig_mobile_get(
    endpoint: str,
    params: dict | None = None,
    *,
    session=None,
    max_retries: int | None = None,
    purpose: str = "auth",
) -> dict:
    """
    Authenticated GET against the Android private API
    (``https://i.instagram.com/api/v1/<endpoint>``).

    ``purpose`` picks the rate limiter: ``"auth"`` for followers pages (paced
    by the caller) or ``"enrich"`` for per-profile lookups (own delay + cap).
    """
    if max_retries is None:
        max_retries = Settings.IG_MAX_RETRIES
    session = _require_session(session)
    url = urljoin(ig_mobile.MOBILE_API_BASE, endpoint.lstrip("/"))
    return await _ig_request(
        url,
        session=session,
        max_retries=max_retries,
        require_auth=True,
        params=params,
        headers=ig_mobile.mobile_headers(session),
        cookies={},
        limiter=_enrich_rate_limiter if purpose == "enrich" else _auth_rate_limiter,
        impersonate=Settings.IG_MOBILE_IMPERSONATE,
    )


async def ig_mobile_graphql(
    friendly_name: str,
    root_field: str,
    variables: dict,
    doc_id: str,
    *,
    session=None,
    extra_headers: dict | None = None,
    max_retries: int | None = None,
) -> dict:
    """POST to the Android app's private GraphQL (``i.instagram.com/graphql/query``)."""
    if max_retries is None:
        max_retries = Settings.IG_MAX_RETRIES
    session = _require_session(session)
    headers = ig_mobile.graphql_headers(session, friendly_name, root_field, doc_id)
    if extra_headers:
        headers.update(extra_headers)
    return await _ig_request(
        ig_mobile.MOBILE_GRAPHQL_URL,
        session=session,
        max_retries=max_retries,
        require_auth=True,
        method="POST",
        data=ig_mobile.graphql_form(friendly_name, variables, doc_id),
        headers=headers,
        cookies={},
        limiter=_auth_rate_limiter,
        impersonate=Settings.IG_MOBILE_IMPERSONATE,
    )


# ── Response classification ──────────────────────────────────────────────────

def _json_or_none(response) -> dict | None:
    try:
        data = response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _message(payload: dict | None) -> str:
    return str((payload or {}).get("message") or "").strip()


def _is_challenge(payload: dict | None) -> bool:
    if not payload:
        return False
    msg = _message(payload).lower()
    return (
        msg in ("challenge_required", "checkpoint_required")
        or bool(payload.get("checkpoint_url"))
        or (isinstance(payload.get("challenge"), dict) and bool(payload["challenge"]))
    )


def _is_throttle(status: int, payload: dict | None) -> bool:
    """429s and the "Please wait a few minutes" family. The message is
    localized by Accept-Language ("Espera unos minutos…"), hence 'minut'."""
    if status == 429:
        return True
    if not payload:
        return False
    msg = _message(payload).lower()
    return (
        bool(payload.get("spam"))
        or payload.get("error_type") == "rate_limit_error"
        or "feedback_required" in msg
        or "minut" in msg
    )


def _is_login_required(status: int, payload: dict | None) -> bool:
    if _message(payload) == "login_required":
        return True
    return status == 401 or bool((payload or {}).get("require_login"))


async def _ig_request(
    url: str,
    *,
    session,
    max_retries: int,
    require_auth: bool,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    headers: dict | None = None,
    cookies: dict | None = None,
    limiter: RateLimiter | None = None,
    impersonate: str | None = None,
) -> dict:
    loop = asyncio.get_running_loop()
    limiter = limiter or _rate_limiter

    if headers is None:
        headers = _base_headers()
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
        via = proxy[:35] if proxy else "direct"

        try:
            await limiter.check_and_wait()

            fn = functools.partial(
                curl_requests.request,
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                cookies=cookies or None,
                proxies=proxies,
                timeout=20,
                **_curl_extra_kwargs(impersonate),
            )
            response = await loop.run_in_executor(None, fn)
            status = response.status_code
            last_status = status
            payload = _json_or_none(response)
            message = _message(payload)

            if _is_challenge(payload):
                logger.warning("challenge/checkpoint on attempt %d via %s: %s", attempt + 1, via, message)
                if require_auth:
                    raise IgChallengeError(
                        "Instagram pide verificar la cuenta (challenge/checkpoint). Abre Instagram con "
                        "esa cuenta, completa la verificación y pega un sessionid nuevo en el panel."
                    )
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                continue

            if _is_throttle(status, payload):
                logger.warning(
                    "throttled (HTTP %d%s) on attempt %d via %s — backing off",
                    status, f": {message}" if message else "", attempt + 1, via,
                )
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await limiter.on_rate_limited()
                continue

            if _is_login_required(status, payload):
                logger.warning("login required (HTTP %d) on attempt %d via %s", status, attempt + 1, via)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                if require_auth:
                    raise IgAuthError("Instagram returned login_required — session invalid or expired")
                await limiter.on_rate_limited()
                continue

            low = message.lower()
            if status == 404 or "user not found" in low:
                return {"error": "not_found", "status_code": status}

            if "not authorized to view user" in low:
                return {"error": "private", "status_code": status}

            if status != 200:
                logger.warning(
                    "HTTP %d on attempt %d for %s%s",
                    status, attempt + 1, url, f": {message}" if message else "",
                )
                continue

            if payload is None:
                logger.warning("non-JSON 200 on attempt %d for %s", attempt + 1, url)
                continue

            if payload.get("status") == "fail":
                logger.warning("status=fail on attempt %d: %s", attempt + 1, message)
                if proxy:
                    ig_proxy_manager.report_error(proxy, Settings.IG_PROXY_ERROR_COOLDOWN)
                await limiter.on_rate_limited()
                continue

            limiter.reset_backoff()
            if proxy:
                ig_proxy_manager.report_success(proxy)
            return payload

        except (IgAuthError, DailyLimitReached):
            raise
        except Exception as e:
            logger.error("ig request attempt %d failed: %s", attempt + 1, e)
            if proxy:
                ig_proxy_manager.report_error(proxy)

    return {"error": "max_retries_exceeded", "status_code": last_status}
