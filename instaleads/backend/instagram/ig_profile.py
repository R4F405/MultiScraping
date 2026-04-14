import logging

from backend.instagram import ig_client, ig_health
from backend.instagram.ig_deduplicator import deduplicator
from backend.storage import database

logger = logging.getLogger(__name__)


async def save_profile_data(
    data: dict,
    job_id: str,
    source_type: str = "dorking",
) -> dict | None:
    """
    Save a pre-fetched profile to database.
    Used by both extract_and_save and ig_followers when data is already fetched.
    Returns the lead dict or None if no email found.
    """
    username = data.get("username")
    if not username:
        return None

    if deduplicator.is_duplicate(username):
        logger.debug("Skipping duplicate: %s", username)
        return None

    ig_health.record_success()

    email = data.get("email")
    email_source = data.get("email_source")

    # If no email in bio, try web enrichment via bio_url
    if not email and data.get("bio_url"):
        from backend.instagram.ig_enrichment import enrich_email_from_bio_url
        enriched = await enrich_email_from_bio_url(data["bio_url"])
        if enriched:
            email = enriched
            email_source = "website"
            logger.debug("Email enriched from website for %s: %s", username, email)

    # If still no email, try cross-platform search (Hunter.io, Snov.io, Google dork)
    if not email:
        from backend.instagram.ig_cross_platform import search_cross_platform
        cross_email = await search_cross_platform(username, data.get("bio_url"))
        if cross_email:
            email = cross_email
            email_source = "cross_platform"
            logger.debug("Email enriched via cross-platform for %s: %s", username, email)

    if not email:
        await database.save_skipped(username, reason="no_email")
        await deduplicator.mark_seen(username)
        logger.debug("No email found for %s (bio + website checked) — skipped", username)
        return None

    # Inject enriched email/source back into data dict for save_lead
    data = {**data, "email": email, "email_source": email_source}

    await database.save_lead(
        job_id=job_id,
        username=data["username"],
        full_name=data.get("full_name"),
        email=email,
        email_source=data.get("email_source"),
        followers_count=data.get("follower_count"),
        is_business=bool(data.get("is_business_account", False)),
        bio_url=data.get("bio_url"),
        source_type=source_type,
        phone=data.get("phone"),
        business_category=data.get("business_category"),
    )
    await deduplicator.mark_seen(username)
    logger.info("Lead saved: %s <%s> (source: %s)", username, email, data.get("email_source"))

    return {
        "username": data["username"],
        "email": email,
        "full_name": data.get("full_name"),
    }


async def extract_and_save(
    username: str,
    job_id: str,
    source_type: str = "dorking",
) -> dict | None:
    """
    Fetch and process one Instagram profile.
    - Saves to ig_leads if email found.
    - Saves to ig_skipped if no email.
    - Returns the lead dict or None.
    """
    if deduplicator.is_duplicate(username):
        logger.debug("Skipping duplicate: %s", username)
        return None

    data = await ig_client.get_profile_best(username)

    if data is None:
        await database.save_skipped(username, reason="fetch_failed")
        await deduplicator.mark_seen(username)
        ig_health.record_error(f"Failed to fetch profile: {username}")
        return None

    return await save_profile_data(data, job_id, source_type)


async def preview_profile(username: str) -> dict | None:
    """Fetch profile for preview without saving to DB."""
    return await ig_client.get_profile(username)
