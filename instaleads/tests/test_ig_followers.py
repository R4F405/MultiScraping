"""
Tests for the followers scraper.

Core requirements:
* pagination goes *beyond* the ~50 followers the desktop web shows, by
  walking the mobile API ``next_max_id`` cursor across pages;
* when the v1 endpoint limits the list (``should_limit_list_of_followers``)
  or fails, the private GraphQL ``FollowersList`` takes over, then the web v1
  endpoint — the same fallback chain instagrapi 3.0.14 uses (Sept 2026);
* the dead legacy ``query_hash`` GraphQL query is no longer used.
"""
import asyncio
from urllib.parse import unquote

import pytest

from backend.scraper import ig_followers
from backend.scraper.ig_client import IgAuthError
from backend.scraper.ig_followers import (
    FollowersError,
    decode_cursor,
    encode_cursor,
    iter_followers,
    resolve_user_id,
    scrape_followers,
)


class _Session:
    authenticated = True
    ds_user_id = "111"
    sessionid = "111%3Atoken%3A1"


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch):
    """iter_followers consults the per-day followers counter and the session;
    stub both so unit tests stay DB-free and never touch the network."""
    async def _zero(mode):
        return 0

    async def _noop(mode):
        return None

    async def _unexpected(*args, **kwargs):
        raise AssertionError("unexpected network call")

    monkeypatch.setattr(ig_followers.db, "get_daily_count", _zero)
    monkeypatch.setattr(ig_followers.db, "increment_daily_count", _noop)
    monkeypatch.setattr(ig_followers, "get_session", lambda: _Session())
    monkeypatch.setattr(ig_followers, "ig_mobile_get", _unexpected)
    monkeypatch.setattr(ig_followers, "ig_mobile_graphql", _unexpected)
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", _unexpected)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)


def _users(start: int, count: int) -> list[dict]:
    return [
        {
            "pk": str(1000 + i),
            "username": f"user{i}",
            "full_name": f"User {i}",
            "is_private": i % 7 == 0,
            "is_verified": False,
        }
        for i in range(start, start + count)
    ]


def _v1_page(start: int, count: int, next_max_id: str | None, limited: bool = False) -> dict:
    page = {"users": _users(start, count), "status": "ok"}
    if next_max_id:
        page["next_max_id"] = next_max_id
    if limited:
        page["should_limit_list_of_followers"] = True
    return page


def _gql_page(start: int, count: int, next_max_id: str | None) -> dict:
    root = {"users": _users(start, count)}
    if next_max_id:
        root["next_max_id"] = next_max_id
    return {"data": {"xdt_api__v1__friendships__followers": root}, "status": "ok"}


# ── cursor format ────────────────────────────────────────────────────────────

def test_cursor_roundtrip():
    assert decode_cursor(encode_cursor("gql", "QVFBxyz")) == ("gql", "QVFBxyz")
    assert encode_cursor("v1", "") == ""


def test_legacy_graphql_cursor_is_discarded():
    # Cursors saved by the old query_hash implementation have no prefix.
    assert decode_cursor("QVFDabc123==") == ("v1", "")
    assert decode_cursor(None) == ("v1", "")


# ── v1 (mobile) pagination ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_iter_followers_paginates_beyond_50(monkeypatch):
    """5 pages × 50 = 250 followers must all be collected (bypasses the ~50 web cap)."""
    pages = [
        _v1_page(0, 50, "c1"),
        _v1_page(50, 50, "c2"),
        _v1_page(100, 50, "c3"),
        _v1_page(150, 50, "c4"),
        _v1_page(200, 50, None),  # last page, no cursor
    ]
    calls = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        calls.append((endpoint, dict(params or {})))
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)

    collected = [f async for f in iter_followers("999", amount=0)]

    assert len(collected) == 250
    assert len({f["username"] for f in collected}) == 250
    assert all(endpoint == "friendships/999/followers/" for endpoint, _ in calls)
    # First request has no cursor; subsequent requests carry next_max_id.
    assert "max_id" not in calls[0][1]
    assert calls[1][1]["max_id"] == "c1"
    assert calls[4][1]["max_id"] == "c4"
    assert calls[0][1]["search_surface"] == "follow_list_page"
    assert calls[0][1]["rank_token"].startswith("111_")
    # Cursor exposed for checkpointing carries the strategy prefix.
    assert collected[0]["_next_cursor"] == "v1:c1"
    assert collected[0]["instagram_id"] == "1000"
    assert collected[0]["is_private"] is True


@pytest.mark.asyncio
async def test_iter_followers_respects_amount_cap(monkeypatch):
    pages = [_v1_page(0, 50, "c1"), _v1_page(50, 50, "c2"), _v1_page(100, 50, None)]
    calls = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        calls.append(params)
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)

    collected = [f async for f in iter_followers("999", amount=70)]

    assert len(collected) == 70
    # Should stop after the 2nd page (only 2 requests needed for 70).
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_iter_followers_stops_at_daily_cap(monkeypatch):
    """When the per-day followers cap is already reached, no page is fetched."""
    calls = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        calls.append(params)
        return _v1_page(0, 50, "c1")

    async def at_cap(mode):
        return 1500

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    monkeypatch.setattr(ig_followers.db, "get_daily_count", at_cap)
    monkeypatch.setattr(ig_followers.Settings, "IG_LIMIT_DAILY_FOLLOWERS", 1500)

    collected = [f async for f in iter_followers("999", amount=100)]
    assert collected == []
    assert calls == []  # capped before any request


@pytest.mark.asyncio
async def test_iter_followers_stops_on_exhausted_cursor(monkeypatch):
    async def fake_mobile_get(endpoint, params=None, **kwargs):
        return _v1_page(0, 30, None)  # single short page, no cursor

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)

    collected = [f async for f in iter_followers("999", amount=0)]
    assert len(collected) == 30


@pytest.mark.asyncio
async def test_iter_followers_cancellation(monkeypatch):
    pages = [_v1_page(0, 50, "c1"), _v1_page(50, 50, "c2"), _v1_page(100, 50, "c3")]
    calls = []
    stop = asyncio.Event()

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        calls.append(params)
        if len(calls) == 1:
            stop.set()  # cancel after first page
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)

    collected = [f async for f in iter_followers("999", amount=0, stop_event=stop)]
    # First page yielded (50), then cancellation stops before the 2nd fetch.
    assert len(collected) == 50
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_iter_followers_resumes_from_saved_cursor(monkeypatch):
    calls = []

    async def fake_graphql(friendly_name, root_field, variables, doc_id, **kwargs):
        calls.append(variables)
        return _gql_page(100, 20, None)

    monkeypatch.setattr(ig_followers, "ig_mobile_graphql", fake_graphql)

    collected = [f async for f in iter_followers("999", start_cursor="gql:resume123")]
    assert len(collected) == 20
    assert calls[0]["max_id"] == "resume123"


# ── fallback chain ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_limited_v1_switches_to_private_graphql(monkeypatch):
    """v1 answers with should_limit_list_of_followers and no cursor after the
    first ~50 → FollowersList GraphQL takes over and keeps paginating; the
    overlapping page is de-duplicated."""
    graphql_calls = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        return _v1_page(0, 50, None, limited=True)

    async def fake_graphql(friendly_name, root_field, variables, doc_id, **kwargs):
        graphql_calls.append((friendly_name, root_field, dict(variables), doc_id))
        if len(graphql_calls) == 1:
            return _gql_page(0, 50, "g1")  # same first page again
        if len(graphql_calls) == 2:
            return _gql_page(50, 50, "g2")
        return _gql_page(100, 10, None)

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    monkeypatch.setattr(ig_followers, "ig_mobile_graphql", fake_graphql)

    collected = [f async for f in iter_followers("999", amount=0)]

    assert len(collected) == 110
    assert len({f["instagram_id"] for f in collected}) == 110
    name, root, variables, doc_id = graphql_calls[0]
    assert name == "FollowersList"
    assert root == "xdt_api__v1__friendships__followers"
    assert doc_id == ig_followers.Settings.IG_FOLLOWERS_DOC_ID
    assert variables["user_id"] == "999"
    assert "max_id" not in variables
    assert graphql_calls[1][2]["max_id"] == "g1"
    assert collected[-1]["_next_cursor"] == ""
    assert collected[60]["_next_cursor"] == "gql:g2"


@pytest.mark.asyncio
async def test_v1_error_falls_back_to_graphql_then_web(monkeypatch):
    web_urls = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        return {"error": "max_retries_exceeded", "status_code": 429}

    async def fake_graphql(*args, **kwargs):
        return {"errors": [{"message": "execution error", "severity": "CRITICAL"}]}

    async def fake_web(url):
        web_urls.append(unquote(url))
        return {"users": _users(0, 12), "status": "ok"}

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    monkeypatch.setattr(ig_followers, "ig_mobile_graphql", fake_graphql)
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_web)

    collected = [f async for f in iter_followers("999", amount=0)]
    assert len(collected) == 12
    assert web_urls[0].startswith("https://www.instagram.com/api/v1/friendships/999/followers/?")


@pytest.mark.asyncio
async def test_iter_followers_raises_when_every_endpoint_fails(monkeypatch):
    async def fail(*args, **kwargs):
        return {"error": "max_retries_exceeded", "status_code": 429}

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fail)
    monkeypatch.setattr(ig_followers, "ig_mobile_graphql", fail)
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fail)
    with pytest.raises(FollowersError):
        _ = [f async for f in iter_followers("999", amount=10)]


@pytest.mark.asyncio
async def test_auth_error_is_not_masked_by_fallbacks(monkeypatch):
    async def dead_session(*args, **kwargs):
        raise IgAuthError("login_required")

    monkeypatch.setattr(ig_followers, "ig_mobile_get", dead_session)
    with pytest.raises(IgAuthError):
        _ = [f async for f in iter_followers("999", amount=10)]


# ── resolve / high-level ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_user_id_via_mobile_usernameinfo(monkeypatch):
    seen = []

    async def fake_mobile_get(endpoint, params=None, **kwargs):
        seen.append(endpoint)
        return {"user": {"pk": 555, "username": "targetacc"}, "status": "ok"}

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    assert await resolve_user_id("@targetacc") == "555"
    assert seen == ["users/targetacc/usernameinfo/"]


@pytest.mark.asyncio
async def test_resolve_user_id_falls_back_to_web_profile_info(monkeypatch):
    async def fake_mobile_get(endpoint, params=None, **kwargs):
        return {"error": "max_retries_exceeded", "status_code": 400}

    async def fake_web(url):
        return {"data": {"user": {"id": "777"}}}

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_web)
    assert await resolve_user_id("someone") == "777"


@pytest.mark.asyncio
async def test_resolve_user_id_not_found(monkeypatch):
    async def fake_mobile_get(endpoint, params=None, **kwargs):
        return {"error": "not_found", "status_code": 404}

    monkeypatch.setattr(ig_followers, "ig_mobile_get", fake_mobile_get)
    assert await resolve_user_id("ghost") is None


@pytest.mark.asyncio
async def test_scrape_followers_requires_session(monkeypatch):
    monkeypatch.setattr(ig_followers, "get_session", lambda: None)
    with pytest.raises(IgAuthError):
        _ = [f async for f in scrape_followers("someaccount", amount=10)]


@pytest.mark.asyncio
async def test_scrape_followers_resolves_iterates_and_saves_cursor(monkeypatch):
    saved = []

    async def fake_resolve(username):
        assert username.lstrip("@") == "targetacc"
        return "555"

    async def fake_iter(user_id, **kwargs):
        assert user_id == "555"
        assert kwargs["start_cursor"] == "v1:old"
        for i in range(3):
            yield {"username": f"f{i}", "instagram_id": str(i), "_next_cursor": "v1:next"}

    async def fake_get_cursor(username):
        return "v1:old"

    async def fake_save_cursor(username, cursor, collected_delta=0):
        saved.append((username, cursor, collected_delta))

    monkeypatch.setattr(ig_followers, "resolve_user_id", fake_resolve)
    monkeypatch.setattr(ig_followers, "iter_followers", fake_iter)
    monkeypatch.setattr(ig_followers.db, "get_followers_cursor", fake_get_cursor)
    monkeypatch.setattr(ig_followers.db, "save_followers_cursor", fake_save_cursor)

    collected = [f async for f in scrape_followers("@targetacc", amount=3)]
    assert [f["username"] for f in collected] == ["f0", "f1", "f2"]
    assert saved[0][1] == "v1:next"


@pytest.mark.asyncio
async def test_scrape_followers_unresolvable_raises(monkeypatch):
    async def fake_resolve(username):
        return None

    monkeypatch.setattr(ig_followers, "resolve_user_id", fake_resolve)
    with pytest.raises(FollowersError):
        _ = [f async for f in scrape_followers("ghost", amount=3)]
