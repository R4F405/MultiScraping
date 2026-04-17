from backend.instagram.ig_proxy_manager import ProxyManager
from backend.config.settings import settings


def test_proxy_manager_without_proxies():
    manager = ProxyManager()
    if not settings.proxy_list and not settings.ig_proxy_url:
        assert manager.has_proxy() is False
        assert manager.choose() == ""


def test_proxy_manager_state_transitions():
    original_list = settings.proxy_list
    original_single = settings.ig_proxy_url
    try:
        settings.proxy_list = ["http://proxy-1:80"]
        settings.ig_proxy_url = ""
        manager = ProxyManager()
        proxy = manager.choose()
        assert proxy == "http://proxy-1:80"
        manager.report_failure(proxy, "429")
        snap1 = manager.snapshot()[proxy]
        assert snap1["failures"] == 1
        manager.report_success(proxy)
        snap2 = manager.snapshot()[proxy]
        assert snap2["success"] == 1
    finally:
        settings.proxy_list = original_list
        settings.ig_proxy_url = original_single
