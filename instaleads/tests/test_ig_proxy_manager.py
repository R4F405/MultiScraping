"""Tests for IgProxyManager — focus on the anti-ban pinned-proxy behaviour."""
from backend.scraper.ig_proxy_manager import IgProxyManager

_PROXIES = [f"http://u:p@10.0.0.{i}:8000" for i in range(5)]


def test_get_pinned_is_stable_for_same_key():
    pm = IgProxyManager()
    pm.init(_PROXIES)
    first = pm.get_pinned("acct-42")
    for _ in range(20):
        assert pm.get_pinned("acct-42") == first  # never hops IPs


def test_get_pinned_none_without_proxies():
    pm = IgProxyManager()
    assert pm.get_pinned("acct-42") is None


def test_get_pinned_falls_back_when_pinned_in_cooldown():
    pm = IgProxyManager()
    pm.init(_PROXIES)
    pinned = pm.get_pinned("acct-42")
    pm.report_error(pinned, cooldown_seconds=300)
    fallback = pm.get_pinned("acct-42")
    assert fallback != pinned  # avoids the cooled-down IP
    assert fallback in _PROXIES


def test_get_pinned_does_not_advance_round_robin_index():
    """Pinning must not disturb the round-robin cursor used by guest requests."""
    pm = IgProxyManager()
    pm.init(_PROXIES)
    pm.get_pinned("acct-42")
    pm.get_pinned("acct-99")
    # Round-robin starts fresh from index 0 regardless of pinned calls.
    assert pm.get_next() == _PROXIES[0]
