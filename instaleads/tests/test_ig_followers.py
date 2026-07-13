"""
Tests for the followers scraper — the core requirement is proving that
pagination goes *beyond* the ~50 followers the desktop web shows, by walking
the GraphQL end_cursor across pages.
"""
import asyncio

import pytest

from backend.scraper import ig_followers
from backend.scraper.ig_client import IgAuthError
from backend.scraper.ig_followers import FollowersError, iter_followers, scrape_followers


@pytest.fixture(autouse=True)
def _stub_daily_counter(monkeypatch):
    """iter_followers now consults the per-day followers counter; stub it to 0
    (unlimited) and no-op the increment so unit tests stay DB-free."""
    async def _zero(mode):
        return 0

    async def _noop(mode):
        return None

    monkeypatch.setattr(ig_followers.db, "get_daily_count", _zero)
    monkeypatch.setattr(ig_followers.db, "increment_daily_count", _noop)


def _make_page(start: int, count: int, end_cursor: str | None):
    """Build a fake GraphQL followers response with `count` users.

    ``end_cursor`` present ⇒ has_next_page True (more pages available);
    absent ⇒ last page.
    """
    edges = [
        {
            "node": {
                "id": str(1000 + i),
                "username": f"user{i}",
                "full_name": f"User {i}",
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


@pytest.mark.asyncio
async def test_iter_followers_paginates_beyond_50(monkeypatch):
    """5 pages × 50 = 250 followers must all be collected (bypasses the ~50 web cap)."""
    pages = [
        _make_page(0, 50, "cursor1"),
        _make_page(50, 50, "cursor2"),
        _make_page(100, 50, "cursor3"),
        _make_page(150, 50, "cursor4"),
        _make_page(200, 50, None),  # last page, no cursor
    ]
    calls = []

    async def fake_get(url):
        calls.append(url)
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)

    collected = [f async for f in iter_followers("999", amount=0)]

    assert len(collected) == 250
    assert len({f["username"] for f in collected}) == 250
    # First request has no cursor; subsequent requests carry the end_cursor
    # inside the URL-encoded GraphQL variables ("after":"cursorN").
    assert "after" not in calls[0]
    assert "cursor1" in calls[1]
    assert "cursor4" in calls[4]


@pytest.mark.asyncio
async def test_iter_followers_respects_amount_cap(monkeypatch):
    pages = [_make_page(0, 50, "c1"), _make_page(50, 50, "c2"), _make_page(100, 50, None)]
    calls = []

    async def fake_get(url):
        calls.append(url)
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)

    collected = [f async for f in iter_followers("999", amount=70)]

    assert len(collected) == 70
    # Should stop after the 2nd page (only 2 requests needed for 70).
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_iter_followers_stops_at_daily_cap(monkeypatch):
    """When the per-day followers cap is already reached, no page is fetched."""
    calls = []

    async def fake_get(url):
        calls.append(url)
        return _make_page(0, 50, "c1")

    async def at_cap(mode):
        return 1500

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    monkeypatch.setattr(ig_followers.db, "get_daily_count", at_cap)
    monkeypatch.setattr(ig_followers.Settings, "IG_LIMIT_DAILY_FOLLOWERS", 1500)

    collected = [f async for f in iter_followers("999", amount=100)]
    assert collected == []
    assert calls == []  # capped before any request


@pytest.mark.asyncio
async def test_iter_followers_stops_on_exhausted_cursor(monkeypatch):
    async def fake_get(url):
        return _make_page(0, 30, None)  # single short page, no cursor

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)

    collected = [f async for f in iter_followers("999", amount=0)]
    assert len(collected) == 30


@pytest.mark.asyncio
async def test_iter_followers_cancellation(monkeypatch):
    pages = [_make_page(0, 50, "c1"), _make_page(50, 50, "c2"), _make_page(100, 50, "c3")]
    calls = []
    stop = asyncio.Event()

    async def fake_get(url):
        calls.append(url)
        if len(calls) == 1:
            stop.set()  # cancel after first page
        return pages[len(calls) - 1]

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MIN", 0.0)
    monkeypatch.setattr(ig_followers.Settings, "IG_FOLLOWERS_DELAY_MAX", 0.0)

    collected = [f async for f in iter_followers("999", amount=0, stop_event=stop)]
    # First page yielded (50), then cancellation stops before the 2nd fetch.
    assert len(collected) == 50
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_iter_followers_raises_on_error(monkeypatch):
    async def fake_get(url):
        return {"error": "max_retries_exceeded", "status_code": 429}

    monkeypatch.setattr(ig_followers, "ig_get_authenticated", fake_get)
    with pytest.raises(FollowersError):
        _ = [f async for f in iter_followers("999", amount=10)]


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
