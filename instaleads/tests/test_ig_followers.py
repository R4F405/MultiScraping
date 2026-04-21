import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_followers_skips_already_seen_username():
    from backend.scraper import ig_followers

    with (
        patch("backend.scraper.ig_followers.get_authenticated_client") as mock_cl_fn,
        patch("backend.scraper.ig_followers.get_profile", new=AsyncMock(return_value=None)),
        patch("backend.scraper.ig_followers.db.insert_ig_skipped", new=AsyncMock()),
        patch("backend.scraper.ig_followers.db.upsert_ig_lead", new=AsyncMock()),
        patch("backend.scraper.ig_followers.db.update_job_progress", new=AsyncMock()),
        patch("backend.scraper.ig_followers.RateLimiter") as MockRL,
        patch("backend.scraper.ig_followers.Deduplicator") as MockDedup,
    ):
        mock_cl = MagicMock()
        mock_cl.user_id_from_username.return_value = "123"
        mock_cl.user_followers.return_value = {"existinguser": MagicMock()}
        mock_cl_fn.return_value = mock_cl

        rl = MagicMock()
        rl.check_and_wait = AsyncMock()
        MockRL.return_value = rl

        dedup = MagicMock()
        dedup.load_from_db = AsyncMock()
        dedup.should_skip = MagicMock(return_value=True)  # already seen
        dedup.mark_seen = MagicMock()
        MockDedup.return_value = dedup

        collected = []
        async for p in ig_followers.get_followers_emails("target", 50, "job-x"):
            collected.append(p)

    assert collected == []
    # rate limiter should NOT have been called for already-seen users
    rl.check_and_wait.assert_not_called()


@pytest.mark.asyncio
async def test_followers_yields_only_profiles_with_email():
    from backend.scraper import ig_followers

    fake_profile = {
        "email": "found@example.com",
        "username": "gooduser",
        "instagram_id": "999",
        "private": False,
    }

    with (
        patch("backend.scraper.ig_followers.get_authenticated_client") as mock_cl_fn,
        patch("backend.scraper.ig_followers.get_profile", new=AsyncMock(return_value=fake_profile)),
        patch("backend.scraper.ig_followers.db.insert_ig_skipped", new=AsyncMock()),
        patch("backend.scraper.ig_followers.db.upsert_ig_lead", new=AsyncMock()),
        patch("backend.scraper.ig_followers.db.update_job_progress", new=AsyncMock()),
        patch("backend.scraper.ig_followers.RateLimiter") as MockRL,
        patch("backend.scraper.ig_followers.Deduplicator") as MockDedup,
    ):
        mock_cl = MagicMock()
        mock_cl.user_id_from_username.return_value = "123"
        mock_cl.user_followers.return_value = {"gooduser": MagicMock()}
        mock_cl_fn.return_value = mock_cl

        rl = MagicMock()
        rl.check_and_wait = AsyncMock()
        MockRL.return_value = rl

        dedup = MagicMock()
        dedup.load_from_db = AsyncMock()
        dedup.should_skip = MagicMock(return_value=False)
        dedup.mark_seen = MagicMock()
        MockDedup.return_value = dedup

        collected = []
        async for p in ig_followers.get_followers_emails("target", 50, "job-y"):
            collected.append(p)

    assert len(collected) == 1
    assert collected[0]["email"] == "found@example.com"
