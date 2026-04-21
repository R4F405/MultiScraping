import logging
import re

from backend.scraper.ig_client import ig_get
from backend.scraper.email_finder import find_email_in_website

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"


async def get_profile(username: str) -> dict | None:
    """Fetch a public Instagram profile and extract all relevant fields.

    Returns None for private profiles or fetch errors.
    The 'email' field may be None if no email was found anywhere.
    """
    url = PROFILE_URL.format(username=username)
    data = await ig_get(url)

    if "error" in data:
        logger.debug("get_profile(%s): fetch error — %s", username, data.get("error"))
        return None

    user = data.get("data", {}).get("user")
    if not user:
        logger.debug("get_profile(%s): no user in response", username)
        return None

    if user.get("is_private"):
        logger.debug("get_profile(%s): private profile — skipping", username)
        return {"username": username, "instagram_id": user.get("id"), "private": True, "email": None}

    email, email_source = _extract_email(user)

    # Fallback: scrape the linked website
    if not email and user.get("external_url"):
        email, email_source = await _email_from_website(user["external_url"])

    return {
        "instagram_id": user.get("id"),
        "username": user.get("username", username),
        "full_name": user.get("full_name"),
        "email": email,
        "email_source": email_source,
        "phone": user.get("business_phone_number"),
        "website": user.get("external_url"),
        "bio": user.get("biography"),
        "follower_count": user.get("follower_count", 0),
        "is_business": user.get("is_business_account", False),
        "private": False,
    }


def _extract_email(user: dict) -> tuple[str | None, str | None]:
    if user.get("business_email"):
        return user["business_email"], "business_field"

    bio = user.get("biography") or ""
    matches = EMAIL_REGEX.findall(bio)
    if matches:
        return matches[0], "bio_regex"

    return None, None


async def _email_from_website(url: str) -> tuple[str | None, str | None]:
    try:
        emails = await find_email_in_website(url)
        if emails:
            return emails[0], "website_scrape"
    except Exception as e:
        logger.debug("website scrape failed for %s: %s", url, e)
    return None, None
