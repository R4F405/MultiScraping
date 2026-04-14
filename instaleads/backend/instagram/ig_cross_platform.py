"""Cross-platform email search for Instagram profiles.

Disabled by default (CROSS_PLATFORM_ENABLED=0). Enable via env var.

When enabled, tries to find an email using:
  1. Google Custom Search dorking: '"username" email OR "@gmail.com"'
     (reuses existing Google CSE credentials)
  2. Hunter.io free API (25 lookups/month) if HUNTER_API_KEY is set
  3. Snov.io free API (50 credits/month) if SNOV_API_KEY is set

Rate limits are conservative per source to preserve free-tier quotas.
"""

import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# Simple in-memory per-hour limits per source
_HOURLY_USAGE: dict[str, list[float]] = {"google": [], "hunter": [], "snov": []}
_MAX_PER_HOUR = {"google": 20, "hunter": 5, "snov": 5}


def _can_use(source: str) -> bool:
    now = time.monotonic()
    cutoff = now - 3600
    _HOURLY_USAGE[source] = [t for t in _HOURLY_USAGE.get(source, []) if t > cutoff]
    limit = _MAX_PER_HOUR.get(source, 10)
    if len(_HOURLY_USAGE[source]) >= limit:
        return False
    _HOURLY_USAGE[source].append(now)
    return True


async def _google_dork_email(username: str) -> str | None:
    """Search Google CSE for email associated with an Instagram username."""
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        return None
    if not _can_use("google"):
        return None

    query = f'"{username}" email OR "@gmail.com" OR "@hotmail.com" site:instagram.com'
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": cse_id, "q": query, "num": 5}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        import re
        EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
        BLOCKED = {"no-reply", "noreply", "donotreply"}
        for item in data.get("items", []):
            for text in (item.get("snippet", ""), item.get("title", "")):
                for match in EMAIL_RE.findall(text):
                    local = match.split("@")[0].lower()
                    if local not in BLOCKED:
                        return match.lower()
    except Exception as exc:
        logger.debug("Google dork email search failed for %s: %s", username, exc)
    return None


async def _hunter_find_email(domain: str) -> str | None:
    """Try Hunter.io email finder for a domain (requires HUNTER_API_KEY)."""
    api_key = os.getenv("HUNTER_API_KEY", "").strip()
    if not api_key or not domain:
        return None
    if not _can_use("hunter"):
        return None

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": api_key, "limit": 1}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        emails = data.get("data", {}).get("emails", [])
        if emails:
            return emails[0].get("value")
    except Exception as exc:
        logger.debug("Hunter.io search failed for %s: %s", domain, exc)
    return None


async def _snov_find_email(domain: str) -> str | None:
    """Try Snov.io email finder for a domain (requires SNOV_CLIENT_ID + SNOV_CLIENT_SECRET)."""
    client_id = os.getenv("SNOV_CLIENT_ID", "").strip()
    client_secret = os.getenv("SNOV_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret or not domain:
        return None
    if not _can_use("snov"):
        return None

    try:
        # Get access token
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://api.snov.io/v1/oauth/access_token",
                json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            )
            if token_resp.status_code != 200:
                return None
            token = token_resp.json().get("access_token")
            if not token:
                return None

            search_resp = await client.post(
                "https://api.snov.io/v1/get-domain-emails",
                json={"access_token": token, "domain": domain, "type": "all", "limit": 1},
            )
            if search_resp.status_code != 200:
                return None
            data = search_resp.json()
            emails = data.get("emails", [])
            if emails:
                return emails[0].get("email")
    except Exception as exc:
        logger.debug("Snov.io search failed for %s: %s", domain, exc)
    return None


async def search_cross_platform(
    username: str,
    bio_url: str | None = None,
) -> str | None:
    """Attempt to find an email via cross-platform sources.

    Only runs when CROSS_PLATFORM_ENABLED=1 in the environment.
    Returns first found email or None.
    """
    enabled = os.getenv("CROSS_PLATFORM_ENABLED", "0") in {"1", "true", "True", "yes", "on"}
    if not enabled:
        return None

    # 1. Google dorking on the username
    email = await _google_dork_email(username)
    if email:
        logger.debug("Cross-platform: found via Google dork for %s: %s", username, email)
        return email

    # 2. Hunter.io / Snov.io on the bio domain (if a website is linked)
    if bio_url:
        from urllib.parse import urlparse
        parsed = urlparse(bio_url if bio_url.startswith("http") else f"https://{bio_url}")
        domain = parsed.netloc
        if domain:
            email = await _hunter_find_email(domain)
            if email:
                logger.debug("Cross-platform: found via Hunter.io for %s: %s", domain, email)
                return email

            email = await _snov_find_email(domain)
            if email:
                logger.debug("Cross-platform: found via Snov.io for %s: %s", domain, email)
                return email

    return None
