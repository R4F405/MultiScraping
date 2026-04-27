import pytest
from unittest.mock import AsyncMock, patch

from backend.scraper.ig_deduplicator import Deduplicator


@pytest.mark.asyncio
async def test_load_from_db_populates_seen():
    dedup = Deduplicator()
    with (
        patch("backend.scraper.ig_deduplicator.db.get_leads_usernames", new=AsyncMock(return_value={"alice", "bob"})),
        patch("backend.scraper.ig_deduplicator.db.get_recent_skipped_usernames", new=AsyncMock(return_value=set())),
    ):
        await dedup.load_from_db()
    assert dedup.should_skip("alice")
    assert dedup.should_skip("bob")
    assert not dedup.should_skip("charlie")


@pytest.mark.asyncio
async def test_should_skip_returns_false_for_new_username():
    dedup = Deduplicator()
    with (
        patch("backend.scraper.ig_deduplicator.db.get_leads_usernames", new=AsyncMock(return_value=set())),
        patch("backend.scraper.ig_deduplicator.db.get_recent_skipped_usernames", new=AsyncMock(return_value=set())),
    ):
        await dedup.load_from_db()
    assert not dedup.should_skip("newuser")


def test_mark_seen_prevents_reprocess():
    dedup = Deduplicator()
    assert not dedup.should_skip("user1")
    dedup.mark_seen("user1")
    assert dedup.should_skip("user1")


def test_skipped_count():
    dedup = Deduplicator()
    dedup.mark_seen("a")
    dedup.mark_seen("b")
    assert dedup.skipped_count == 2


@pytest.mark.asyncio
async def test_load_from_db_merges_leads_and_skipped():
    dedup = Deduplicator()
    with (
        patch("backend.scraper.ig_deduplicator.db.get_leads_usernames", new=AsyncMock(return_value={"from_leads"})),
        patch("backend.scraper.ig_deduplicator.db.get_recent_skipped_usernames", new=AsyncMock(return_value={"from_skipped"})),
    ):
        await dedup.load_from_db()
    assert dedup.should_skip("from_leads")
    assert dedup.should_skip("from_skipped")
