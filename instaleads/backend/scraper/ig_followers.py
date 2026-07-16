"""
Followers scraper (Modo B) — authenticated.

Instagram's desktop web only reveals a small slice of an account's followers
in the modal (~50 at a time) and requires you to keep scrolling to load more.

**Primary strategy — private web API (what the IG web front-end uses in 2026).**
The logged-in web app loads the followers modal by calling the private web
endpoint:

    GET /api/v1/friendships/{user_id}/followers/?count={n}&search_surface=follow_list_page

Each response carries a ``users`` array and, while more pages remain, a
``next_max_id`` cursor. Feeding that cursor back as ``&max_id={cursor}`` walks
the **full** followers list, page by page, well past the ~50 the modal shows on
screen. The ``search_surface=follow_list_page`` parameter is what keeps the
endpoint paginating instead of returning the capped
``should_limit_list_of_followers`` slice. This path only needs the ``sessionid``
cookie plus the web ``x-ig-app-id`` header (see :mod:`ig_session`).

**Fallback strategy — web GraphQL.** If the private endpoint is unavailable or
returns the capped list on the very first page, the scraper falls back to the
GraphQL followers query:

    GET /graphql/query/?query_hash={hash}&variables={"id","first","after"}

paginating via ``edge_followed_by.page_info.end_cursor``. The old
``query_hash`` Instagram used here was retired; the hash below is the one the
maintained ``instagrapi`` project still ships (``user_followers_gql_chunk``).

The historical ``query_hash`` GET endpoint has grown unreliable, which is why
the private web API is now the primary path rather than the fallback.

Typical flow:
  1. resolve the target account's numeric user id (``web_profile_info``)
  2. iterate followers via the cursor, yielding lightweight follower records
     (``instagram_id``, ``username``, ``full_name``, ``is_private``…)
  3. optionally enrich each follower with a full profile fetch to extract email
"""

import asyncio
import json
import logging
import random
from dataclasses import dataclass
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

# Primary: private web API used by the logged-in web app's followers modal.
# ``search_surface=follow_list_page`` is required to keep it paginating past the
# capped ~50 slice; pagination is driven by the ``next_max_id`` cursor.
_FOLLOWERS_V1_URL = (
    "https://www.instagram.com/api/v1/friendships/{user_id}/followers/"
    "?count={count}&search_surface=follow_list_page"
)

# Fallback: web GraphQL followers query. Hash tracks instagrapi's
# ``user_followers_gql_chunk`` (the previous hash was retired by Instagram).
_FOLLOWERS_QUERY_HASH = "37479f2b8209594dde7facb0d904896a"
_GRAPHQL_URL = "https://www.instagram.com/graphql/query/?query_hash={query_hash}&variables={variables}"

# Cursor strategy tags. The persisted resume cursor is prefixed with the
# strategy it belongs to (``v1:...`` / ``gql:...``) so a resumed job knows which
# endpoint the cursor came from and never feeds a v1 max_id to GraphQL.
_STRATEGY_V1 = "v1"
_STRATEGY_GQL = "gql"


class FollowersError(RuntimeError):
    """Non-auth operational failure while scraping followers."""


@dataclass
class _Page:
    """One page of followers normalized across the v1 and GraphQL endpoints."""

    followers: list[dict]
    next_cursor: str  # raw, untagged; "" when the list is exhausted
    has_next: bool
    capped: bool  # Instagram returned the limited follower slice (no cursor)
    error: str | None


def _followers_v1_url(user_id: str, count: int, max_id: str = "") -> str:
    """Build the private-web-API followers URL for one page (optionally after a cursor)."""
    url = _FOLLOWERS_V1_URL.format(user_id=quote(str(user_id)), count=count)
    if max_id:
        url += f"&max_id={quote(str(max_id))}"
    return url


def _followers_gql_url(user_id: str, page_size: int, after: str = "") -> str:
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


def _tag_cursor(strategy: str, raw: str) -> str:
    """Prefix a raw cursor with its strategy for safe resume; "" stays "" (done)."""
    return f"{strategy}:{raw}" if raw else ""


def _parse_cursor(tagged: str) -> tuple[str | None, str]:
    """Split a persisted cursor into (strategy, raw_cursor).

    Returns ``(None, "")`` for an empty or legacy/untagged cursor so the caller
    starts a fresh walk (untagged cursors came from the retired GraphQL hash and
    are not reusable).
    """
    if tagged and ":" in tagged:
        strat, raw = tagged.split(":", 1)
        if strat in (_STRATEGY_V1, _STRATEGY_GQL):
            return strat, raw
    return None, ""


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
    """Normalize a follower entry from either endpoint (v1 ``users`` / GraphQL ``node``)."""
    return {
        "instagram_id": str(entry.get("pk") or entry.get("pk_id") or entry.get("id") or "") or None,
        "username": entry.get("username"),
        "full_name": entry.get("full_name"),
        "is_private": bool(entry.get("is_private")),
        "is_verified": bool(entry.get("is_verified")),
        "profile_pic_url": entry.get("profile_pic_url"),
    }


async def _fetch_v1_page(user_id: str, count: int, max_id: str) -> _Page:
    """Fetch one page from the private web API followers endpoint."""
    data = await ig_get_authenticated(_followers_v1_url(user_id, count, max_id))
    if data.get("error"):
        return _Page([], "", False, False, str(data.get("error")))

    users = data.get("users") or []
    followers = [_normalize_follower(u) for u in users]
    next_cursor = str(data.get("next_max_id") or "")
    has_next = bool(next_cursor)
    # Instagram signals the capped follower list with this flag and no cursor.
    capped = bool(data.get("should_limit_list_of_followers")) and not next_cursor
    return _Page(followers, next_cursor, has_next, capped, None)


async def _fetch_gql_page(user_id: str, first: int, after: str) -> _Page:
    """Fetch one page from the web GraphQL followers query."""
    data = await ig_get_authenticated(_followers_gql_url(user_id, first, after))
    if data.get("error"):
        return _Page([], "", False, False, str(data.get("error")))

    edge = (data.get("data") or {}).get("user", {}).get("edge_followed_by") or {}
    edges = edge.get("edges") or []
    followers = [_normalize_follower(e.get("node") or {}) for e in edges]
    page_info = edge.get("page_info") or {}
    next_cursor = str(page_info.get("end_cursor") or "")
    has_next = bool(page_info.get("has_next_page")) and bool(next_cursor)
    return _Page(followers, next_cursor, has_next, False, None)


async def _fetch_page(strategy: str, user_id: str, page_size: int, cursor: str) -> _Page:
    if strategy == _STRATEGY_GQL:
        return await _fetch_gql_page(user_id, page_size, cursor)
    return await _fetch_v1_page(user_id, page_size, cursor)


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
        start_cursor: resume from a previously saved cursor (checkpointing). The
            cursor is strategy-tagged (``v1:``/``gql:``); an empty or legacy
            untagged cursor starts a fresh walk.

    Each yielded dict also carries ``_next_cursor`` (strategy-tagged) so callers
    can persist the cursor for resume-after-throttle.

    On a fresh walk the private web API (v1) is tried first; if it is
    unavailable or capped on the first page, it transparently falls back to the
    GraphQL query. A resumed walk stays on the strategy its saved cursor
    belongs to.

    Raises:
        IgAuthError: no/invalid session.
        FollowersError: repeated operational failures.
    """
    page_size = page_size or Settings.IG_FOLLOWERS_PAGE_SIZE
    hard_cap = Settings.IG_FOLLOWERS_MAX_PER_JOB
    limit = amount if amount and amount > 0 else hard_cap
    limit = min(limit, hard_cap)

    strategy, cursor = _parse_cursor(start_cursor)
    fresh = strategy is None
    if fresh:
        strategy = _STRATEGY_V1
        cursor = ""
    switched = False

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

        page = await _fetch_page(strategy, user_id, page_size, cursor)
        await db.increment_daily_count(_FOLLOWERS_DAILY_MODE)

        # On a fresh v1 walk, transparently fall back to GraphQL if the private
        # endpoint errors out or returns the capped slice on the very first page.
        if fresh and not switched and strategy == _STRATEGY_V1 and yielded == 0 and (page.error or page.capped):
            logger.warning(
                "iter_followers(%s): v1 followers %s — falling back to GraphQL",
                user_id, "errored" if page.error else "capped",
            )
            strategy = _STRATEGY_GQL
            cursor = ""
            switched = True
            continue

        if page.error:
            # Transient (max_retries_exceeded) — surface as operational error so
            # the caller can decide whether partial results are acceptable.
            raise FollowersError(f"followers fetch failed: {page.error}")

        if not page.followers:
            empty_pages += 1
            if empty_pages >= 2 or not page.has_next:
                logger.debug("iter_followers(%s): no more followers (yielded=%d)", user_id, yielded)
                return
        else:
            empty_pages = 0

        tagged_cursor = _tag_cursor(strategy, page.next_cursor) if page.has_next else ""
        for follower in page.followers:
            follower["_next_cursor"] = tagged_cursor
            yield follower
            yielded += 1
            if yielded >= limit:
                logger.info("iter_followers(%s): reached limit %d", user_id, limit)
                return

        if not page.has_next:
            logger.debug("iter_followers(%s): cursor exhausted at %d followers", user_id, yielded)
            return

        cursor = page.next_cursor

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

    Automatically resumes from the cursor saved on a previous run for this same
    account (see :mod:`backend.storage.database` — table ``ig_followers_cursor``),
    instead of re-walking the same first page every time. Pass
    ``reset_cursor=True`` to start over from the beginning.

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
