import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_profile_extracts_business_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "123",
                "username": "testuser",
                "full_name": "Test User",
                "biography": "No email here",
                "business_email": "business@example.com",
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 1000,
                "is_business_account": True,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("testuser")

    assert profile is not None
    assert profile["email"] == "business@example.com"
    assert profile["email_source"] == "business_field"


@pytest.mark.asyncio
async def test_get_profile_extracts_bio_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "456",
                "username": "biouser",
                "full_name": "Bio User",
                "biography": "Contact me: hello@biouser.com",
                "business_email": None,
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 500,
                "is_business_account": False,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("biouser")

    assert profile["email"] == "hello@biouser.com"
    assert profile["email_source"] == "bio_regex"


@pytest.mark.asyncio
async def test_get_profile_returns_none_for_private():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "789",
                "username": "privateuser",
                "is_private": True,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("privateuser")

    assert profile["private"] is True
    assert profile["email"] is None


@pytest.mark.asyncio
async def test_get_profile_returns_none_email_when_no_email():
    from backend.scraper.ig_profile import get_profile

    fake_response = {
        "data": {
            "user": {
                "id": "000",
                "username": "noemailuser",
                "full_name": "No Email",
                "biography": "Just a regular bio without contact info",
                "business_email": None,
                "business_phone_number": None,
                "external_url": None,
                "follower_count": 200,
                "is_business_account": False,
                "is_private": False,
            }
        }
    }
    with patch("backend.scraper.ig_profile.ig_get", new=AsyncMock(return_value=fake_response)):
        profile = await get_profile("noemailuser")

    assert profile["email"] is None


@pytest.mark.asyncio
async def test_get_profile_returns_none_on_fetch_error():
    from backend.scraper.ig_profile import get_profile

    with patch(
        "backend.scraper.ig_profile.ig_get",
        new=AsyncMock(return_value={"error": "max_retries_exceeded"}),
    ):
        profile = await get_profile("erroruser")

    assert profile is None
