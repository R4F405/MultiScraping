import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_check_returns_rate_limited_when_daily_limit_reached():
    from backend.scraper import ig_health
    ig_health._health_cache = None
    ig_health._health_cache_at = None

    with (
        patch("backend.scraper.ig_health.db.get_daily_count", new=AsyncMock(return_value=120)),
        patch("backend.scraper.ig_health.Settings.IG_LIMIT_DAILY_UNAUTHENTICATED", 100),
        patch("backend.scraper.ig_health.Settings.IG_PROXY_LIST", []),
        patch("backend.scraper.ig_health.db.insert_health_log", new=AsyncMock()),
    ):
        result = await ig_health.run_health_check()

    assert result["status"] == "ok"
    assert result["unauth_mode"] == "rate_limited"
    assert "Límite diario alcanzado" in result["message"]


@pytest.mark.asyncio
async def test_health_check_returns_ok_when_endpoint_works():
    from backend.scraper import ig_health
    ig_health._health_cache = None
    ig_health._health_cache_at = None

    with (
        patch("backend.scraper.ig_health.db.get_daily_count", new=AsyncMock(return_value=12)),
        patch("backend.scraper.ig_health.Settings.IG_LIMIT_DAILY_UNAUTHENTICATED", 100),
        patch("backend.scraper.ig_health.Settings.IG_PROXY_LIST", ["http://proxy-1"]),
        patch("backend.scraper.ig_health.db.insert_health_log", new=AsyncMock()),
    ):
        result = await ig_health.run_health_check()

    assert result["status"] == "ok"
    assert result["unauth_mode"] == "ok"
    assert "proxies activos" in result["message"]
    assert result["fix_guide"] is None
