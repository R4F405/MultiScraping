"""
TikTok profile extraction and email discovery pipeline.
"""
import logging
import re

from backend.storage import database
from backend.tiktok.email_finder import find_email_in_website
from backend.tiktok.email_verifier import verify_email_mx
from backend.tiktok.tt_browser import fetch_profile_rehydration
from backend.tiktok.tt_health import record_error, record_success
from backend.tiktok.tt_rate_limiter import limiter

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_EMAIL_PATTERNS = [
    "@2x", "@3x", "sentry", "example.com", "schema.org",
    "wixpress.com", "w3.org", "demolink.org", "your-domain",
    "yourdomain", "domain.com", "email@",
]


def _extract_email_from_text(text: str) -> str | None:
    """Extract first valid email from text, filtering out known false positives."""
    if not text:
        return None
    matches = EMAIL_RE.findall(text)
    clean = [e for e in matches if not any(p in e.lower() for p in _SKIP_EMAIL_PATTERNS)]
    return clean[0] if clean else None


async def extract_and_save(
    username: str,
    job_id: str,
    min_followers: int = 0,
) -> dict | None:
    """
    Full extraction pipeline for a single TikTok creator.

    Steps:
    1. Rate limit check + delay
    2. Fetch profile via __UNIVERSAL_DATA_FOR_REHYDRATION__
    3. Apply min_followers filter
    4. Regex email search in signature (bio)
    5. If no email + bioLink exists: visit external website
    6. If email found: verify MX records
    7. Save lead or skipped to DB

    Returns:
        Lead dict if email found and valid, None otherwise.
    """
    # 1. Rate limit
    await limiter.wait()

    # 2. Fetch profile
    try:
        profile = await fetch_profile_rehydration(username)
    except Exception as exc:
        logger.error("extract_and_save: profile fetch failed for @%s: %s", username, exc)
        record_error(str(exc))
        await database.save_skipped(username, f"fetch_error: {exc}")
        return None

    if not profile:
        logger.debug("extract_and_save: no profile data for @%s", username)
        await database.save_skipped(username, "no_profile_data")
        return None

    record_success()

    unique_id = profile.get("uniqueId") or username
    nickname = profile.get("nickname") or ""
    signature = profile.get("signature") or ""
    follower_count = int(profile.get("followerCount") or 0)
    verified = bool(profile.get("verified", False))
    bio_link = profile.get("bioLink")

    # 3. Min followers filter
    if follower_count < min_followers:
        logger.debug(
            "extract_and_save: @%s skipped — %d followers < %d minimum",
            username, follower_count, min_followers
        )
        await database.save_skipped(username, f"low_followers:{follower_count}")
        return None

    # 4. Regex email in bio
    email = _extract_email_from_text(signature)
    email_source = "bio" if email else None

    # 5. If no email but has bio link, scrape the website
    if not email and bio_link:
        logger.debug("extract_and_save: @%s — no email in bio, checking bioLink: %s", username, bio_link)
        try:
            found = await find_email_in_website(bio_link)
            if found:
                email = found[0]
                email_source = "biolink"
        except Exception as exc:
            logger.debug("extract_and_save: biolink fetch failed for @%s: %s", username, exc)

    if not email:
        await database.save_skipped(username, "no_email_found")
        return None

    # 6. Verify MX
    mx_status = await verify_email_mx(email)
    if mx_status == "invalid":
        logger.debug("extract_and_save: @%s email %s has no MX record, skipping", username, email)
        await database.save_skipped(username, f"invalid_mx:{email}")
        return None

    # 7. Save lead
    await database.save_lead(
        job_id=job_id,
        username=unique_id,
        nickname=nickname,
        email=email,
        email_source=email_source,
        followers_count=follower_count,
        verified=verified,
        bio_link=bio_link,
        bio_text=signature[:500] if signature else None,
    )

    logger.info(
        "extract_and_save: lead saved @%s — %s (source: %s, followers: %d)",
        unique_id, email, email_source, follower_count
    )

    return {
        "username": unique_id,
        "nickname": nickname,
        "email": email,
        "email_source": email_source,
        "followers_count": follower_count,
        "verified": verified,
        "bio_link": bio_link,
    }
