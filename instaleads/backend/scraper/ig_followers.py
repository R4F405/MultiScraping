"""
Followers scraper (Modo B) — authenticated.

State of Instagram (Sept/Oct 2026; instagrapi 3.0.20, issues #2798 / #2811):

* The legacy web GraphQL ``query_hash`` followers query returns the right
  ``count`` but always-empty ``edges`` — not used any more.
* For many accounts every list endpoint now stops at ~47–50 followers
  (``should_limit_list_of_followers`` or simply no ``next_max_id``), while
  the account has thousands.

So instead of trusting a single endpoint, the iterator walks a chain of
*sources* and keeps going while it has fewer unique followers than the target
account really has (``follower_count``):

  1. ``v1``          — Android API ``friendships/{id}/followers/`` (``max_id``)
  2. ``gql``         — Android private GraphQL ``FollowersList``
  3. ``web``         — ``www.instagram.com/api/v1/friendships/{id}/followers/``
  4. ``v1_earliest`` — v1 sorted ``date_followed_earliest``
  5. ``v1_latest``   — v1 sorted ``date_followed_latest``
  6. ``search``      — search *inside* the followers list (the app's search
     box, instagrapi ``search_followers_v1``) by username prefixes:
     ``a``, ``b``, … and, for every prefix whose results hit the cap,
     ``aa``, ``ab``, … up to IG_FOLLOWERS_SEARCH_MAX_DEPTH. The union of all
     searches recovers the followers the capped list hides.

Every source de-duplicates against what was already yielded. The cursor is
persisted as ``"<source>:<position>"`` (a ``max_id``, or the search prefix) so
a later job resumes where this one stopped. A ``report`` dict records what
each source returned, so the job can explain a short result to the user.
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

SOURCES = ("v1", "gql", "web", "v1_earliest", "v1_latest", "search")
SOURCE_LABELS = {
    "v1": "API móvil",
    "gql": "GraphQL privada",
    "web": "web",
    "v1_earliest": "orden antiguos",
    "v1_latest": "orden recientes",
    "search": "búsqueda por letras",
}
# Kept for callers/tests that only care about the three list endpoints.
STRATEGIES = SOURCES[:3]

# Instagram usernames only use these characters.
SEARCH_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789._"

# A source is abandoned after this many consecutive pages with no new follower.
_MAX_EMPTY_PAGES = 2
# Safety valve per search prefix when Instagram does paginate a search.
_MAX_PAGES_PER_QUERY = 20
# A search sweep stops after this many consecutive failed requests.
_MAX_SEARCH_FAILURES = 3
# Lists come back without deactivated/hidden accounts, so "complete" means
# reaching this share of the profile's follower_count.
_COMPLETE_RATIO = 0.95

_WEB_PROFILE_URL = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
_WEB_FOLLOWERS_URL = "https://www.instagram.com/api/v1/friendships/{user_id}/followers/?{query}"
_GQL_FRIENDLY_NAME = "FollowersList"
_GQL_ROOT_FIELD = "xdt_api__v1__friendships__followers"
_ORDERS = {"v1_earliest": "date_followed_earliest", "v1_latest": "date_followed_latest"}


class FollowersError(RuntimeError):
    """Non-auth operational failure while scraping followers."""


def encode_cursor(source: str, position: str) -> str:
    return f"{source}:{position}" if position else ""


def decode_cursor(raw: str | None) -> tuple[str, str]:
    """``"v1:QVFB…"`` → ``("v1", "QVFB…")``. Cursors saved by the old
    query_hash implementation carry no prefix and can't be resumed by the
    current endpoints → start over (the deduplicator skips known users)."""
    if not raw:
        return "v1", ""
    source, sep, position = raw.partition(":")
    if sep and source in SOURCES and position:
        if source == "search" and any(c not in SEARCH_ALPHABET for c in position):
            return "v1", ""
        return source, position
    logger.info("Discarding legacy followers cursor (pre-Sept-2026 GraphQL format)")
    return "v1", ""


async def resolve_user(username: str) -> dict | None:
    """Resolve a username to ``{"id", "follower_count", "is_private"}``.

    Tries the mobile ``usernameinfo`` endpoint first, then ``web_profile_info``
    through the mobile host, then the web host (increasingly 400/429 on
    datacenter IPs in 2026).
    """
    username = username.strip().lstrip("@")
    if not username:
        return None
    safe = quote(username)

    def _pack(user: dict, uid) -> dict:
        count = user.get("follower_count")
        if not isinstance(count, int):
            count = (user.get("edge_followed_by") or {}).get("count")
        return {
            "id": str(uid),
            "follower_count": count if isinstance(count, int) else 0,
            "is_private": bool(user.get("is_private")),
        }

    data = await ig_mobile_get(f"users/{safe}/usernameinfo/")
    user = data.get("user") if not data.get("error") else None
    if user and (user.get("pk") or user.get("id")):
        return _pack(user, user.get("pk") or user.get("id"))
    if data.get("error") == "not_found":
        return None

    for fetch in (
        lambda: ig_mobile_get("users/web_profile_info/", params={"username": username}),
        lambda: ig_get_authenticated(_WEB_PROFILE_URL.format(username=safe)),
    ):
        data = await fetch()
        if data.get("error"):
            logger.warning("resolve_user(%s): %s", username, data.get("error"))
            continue
        user = (data.get("data") or {}).get("user")
        if user and user.get("id"):
            return _pack(user, user["id"])
    return None


async def resolve_user_id(username: str) -> str | None:
    """Resolve an Instagram username to its numeric user id."""
    info = await resolve_user(username)
    return info["id"] if info else None


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
    source: str, user_id: str, page_size: int, max_id: str, rank_token: str, query: str = ""
) -> tuple[list[dict], str, bool] | None:
    """Fetch one page from ``source``.

    Returns ``(users, next_max_id, limited)`` or ``None`` when that source
    failed. ``IgAuthError`` propagates: a dead session won't be fixed by
    another endpoint.
    """
    if source in ("v1", "v1_earliest", "v1_latest", "search"):
        if source == "search":
            # Exactly what the app's search box inside a followers list sends
            # (instagrapi search_followers_v1).
            params = {"search_surface": "follow_list_page", "query": query, "enable_groups": "true"}
        else:
            params = {
                "count": page_size,
                "rank_token": rank_token,
                "search_surface": "follow_list_page",
                "query": "",
                "enable_groups": "true",
            }
        if source in _ORDERS:
            params["order"] = _ORDERS[source]
        if max_id:
            params["max_id"] = max_id
        data = await ig_mobile_get(f"friendships/{user_id}/followers/", params=params)
        if data.get("error") or not isinstance(data.get("users"), list):
            logger.warning("followers %s(%s%s): %s", source, user_id, f", q={query}" if query else "",
                           data.get("error") or "no users in payload")
            return None
        return data["users"], str(data.get("next_max_id") or ""), bool(data.get("should_limit_list_of_followers"))

    if source == "gql":
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
        return root["users"], str(root.get("next_max_id") or ""), bool(root.get("should_limit_list_of_followers"))

    # web
    params = {"count": 12, "search_surface": "follow_list_page"}
    if max_id:
        params["max_id"] = max_id
    data = await ig_get_authenticated(_WEB_FOLLOWERS_URL.format(user_id=user_id, query=urlencode(params)))
    if data.get("error") or not isinstance(data.get("users"), list):
        logger.warning("followers web(%s): %s", user_id, data.get("error") or "no users in payload")
        return None
    return data["users"], str(data.get("next_max_id") or ""), bool(data.get("should_limit_list_of_followers"))


def _search_stack(resume: str = "") -> list[str]:
    """DFS stack of prefixes (popped from the end) in lexicographic preorder,
    optionally positioned so the sweep continues at ``resume``."""
    alpha = SEARCH_ALPHABET
    if not resume:
        return list(reversed(alpha))
    stack: list[str] = []
    for i, ch in enumerate(resume):
        base = resume[:i]
        later = [base + c for c in alpha[alpha.index(ch) + 1:]]
        stack.extend(reversed(later))
    stack.append(resume)
    return stack


class _Stop(Exception):
    """Internal: stop the whole walk (limit, cancellation, daily cap)."""


class _Walker:
    def __init__(self, user_id, limit, page_size, stop_event, follower_count, report):
        self.user_id = user_id
        self.limit = limit
        self.page_size = page_size
        self.stop_event = stop_event
        self.follower_count = follower_count
        self.report = report
        self.rank_token = ig_mobile.rank_token(get_session())
        self.seen: set[str] = set()
        self.requests = 0
        self.rested_at = 0

    @property
    def unique(self) -> int:
        return len(self.seen)

    def complete(self) -> bool:
        return bool(self.follower_count) and self.unique >= self.follower_count * _COMPLETE_RATIO

    async def page(self, source, max_id="", query=""):
        """Pacing + caps + one request. Raises _Stop when the walk must end."""
        if self.stop_event is not None and self.stop_event.is_set():
            self.report["stop"] = "cancelled"
            raise _Stop()
        daily_cap = Settings.IG_LIMIT_DAILY_FOLLOWERS
        if daily_cap and await db.get_daily_count(_FOLLOWERS_DAILY_MODE) >= daily_cap:
            logger.warning("iter_followers(%s): daily followers cap reached (%d)", self.user_id, daily_cap)
            self.report["stop"] = "daily_cap"
            raise _Stop()
        if self.requests:
            rest_every = Settings.IG_FOLLOWERS_REST_EVERY
            if rest_every and self.unique - self.rested_at >= rest_every:
                self.rested_at = self.unique
                logger.info("iter_followers(%s): resting %.0fs after %d followers",
                            self.user_id, Settings.IG_FOLLOWERS_REST_SECONDS, self.unique)
                await asyncio.sleep(Settings.IG_FOLLOWERS_REST_SECONDS)
            else:
                await asyncio.sleep(random.uniform(Settings.IG_FOLLOWERS_DELAY_MIN, Settings.IG_FOLLOWERS_DELAY_MAX))
            if self.stop_event is not None and self.stop_event.is_set():
                self.report["stop"] = "cancelled"
                raise _Stop()
        self.requests += 1
        result = await _fetch_page(source, self.user_id, self.page_size, max_id, self.rank_token, query)
        await db.increment_daily_count(_FOLLOWERS_DAILY_MODE)
        return result

    def fresh(self, users, cursor):
        """New followers from a page, each tagged with the resume cursor."""
        out = []
        for entry in users:
            follower = _normalize_follower(entry)
            key = follower["instagram_id"] or follower["username"]
            if not key or key in self.seen:
                continue
            self.seen.add(key)
            follower["_next_cursor"] = cursor
            out.append(follower)
        return out


async def iter_followers(
    user_id: str,
    *,
    amount: int = 0,
    page_size: int | None = None,
    stop_event: asyncio.Event | None = None,
    start_cursor: str = "",
    follower_count: int = 0,
    report: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Yield followers of ``user_id`` one by one, going past Instagram's ~50 cap.

    Args:
        user_id: numeric account id whose followers to fetch.
        amount: stop after this many followers (0 = all available, capped by
            IG_FOLLOWERS_MAX_PER_JOB).
        page_size: followers requested per page (Instagram may return fewer).
        stop_event: cooperative cancellation checked before every request.
        start_cursor: resume point saved by a previous run (``source:position``).
        follower_count: the account's real follower count (0 = unknown). With
            it the walk keeps trying sources until ~all are collected; without
            it, it only moves on when a source is visibly limited or failed.
        report: optional dict filled with what each source returned.

    Each yielded dict also carries ``_next_cursor`` so callers can persist it.

    Raises:
        IgAuthError: no/invalid session (or challenge required).
        FollowersError: every source failed before anything was collected.
    """
    page_size = page_size or Settings.IG_FOLLOWERS_PAGE_SIZE
    hard_cap = Settings.IG_FOLLOWERS_MAX_PER_JOB
    requested = amount if amount and amount > 0 else 0
    limit = min(requested or hard_cap, hard_cap)

    report = report if report is not None else {}
    report.setdefault("sources", [])
    report["follower_count"] = follower_count
    report["requested"] = requested
    report["job_cap"] = hard_cap
    report.setdefault("stop", "")

    walker = _Walker(user_id, limit, page_size, stop_event, follower_count, report)
    yielded = 0
    start_source, start_pos = decode_cursor(start_cursor)
    if start_source == "search" and not Settings.IG_FOLLOWERS_SEARCH_FALLBACK:
        start_source, start_pos = "v1", ""
    sources = [s for s in SOURCES[SOURCES.index(start_source):]
               if s != "search" or Settings.IG_FOLLOWERS_SEARCH_FALLBACK]

    try:
        for source in sources:
            entry = {"source": source, "requests": 0, "new": 0, "end": ""}
            report["sources"].append(entry)
            position = start_pos if source == start_source else ""
            before_requests = walker.requests
            run = _run_search(walker, entry, position) if source == "search" else _run_paged(
                walker, source, entry, position)
            try:
                async for follower in run:
                    entry["new"] += 1
                    yielded += 1
                    report["unique"] = walker.unique
                    yield follower
                    if yielded >= limit:
                        report["stop"] = "limit"
                        raise _Stop()
            finally:
                await run.aclose()
                entry["requests"] = walker.requests - before_requests
                report["unique"] = walker.unique

            if walker.complete():
                report["stop"] = "complete"
                return
            if not follower_count and entry["end"] == "exhausted":
                # Unknown size and the list ended normally: it's complete.
                report["stop"] = "complete"
                return
            logger.info(
                "iter_followers(%s): %s ended (%s) with %d unique of %s — trying next source",
                user_id, source, entry["end"], walker.unique, follower_count or "?",
            )
    except _Stop:
        return

    report["stop"] = report.get("stop") or "sources_exhausted"
    if not walker.unique and report["sources"] and all(e["end"] == "failed" for e in report["sources"]):
        raise FollowersError(
            f"followers fetch failed with every endpoint ({', '.join(e['source'] for e in report['sources'])})"
        )


async def _run_paged(walker: _Walker, source: str, entry: dict, max_id: str):
    """Walk one list endpoint until its cursor ends. Sets ``entry["end"]`` to
    exhausted | limited | failed | no_new."""
    empty_pages = 0
    while True:
        page = await walker.page(source, max_id)
        if page is None:
            entry["end"] = "failed"
            return
        users, next_max_id, limited = page
        new = walker.fresh(users, encode_cursor(source, next_max_id))
        for follower in new:
            yield follower
        if not next_max_id:
            capped = limited or (walker.follower_count and walker.unique < walker.follower_count * _COMPLETE_RATIO)
            entry["end"] = "limited" if capped else "exhausted"
            return
        empty_pages = 0 if new else empty_pages + 1
        if empty_pages >= _MAX_EMPTY_PAGES:
            entry["end"] = "no_new"
            return
        max_id = next_max_id


async def _run_search(walker: _Walker, entry: dict, resume: str):
    """Search inside the followers list by username prefixes (DFS). A prefix
    whose results look capped (≥ saturation and not paginated) is split into
    longer prefixes."""
    stack = _search_stack(resume)
    max_depth = max(1, Settings.IG_FOLLOWERS_SEARCH_MAX_DEPTH)
    saturation = max(1, Settings.IG_FOLLOWERS_SEARCH_SATURATION)
    # Instagram's per-search cap isn't documented (≈50 today, could change):
    # learn it from the largest unpaginated answer seen and treat a prefix as
    # capped when it returns ≥ 90% of that.
    cap_seen = 0
    failures = 0
    queries = 0
    while stack:
        prefix = stack.pop()
        cursor = encode_cursor("search", prefix)
        total = 0
        paginated = False
        max_id = ""
        for _ in range(_MAX_PAGES_PER_QUERY):
            page = await walker.page("search", max_id, prefix)
            if page is None:
                failures += 1
                if failures >= _MAX_SEARCH_FAILURES:
                    entry["end"] = "failed"
                    return
                break
            failures = 0
            users, next_max_id, _ = page
            total += len(users)
            for follower in walker.fresh(users, cursor):
                yield follower
            if not next_max_id:
                break
            paginated = True
            max_id = next_max_id
        queries += 1
        if not paginated:
            cap_seen = max(cap_seen, total)
        threshold = int(cap_seen * 0.9) if cap_seen >= 20 else saturation
        if total >= threshold and not paginated and len(prefix) < max_depth:
            stack.extend(reversed([prefix + c for c in SEARCH_ALPHABET]))
        if walker.follower_count and walker.unique >= walker.follower_count:
            break
    entry["queries"] = queries
    entry["end"] = "exhausted"


def describe_report(report: dict) -> str | None:
    """Human (Spanish) explanation when fewer followers than expected were
    collected; None when the result is complete or the job was cancelled."""
    stop = report.get("stop")
    if stop in ("complete", "cancelled") or not report.get("sources"):
        return None
    unique = report.get("unique", 0)
    total = report.get("follower_count") or 0
    requested = report.get("requested") or 0
    if stop == "limit":
        if requested and requested > report.get("job_cap", 0) and unique >= report.get("job_cap", 0):
            return (f"Se paró en el tope por búsqueda ({report['job_cap']} seguidores). Súbelo en "
                    "Configuración → «Máximo de seguidores por búsqueda» o relanza para continuar.")
        return None

    parts = []
    for e in report["sources"]:
        label = SOURCE_LABELS.get(e["source"], e["source"])
        end = {"failed": "error", "limited": "lista limitada", "no_new": "sin nuevos",
               "exhausted": "fin"}.get(e["end"], e["end"] or "—")
        extra = f", {e['queries']} búsquedas" if e.get("queries") else ""
        parts.append(f"{label}: +{e['new']} ({end}{extra})")
    of_total = f" de {total:,}".replace(",", ".") if total else ""
    head = f"Instagram solo devolvió {unique:,}{of_total} seguidores.".replace(",", ".")
    if stop == "daily_cap":
        head += (f" Se alcanzó el límite diario ({Settings.IG_LIMIT_DAILY_FOLLOWERS} peticiones); "
                 "relanza mañana y continuará donde se quedó.")
    elif not Settings.IG_FOLLOWERS_SEARCH_FALLBACK:
        head += " Activa «Buscar por letras si Instagram limita la lista» en Configuración para sacar más."
    return f"{head} Recorrido — " + " · ".join(parts)


async def scrape_followers(
    target_username: str,
    *,
    amount: int = 0,
    stop_event: asyncio.Event | None = None,
    reset_cursor: bool = False,
    report: dict | None = None,
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
    report = report if report is not None else {}
    session = get_session()
    if session is None or not session.authenticated:
        raise IgAuthError(
            "Followers mode requires an authenticated Instagram session. "
            "Set IG_SESSIONID (or IG_SESSION_FILE)."
        )

    info = await resolve_user(target_username)
    if not info:
        raise FollowersError(f"Could not resolve @{target_username} (private, non-existent, or blocked).")
    user_id = info["id"]
    if info["is_private"]:
        logger.warning("scrape_followers: @%s is private — only works if this account follows it", target_username)

    if reset_cursor:
        await db.reset_followers_cursor(target_username)
        start_cursor = ""
    else:
        start_cursor = await db.get_followers_cursor(target_username) or ""
        if start_cursor:
            logger.info("scrape_followers: @%s resuming from saved cursor %s", target_username,
                        start_cursor.split(":", 1)[0])

    logger.info(
        "scrape_followers: @%s → user_id=%s, %s followers (amount=%s)",
        target_username, user_id, info["follower_count"] or "?", amount or "all",
    )

    last_saved_cursor = start_cursor
    new_in_page = 0
    async for follower in iter_followers(
        user_id, amount=amount, stop_event=stop_event, start_cursor=start_cursor,
        follower_count=info["follower_count"], report=report,
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
    if report.get("stop") in ("complete", "sources_exhausted"):
        # Walked everything Instagram would give: next job starts from the top
        # again (to pick up new followers); the deduplicator skips known ones.
        await db.reset_followers_cursor(target_username)
