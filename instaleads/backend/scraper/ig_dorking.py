import logging
import re
import uuid
from typing import AsyncGenerator

import curl_cffi.requests as curl_requests

from backend.scraper.ig_deduplicator import Deduplicator
from backend.scraper.ig_profile import get_profile
from backend.scraper.ig_rate_limiter import DailyLimitReached
from backend.storage import database as db

logger = logging.getLogger(__name__)

DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"

INSTAGRAM_USERNAME_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]{2,30})"
)

DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

_SKIP_SLUGS = {
    "p", "reel", "explore", "stories", "tv", "accounts", "about",
    "privacy", "legal", "help", "press", "api",
}


def _build_queries(niche: str, location: str) -> list[str]:
    return [
        f"instagram {niche} {location} @gmail.com",
        f"instagram {niche} {location} @hotmail.com",
        f"instagram {niche} {location} email contacto",
        f"instagram {niche} {location}",
    ]


async def _scrape_serp(query: str) -> list[str]:
    """Returns Instagram usernames found in DuckDuckGo search results for a query."""
    try:
        response = curl_requests.get(
            DDG_SEARCH_URL,
            params={"q": query},
            headers=DDG_HEADERS,
            impersonate="chrome131",
            timeout=15,
        )
        html = response.text
        candidates = INSTAGRAM_USERNAME_RE.findall(html)
        usernames = [u for u in candidates if u.lower() not in _SKIP_SLUGS]
        logger.debug("DDG query '%s' → %d usernames", query[:60], len(usernames))
        return list(dict.fromkeys(usernames))
    except Exception as e:
        logger.error("DDG SERP scrape failed: %s", e)
        return []


async def search_and_extract(
    niche: str,
    location: str,
    max_results: int,
    job_id: str,
) -> AsyncGenerator[dict, None]:
    """Async generator: yields one profile dict per profile found WITH email.

    Profiles without email or already in DB are silently discarded.
    """
    dedup = Deduplicator()
    await dedup.load_from_db()

    queries = _build_queries(niche, location)
    emails_found = 0

    for query in queries:
        if emails_found >= max_results:
            break

        usernames = await _scrape_serp(query)

        for username in usernames:
            if emails_found >= max_results:
                break

            if dedup.should_skip(username):
                logger.debug("@%s already in DB — skipping (quota preserved)", username)
                continue

            try:
                profile = await get_profile(username)
            except DailyLimitReached:
                logger.info("Job %s: quota diaria alcanzada con %d emails encontrados", job_id[:8], emails_found)
                return

            if profile is None:
                dedup.mark_seen(username)
                await db.update_job_progress(job_id, emails_found, emails_found)
                continue

            if profile.get("private"):
                await db.insert_ig_skipped(username, profile.get("instagram_id"), "private")
                dedup.mark_seen(username)
                await db.update_job_progress(job_id, emails_found, emails_found)
                continue

            if not profile.get("email"):
                await db.insert_ig_skipped(username, profile.get("instagram_id"), "no_email")
                dedup.mark_seen(username)
                await db.update_job_progress(job_id, emails_found, emails_found)
                continue

            await db.upsert_ig_lead(
                profile,
                job_id=job_id,
                source_type="dorking",
                source_value=f"{niche}|{location}",
            )
            emails_found += 1
            dedup.mark_seen(username)
            await db.update_job_progress(job_id, emails_found, emails_found)
            logger.info("Job %s: %d/%d emails encontrados", job_id[:8], emails_found, max_results)
            yield profile
