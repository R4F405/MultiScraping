"""
Followers scraper (Modo B) — authenticated.

Instagram's desktop web only reveals a small slice of an account's followers
(~50 at a time in the modal) and requires you to scroll to load more. Under
the hood the web app calls the private endpoint:

    GET /api/v1/friendships/{user_id}/followers/?count=50&max_id={cursor}&search_surface=follow_list_page

which returns one page of followers plus a ``next_max_id`` cursor. This module
walks that cursor until it is exhausted (or a target/limit is reached), so it
collects the **full** followers list rather than the ~50 the desktop UI shows.

The endpoint requires an authenticated session (see :mod:`ig_session`).

Typical flow:
  1. resolve the target account's numeric user id (``web_profile_info``)
  2. iterate followers via the cursor, yielding lightweight follower records
     (``pk``, ``username``, ``full_name``, ``is_private``…)
  3. optionally enrich each follower with a full profile fetch to extract email
"""

import asyncio
import logging
import random
from typing import AsyncGenerator
from urllib.parse import quote

from backend.config.settings import Settings
from backend.scraper.ig_client import IgAuthError, ig_get_authenticated
from backend.scraper.ig_session import get_session

logger = logging.getLogger(__name__)

_PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
_FOLLOWERS_URL = (
    "https://www.instagram.com/api/v1/friendships/{user_id}/followers/"
    "?count={count}&search_surface=follow_list_page"
)


class FollowersError(RuntimeError):
    """Non-auth operational failure while scraping followers."""


async def resolve_user_id(username: str) -> str | None:
    """Resolve an Instagram username to its numeric user id via the web API."""
    username = username.strip().lstrip("@")
    if not username:
        return None
    data = await ig_get_authenticated(_PROFILE_URL.format(username=quote(username)))
    if data.get("error"):
        logger.warning("resolve_user_id(%s): %s", username, data.get("error"))
        return None
    user = data.get("data", {}).get("user")
    if not user:
        return None
    return user.get("id")


def _normalize_follower(entry: dict) -> dict:
    """Normalize a follower entry from the followers endpoint."""
    return {
        "instagram_id": str(entry.get("pk") or entry.get("id") or "") or None,
        "username": entry.get("username"),
        "full_name": entry.get("full_name"),
        "is_private": bool(entry.get("is_private")),
        "is_verified": bool(entry.get("is_verified")),
        "profile_pic_url": entry.get("profile_pic_url"),
    }


async def iter_followers(
    user_id: str,
    *,
    amount: int = 0,
    page_size: int | None = None,
    stop_event: asyncio.Event | None = None,
    start_max_id: str = "",
) -> AsyncGenerator[dict, None]:
    """
    Yield followers of ``user_id`` one by one, paginating past the web's ~50 cap.

    Args:
        user_id: numeric account id whose followers to fetch.
        amount: stop after this many followers (0 = all available, capped by
            IG_FOLLOWERS_MAX_PER_JOB).
        page_size: followers requested per page (Instagram may return fewer).
        stop_event: cooperative cancellation checked between pages.
        start_max_id: resume from a previously saved cursor (checkpointing).

    Each yielded dict also carries ``_next_max_id`` so callers can persist the
    cursor for resume-after-throttle.

    Raises:
        IgAuthError: no/invalid session.
        FollowersError: repeated operational failures.
    """
    page_size = page_size or Settings.IG_FOLLOWERS_PAGE_SIZE
    hard_cap = Settings.IG_FOLLOWERS_MAX_PER_JOB
    limit = amount if amount and amount > 0 else hard_cap
    limit = min(limit, hard_cap)

    base_url = _FOLLOWERS_URL.format(user_id=user_id, count=page_size)
    max_id = start_max_id
    yielded = 0
    empty_pages = 0

    while yielded < limit:
        if stop_event is not None and stop_event.is_set():
            logger.info("iter_followers(%s): cancelled after %d", user_id, yielded)
            return

        url = base_url + (f"&max_id={quote(str(max_id))}" if max_id else "")
        data = await ig_get_authenticated(url)

        if data.get("error"):
            # Transient (max_retries_exceeded) — surface as operational error so
            # the caller can decide whether partial results are acceptable.
            raise FollowersError(f"followers fetch failed: {data.get('error')}")

        users = data.get("users") or []
        next_max_id = data.get("next_max_id") or ""

        if not users:
            empty_pages += 1
            if empty_pages >= 2 or not next_max_id:
                logger.debug("iter_followers(%s): no more followers (yielded=%d)", user_id, yielded)
                return
        else:
            empty_pages = 0

        for entry in users:
            follower = _normalize_follower(entry)
            follower["_next_max_id"] = next_max_id
            yield follower
            yielded += 1
            if yielded >= limit:
                logger.info("iter_followers(%s): reached limit %d", user_id, limit)
                return

        if not next_max_id:
            logger.debug("iter_followers(%s): cursor exhausted at %d followers", user_id, yielded)
            return

        max_id = next_max_id
        await asyncio.sleep(
            random.uniform(Settings.IG_FOLLOWERS_DELAY_MIN, Settings.IG_FOLLOWERS_DELAY_MAX)
        )


async def scrape_followers(
    target_username: str,
    *,
    amount: int = 0,
    stop_event: asyncio.Event | None = None,
) -> AsyncGenerator[dict, None]:
    """
    High-level helper: resolve the target account then yield its followers.

    Raises IgAuthError when no session is configured, FollowersError when the
    account cannot be resolved.
    """
    session = get_session()
    if session is None or not session.authenticated:
        raise IgAuthError(
            "Followers mode requires an authenticated Instagram session. "
            "Set IG_SESSIONID (or IG_SESSION_FILE)."
        )

    user_id = await resolve_user_id(target_username)
    if not user_id:
        raise FollowersError(f"Could not resolve @{target_username} (private, non-existent, or blocked).")

    logger.info("scrape_followers: @%s → user_id=%s (amount=%s)", target_username, user_id, amount or "all")
    async for follower in iter_followers(user_id, amount=amount, stop_event=stop_event):
        yield follower
