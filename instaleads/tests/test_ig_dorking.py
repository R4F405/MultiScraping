import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_dorking_skips_username_already_in_db():
    from backend.scraper import ig_dorking

    collected = []

    with (
        patch("backend.scraper.ig_dorking._scrape_google_serp", new=AsyncMock(return_value=["existinguser"])),
        patch("backend.scraper.ig_dorking.get_profile", new=AsyncMock(return_value={"email": "x@x.com", "username": "existinguser"})),
        patch("backend.scraper.ig_dorking.db.insert_ig_skipped", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.db.upsert_ig_lead", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.db.update_job_progress", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.Deduplicator") as MockDedup,
    ):
        mock_dedup = MagicMock()
        mock_dedup.load_from_db = AsyncMock()
        mock_dedup.should_skip = MagicMock(return_value=True)  # already in DB
        mock_dedup.mark_seen = MagicMock()
        MockDedup.return_value = mock_dedup

        async for profile in ig_dorking.search_and_extract("foto", "Valencia", 10, "job-1"):
            collected.append(profile)

    assert collected == []  # nothing yielded — username was skipped


@pytest.mark.asyncio
async def test_dorking_yields_only_profiles_with_email():
    from backend.scraper import ig_dorking

    profiles = [
        {"email": "yes@x.com", "username": "hasmail", "instagram_id": "1", "private": False},
        {"email": None, "username": "nomail", "instagram_id": "2", "private": False},
    ]
    call_count = 0

    async def fake_get_profile(username):
        nonlocal call_count
        p = profiles[call_count % len(profiles)]
        call_count += 1
        return p

    with (
        patch("backend.scraper.ig_dorking._scrape_google_serp", new=AsyncMock(return_value=["hasmail", "nomail"])),
        patch("backend.scraper.ig_dorking.get_profile", side_effect=fake_get_profile),
        patch("backend.scraper.ig_dorking.db.insert_ig_skipped", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.db.upsert_ig_lead", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.db.update_job_progress", new=AsyncMock()),
        patch("backend.scraper.ig_dorking.Deduplicator") as MockDedup,
    ):
        mock_dedup = MagicMock()
        mock_dedup.load_from_db = AsyncMock()
        mock_dedup.should_skip = MagicMock(return_value=False)
        mock_dedup.mark_seen = MagicMock()
        MockDedup.return_value = mock_dedup

        collected = []
        async for profile in ig_dorking.search_and_extract("foto", "Valencia", 10, "job-2"):
            collected.append(profile)

    assert len(collected) == 1
    assert collected[0]["email"] == "yes@x.com"
