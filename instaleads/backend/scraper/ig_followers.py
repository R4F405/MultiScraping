import logging
from typing import AsyncGenerator

from backend.config.settings import Settings
from backend.scraper.ig_deduplicator import Deduplicator
from backend.scraper.ig_profile import get_profile
from backend.scraper.ig_rate_limiter import RateLimiter
from backend.scraper.ig_session import get_authenticated_client
from backend.storage import database as db

logger = logging.getLogger(__name__)


async def get_followers_emails(
    target_username: str,
    max_followers: int,
    job_id: str,
) -> AsyncGenerator[dict, None]:
    """Async generator: yields one profile dict per follower found WITH email.

    Always sequential — instagrapi must never be used concurrently.
    """
    if max_followers > Settings.IG_LIMIT_DAILY_AUTHENTICATED:
        max_followers = Settings.IG_LIMIT_DAILY_AUTHENTICATED
        logger.warning(
            "max_followers capped at daily limit (%d)", Settings.IG_LIMIT_DAILY_AUTHENTICATED
        )

    cl = get_authenticated_client(Settings.IG_USERNAME, "")
    rate_limiter = RateLimiter(mode="auth")

    dedup = Deduplicator()
    await dedup.load_from_db()

    try:
        user_id = cl.user_id_from_username(target_username)
        followers: dict = cl.user_followers(user_id, amount=max_followers)
    except Exception as e:
        logger.error("Could not fetch followers of @%s: %s", target_username, e)
        return

    processed = 0
    emails_found = 0

    for username in followers.keys():
        if processed >= max_followers:
            break

        if dedup.should_skip(username):
            logger.debug("@%s already in DB — skipping (quota preserved)", username)
            continue

        try:
            await rate_limiter.check_and_wait()
        except Exception as e:
            logger.warning("Rate limit reached: %s", e)
            break

        profile = await get_profile(username)
        processed += 1

        if profile is None:
            dedup.mark_seen(username)
            continue

        if profile.get("private"):
            await db.insert_ig_skipped(username, profile.get("instagram_id"), "private")
            dedup.mark_seen(username)
            continue

        if not profile.get("email"):
            await db.insert_ig_skipped(username, profile.get("instagram_id"), "no_email")
            dedup.mark_seen(username)
            continue

        await db.upsert_ig_lead(
            profile,
            job_id=job_id,
            source_type="followers",
            source_value=target_username,
        )
        emails_found += 1
        dedup.mark_seen(username)
        await db.update_job_progress(job_id, processed, emails_found)
        yield profile
