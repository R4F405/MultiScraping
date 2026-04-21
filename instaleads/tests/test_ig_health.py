import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_check_returns_broken_when_endpoint_fails():
    from backend.scraper.ig_health import run_health_check

    with (
        patch("backend.scraper.ig_health.ig_get", new=AsyncMock(return_value={"error": "max_retries_exceeded"})),
        patch("backend.scraper.ig_health.session_info", return_value={"logged_in": False}),
        patch("backend.scraper.ig_health.db.insert_health_log", new=AsyncMock()),
    ):
        result = await run_health_check()

    assert result["status"] == "broken"
    assert result["unauth_mode"] == "broken"
    assert result["fix_guide"] is not None


@pytest.mark.asyncio
async def test_health_check_returns_ok_when_endpoint_works():
    from backend.scraper.ig_health import run_health_check

    fake_ok = {"data": {"user": {"id": "123"}}}
    with (
        patch("backend.scraper.ig_health.ig_get", new=AsyncMock(return_value=fake_ok)),
        patch("backend.scraper.ig_health.session_info", return_value={"logged_in": False}),
        patch("backend.scraper.ig_health.db.insert_health_log", new=AsyncMock()),
    ):
        result = await run_health_check()

    assert result["status"] == "ok"
    assert result["unauth_mode"] == "ok"
    assert result["auth_mode"] == "no_session"
    assert result["fix_guide"] is None
