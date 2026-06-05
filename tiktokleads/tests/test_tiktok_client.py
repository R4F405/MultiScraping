from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config.settings import Settings
from backend.scraper import tiktok_client as tiktok_client_mod
from backend.scraper.tiktok_client import (
    TikTokLiveClient,
    _discovery_tag_slug,
    _handle_from_discovery_href,
    _http_enrich_has_contact_signal,
    _parse_compact_number,
)


def test_http_enrich_has_contact_signal():
    # Solo email cuenta como señal de contacto
    assert _http_enrich_has_contact_signal({"email": "a@b.co", "phone": None, "website": None})
    # Phone y website NO son suficientes (website es muy común en TikTok)
    assert not _http_enrich_has_contact_signal(
        {"email": None, "phone": "+34 600 000 000", "website": ""}
    )
    assert not _http_enrich_has_contact_signal(
        {"email": None, "phone": "  ", "website": "https://x.com"}
    )
    assert not _http_enrich_has_contact_signal(
        {"email": None, "phone": None, "website": None}
    )


@pytest.mark.asyncio
async def test_enrich_via_http_skips_jina_when_jina_fallback_false():
    client = TikTokLiveClient()
    n = {"c": 0}

    async def track_get(c, url, **kw):
        n["c"] += 1
        m = MagicMock()
        m.status_code = 200
        m.text = "<html></html>"
        return m

    with patch.object(client, "_http_get_with_proxy_fallback", new=AsyncMock(side_effect=track_get)):
        await client._enrich_via_http("id", "someuser", jina_fallback=False)
    assert n["c"] == 1


@pytest.mark.asyncio
async def test_enrich_via_http_calls_jina_when_empty_html_and_fallback_true():
    client = TikTokLiveClient()
    n = {"c": 0}

    async def track_get(c, url, **kw):
        n["c"] += 1
        m = MagicMock()
        m.status_code = 200
        m.text = "" if n["c"] == 1 else "<html>ok</html>"
        return m

    with patch.object(client, "_http_get_with_proxy_fallback", new=AsyncMock(side_effect=track_get)):
        await client._enrich_via_http("id", "someuser", jina_fallback=True)
    assert n["c"] == 2


def test_parse_compact_number():
    assert _parse_compact_number("1.2K") == 1200
    assert _parse_compact_number("2.5M") == 2_500_000
    assert _parse_compact_number("3B") == 3_000_000_000
    assert _parse_compact_number("123") == 123


def test_extract_profile_from_universal_data():
    html = """
    <script>
    window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = {"__DEFAULT_SCOPE__":{"webapp.user-detail":{"userInfo":{"user":{"id":"123","uniqueId":"creator.test","nickname":"Creator","signature":"contacto: c@x.com","verified":true},"stats":{"followerCount":"1.2K","followingCount":"55","heartCount":"3.1M"}}}}};
    </script>
    """
    client = TikTokLiveClient()
    parsed = client._extract_profile_from_universal_data(html)
    assert parsed["author_id"] == "123"
    assert parsed["handle"] == "creator.test"
    assert parsed["verified"] is True
    assert parsed["followers"] == 1200
    assert parsed["likes"] == 3_100_000


def test_discovery_tag_slug_hashtag_strips_location_pipe():
    assert _discovery_tag_slug("hashtag", "nutricionista|Barcelona") == "nutricionista"
    assert _discovery_tag_slug("hashtag", "#mealprep") == "mealprep"


def test_handle_from_discovery_href_absolute_and_relative():
    assert _handle_from_discovery_href("/@some.user") == "some.user"
    assert (
        _handle_from_discovery_href("https://www.tiktok.com/@Other_User/video/123")
        == "Other_User"
    )
    assert _handle_from_discovery_href("https://example.com/x") is None


def test_search_url_hashtag_uses_niche_not_full_pipe_target():
    c = TikTokLiveClient()
    url = c._search_url("hashtag", "nutricionista|Barcelona")
    assert "tag/nutricionista" in url
    assert "%7C" not in url


def test_search_url_keyword_uses_space_not_pipe_in_query():
    c = TikTokLiveClient()
    url = c._search_url("keyword", "nutricionista|Barcelona")
    assert "search?q=" in url
    assert "nutricionista+Barcelona" in url or "nutricionista%20Barcelona" in url
    assert "%7C" not in url


@pytest.mark.asyncio
async def test_discover_via_http_serp_path_uses_tiktok_serp():
    """Cableado: modo serp usa build_dork_queries + scrape_serp_handles_for_query."""
    client = TikTokLiveClient()

    async def fake_serp(*args, **kwargs):
        return ["lead_handle"]

    with patch.object(Settings, "TIKTOKLEADS_DISCOVERY_PRIMARY", "serp"):
        with patch(
            "backend.scraper.tiktok_client.tiktok_serp.build_dork_queries",
            return_value=["site:tiktok.com/@ x"],
        ):
            with patch(
                "backend.scraper.tiktok_client.tiktok_serp.scrape_serp_handles_for_query",
                new=AsyncMock(side_effect=fake_serp),
            ):
                with patch.object(
                    client, "_http_get_with_proxy_fallback", new=AsyncMock(return_value=None)
                ):
                    out = await client._discover_via_http("keyword", "café", 3)
    assert len(out) == 1
    assert out[0]["author_handle"] == "lead_handle"


@pytest.mark.asyncio
async def test_enrich_author_http_first_skips_playwright_when_has_contact():
    http_prof = {
        "author_id": "a1",
        "handle": "u",
        "nickname": "U",
        "bio": "",
        "followers": 1,
        "following": 0,
        "likes": 0,
        "verified": False,
        "email": "x@y.com",
        "phone": None,
        "website": None,
    }
    pw_entered = {"n": 0}

    class EvilCM:
        async def __aenter__(self):
            pw_entered["n"] += 1
            raise AssertionError("playwright no debe abrirse")

        async def __aexit__(self, *a):
            return None

    def fake_ap():
        return EvilCM()

    client = TikTokLiveClient()
    with patch.object(tiktok_client_mod, "async_playwright", fake_ap):
        with patch.object(Settings, "TIKTOKLEADS_ENRICH_HTTP_FIRST", True):
            with patch.object(
                client, "_enrich_via_http", new=AsyncMock(return_value=http_prof)
            ):
                out = await client.enrich_author("a1", "user")
    assert out == http_prof
    assert pw_entered["n"] == 0


@pytest.mark.asyncio
async def test_enrich_author_http_first_uses_playwright_when_no_contact():
    http_empty = {
        "author_id": "a1",
        "handle": "myuser",
        "nickname": "U",
        "bio": "bio",
        "followers": 10,
        "following": 1,
        "likes": 1,
        "verified": False,
        "email": None,
        "phone": None,
        "website": None,
    }
    udd_html = """
<script>
window.__UNIVERSAL_DATA_FOR_REHYDRATION__ = {"__DEFAULT_SCOPE__":{"webapp.user-detail":{"userInfo":{"user":{"id":"123","uniqueId":"myuser","nickname":"My","signature":"","verified":false},"stats":{"followerCount":"100","followingCount":"1","heartCount":"10"}}}}};
</script>
"""

    class FakePage:
        def set_default_timeout(self, t):
            pass

        async def goto(self, *a, **k):
            pass

        async def wait_for_timeout(self, ms):
            pass

        async def add_init_script(self, s):
            pass

        async def content(self):
            return udd_html

        async def title(self):
            return "t"

        async def evaluate(self, s):
            return ""

        async def get_attribute(self, *a, **k):
            return None

    class FakeCtx:
        async def add_init_script(self, s):
            pass

        async def new_page(self):
            return FakePage()

        async def close(self):
            pass

    class FakeBrowser:
        async def new_context(self, **k):
            return FakeCtx()

        async def close(self):
            pass

    class FakeAPCtx:
        async def __aenter__(self):
            m = MagicMock()
            m.chromium.launch = AsyncMock(return_value=FakeBrowser())
            return m

        async def __aexit__(self, *a):
            return None

    def fake_ap():
        return FakeAPCtx()

    client = TikTokLiveClient()
    with patch.object(tiktok_client_mod, "async_playwright", fake_ap):
        with patch.object(Settings, "TIKTOKLEADS_ENRICH_HTTP_FIRST", True):
            with patch.object(
                client, "_enrich_via_http", new=AsyncMock(return_value=http_empty)
            ):
                out = await client.enrich_author("a1", "myuser")
    assert out["handle"] == "myuser"
    assert out["author_id"] == "123"


@pytest.mark.asyncio
async def test_http_get_fallback_skips_blocked_response():
    """Fix #1: no devolver respuesta bloqueada aunque status < 500."""
    import httpx
    client = TikTokLiveClient()

    async def mock_get(client_obj, url, proxy, **kw):
        m = MagicMock()
        m.status_code = 403
        m.text = "Access Denied - captcha required"
        return m

    with patch.object(client, "_do_get", new=AsyncMock(side_effect=mock_get)):
        async with httpx.AsyncClient() as http_client:
            resp = await client._http_get_with_proxy_fallback(http_client, "http://test.com")

    assert resp is None  # No debe devolver respuesta bloqueada


def test_candidate_proxies_filters_zero_success(monkeypatch):
    """Fix #3: filtrar proxies con 0% éxito."""
    monkeypatch.setattr(Settings, "TIKTOKLEADS_PROXY_LIST", ["proxy1", "proxy2"])
    client = TikTokLiveClient()

    # Simular stats: proxy1 = 0/10, proxy2 = 5/5
    TikTokLiveClient._proxy_stats = {
        "proxy1": {"ok": 0, "fail": 10},
        "proxy2": {"ok": 5, "fail": 5},
    }
    TikTokLiveClient._proxy_cooldown_until = {}

    candidates = client._candidate_proxies(max_proxy_tries=5)

    assert "proxy1" not in candidates  # Filtrado por 0% éxito
    assert "proxy2" in candidates


def test_candidate_proxies_orders_by_success_and_direct_last(monkeypatch):
    monkeypatch.setattr(Settings, "TIKTOKLEADS_PROXY_LIST", ["weak", "strong"])
    TikTokLiveClient._proxy_stats = {
        "weak": {"ok": 1, "fail": 9},
        "strong": {"ok": 9, "fail": 1},
    }
    TikTokLiveClient._proxy_cooldown_until = {}
    client = TikTokLiveClient()
    c = client._candidate_proxies(max_proxy_tries=4)
    assert c[0] == "strong"
    assert c[1] == "weak"
    assert c[-1] is None


def test_ua_profiles_are_coherent():
    """Verifica que UA y Client Hints son coherentes en cada perfil."""
    from backend.scraper.tiktok_client import TikTokLiveClient
    
    for profile in TikTokLiveClient._user_agent_profiles:
        ua = profile["ua"]
        platform = profile["platform"]
        
        # Windows UA debe tener Windows platform
        if "Windows" in ua:
            assert platform == "Windows"
            if profile["sec-ch-ua-platform"]:
                assert '"Windows"' in profile["sec-ch-ua-platform"]
        
        # macOS UA debe tener macOS platform
        elif "Macintosh" in ua or "Mac OS X" in ua:
            assert platform == "macOS"
            if profile["sec-ch-ua-platform"]:
                assert '"macOS"' in profile["sec-ch-ua-platform"]
        
        # Linux UA debe tener Linux platform
        elif "Linux" in ua:
            assert platform == "Linux"
            if profile["sec-ch-ua-platform"]:
                assert '"Linux"' in profile["sec-ch-ua-platform"]
        
        # Firefox no debe tener Client Hints
        if "Firefox" in ua:
            assert profile["sec-ch-ua"] is None


@pytest.mark.asyncio
async def test_cookie_jar_persists_between_requests():
    """Verifica que cookies se persisten entre requests."""
    from unittest.mock import MagicMock
    import httpx
    
    client = TikTokLiveClient()
    
    # Mock response con cookies
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html>test</html>"
    mock_resp.cookies = {"session_id": "abc123", "csrf_token": "xyz789"}
    
    with patch.object(client, "_do_get", return_value=mock_resp):
        async with httpx.AsyncClient() as http_client:
            resp = await client._http_get_with_proxy_fallback(http_client, "http://test.com")
    
    # Verificar cookies guardadas
    assert "session_id" in client._cookie_jar
    assert client._cookie_jar["session_id"] == "abc123"


@pytest.mark.asyncio
async def test_playwright_stealth_hides_webdriver():
    """Opcional: playwright-stealth v2 aplica en el context, no en la page."""
    try:
        from playwright_stealth import Stealth
    except ImportError:
        pytest.skip("playwright-stealth no disponible")

    from backend.scraper.tiktok_client import TikTokLiveClient

    client = TikTokLiveClient()
    if not client.playwright_installed():
        pytest.skip("Playwright no disponible")

    stealth = Stealth()
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await client._launch_browser(p)
        except RuntimeError as e:
            if "Executable doesn't exist" in str(e) or "playwright" in str(e).lower():
                pytest.skip("Playwright browsers not installed")
            raise
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        await page.goto("data:text/html,<html></html>")
        webdriver_value = await page.evaluate("() => navigator.webdriver")

        plugins_count = await page.evaluate("() => navigator.plugins.length")

        await browser.close()

        assert webdriver_value is None or webdriver_value is False
        assert plugins_count > 0
