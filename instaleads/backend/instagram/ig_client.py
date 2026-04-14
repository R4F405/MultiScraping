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

_CHROME_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="131", "Google Chrome";v="131"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_IG_APP_ID = "936619743392459"

_session: AsyncSession | None = None


class RateLimitedError(Exception):
    """Raised when Instagram returns 429 (rate limited)."""

    pass


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
        **_CHROME_HEADERS,
        "X-IG-App-ID": _IG_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{username}/",
        "Accept": "application/json",
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
            retry_on=(RateLimitedError,),  # Only retry on rate limit, not network errors
        )
    except RateLimitedError as exc:
        logger.error("Rate limited after retries for %s: %s", username, exc)
        return None
    except Exception as exc:
        logger.warning("Failed to fetch profile %s: %s", username, exc)
        return None


def extract_email_from_text(text: str) -> str | None:
    matches = _EMAIL_RE.findall(text or "")
    return matches[0] if matches else None


async def _fetch_profile_authenticated_request(username: str) -> dict[str, Any] | None:
    """Internal: make the authenticated request to get profile via instagrapi."""
    from backend.instagram import ig_session

    cl = ig_session.get_client()
    user_id = cl.user_id_from_username(username)
    user = cl.user_info(user_id)

    # Priority cascade for email extraction
    email = user.public_email
    email_source = "public_email" if email else None

    if not email and user.biography:
        emails_in_bio = _EMAIL_RE.findall(user.biography)
        if emails_in_bio:
            email = emails_in_bio[0]
            email_source = "bio"

    if not email and user.external_url:
        emails_in_url = _EMAIL_RE.findall(user.external_url)
        if emails_in_url:
            email = emails_in_url[0]
            email_source = "external_url"

    bio_url = None
    if user.bio_links:
        bio_url = user.bio_links[0].url if hasattr(user.bio_links[0], "url") else None
    if not bio_url and user.external_url:
        bio_url = user.external_url

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
        logger.debug("Failed to fetch authenticated profile for %s after retries: %s", username, exc)
        return None


async def _fetch_profile_authenticated_by_id_request(user_id: int) -> dict[str, Any] | None:
    """Internal: fetch authenticated profile by ID via instagrapi."""
    from backend.instagram import ig_session

    cl = ig_session.get_client()
    user = cl.user_info(user_id)

    # Priority cascade for email extraction
    email = user.public_email
    email_source = "public_email" if email else None

    if not email and user.biography:
        emails_in_bio = _EMAIL_RE.findall(user.biography)
        if emails_in_bio:
            email = emails_in_bio[0]
            email_source = "bio"

    if not email and user.external_url:
        emails_in_url = _EMAIL_RE.findall(user.external_url)
        if emails_in_url:
            email = emails_in_url[0]
            email_source = "external_url"

    bio_url = None
    if user.bio_links:
        bio_url = user.bio_links[0].url if hasattr(user.bio_links[0], "url") else None
    if not bio_url and user.external_url:
        bio_url = user.external_url

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
        logger.debug("Failed to fetch authenticated profile by ID %d after retries: %s", user_id, exc)
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

        email = user.public_email
        email_source = "public_email" if email else None

        if not email and user.biography:
            emails_in_bio = _EMAIL_RE.findall(user.biography)
            if emails_in_bio:
                email = emails_in_bio[0]
                email_source = "bio"

        if not email and user.external_url:
            emails_in_url = _EMAIL_RE.findall(user.external_url)
            if emails_in_url:
                email = emails_in_url[0]
                email_source = "external_url"

        bio_url = None
        if user.bio_links:
            bio_url = user.bio_links[0].url if hasattr(user.bio_links[0], "url") else None
        if not bio_url and user.external_url:
            bio_url = user.external_url

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
        logger.debug("Failed to fetch profile by ID %d with external client: %s", user_id, exc)
        return None


async def get_profile_best(username: str) -> dict[str, Any] | None:
    """
    Smart dispatcher: try authenticated profile first, fallback to public.
    Returns None if profile not found by either method.
    """
    # Try authenticated first if session available
    profile = await get_profile_authenticated(username)
    if profile:
        return profile

    # Fallback to unauthenticated
    return await get_profile(username)
