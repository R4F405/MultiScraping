"""
Followers scraper (Modo B) — authenticated.

State of Instagram as of September 2026 (see instagrapi issue #2798 / PR #2801
and instagrapi 3.0.14):

* The legacy web GraphQL ``/graphql/query/?query_hash=…`` followers query —
  what this module used before — now returns the right ``count`` but
  **always-empty** ``edges`` and ``has_next_page: false``. That is why jobs
  finished with 0 followers.
* What still works, in order of preference (the same chain instagrapi uses):

  1. ``v1``  — Android private API
     ``GET i.instagram.com/api/v1/friendships/{id}/followers/`` paginated with
     ``max_id`` / ``next_max_id``.
  2. ``gql`` — Android private GraphQL ``FollowersList``
     (``POST i.instagram.com/graphql/query``, root field
     ``xdt_api__v1__friendships__followers``). Used when v1 answers with
     ``should_limit_list_of_followers`` or fails. Same ``max_id`` cursor.
  3. ``web`` — ``www.instagram.com/api/v1/friendships/{id}/followers/`` with
     the browser session. Last resort; Instagram caps it at ~50 for most
     sessions.

The cursor is persisted as ``"<strategy>:<max_id>"`` so a later job on the
same account resumes where the previous one stopped.

The endpoints require an authenticated session (see :mod:`ig_session`).
"""

import asyncio
import logging
import random
from typing import AsyncGenerator
from urllib.parse import quote, urlencode

from backend.config.settings import Settings
from backend.scraper import ig_mobile
from backend.scraper.ig_client import (
    IgAuthError,
    ig_get_authenticated,
    ig_mobile_get,
    ig_mobile_graphql,
)
from backend.scraper.ig_session import get_session
from backend.storage import database as db

logger = logging.getLogger(__name__)

# Daily counter key used for the authenticated followers endpoint (kept
# separate from the "unauth" dorking counter so its cap is account-scoped).
_FOLLOWERS_DAILY_MODE = "followers"

STRATEGIES = ("v1", "gql", "web")

_WEB_PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
_WEB_FOLLOWERS_URL = "https://www.instagram.com/api/v1/friendships/{user_id}/followers/?{query}"
_GQL_FRIENDLY_NAME = "FollowersList"
_GQL_ROOT_FIELD = "xdt_api__v1__friendships__followers"


class FollowersError(RuntimeError):
    """Non-auth operational failure while scraping followers."""


def encode_cursor(strategy: str, max_id: str) -> str:
    return f"{strategy}:{max_id}" if max_id else ""


def decode_cursor(raw: str | None) -> tuple[str, str]:
    """``"v1:QVFB…"`` → ``("v1", "QVFB…")``. Cursors saved by the old
    query_hash implementation carry no prefix and can't be resumed by the
    current endpoints → start over (the deduplicator skips known users)."""
    if not raw:
        return "v1", ""
    strategy, sep, max_id = raw.partition(":")
    if sep and strategy in STRATEGIES and max_id:
        return strategy, max_id
    logger.info("Discarding legacy followers cursor (pre-Sept-2026 GraphQL format)")
    return "v1", ""


async def resolve_user_id(username: str) -> str | None:
    """Resolve an Instagram username to its numeric user id.

    Tries the mobile ``usernameinfo`` endpoint first, then ``web_profile_info``
    through the mobile host, then the web host (increasingly 400/429 on
    datacenter IPs in 2026).
    """
    username = username.strip().lstrip("@")
    if not username:
        return None
    safe = quote(username)

    data = await ig_mobile_get(f"users/{safe}/usernameinfo/")
    user = data.get("user") if not data.get("error") else None
    if user and (user.get("pk") or user.get("id")):
        if user.get("is_private"):
            logger.warning("resolve_user_id(%s): target is private — only works if you follow it", username)
        return str(user.get("pk") or user.get("id"))
    if data.get("error") == "not_found":
        return None

    for fetch in (
        lambda: ig_mobile_get("users/web_profile_info/", params={"username": username}),
        lambda: ig_get_authenticated(_WEB_PROFILE_URL.format(username=safe)),
    ):
        data = await fetch()
        if data.get("error"):
            logger.warning("resolve_user_id(%s): %s", username, data.get("error"))
            continue
        user = (data.get("data") or {}).get("user")
        if user and user.get("id"):
            return str(user["id"])
    return None


def _normalize_follower(entry: dict) -> dict:
    """Normalize a follower entry (mobile ``users[]`` or legacy GraphQL node)."""
    return {
        "instagram_id": str(entry.get("pk") or entry.get("pk_id") or entry.get("id") or "") or None,
        "username": entry.get("username"),
        "full_name": entry.get("full_name"),
        "is_private": bool(entry.get("is_private")),
        "is_verified": bool(entry.get("is_verified")),
        "profile_pic_url": entry.get("profile_pic_url"),
    }


def _gql_root(data: dict) -> dict:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    root = payload.get(_GQL_ROOT_FIELD)
    if isinstance(root, dict):
        return root
    for key, value in payload.items():
        if _GQL_ROOT_FIELD in str(key) and isinstance(value, dict):
            return value
    return {}


async def _fetch_page(
    strategy: str, user_id: str, page_size: int, max_id: str, rank_token: str
) -> tuple[list[dict], str, bool] | None:
    """Fetch one page with ``strategy``.

    Returns ``(users, next_max_id, limited)`` or ``None`` when that strategy
    failed (so the caller moves on to the next one). ``IgAuthError``
    propagates: a dead session won't be fixed by another endpoint.
    """
    if strategy == "v1":
        params = {
            "count": page_size,
            "rank_token": rank_token,
            "search_surface": "follow_list_page",
            "query": "",
            "enable_groups": "true",
        }
        if max_id:
            params["max_id"] = max_id
        data = await ig_mobile_get(f"friendships/{user_id}/followers/", params=params)
        if data.get("error") or not isinstance(data.get("users"), list):
            logger.warning("followers v1(%s): %s", user_id, data.get("error") or "no users in payload")
            return None
        return data["users"], str(data.get("next_max_id") or ""), bool(data.get("should_limit_list_of_followers"))

    if strategy == "gql":
        variables = {
            "user_id": str(user_id),
            "skip_suggested_users": True,
            "skip_more_groups_available": True,
            "skip_friendship_followers_fields": True,
            "request_data": {"rank_token": rank_token, "enableGroups": True},
            "skip_page_size": True,
            "skip_pending_admins": True,
            "skip_has_more": True,
            "search_surface": "follow_list_page",
            "query": "",
            "skip_big_list": True,
            "include_unseen_count": True,
        }
        if max_id:
            variables["max_id"] = max_id
        data = await ig_mobile_graphql(
            _GQL_FRIENDLY_NAME,
            _GQL_ROOT_FIELD,
            variables,
            Settings.IG_FOLLOWERS_DOC_ID,
            extra_headers={"X-FB-RMD": "state=URL_ELIGIBLE"},
        )
        if data.get("error") or data.get("errors"):
            logger.warning("followers gql(%s): %s", user_id, data.get("error") or data.get("errors"))
            return None
        root = _gql_root(data)
        if not isinstance(root.get("users"), list):
            logger.warning("followers gql(%s): missing %s payload", user_id, _GQL_ROOT_FIELD)
            return None
        return root["users"], str(root.get("next_max_id") or ""), False

    # web
    query = {"count": 12, "search_surface": "follow_list_page"}
    if max_id:
        query["max_id"] = max_id
    data = await ig_get_authenticated(_WEB_FOLLOWERS_URL.format(user_id=user_id, query=urlencode(query)))
    if data.get("error") or not isinstance(data.get("users"), list):
        logger.warning("followers web(%s): %s", user_id, data.get("error") or "no users in payload")
        return None
    return data["users"], str(data.get("next_max_id") or ""), bool(data.get("should_limit_list_of_followers"))


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
        start_cursor: resume point saved by a previous run (``strategy:max_id``).

    Each yielded dict also carries ``_next_cursor`` so callers can persist the
    cursor for resume-after-throttle.

    Raises:
        IgAuthError: no/invalid session (or challenge required).
        FollowersError: every strategy failed before anything was collected.
    """
    page_size = page_size or Settings.IG_FOLLOWERS_PAGE_SIZE
    hard_cap = Settings.IG_FOLLOWERS_MAX_PER_JOB
    limit = amount if amount and amount > 0 else hard_cap
    limit = min(limit, hard_cap)

    strategy, max_id = decode_cursor(start_cursor)
    token = ig_mobile.rank_token(get_session())
    seen: set[str] = set()
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

        page = await _fetch_page(strategy, user_id, page_size, max_id, token)
        await db.increment_daily_count(_FOLLOWERS_DAILY_MODE)

        if page is None:
            nxt = STRATEGIES.index(strategy) + 1
            if nxt >= len(STRATEGIES):
                raise FollowersError(
                    f"followers fetch failed with every endpoint ({', '.join(STRATEGIES)}) "
                    f"after {yielded} followers"
                )
            logger.info("iter_followers(%s): %s failed → falling back to %s", user_id, strategy, STRATEGIES[nxt])
            strategy = STRATEGIES[nxt]
            continue

        users, next_max_id, limited = page
        fresh = 0
        cursor_out = encode_cursor(strategy, next_max_id)
        for entry in users:
            follower = _normalize_follower(entry)
            key = follower["instagram_id"] or follower["username"]
            if not key or key in seen:
                continue
            seen.add(key)
            fresh += 1
            follower["_next_cursor"] = cursor_out
            yield follower
            yielded += 1
            if yielded >= limit:
                logger.info("iter_followers(%s): reached limit %d", user_id, limit)
                return

        if limited and not next_max_id and strategy == "v1":
            # Instagram capped the v1 list for this session: re-read this
            # same page through private GraphQL, which keeps paginating.
            logger.info(
                "iter_followers(%s): v1 list limited (should_limit_list_of_followers) after %d — "
                "switching to private GraphQL", user_id, yielded,
            )
            strategy = "gql"
            continue

        if not next_max_id:
            logger.debug("iter_followers(%s): cursor exhausted at %d followers", user_id, yielded)
            return

        empty_pages = empty_pages + 1 if fresh == 0 else 0
        if empty_pages >= 2:
            logger.debug("iter_followers(%s): two pages without new followers — stopping", user_id)
            return
        max_id = next_max_id

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

    Automatically resumes from the cursor saved on a previous run for this
    same account (see :mod:`backend.storage.database` — table
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
