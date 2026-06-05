import asyncio

import pytest
from unittest.mock import patch

from backend.scraper.tiktok_client import TikTokLiveClient
from backend.scraper.tiktok_rate_limiter import EnrichThrottle, RateLimiter


@pytest.mark.asyncio
async def test_enrich_throttle_concurrency_one_max_one_in_flight():
    rl = RateLimiter()
    throttle = EnrichThrottle(rl, concurrency=1, bucket_capacity=50.0, refill_rate=1000.0)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def work():
        nonlocal in_flight, max_in_flight
        await throttle.acquire_start()
        try:
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.04)
            async with lock:
                in_flight -= 1
        finally:
            throttle.release_slot()

    await asyncio.gather(work(), work())
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_enrich_throttle_concurrency_two_can_overlap():
    rl = RateLimiter()
    throttle = EnrichThrottle(rl, concurrency=2, bucket_capacity=50.0, refill_rate=1000.0)
    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def work():
        nonlocal in_flight, max_in_flight
        await throttle.acquire_start()
        try:
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.06)
            async with lock:
                in_flight -= 1
        finally:
            throttle.release_slot()

    await asyncio.gather(work(), work())
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_wait_turn_for_request_skips_discovery_while_enrich_phase_sequential():
    client = TikTokLiveClient()
    calls: list[str] = []

    async def disc():
        calls.append("discovery")

    client.set_runtime_hooks(wait_turn_cb=disc, wait_turn_enrich_cb=None)
    client._enrich_phase = True
    await client._wait_turn_for_request()
    assert calls == []
    client._enrich_phase = False
    await client._wait_turn_for_request()
    assert calls == ["discovery"]


@pytest.mark.asyncio
async def test_wait_turn_for_request_uses_enrich_hook_when_configured():
    client = TikTokLiveClient()
    calls: list[str] = []

    async def disc():
        calls.append("discovery")

    async def enr():
        calls.append("enrich")

    client.set_runtime_hooks(wait_turn_cb=disc, wait_turn_enrich_cb=enr)
    client._enrich_phase = True
    await client._wait_turn_for_request()
    client._enrich_phase = False
    await client._wait_turn_for_request()
    assert calls == ["enrich", "discovery"]


@pytest.mark.asyncio
async def test_on_retryable_error_serializes_backoff_body():
    limiter = RateLimiter()
    active = 0
    max_active = 0
    inner = asyncio.Lock()
    real_sleep = asyncio.sleep

    async def counting_sleep(_t):
        nonlocal active, max_active
        async with inner:
            active += 1
            max_active = max(max_active, active)
        await real_sleep(0.01)
        async with inner:
            active -= 1

    async def one():
        with patch("backend.scraper.tiktok_rate_limiter.asyncio.sleep", new=counting_sleep):
            await limiter.on_retryable_error()

    await asyncio.gather(one(), one())
    assert max_active == 1
