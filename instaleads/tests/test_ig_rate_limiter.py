import pytest
from unittest.mock import AsyncMock, patch

from backend.scraper.ig_rate_limiter import DailyLimitReached, RateLimiter
from backend.config.settings import Settings


@pytest.mark.asyncio
async def test_daily_limit_raises_when_reached():
    limiter = RateLimiter(mode="unauth")
    with patch("backend.scraper.ig_rate_limiter.db.get_daily_count", new=AsyncMock(return_value=999)):
        with pytest.raises(DailyLimitReached):
            await limiter.check_and_wait()


@pytest.mark.asyncio
async def test_daily_limit_ok_when_under():
    limiter = RateLimiter(mode="unauth")
    with (
        patch("backend.scraper.ig_rate_limiter.db.get_daily_count", new=AsyncMock(return_value=0)),
        patch("backend.scraper.ig_rate_limiter.db.increment_daily_count", new=AsyncMock()),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await limiter.check_and_wait()  # should not raise


@pytest.mark.asyncio
async def test_backoff_doubles_on_rate_limited():
    limiter = RateLimiter(mode="unauth")
    initial = limiter._backoff
    with patch("asyncio.sleep", new=AsyncMock()):
        await limiter.on_rate_limited()
    assert limiter._backoff == initial * Settings.IG_BACKOFF_MULTIPLIER


@pytest.mark.asyncio
async def test_backoff_capped_at_max():
    limiter = RateLimiter(mode="unauth")
    limiter._backoff = Settings.IG_BACKOFF_MAX
    with patch("asyncio.sleep", new=AsyncMock()):
        await limiter.on_rate_limited()
    assert limiter._backoff == Settings.IG_BACKOFF_MAX


def test_reset_backoff():
    limiter = RateLimiter(mode="auth")
    limiter._backoff = 999
    limiter.reset_backoff()
    assert limiter._backoff == Settings.IG_BACKOFF_INITIAL
