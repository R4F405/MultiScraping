import logging
import re

from backend.instagram import ig_client, ig_health, ig_session
from backend.instagram.ig_deduplicator import deduplicator
from backend.instagram.ig_enrichment import enrich_email_from_bio_url
from backend.instagram.ig_rate_limiter import RateLimitExceeded, auth_limiter
from backend.storage import database

logger = logging.getLogger(__name__)

# Patterns that suggest a business/creator account worth prioritizing
_BUSINESS_USERNAME_RE = re.compile(
    r"(studio|photo|foto|design|art|oficial|official|shop|store|brand|agency|media|"
    r"creative|photography|videography|marketing|productions?|films?)",
    re.IGNORECASE,
)


def _prioritize_followers(
    follower_items: list[tuple],
) -> tuple[list[tuple], int]:
    """Sort followers to process business-like accounts first, skip private ones.

    Returns (prioritized_list, skipped_private_count).
    Private accounts rarely expose emails so we skip them entirely to save
    authenticated API quota.
    """
    public_items: list[tuple] = []
    skipped_private = 0

    for pk, user_short in follower_items:
        if getattr(user_short, "is_private", False):
            skipped_private += 1
            continue
        public_items.append((pk, user_short))

    def _business_score(item: tuple) -> int:
        _, user_short = item
        username = getattr(user_short, "username", "") or ""
        full_name = getattr(user_short, "full_name", "") or ""
        score = 0
        if _BUSINESS_USERNAME_RE.search(username):
            score += 2
        if full_name.strip():
            score += 1
        return score

    public_items.sort(key=_business_score, reverse=True)
    return public_items, skipped_private


async def extract_followers_leads(
    target_username: str,
    job_id: str,
    max_results: int = 200,
    email_goal: int | None = None,
    initial_emails_found: int = 0,
    initial_processed: int = 0,
    initial_from_ig: int = 0,
    initial_from_web: int = 0,
    initial_enrichment_attempts: int = 0,
    initial_enrichment_successes: int = 0,
    initial_skipped_private: int = 0,
    account_pool=None,  # AccountPool | None
) -> dict[str, int | bool | str | None]:
    """
    Authenticated Mode B: iterate over followers of target_username,
    extract profile data for each via instagrapi, and save leads with emails.
    Sequential — no concurrency.
    Uses instagrapi directly to avoid double requests.
    Private accounts are skipped before consuming any auth quota.
    Deduplication check happens before auth_limiter.wait() to avoid wasting slots.

    When account_pool is provided, rotates across pool accounts automatically.
    Each account uses its own AuthLimiter. When one account is rate-limited,
    the pool switches to the next available account.
    """
    from backend.instagram.ig_profile import save_profile_data

    # Use pool if available and not empty, otherwise fall back to single session
    use_pool = account_pool is not None and not account_pool.is_empty()

    if use_pool:
        # Get initial client from pool (resolves target username)
        try:
            cl, active_limiter, active_username = await account_pool.get_next_client()
        except Exception as exc:
            logger.error("Pool unavailable: %s", exc)
            use_pool = False
            cl = ig_session.get_client()
            active_limiter = auth_limiter
            active_username = None
    else:
        cl = ig_session.get_client()  # Raises if no session
        active_limiter = auth_limiter
        active_username = None

    try:
        user_id = cl.user_id_from_username(target_username)
    except Exception as exc:
        msg = f"Could not resolve user ID for '{target_username}': {exc}"
        logger.error(msg)
        ig_health.record_error(msg)
        return {"stopped_reason": "failed", "processed": 0, "emails_found": 0}

    logger.info("Starting follower extraction for @%s (user_id=%s)", target_username, user_id)

    processed = initial_processed
    emails_found = initial_emails_found
    from_ig = initial_from_ig
    from_web = initial_from_web
    enrichment_attempts = initial_enrichment_attempts
    enrichment_successes = initial_enrichment_successes
    skipped_private = initial_skipped_private
    profile_fetch_failures = 0

    try:
        followers = cl.user_followers(user_id, amount=max_results)
    except Exception as exc:
        msg = f"Failed to fetch followers for '{target_username}': {exc}"
        logger.error(msg)
        ig_health.record_error(msg)
        return {"stopped_reason": "failed", "processed": 0, "emails_found": 0}

    # followers can be dict[user_id, UserShort] or list of UserShort
    raw_items = (
        list(followers.items()) if isinstance(followers, dict)
        else [(f.pk, f) for f in followers]
    )

    # Pre-filter: skip private accounts, prioritize business-like ones
    follower_items, new_skipped_private = _prioritize_followers(raw_items[:max_results])
    skipped_private += new_skipped_private
    logger.info(
        "Followers after pre-filter: %d public (skipped %d private)",
        len(follower_items), new_skipped_private,
    )

    for pk, user_short in follower_items:
        username = user_short.username

        # Dedup check BEFORE consuming an auth rate-limit slot
        if deduplicator.is_duplicate(username):
            processed += 1
            continue

        # Apply rate limit — rotate pool accounts on exhaustion
        if use_pool:
            try:
                await active_limiter.wait()
            except RateLimitExceeded as exc:
                # Current account rate-limited; put it in cooldown and try next
                retry_after = getattr(exc, "retry_after_seconds", 1800) or 1800
                await account_pool.mark_rate_limited(active_username, seconds=retry_after)
                try:
                    cl, active_limiter, active_username = await account_pool.get_next_client()
                    await active_limiter.wait()
                    logger.info("Pool: rotated to account %s", active_username)
                except Exception as pool_exc:
                    logger.warning("Pool exhausted: %s", pool_exc)
                    raise RateLimitExceeded(
                        "All pool accounts are rate-limited.",
                        retry_after_seconds=retry_after,
                    ) from pool_exc
        else:
            try:
                await active_limiter.wait()
            except RateLimitExceeded as exc:
                logger.warning("Auth rate limit hit: %s", exc)
                raise

        # Fetch full profile using instagrapi directly by ID (avoids extra lookup)
        try:
            # Rate limit is already applied at loop level,
            # so skip the internal limiter to avoid double-counting per follower.
            if use_pool:
                data = await ig_client.get_profile_authenticated_by_id_with_client(pk, cl)
            else:
                data = await ig_client.get_profile_authenticated_by_id(pk, apply_rate_limit=False)
            if data:
                if not data.get("email"):
                    enrichment_attempts += 1
                    enrichment_source = None
                    enriched = await enrich_email_from_bio_url(data.get("bio_url"))
                    if enriched:
                        enrichment_source = "website"
                    if not enriched:
                        # Cross-platform fallback (only if enabled via env)
                        from backend.instagram.ig_cross_platform import search_cross_platform
                        enriched = await search_cross_platform(
                            data.get("username", ""),
                            bio_url=data.get("bio_url"),
                        )
                        if enriched:
                            enrichment_source = "cross_platform"
                    if enriched:
                        data["email"] = enriched
                        data["email_source"] = enrichment_source or "website"
                        enrichment_successes += 1
                lead = await save_profile_data(data, job_id=job_id, source_type="followers")
                if lead:
                    emails_found += 1
                    if data.get("email_source") == "website":
                        from_web += 1
                    else:
                        from_ig += 1
            else:
                await database.save_skipped(username, reason="fetch_failed")
                await deduplicator.mark_seen(username)
                ig_health.record_error(f"Failed to fetch profile: {username}")
        except Exception as exc:
            logger.warning("Error processing follower @%s: %s", username, exc)
            await database.save_skipped(username, reason="fetch_error")
            await deduplicator.mark_seen(username)
            ig_health.record_error(f"Follower error: {username} — {exc}")
            profile_fetch_failures += 1

        processed += 1

        # Update progress in DB every 5 profiles
        if processed % 5 == 0:
            await database.update_job_fields(
                job_id,
                progress=processed,
                profiles_scanned=processed,
                emails_found=emails_found,
                enrichment_attempts=enrichment_attempts,
                enrichment_successes=enrichment_successes,
                emails_from_ig=from_ig,
                emails_from_web=from_web,
                skipped_private=skipped_private,
                profile_fetch_failures=profile_fetch_failures,
                enrichment_failures=max(0, enrichment_attempts - enrichment_successes),
            )

        if email_goal and emails_found >= email_goal:
            logger.info("Email goal %d reached — stopping early", email_goal)
            return {
                "stopped_reason": "goal_reached",
                "processed": processed,
                "emails_found": emails_found,
                "from_ig": from_ig,
                "from_web": from_web,
                "enrichment_attempts": enrichment_attempts,
                "enrichment_successes": enrichment_successes,
                "skipped_private": skipped_private,
                "profile_fetch_failures": profile_fetch_failures,
                "enrichment_failures": max(0, enrichment_attempts - enrichment_successes),
            }

    await database.update_job_fields(
        job_id,
        progress=processed,
        profiles_scanned=processed,
        emails_found=emails_found,
        enrichment_attempts=enrichment_attempts,
        enrichment_successes=enrichment_successes,
        emails_from_ig=from_ig,
        emails_from_web=from_web,
        skipped_private=skipped_private,
        profile_fetch_failures=profile_fetch_failures,
        enrichment_failures=max(0, enrichment_attempts - enrichment_successes),
    )
    logger.info(
        "Follower extraction complete: %d processed, %d emails found, %d private skipped",
        processed, emails_found, skipped_private,
    )
    return {
        "stopped_reason": "target_exhausted",
        "processed": processed,
        "emails_found": emails_found,
        "from_ig": from_ig,
        "from_web": from_web,
        "enrichment_attempts": enrichment_attempts,
        "enrichment_successes": enrichment_successes,
        "skipped_private": skipped_private,
        "profile_fetch_failures": profile_fetch_failures,
        "enrichment_failures": max(0, enrichment_attempts - enrichment_successes),
    }
