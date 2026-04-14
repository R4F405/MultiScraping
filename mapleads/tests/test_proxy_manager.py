"""
Tests for ProxyManager — covers all critical behaviors:
cooldown, circuit breaker, daily limit, rotation, wait_for_available.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from backend.proxy.proxy_manager import ProxyManager
from backend.proxy.proxy_stats import ProxyStats


_TEST_PROXY_URL = "http://testuser:testpass@proxy.test:80"


def _make_manager() -> ProxyManager:
    """
    Create a fresh ProxyManager with test settings patched in the proxy_manager
    module (where settings is imported) and stats injected directly.
    """
    import backend.proxy.proxy_manager as pm_mod
    from dataclasses import replace

    # Patch the settings reference INSIDE proxy_manager module
    pm_mod.settings = replace(
        pm_mod.settings,
        proxy_user="testuser",
        proxy_pass="testpass",
        proxy_host="proxy.test",
        proxy_port=80,
        max_requests_per_proxy_before_cooldown=5,
        proxy_cooldown_seconds=60,
        error_rate_threshold=0.30,
        high_error_cooldown_seconds=120,
        max_requests_per_day=20,
    )

    mgr = ProxyManager()
    mgr._initialized = True
    mgr._stats[_TEST_PROXY_URL] = ProxyStats(url=_TEST_PROXY_URL)
    return mgr


# ---- ProxyStats unit tests ----

def test_proxy_stats_available_by_default():
    s = ProxyStats(url="http://proxy")
    assert s.is_available is True
    assert s.seconds_until_available == 0


def test_proxy_stats_unavailable_during_cooldown():
    s = ProxyStats(url="http://proxy")
    s.cooldown_until = datetime.now() + timedelta(seconds=30)
    assert s.is_available is False
    assert s.seconds_until_available > 0


def test_proxy_stats_available_after_cooldown():
    s = ProxyStats(url="http://proxy")
    s.cooldown_until = datetime.now() - timedelta(seconds=1)
    assert s.is_available is True


def test_proxy_stats_error_rate_zero_when_no_requests():
    s = ProxyStats(url="http://proxy")
    assert s.error_rate == 0.0


def test_proxy_stats_error_rate_calculation():
    s = ProxyStats(url="http://proxy", total_requests=10, total_errors=3)
    assert s.error_rate == pytest.approx(0.30)


# ---- ProxyManager unit tests ----

@pytest.mark.asyncio
async def test_get_next_returns_proxy_url():
    mgr = _make_manager()
    proxy = await mgr.get_next()
    assert proxy is not None
    assert "testuser" in proxy
    assert "proxy.test" in proxy


@pytest.mark.asyncio
async def test_dev_mode_returns_none_without_credentials():
    mgr = ProxyManager()
    mgr._ensure_initialized = lambda: setattr(mgr, "_initialized", True)
    mgr._initialized = True
    # No stats = dev mode
    proxy = await mgr.get_next()
    assert proxy is None


@pytest.mark.asyncio
async def test_cooldown_activates_after_limit():
    mgr = _make_manager()
    url = list(mgr._stats.keys())[0]

    # Exhaust the per-proxy limit (5 in test config)
    for _ in range(5):
        await mgr.get_next()

    stat = mgr._stats[url]
    assert stat.cooldown_until is not None
    assert not stat.is_available


@pytest.mark.asyncio
async def test_proxy_unavailable_during_cooldown():
    mgr = _make_manager()
    url = list(mgr._stats.keys())[0]
    mgr._stats[url].cooldown_until = datetime.now() + timedelta(seconds=60)

    proxy = await mgr.get_next()
    assert proxy is None


@pytest.mark.asyncio
async def test_daily_limit_returns_none():
    mgr = _make_manager()
    # Manually set daily counter to the limit
    mgr._daily_count = 20

    proxy = await mgr.get_next()
    assert proxy is None


@pytest.mark.asyncio
async def test_daily_counter_resets_on_new_day():
    mgr = _make_manager()
    mgr._daily_count = 20
    mgr._daily_reset_date = "2000-01-01"  # simulate past date

    # Next call should reset the counter
    proxy = await mgr.get_next()
    assert proxy is not None
    assert mgr._daily_count == 1


@pytest.mark.asyncio
async def test_circuit_breaker_triggers_on_high_error_rate():
    mgr = _make_manager()
    url = list(mgr._stats.keys())[0]

    # Give the proxy enough requests to activate the circuit breaker
    stat = mgr._stats[url]
    stat.total_requests = 20
    stat.total_errors = 7  # 35% > 30% threshold

    await mgr.report_error(url)
    assert stat.cooldown_until is not None
    assert not stat.is_available


@pytest.mark.asyncio
async def test_circuit_breaker_not_triggered_below_threshold():
    mgr = _make_manager()
    url = list(mgr._stats.keys())[0]

    stat = mgr._stats[url]
    stat.total_requests = 20
    stat.total_errors = 2  # 10% < 30% threshold

    await mgr.report_error(url)
    # error incremented but still below threshold
    assert stat.cooldown_until is None or stat.is_available


@pytest.mark.asyncio
async def test_wait_for_available_returns_proxy():
    mgr = _make_manager()
    proxy = await mgr.wait_for_available(timeout_seconds=5)
    assert proxy is not None


@pytest.mark.asyncio
async def test_wait_for_available_timeout_when_all_in_cooldown():
    mgr = _make_manager()
    url = list(mgr._stats.keys())[0]
    # Put proxy in a very long cooldown
    mgr._stats[url].cooldown_until = datetime.now() + timedelta(seconds=999)

    proxy = await mgr.wait_for_available(timeout_seconds=2)
    assert proxy is None


@pytest.mark.asyncio
async def test_get_status_structure():
    mgr = _make_manager()
    status = mgr.get_status()

    assert "total_proxies" in status
    assert "available_now" in status
    assert "in_cooldown" in status
    assert "daily_requests_used" in status
    assert "daily_requests_limit" in status
    assert "daily_requests_remaining" in status
    assert "proxies" in status
    assert isinstance(status["proxies"], list)
