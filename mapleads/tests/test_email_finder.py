import pytest

from backend.scraper.email_finder import (
    EMAIL_REGEX,
    _discover_contact_link_urls,
    _extract_emails,
    is_social_url,
    normalize_http_url,
    pick_best_email,
)


def test_extract_basic_email():
    html = '<a href="mailto:info@empresa.com">Contacto</a>'
    emails = _extract_emails(html)
    assert "info@empresa.com" in emails


def test_extract_multiple_emails():
    html = "Escríbenos a ventas@empresa.com o soporte@empresa.com"
    emails = _extract_emails(html)
    assert "ventas@empresa.com" in emails
    assert "soporte@empresa.com" in emails


def test_filter_sentry_email():
    html = "error at abc@sentry.io and real@empresa.com"
    emails = _extract_emails(html)
    assert "abc@sentry.io" not in emails
    assert "real@empresa.com" in emails


def test_filter_image_ref():
    html = "background: url(logo@2x.png) and contact@empresa.com"
    emails = _extract_emails(html)
    assert not any("@2x" in e for e in emails)
    assert "contact@empresa.com" in emails


def test_filter_example_com():
    html = "test@example.com is not a real email"
    emails = _extract_emails(html)
    assert "test@example.com" not in emails


def test_returns_deduplicated():
    html = "info@empresa.com info@empresa.com info@empresa.com"
    emails = _extract_emails(html)
    assert emails.count("info@empresa.com") == 1


def test_no_emails_returns_empty():
    html = "<html><body><p>Sin emails aquí</p></body></html>"
    emails = _extract_emails(html)
    assert emails == []


def test_extract_mailto_encoded():
    html = '<a href="mailto:info%40empresa.com?subject=Hola">Contacto</a>'
    emails = _extract_emails(html)
    assert "info@empresa.com" in emails


def test_extract_html_escaped_email():
    html = "Escríbenos a contacto&#64;empresa.com"
    emails = _extract_emails(html)
    assert "contacto@empresa.com" in emails


def test_extract_obfuscated_at_dot():
    html = "<p>Escríbenos a ventas [at] empresa [dot] com</p>"
    emails = _extract_emails(html)
    assert "ventas@empresa.com" in emails


def test_extract_data_email_attribute():
    html = '<footer data-email="hola&#64;empresa.com"></footer>'
    emails = _extract_emails(html)
    assert "hola@empresa.com" in emails


def test_extract_email_normalizes_spaces_and_encoding():
    html = '<a href="mailto:%20info@empresa.com">Mail</a> info@empresa.com '
    emails = _extract_emails(html)
    assert "info@empresa.com" in emails


def test_extract_email_from_jsonld():
    html = """
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Organization",
      "name": "Empresa Demo",
      "email": "info@empresa.com"
    }
    </script>
    """
    emails = _extract_emails(html)
    assert "info@empresa.com" in emails


def test_extract_email_from_js_concat():
    html = """
    <script>
      const mail = "contacto" + "@" + "empresa.com";
    </script>
    """
    emails = _extract_emails(html)
    assert "contacto@empresa.com" in emails


def test_discover_contact_link_urls_same_origin():
    html = """
    <html><body>
      <a href="/paginas/contacto">Contacto</a>
      <a href="https://externo.com/c">Externo</a>
    </body></html>
    """
    urls = _discover_contact_link_urls(html, "https://miempresa.com")
    assert any("/paginas/contacto" in u for u in urls)
    assert all("miempresa.com" in u for u in urls)


def test_discover_contact_link_by_aria_label():
    html = """
    <html><body>
      <a href="/page?id=7" aria-label="Contacto principal">Escríbenos</a>
    </body></html>
    """
    urls = _discover_contact_link_urls(html, "https://miempresa.com")
    assert any("/page?id=7" in u for u in urls)


def test_pick_best_prefers_contact_prefix_and_domain():
    site = "https://miempresa.com"
    emails = ["zzz@spam.com", "info@miempresa.com", "contacto@miempresa.com"]
    best = pick_best_email(emails, site)
    assert best == "contacto@miempresa.com"


def test_regex_matches_various_formats():
    cases = [
        "user@domain.com",
        "user.name+tag@sub.domain.org",
        "user_123@empresa.es",
    ]
    for email in cases:
        assert EMAIL_REGEX.search(email), f"Should match: {email}"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://instagram.com/miempresa", True),
        ("https://www.instagram.com/miempresa", True),
        ("https://l.instagram.com/?u=https%3A%2F%2Fmiempresa.com", True),
        ("https://m.facebook.com/pages/miempresa", True),
        ("https://tiktok.com/@miempresa", True),
        ("https://linkedin.com/company/miempresa", True),
        ("https://x.com/miempresa", True),
        ("https://twitter.com/miempresa", True),
        ("https://youtube.com/@miempresa", True),
        ("https://youtu.be/abcdef", True),
        ("https://linktr.ee/miempresa", True),
        ("https://tripadvisor.com/Restaurant_Review", True),
        ("https://g.page/miempresa", True),
        ("https://forms.gle/CShfkhTrWWGJLhNh7", True),
        ("https://miempresa.com", False),
        ("http://sub.miempresa.com/contacto", False),
        ("miempresa.com/contacto", False),  # scheme-less
    ],
)
def test_is_social_url(url: str, expected: bool):
    assert is_social_url(url) is expected


def test_pick_best_avoids_noreply_when_better_options_exist():
    site = "https://miempresa.com"
    emails = ["noreply@miempresa.com", "contacto@miempresa.com", "info@miempresa.com"]
    best = pick_best_email(emails, site)
    assert best == "contacto@miempresa.com"


@pytest.mark.parametrize(
    "raw,expected_prefix",
    [
        ("miempresa.com", "https://"),
        ("http://miempresa.com", "http://"),
        ("https://miempresa.com", "https://"),
    ],
)
def test_normalize_http_url_accepts_and_defaults_scheme(raw: str, expected_prefix: str):
    out = normalize_http_url(raw)
    assert out is not None
    assert out.startswith(expected_prefix)


@pytest.mark.parametrize("raw", ["mailto:info@miempresa.com", "tel:+34123456789", ""])
def test_normalize_http_url_rejects_non_http(raw: str):
    assert normalize_http_url(raw) is None


@pytest.mark.asyncio
async def test_find_email_uses_root_contact_paths(monkeypatch):
    from backend.scraper import email_finder as ef

    fetched_urls: list[str] = []

    async def _fake_fetch(url: str, _proxy: str | None) -> tuple[str, bool, str | None]:
        fetched_urls.append(url)
        if url == "https://miempresa.com/contacto":
            return "<html>contacto@miempresa.com</html>", False, None
        return "<html>sin correo</html>", False, None

    class _ProxyStub:
        _stats = {}

        async def wait_for_available(self):
            return None

        async def report_success(self, _proxy):
            return None

    monkeypatch.setattr(ef, "_fetch_page", _fake_fetch)
    monkeypatch.setattr(ef, "proxy_manager", _ProxyStub())

    emails = await ef.find_email_in_website("https://miempresa.com/servicios/dental")
    assert "contacto@miempresa.com" in emails
    assert "https://miempresa.com/contacto" in fetched_urls


@pytest.mark.asyncio
async def test_find_email_follows_discovered_contact_link(monkeypatch):
    from backend.scraper import email_finder as ef

    fetched_urls: list[str] = []

    async def _fake_fetch(url: str, _proxy: str | None) -> tuple[str, bool, str | None]:
        fetched_urls.append(url)
        if url.rstrip("/") == "https://miempresa.com":
            return """
            <html><body>
              <a href="/paginas/contacto">Contacto</a>
            </body></html>
            """, False, None
        if "/paginas/contacto" in url:
            return "<html>hola@miempresa.com</html>", False, None
        return "<html>vacío</html>", False, None

    class _ProxyStub:
        _stats = {}

        async def wait_for_available(self):
            return None

        async def report_success(self, _proxy):
            return None

    monkeypatch.setattr(ef, "_fetch_page", _fake_fetch)
    monkeypatch.setattr(ef, "proxy_manager", _ProxyStub())

    emails = await ef.find_email_in_website("https://miempresa.com/")
    assert "hola@miempresa.com" in emails
    assert any("/paginas/contacto" in u for u in fetched_urls)


@pytest.mark.asyncio
async def test_fetch_page_fallbacks_to_direct_when_proxy_fails(monkeypatch):
    from backend.scraper import email_finder as ef

    calls: list[dict] = []

    class _Resp:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    def _fake_get(url, **kwargs):
        calls.append({"url": url, "proxies": kwargs.get("proxies")})
        if kwargs.get("proxies"):
            raise RuntimeError("proxy tunnel failed")
        return _Resp(200, "<html>contacto@miempresa.com</html>")

    monkeypatch.setattr(ef.curl_requests, "get", _fake_get)

    html, used_proxy, reason = await ef._fetch_page("https://miempresa.com", "http://proxy:8080")
    assert "contacto@miempresa.com" in html
    assert used_proxy is False
    assert reason is None
    assert len(calls) == 2
    assert calls[0]["proxies"] is not None
    assert calls[1]["proxies"] is None


@pytest.mark.asyncio
async def test_find_email_force_direct_skips_proxy_manager(monkeypatch):
    from backend.scraper import email_finder as ef

    class _ProxyStub:
        _stats = {"p1": object()}

        async def wait_for_available(self):
            raise AssertionError("wait_for_available should not be called in force_direct mode")

        async def report_success(self, _proxy):
            return None

    async def _fake_fetch(_url: str, proxy: str | None) -> tuple[str, bool, str | None]:
        assert proxy is None
        return "<html>directo@miempresa.com</html>", False, None

    monkeypatch.setattr(ef, "proxy_manager", _ProxyStub())
    monkeypatch.setattr(ef, "_fetch_page", _fake_fetch)
    monkeypatch.setattr(ef.settings, "email_scraper_force_direct", True)

    emails = await ef.find_email_in_website("https://miempresa.com")
    assert "directo@miempresa.com" in emails


@pytest.mark.asyncio
async def test_diagnostics_upgrades_form_vendor_specificity(monkeypatch):
    from backend.scraper import email_finder as ef

    calls = {"n": 0}

    async def _fake_fetch(_url: str, _proxy: str | None):
        calls["n"] += 1
        if calls["n"] == 1:
            return "<html><form action='/search'></form></html>", False, None
        if calls["n"] == 2:
            return "<html><div class='fluentform_wrapper'>x</div></html>", False, None
        return "<html>sin correo</html>", False, None

    class _ProxyStub:
        _stats = {}

        async def wait_for_available(self):
            return None

        async def report_success(self, _proxy):
            return None

    monkeypatch.setattr(ef, "_fetch_page", _fake_fetch)
    monkeypatch.setattr(ef, "proxy_manager", _ProxyStub())
    monkeypatch.setattr(ef.settings, "email_scraper_force_direct", True)

    diag = await ef.find_email_in_website_diagnostics("https://miempresa.com")
    assert diag["form_vendor"] == "fluentform"
