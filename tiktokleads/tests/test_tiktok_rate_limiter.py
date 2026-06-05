import pytest
from unittest.mock import AsyncMock, patch

from backend.scraper.tiktok_rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_wait_turn_does_not_crash():
    limiter = RateLimiter()
    with patch("asyncio.sleep", new=AsyncMock()):
        await limiter.wait_turn()


@pytest.mark.asyncio
async def test_backoff_grows_and_resets():
    limiter = RateLimiter()
    start = limiter._backoff
    with patch("asyncio.sleep", new=AsyncMock()):
        await limiter.on_retryable_error()
    assert limiter._backoff >= start
    limiter.reset_backoff()
    assert limiter._backoff == start
