"""
Tests for the followers scraper.

The core requirement is proving that pagination goes *beyond* the ~50 followers
the desktop web shows. The primary path is the private web API
(``friendships/{id}/followers/`` walked via ``next_max_id``); when that endpoint
is capped or unavailable the scraper falls back to the GraphQL query
(``edge_followed_by.page_info.end_cursor``). Both are exercised here.
"""
import asyncio

import pytest

from backend.scraper import ig_followers
from backend.scraper.ig_client import IgAuthError
from backend.scraper.ig_followers import FollowersError, iter_followers, scrape_followers


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    """Keep unit tests DB-free: iter_followers consults the per-day followers
    counter, and scrape_followers reads/writes the resume cursor. Stub them all
    so no real SQLite connection is needed."""
    async def _zero(mode):
        return 0

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ig_followers.db, "get_daily_count", _zero)
    monkeypatch.setattr(ig_followers.db, "increment_daily_count", _noop)
    monkeypatch.setattr(ig_followers.db, "get_followers_cursor", _noop)
    monkeypatch.setattr(ig_followers.db, "save_followers_cursor", _noop)
    monkeypatch.setattr(ig_followers.db, "reset_followers_cursor", _noop)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)


# ── Fake response builders ────────────────────────────────────────────────────

def _make_v1_page(start: int, count: int, next_max_id: str | None):
    """Build a fake private-web-API followers response with `count` users.

    ``next_max_id`` present ⇒ more pages available; absent ⇒ last page.
    """
    users = [
        {
            "pk": 1000 + i,
            "username": f"user{i}",
            "full_name": f"User {i}",
            "is_private": False,
            "is_verified": False,
            "profile_pic_url": f"https://pic/{i}.jpg",
        }
        for i in range(start, start + count)
    ]
    payload = {"users": users, "status": "ok"}
    if next_max_id:
        payload["next_max_id"] = next_max_id
    return payload


def _make_v1_capped_page(start: int, count: int):
    """Private-web-API response that hit the ~50 cap: no cursor + limit flag."""
    payload = _make_v1_page(start, count, None)
    payload["should_limit_list_of_followers"] = True
    payload["has_more"] = False
    return payload


def _make_gql_page(start: int, count: int, end_cursor: str | None):
    """Build a fake GraphQL followers response with `count` users."""
    edges = [
        {
            "node": {
                "id": str(2000 + i),
                "username": f"guser{i}",
                "full_name": f"GUser {i}",
                "is_private": False,
                "is_verified": False,
            }
        }
        for i in range(start, start + count)
    ]
    return {
        "data": {
            "user": {
                "edge_followed_by": {
                    "edges": edges,
                    "page_info": {
                        "has_next_page": bool(end_cursor),
                        "end_cursor": end_cursor,
                    },
                }
            }
        },
        "status": "ok",
    }


def _sequenced_get(responses, calls):
    """Return an async ig_get stub that yields `responses` in order, recording URLs."""
    async def fake_get(url):
        calls.append(url)
        return responses[len(calls) - 1]

    return fake_get


# ── Primary path: private web API (v1) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_iter_followers_paginates_beyond_50(monkeypatch):
    """5 pages × 50 = 250 followers must all be collected (bypasses the ~50 web cap)."""
    pages = [
        _make_v1_page(0, 50, "100"),
        _make_v1_page(50, 50, "150"),
        _make_v1_page(100, 50, "200"),
        _make_v1_page(150, 50, "250"),
        _make_v1_page(200, 50, None),  # last page, no next_max_id
    ]
    calls = []
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _sequenced_get(pages, calls))

    collected = [f async for f in iter_followers("999", amount=0)]

    assert len(collected) == 250
    assert len({f["username"] for f in collected}) == 250
    # Every request hits the private followers endpoint with the surface param.
    assert "friendships/999/followers/" in calls[0]
    assert "search_surface=follow_list_page" in calls[0]
    # First request has no cursor; subsequent requests carry the max_id cursor.
    assert "max_id" not in calls[0]
    assert "max_id=100" in calls[1]
    assert "max_id=250" in calls[4]


@pytest.mark.asyncio
async def test_iter_followers_respects_amount_cap(monkeypatch):
    pages = [_make_v1_page(0, 50, "c1"), _make_v1_page(50, 50, "c2"), _make_v1_page(100, 50, None)]
    calls = []
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _sequenced_get(pages, calls))

    collected = [f async for f in iter_followers("999", amount=70)]

    assert len(collected) == 70
    # Should stop after the 2nd page (only 2 requests needed for 70).
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_iter_followers_stops_at_daily_cap(monkeypatch):
    """When the per-day followers cap is already reached, no page is fetched."""
    calls = []
    monkeypatch.setattr(
        ig_followers, "ig_get_authenticated", _sequenced_get([_make_v1_page(0, 50, "c1")], calls)
    )

    async def at_cap(mode):
        return 1500

    monkeypatch.setattr(ig_followers.db, "get_daily_count", at_cap)
    monkeypatch.setattr(ig_followers.Settings, "IG_LIMIT_DAILY_FOLLOWERS", 1500)

    collected = [f async for f in iter_followers("999", amount=100)]
    assert collected == []
    assert calls == []  # capped before any request


@pytest.mark.asyncio
async def test_iter_followers_stops_on_exhausted_cursor(monkeypatch):
    async def fake_get(url):
        return _make_v1_page(0, 30, None)  # single short page, no cursor

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)

    collected = [f async for f in iter_followers("999", amount=0)]
    assert len(collected) == 30


@pytest.mark.asyncio
async def test_iter_followers_cancellation(monkeypatch):
    pages = [_make_v1_page(0, 50, "c1"), _make_v1_page(50, 50, "c2"), _make_v1_page(100, 50, "c3")]
    calls = []
    stop = asyncio.Event()

    async def fake_get(url):
        calls.append(url)
        if len(calls) == 1:
            stop.set()  # cancel after first page
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)

    collected = [f async for f in iter_followers("999", amount=0, stop_event=stop)]
    # First page yielded (50), then cancellation stops before the 2nd fetch.
    assert len(collected) == 50
    assert len(calls) == 1


# ── Fallback path: GraphQL ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_iter_followers_falls_back_to_gql_when_v1_capped(monkeypatch):
    """A capped v1 first page (no cursor) transparently switches to GraphQL."""
    responses = [
        _make_v1_capped_page(0, 30),      # v1 first page: capped, discarded
        _make_gql_page(0, 50, "gc1"),     # gql page 1
        _make_gql_page(50, 40, None),     # gql page 2 (last)
    ]
    calls = []
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _sequenced_get(responses, calls))

    collected = [f async for f in iter_followers("999", amount=0)]

    # The capped v1 slice is discarded; only the 90 GraphQL followers remain.
    assert len(collected) == 90
    assert all(f["username"].startswith("guser") for f in collected)
    assert "friendships/999/followers/" in calls[0]  # tried v1 first
    assert "graphql" in calls[1]                      # switched to GraphQL
    assert "graphql" in calls[2]


@pytest.mark.asyncio
async def test_iter_followers_falls_back_to_gql_on_v1_error(monkeypatch):
    """A v1 error on the first page falls back to GraphQL instead of failing."""
    responses = [
        {"error": "max_retries_exceeded", "status_code": 429},  # v1 errors
        _make_gql_page(0, 20, None),                            # gql succeeds
    ]
    calls = []
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _sequenced_get(responses, calls))

    collected = [f async for f in iter_followers("999", amount=0)]

    assert len(collected) == 20
    assert "friendships/999/followers/" in calls[0]
    assert "graphql" in calls[1]


@pytest.mark.asyncio
async def test_iter_followers_raises_when_both_endpoints_fail(monkeypatch):
    """If both the v1 endpoint and the GraphQL fallback error, raise."""
    async def fake_get(url):
        return {"error": "max_retries_exceeded", "status_code": 429}

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    with pytest.raises(FollowersError):
        _ = [f async for f in iter_followers("999", amount=10)]


@pytest.mark.asyncio
async def test_iter_followers_resumes_gql_cursor(monkeypatch):
    """A saved gql-tagged cursor resumes the GraphQL walk (no v1 attempt)."""
    responses = [_make_gql_page(0, 25, None)]
    calls = []
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _sequenced_get(responses, calls))

    collected = [f async for f in iter_followers("999", amount=0, start_cursor="gql:SAVED")]

    assert len(collected) == 25
    assert "graphql" in calls[0]      # went straight to GraphQL
    assert "SAVED" in calls[0]        # resumed from the saved cursor


# ── scrape_followers (high level) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scrape_followers_requires_session(monkeypatch):
    monkeypatch.setattr(ig_followers, "get_session", lambda: None)
    with pytest.raises(IgAuthError):
        _ = [f async for f in scrape_followers("someaccount", amount=10)]


@pytest.mark.asyncio
async def test_scrape_followers_resolves_and_iterates(monkeypatch):
    class _S:
        authenticated = True

    monkeypatch.setattr(ig_followers, "get_session", lambda: _S())

    async def fake_resolve(username):
        # resolve_user_id itself strips the leading '@'; here it's mocked,
        # so it receives the raw target passed by scrape_followers.
        assert username.lstrip("@") == "targetacc"
        return "555"

    async def fake_iter(user_id, **kwargs):
        assert user_id == "555"
        for i in range(3):
            yield {"username": f"f{i}", "instagram_id": str(i)}

    monkeypatch.setattr(ig_followers, "resolve_user_id", fake_resolve)
    monkeypatch.setattr(ig_followers, "iter_followers", fake_iter)

    collected = [f async for f in scrape_followers("@targetacc", amount=3)]
    assert [f["username"] for f in collected] == ["f0", "f1", "f2"]


@pytest.mark.asyncio
async def test_scrape_followers_unresolvable_raises(monkeypatch):
    class _S:
        authenticated = True

    monkeypatch.setattr(ig_followers, "get_session", lambda: _S())

    async def fake_resolve(username):
        return None

    monkeypatch.setattr(ig_followers, "resolve_user_id", fake_resolve)
    with pytest.raises(FollowersError):
        _ = [f async for f in scrape_followers("ghost", amount=3)]
