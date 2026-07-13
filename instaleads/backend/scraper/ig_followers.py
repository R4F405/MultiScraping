"""
Followers scraper (Modo B) — authenticated.

Instagram's desktop web only reveals a small slice of an account's followers
(~50 at a time in the modal) and requires you to scroll to load more.

The private mobile endpoint ``/api/v1/friendships/{user_id}/followers/`` refuses
to paginate for most sessions: it responds with ``should_limit_list_of_followers:
true``, ``has_more: false`` and **no** ``next_max_id`` cursor, so it caps every
scrape at the first ~50 followers regardless of the account size.

The web GraphQL endpoint is not subject to that limit and keeps paginating:

    GET /graphql/query/?query_hash={hash}&variables={"id","first","after"}

Each response carries ``edge_followed_by.page_info.end_cursor`` and
``has_next_page``. This module walks that cursor until it is exhausted (or a
target/limit is reached), so it collects the **full** followers list rather than
the ~50 the desktop UI and the mobile endpoint expose.

The endpoint requires an authenticated session (see :mod:`ig_session`).

Typical flow:
  1. resolve the target account's numeric user id (``web_profile_info``)
  2. iterate followers via the cursor, yielding lightweight follower records
     (``id``, ``username``, ``full_name``, ``is_private``…)
  3. optionally enrich each follower with a full profile fetch to extract email
"""

import asyncio
import json
import logging
import random
from typing import AsyncGenerator
from urllib.parse import quote

from backend.config.settings import Settings
from backend.scraper.ig_client import IgAuthError, ig_get_authenticated
from backend.scraper.ig_session import get_session
from backend.storage import database as db

logger = logging.getLogger(__name__)

# Daily counter key used for the authenticated followers endpoint (kept
# separate from the "unauth" dorking counter so its cap is account-scoped).
_FOLLOWERS_DAILY_MODE = "followers"

_PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
# GraphQL followers query. Unlike the mobile endpoint it is not capped by
# ``should_limit_list_of_followers`` and keeps handing back an end_cursor.
_FOLLOWERS_QUERY_HASH = "c76146de99bb02f6415203be841dd25a"
_GRAPHQL_URL = "https://www.instagram.com/graphql/query/?query_hash={query_hash}&variables={variables}"


def _followers_page_url(user_id: str, page_size: int, after: str = "") -> str:
    """Build the GraphQL followers URL for one page (optionally after a cursor)."""
    variables: dict = {
        "id": user_id,
        "include_reel": False,
        "fetch_mutual": False,
        "first": page_size,
    }
    if after:
        variables["after"] = after
    encoded = quote(json.dumps(variables, separators=(",", ":")))
    return _GRAPHQL_URL.format(query_hash=_FOLLOWERS_QUERY_HASH, variables=encoded)


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
    start_cursor: str = "",
) -> AsyncGenerator[dict, None]:
    """
    Yield followers of ``user_id`` one by one, paginating past the web's ~50 cap.

    Args:
        user_id: numeric account id whose followers to fetch.
        amount: stop after this many followers (0 = all available, capped by
            IG_FOLLOWERS_MAX_PER_JOB).
        page_size: followers requested per page (Instagram may return fewer).
        stop_event: cooperative cancellation checked between pages.
        start_cursor: resume from a previously saved GraphQL cursor (checkpointing).

    Each yielded dict also carries ``_next_cursor`` so callers can persist the
    cursor for resume-after-throttle.

    Raises:
        IgAuthError: no/invalid session.
        FollowersError: repeated operational failures.
    """
    page_size = page_size or Settings.IG_FOLLOWERS_PAGE_SIZE
    hard_cap = Settings.IG_FOLLOWERS_MAX_PER_JOB
    limit = amount if amount and amount > 0 else hard_cap
    limit = min(limit, hard_cap)

    cursor = start_cursor
    yielded = 0
    empty_pages = 0
    rested_at = 0

    while yielded < limit:
        if stop_event is not None and stop_event.is_set():
            logger.info("iter_followers(%s): cancelled after %d", user_id, yielded)
            return

        # Anti-ban: stop once the account hits its per-day followers cap.
        daily_cap = Settings.IG_LIMIT_DAILY_FOLLOWERS
        if daily_cap and await db.get_daily_count(_FOLLOWERS_DAILY_MODE) >= daily_cap:
            logger.warning(
                "iter_followers(%s): daily followers cap reached (%d) — stopping at %d",
                user_id, daily_cap, yielded,
            )
            return

        url = _followers_page_url(user_id, page_size, cursor)
        data = await ig_get_authenticated(url)
        await db.increment_daily_count(_FOLLOWERS_DAILY_MODE)

        if data.get("error"):
            # Transient (max_retries_exceeded) — surface as operational error so
            # the caller can decide whether partial results are acceptable.
            raise FollowersError(f"followers fetch failed: {data.get('error')}")

        edge = (data.get("data") or {}).get("user", {}).get("edge_followed_by") or {}
        edges = edge.get("edges") or []
        page_info = edge.get("page_info") or {}
        next_cursor = page_info.get("end_cursor") or ""
        has_next = bool(page_info.get("has_next_page"))

        if not edges:
            empty_pages += 1
            if empty_pages >= 2 or not has_next or not next_cursor:
                logger.debug("iter_followers(%s): no more followers (yielded=%d)", user_id, yielded)
                return
        else:
            empty_pages = 0

        for entry in edges:
            follower = _normalize_follower(entry.get("node") or {})
            follower["_next_cursor"] = next_cursor
            yield follower
            yielded += 1
            if yielded >= limit:
                logger.info("iter_followers(%s): reached limit %d", user_id, limit)
                return

        if not has_next or not next_cursor:
            logger.debug("iter_followers(%s): cursor exhausted at %d followers", user_id, yielded)
            return

        cursor = next_cursor

        # Anti-ban: longer rest every N followers to break the steady cadence.
        rest_every = Settings.IG_FOLLOWERS_REST_EVERY
        if rest_every and yielded - rested_at >= rest_every:
            rested_at = yielded
            logger.info(
                "iter_followers(%s): resting %.0fs after %d followers",
                user_id, Settings.IG_FOLLOWERS_REST_SECONDS, yielded,
            )
            await asyncio.sleep(Settings.IG_FOLLOWERS_REST_SECONDS)
        else:
            await asyncio.sleep(
                random.uniform(Settings.IG_FOLLOWERS_DELAY_MIN, Settings.IG_FOLLOWERS_DELAY_MAX)
            )


async def scrape_followers(
    target_username: str,
    *,
    amount: int = 0,
    stop_event: asyncio.Event | None = None,
    reset_cursor: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    High-level helper: resolve the target account then yield its followers.

    Automatically resumes from the GraphQL cursor saved on a previous run for
    this same account (see :mod:`backend.storage.database` — table
    ``ig_followers_cursor``), instead of re-walking the same first page every
    time. Pass ``reset_cursor=True`` to start over from the beginning.

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

    if reset_cursor:
        await db.reset_followers_cursor(target_username)
        start_cursor = ""
    else:
        start_cursor = await db.get_followers_cursor(target_username) or ""
        if start_cursor:
            logger.info("scrape_followers: @%s resuming from saved cursor", target_username)

    logger.info("scrape_followers: @%s → user_id=%s (amount=%s)", target_username, user_id, amount or "all")

    last_saved_cursor = start_cursor
    new_in_page = 0
    async for follower in iter_followers(
        user_id, amount=amount, stop_event=stop_event, start_cursor=start_cursor
    ):
        next_cursor = follower.get("_next_cursor") or ""
        if next_cursor and next_cursor != last_saved_cursor:
            # Crossed a page boundary — persist so a crash/cancel mid-run
            # still resumes past everything already yielded.
            await db.save_followers_cursor(target_username, next_cursor, collected_delta=new_in_page)
            last_saved_cursor = next_cursor
            new_in_page = 0
        new_in_page += 1
        yield follower

    if new_in_page:
        await db.save_followers_cursor(target_username, last_saved_cursor, collected_delta=new_in_page)
