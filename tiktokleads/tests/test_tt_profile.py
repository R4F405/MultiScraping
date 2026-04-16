"""
Unit tests for the tt_profile extraction pipeline.
Mocks fetch_profile_rehydration and find_email_in_website to avoid browser.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.tiktok.tt_profile import _extract_email_from_text, extract_and_save


# ── _extract_email_from_text ──────────────────────────────────────────────────

def test_extract_email_from_bio_text():
    bio = "Fotógrafo freelance 📷 Contacto: foto@gmail.com | Instagram DMs"
    assert _extract_email_from_text(bio) == "foto@gmail.com"


def test_extract_email_none_when_empty():
    assert _extract_email_from_text("") is None
    assert _extract_email_from_text(None) is None


def test_skip_patterns_filtered():
    bio = "Use img@2x.png for retina | contact@example.com for business"
    # example.com debe ser filtrado, @2x también
    result = _extract_email_from_text(bio)
    assert result is None


def test_extract_first_valid_email_from_multiple():
    bio = "contact@real-business.com or info@domain.com"
    result = _extract_email_from_text(bio)
    assert result == "contact@real-business.com"


# ── extract_and_save ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_min_followers_filter_saves_skipped():
    """Profile with followers below min_followers should be skipped."""
    fake_profile = {
        "uniqueId": "lowfollower",
        "nickname": "Low Follower",
        "signature": "email@test.com",
        "followerCount": 50,
        "verified": False,
        "bioLink": None,
    }
    with (
        patch("backend.tiktok.tt_profile.fetch_profile_rehydration", new=AsyncMock(return_value=fake_profile)),
        patch("backend.tiktok.tt_profile.limiter.wait", new=AsyncMock()),
        patch("backend.storage.database.save_skipped", new=AsyncMock()) as mock_skip,
    ):
        result = await extract_and_save("lowfollower", job_id="test-job", min_followers=1000)
        assert result is None
        mock_skip.assert_called_once()
        call_args = mock_skip.call_args[0]
        assert "low_followers" in call_args[1]


@pytest.mark.asyncio
async def test_email_from_bio_detected():
    """Profile with email in bio should be saved as lead with source='bio'."""
    fake_profile = {
        "uniqueId": "fotobio",
        "nickname": "Foto Bio",
        "signature": "Fotografa profesional. Contacto: foto@studio.es",
        "followerCount": 5000,
        "verified": False,
        "bioLink": None,
    }
    with (
        patch("backend.tiktok.tt_profile.fetch_profile_rehydration", new=AsyncMock(return_value=fake_profile)),
        patch("backend.tiktok.tt_profile.limiter.wait", new=AsyncMock()),
        patch("backend.tiktok.tt_profile.verify_email_mx", new=AsyncMock(return_value="valid")),
        patch("backend.storage.database.save_lead", new=AsyncMock()) as mock_save,
    ):
        result = await extract_and_save("fotobio", job_id="test-job", min_followers=0)
        assert result is not None
        assert result["email"] == "foto@studio.es"
        assert result["email_source"] == "bio"
        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_email_from_biolink_when_bio_empty():
    """Profile with no email in bio but with bioLink should try external website."""
    fake_profile = {
        "uniqueId": "nobio",
        "nickname": "No Bio Email",
        "signature": "Soy creador de contenido 🎬",
        "followerCount": 3000,
        "verified": False,
        "bioLink": "https://mi-estudio.com",
    }
    with (
        patch("backend.tiktok.tt_profile.fetch_profile_rehydration", new=AsyncMock(return_value=fake_profile)),
        patch("backend.tiktok.tt_profile.limiter.wait", new=AsyncMock()),
        patch("backend.tiktok.tt_profile.find_email_in_website", new=AsyncMock(return_value=["info@mi-estudio.com"])),
        patch("backend.tiktok.tt_profile.verify_email_mx", new=AsyncMock(return_value="valid")),
        patch("backend.storage.database.save_lead", new=AsyncMock()) as mock_save,
    ):
        result = await extract_and_save("nobio", job_id="test-job", min_followers=0)
        assert result is not None
        assert result["email"] == "info@mi-estudio.com"
        assert result["email_source"] == "biolink"
        mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_no_email_saves_skipped():
    """Profile with no email anywhere should be saved as skipped."""
    fake_profile = {
        "uniqueId": "noemail",
        "nickname": "No Email",
        "signature": "Solo hago videos 🎥",
        "followerCount": 2000,
        "verified": False,
        "bioLink": None,
    }
    with (
        patch("backend.tiktok.tt_profile.fetch_profile_rehydration", new=AsyncMock(return_value=fake_profile)),
        patch("backend.tiktok.tt_profile.limiter.wait", new=AsyncMock()),
        patch("backend.storage.database.save_skipped", new=AsyncMock()) as mock_skip,
    ):
        result = await extract_and_save("noemail", job_id="test-job", min_followers=0)
        assert result is None
        mock_skip.assert_called_once()
        assert "no_email" in mock_skip.call_args[0][1]


@pytest.mark.asyncio
async def test_invalid_mx_saves_skipped():
    """Profile with email that has no MX record should be skipped."""
    fake_profile = {
        "uniqueId": "badmx",
        "nickname": "Bad MX",
        "signature": "contact@fake-domain-xyz.invalid",
        "followerCount": 2000,
        "verified": False,
        "bioLink": None,
    }
    with (
        patch("backend.tiktok.tt_profile.fetch_profile_rehydration", new=AsyncMock(return_value=fake_profile)),
        patch("backend.tiktok.tt_profile.limiter.wait", new=AsyncMock()),
        patch("backend.tiktok.tt_profile.verify_email_mx", new=AsyncMock(return_value="invalid")),
        patch("backend.storage.database.save_skipped", new=AsyncMock()) as mock_skip,
    ):
        result = await extract_and_save("badmx", job_id="test-job", min_followers=0)
        assert result is None
        mock_skip.assert_called_once()
        assert "invalid_mx" in mock_skip.call_args[0][1]
