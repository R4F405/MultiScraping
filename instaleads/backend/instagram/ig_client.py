import json
import logging
import re
from typing import Any

from curl_cffi.requests import AsyncSession

from backend.config.settings import settings
from backend.instagram.ig_rate_limiter import auth_limiter, unauth_limiter
from backend.instagram.ig_retry import retry_with_backoff

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_XHR_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json, text/plain, */*",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_IG_APP_ID = "936619743392459"

_session: AsyncSession | None = None


class RateLimitedError(Exception):
    """Raised when Instagram returns 429 (rate limited)."""

    pass


def _classify_profile_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "429" in text or "rate limit" in text:
        return "rate_limit"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "connection" in text or "network" in text:
        return "network"
    if "login" in text or "session" in text or "challenge" in text:
        return "auth"
    return "unknown"


def _to_optional_str(value: Any) -> str | None:
    """Normalize values like HttpUrl to plain str for API responses."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _get_session() -> AsyncSession:
    """Get or create persistent HTTP session for curl_cffi."""
    global _session
    if _session is None:
        # Configure proxy if available
        proxies = None
        if settings.proxy_url:
            proxies = {
                "http": settings.proxy_url,
                "https": settings.proxy_url,
            }
            logger.info("Using proxy for HTTP requests: %s", settings.proxy_url.split("://")[1] if "://" in settings.proxy_url else settings.proxy_url)

        _session = AsyncSession(impersonate="chrome131", proxies=proxies)
    return _session


async def close_session() -> None:
    """Close the persistent HTTP session."""
    global _session
    if _session:
        await _session.close()
        _session = None


async def _fetch_profile_request(username: str) -> dict[str, Any] | None:
    """Internal: make the actual HTTP request to get profile."""
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {
        **_XHR_HEADERS,
        "X-IG-App-ID": _IG_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{username}/",
    }

    session = await _get_session()
    try:
        resp = await session.get(url, headers=headers, timeout=15)
    except Exception as exc:
        logger.warning("Network error fetching %s: %s", username, exc)
        raise

    if resp.status_code == 404:
        logger.debug("Profile not found: %s", username)
        return None
    if resp.status_code == 429:
        logger.warning("Rate limited by Instagram fetching %s", username)
        raise RateLimitedError(f"Instagram rate limit for {username}")
    if resp.status_code >= 500:
        raise IOError(f"Instagram server error {resp.status_code} for {username}")
    if resp.status_code != 200:
        logger.warning("Unexpected status %d for %s", resp.status_code, username)
        return None

    try:
        data = resp.json()
        user = data["data"]["user"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to parse profile JSON for %s: %s", username, exc)
        return None

    biography: str = user.get("biography") or ""
    bio_url: str | None = (user.get("bio_links") or [{}])[0].get("url") or user.get("external_url")
    emails_in_bio = _EMAIL_RE.findall(biography)
    email = emails_in_bio[0] if emails_in_bio else None

    return {
        "username": user.get("username", username),
        "full_name": user.get("full_name"),
        "biography": biography,
        "bio_url": bio_url,
        "is_business_account": bool(user.get("is_business_account", False)),
        "follower_count": (
            user.get("edge_followed_by", {}).get("count")
            or user.get("follower_count")
            or user.get("followers")
            or 0
        ),
        "profile_pic_url": _to_optional_str(user.get("profile_pic_url_hd") or user.get("profile_pic_url")),
        "email": email,
        "email_source": "bio" if email else None,
        "is_private": bool(user.get("is_private", False)),
    }


async def get_profile(username: str) -> dict[str, Any] | None:
    """
    Fetch public Instagram profile data without authentication.
    Applies rate limiting before the request.
    Retries on transient errors (network, rate limiting).
    Returns None if profile is not found or access is restricted.
    """
    await unauth_limiter.wait()

    try:
        return await retry_with_backoff(
            _fetch_profile_request,
            username,
            max_retries=settings.retry_max_attempts,
            base_delay=settings.retry_base_delay,
            max_delay=settings.retry_max_delay,
            retry_on=(Exception,),  # Retry on network errors, server errors, and rate limits
        )
    except RateLimitedError as exc:
        logger.error("Rate limited after retries for %s: %s", username, exc)
        return None
    except Exception as exc:
        err = _classify_profile_error(exc)
        logger.warning("Failed to fetch profile %s (%s): %s", username, err, exc)
        return None


def extract_email_from_text(text: str) -> str | None:
    matches = _EMAIL_RE.findall(text or "")
    return matches[0] if matches else None


def _extract_email_and_bio_url(user: Any) -> tuple[str | None, str | None, str | None]:
    """Extract (email, email_source, bio_url) from an instagrapi UserShort/User object."""
    email: str | None = None
    email_source: str | None = None

    if getattr(user, "public_email", None):
        email = user.public_email
        email_source = "public_email"

    if not email and getattr(user, "biography", None):
        matches = _EMAIL_RE.findall(user.biography)
        if matches:
            email = matches[0]
            email_source = "bio"

    if not email and getattr(user, "external_url", None):
        matches = _EMAIL_RE.findall(user.external_url)
        if matches:
            email = matches[0]
            email_source = "external_url"

    bio_url: str | None = None
    bio_links = getattr(user, "bio_links", None)
    if bio_links:
        first = bio_links[0]
        bio_url = _to_optional_str(first.url if hasattr(first, "url") else None)
    if not bio_url and getattr(user, "external_url", None):
        bio_url = _to_optional_str(user.external_url)

    return email, email_source, bio_url


async def _fetch_profile_authenticated_request(username: str) -> dict[str, Any] | None:
    """Internal: make the authenticated request to get profile via instagrapi."""
    from backend.instagram import ig_session

    cl = ig_session.get_client()
    user_id = cl.user_id_from_username(username)
    user = cl.user_info(user_id)
    email, email_source, bio_url = _extract_email_and_bio_url(user)

    return {
        "username": user.username,
        "full_name": user.full_name,
        "biography": user.biography or "",
        "bio_url": bio_url,
        "is_business_account": user.is_business,
        "follower_count": user.follower_count,
        "profile_pic_url": _to_optional_str(user.profile_pic_url),
        "email": email,
        "email_source": email_source,
        "is_private": user.is_private,
        "phone": user.contact_phone_number,
        "business_category": user.business_category_name,
    }


async def get_profile_authenticated(username: str) -> dict[str, Any] | None:
    """
    Fetch Instagram profile using authenticated instagrapi client.
    Extracts public_email, phone, business_category when available.
    Retries on transient errors (network, rate limiting).
    Returns None if client unavailable or profile not found.
    """
    from backend.instagram import ig_session

    if not ig_session.is_logged_in():
        return None

    await auth_limiter.wait()

    try:
        return await retry_with_backoff(
            _fetch_profile_authenticated_request,
            username,
            max_retries=settings.retry_max_attempts,
            base_delay=settings.retry_base_delay,
            max_delay=settings.retry_max_delay,
            retry_on=(Exception,),  # Retry on any transient error
        )
    except Exception as exc:
        err = _classify_profile_error(exc)
        logger.debug("Failed to fetch authenticated profile for %s after retries (%s): %s", username, err, exc)
        return None


async def _fetch_profile_authenticated_by_id_request(user_id: int) -> dict[str, Any] | None:
    """Internal: fetch authenticated profile by ID via instagrapi."""
    from backend.instagram import ig_session

    cl = ig_session.get_client()
    user = cl.user_info(user_id)
    email, email_source, bio_url = _extract_email_and_bio_url(user)

    return {
        "username": user.username,
        "full_name": user.full_name,
        "biography": user.biography or "",
        "bio_url": bio_url,
        "is_business_account": user.is_business,
        "follower_count": user.follower_count,
        "profile_pic_url": _to_optional_str(user.profile_pic_url),
        "email": email,
        "email_source": email_source,
        "is_private": user.is_private,
        "phone": user.contact_phone_number,
        "business_category": user.business_category_name,
    }


async def get_profile_authenticated_by_id(
    user_id: int,
    apply_rate_limit: bool = True,
) -> dict[str, Any] | None:
    """
    Fetch Instagram profile by user ID using authenticated instagrapi client.
    Avoids extra username→user_id lookup when ID is already known.
    Retries on transient errors.
    """
    from backend.instagram import ig_session

    if not ig_session.is_logged_in():
        return None

    if apply_rate_limit:
        await auth_limiter.wait()

    try:
        return await retry_with_backoff(
            _fetch_profile_authenticated_by_id_request,
            user_id,
            max_retries=settings.retry_max_attempts,
            base_delay=settings.retry_base_delay,
            max_delay=settings.retry_max_delay,
            retry_on=(Exception,),  # Retry on any transient error
        )
    except Exception as exc:
        err = _classify_profile_error(exc)
        logger.debug("Failed to fetch authenticated profile by ID %d after retries (%s): %s", user_id, err, exc)
        return None


async def get_profile_authenticated_by_id_with_client(
    user_id: int,
    client: object,
) -> dict[str, Any] | None:
    """Fetch authenticated profile by ID using an externally provided instagrapi Client.

    Used by the account pool so each account's client is used directly
    instead of the global singleton. Rate limiting must be applied by the caller.
    """
    try:
        cl = client
        user = cl.user_info(user_id)
        email, email_source, bio_url = _extract_email_and_bio_url(user)

        return {
            "username": user.username,
            "full_name": user.full_name,
            "biography": user.biography or "",
            "bio_url": bio_url,
            "is_business_account": user.is_business,
            "follower_count": user.follower_count,
            "profile_pic_url": _to_optional_str(user.profile_pic_url),
            "email": email,
            "email_source": email_source,
            "is_private": user.is_private,
            "phone": user.contact_phone_number,
            "business_category": user.business_category_name,
        }
    except Exception as exc:
        err = _classify_profile_error(exc)
        logger.debug("Failed to fetch profile by ID %d with external client (%s): %s", user_id, err, exc)
        return None


async def get_profile_with_pool_client(username: str, client: object) -> dict[str, Any] | None:
    """Fetch profile by username using an externally provided instagrapi Client.

    Used when no single session is active but a pool account client is available.
    Rate limiting must be applied by the caller.
    """
    try:
        cl = client
        user_id = cl.user_id_from_username(username)
        user = cl.user_info(user_id)
        email, email_source, bio_url = _extract_email_and_bio_url(user)

        return {
            "username": user.username,
            "full_name": user.full_name,
            "biography": user.biography or "",
            "bio_url": bio_url,
            "is_business_account": user.is_business,
            "follower_count": user.follower_count,
            "profile_pic_url": _to_optional_str(user.profile_pic_url),
            "email": email,
            "email_source": email_source,
            "is_private": user.is_private,
            "phone": user.contact_phone_number,
            "business_category": user.business_category_name,
        }
    except Exception as exc:
        err = _classify_profile_error(exc)
        logger.debug("Pool client fetch failed for %s (%s): %s", username, err, exc)
        return None


async def get_profile_best(username: str) -> dict[str, Any] | None:
    """
    Smart dispatcher: try authenticated profile first, then pool accounts, fallback to public.
    Returns None if profile not found by any method.
    """
    # 1. Try single session if active
    profile = await get_profile_authenticated(username)
    if profile:
        return profile

    # 2. Try pool account if available (Instagram now requires auth for profile API)
    try:
        from backend.instagram.ig_account_pool import account_pool
        if not account_pool.is_empty():
            cl, limiter, pool_username = await account_pool.get_next_client()
            await limiter.wait()
            profile = await get_profile_with_pool_client(username, cl)
            if profile:
                logger.debug("Profile fetched via pool account %s for @%s", pool_username, username)
                return profile
    except Exception as exc:
        logger.debug("Pool account fetch failed for @%s: %s", username, exc)

    # 3. Fallback to unauthenticated (may be blocked by Instagram)
    return await get_profile(username)
