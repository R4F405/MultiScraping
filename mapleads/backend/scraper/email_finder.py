"""
Lightweight email discovery from business websites.

Covers: homepage, common /contact paths, same-origin links that look like contact
pages, mailto/data attributes, simple obfuscations. Does NOT cover: emails only
in images, sites behind heavy bot/Captcha, or JS-only SPAs without server HTML
(use optional Playwright when installed + EMAIL_SCRAPER_USE_PLAYWRIGHT=1).
"""

import asyncio
import html as html_lib
import logging
import re
from urllib.parse import unquote, urljoin, urlparse

import curl_cffi.requests as curl_requests
from bs4 import BeautifulSoup

from backend.config.settings import settings
from backend.proxy.proxy_manager import proxy_manager

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_PATTERNS = [
    "@2x", "@3x", "sentry", "example.com", "schema.org",
    "wixpress.com", "w3.org", "demolink.org", "your-domain",
    "yourdomain", "domain.com", "email@",
]

_CONTACT_PATHS = [
    "/contacto",
    "/contact",
    "/contact-us",
    "/contactenos",
    "/contactar",
    "/about",
    "/about-us",
    "/sobre-nosotros",
    "/empresa",
    "/impressum",
]

_LINK_KEYWORDS = (
    "contact", "contacto", "kontakt", "about", "nosotros", "equipo",
    "impressum", "ubicacion", "ubication", "location", "legal", "aviso",
)

# Hard cap on HTTP fetches per business (proxies + politeness).
_MAX_FETCH_REQUESTS = 12
_MAX_DISCOVERED_LINKS = 6

_SOCIAL_HOSTS = {
    "facebook.com",
    "fb.com",
    "instagram.com",
    "instagr.am",
    "linkedin.com",
    "pinterest.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


def normalize_http_url(url: str) -> str | None:
    """
    Normalize a user-provided URL into a usable http(s) URL.

    Returns None when the URL is empty or clearly non-http (mailto/tel/etc.).
    """
    raw = (url or "").strip()
    if not raw:
        return None

    # Reject non-web schemes early.
    lowered = raw.lower()
    if lowered.startswith(("mailto:", "tel:", "sms:", "whatsapp:")):
        return None

    # Accept scheme-less URLs by defaulting to https.
    if not lowered.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc:
        return None

    return raw


def _host_key(netloc: str) -> str:
    h = (netloc or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def is_social_url(url: str) -> bool:
    """
    Return True when the URL is for a social network (not a business website).
    """
    normalized = normalize_http_url(url)
    if normalized is None:
        # Treat non-http(s) inputs as non-scrapable (skip).
        return True

    host = (urlparse(normalized).hostname or "").lower().strip(".")
    if not host:
        return True

    # Remove common subdomain prefixes but keep the registrable domain check via suffix.
    if host.startswith("www."):
        host = host[4:]

    return host in _SOCIAL_HOSTS or any(host.endswith("." + h) for h in _SOCIAL_HOSTS)


async def _fetch_page(url: str, proxy: str | None) -> str:
    """Fetch a URL and return its HTML. Returns empty string on error."""
    proxies = {"https": proxy, "http": proxy} if proxy else None
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                url,
                headers=_HEADERS,
                proxies=proxies,
                impersonate="chrome124",
                timeout=10,
                allow_redirects=True,
            ),
        )
        if response.status_code == 200:
            return response.text
        logger.debug("_fetch_page %s → status %d", url, response.status_code)
        return ""
    except Exception as exc:
        logger.debug("_fetch_page error for %s: %s", url, exc)
        return ""


def _extract_obfuscated_emails(source: str) -> list[str]:
    """Patterns like user [at] domain [dot] com or user(at)domain(dot)com."""
    out: list[str] = []
    # [at] / (at) / {at}
    for m in re.finditer(
        r"([a-zA-Z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\})\s*"
        r"([a-zA-Z0-9.\-]+)\s*(?:\[dot\]|\(dot\)|\{dot\})\s*"
        r"([a-zA-Z]{2,})",
        source,
        flags=re.IGNORECASE,
    ):
        local, dom, tld = m.group(1), m.group(2), m.group(3)
        candidate = f"{local}@{dom}.{tld}"
        if EMAIL_REGEX.fullmatch(candidate):
            out.append(candidate.lower())
    return out


def _extract_data_attribute_emails(source: str) -> list[str]:
    """data-email=..., data-contact-email=..."""
    found: list[str] = []
    for m in re.finditer(
        r'data-(?:email|contact-email|contact_email)\s*=\s*["\']([^"\']+)["\']',
        source,
        flags=re.IGNORECASE,
    ):
        val = html_lib.unescape(m.group(1).strip())
        if "@" in val:
            for e in EMAIL_REGEX.findall(val):
                found.append(e)
    return found


def _extract_emails(page_html: str) -> list[str]:
    """Extract and filter emails from HTML string."""
    source = html_lib.unescape(page_html or "")
    found = list(EMAIL_REGEX.findall(source))
    found.extend(_extract_obfuscated_emails(source))
    found.extend(_extract_data_attribute_emails(source))
    # Also catch explicit mailto links, including URL-encoded addresses.
    mailto_hits = re.findall(r"mailto:([^\"'?\s>]+)", source, flags=re.IGNORECASE)
    found.extend(unquote(hit) for hit in mailto_hits if hit)
    clean = [
        e for e in found
        if not any(skip in e.lower() for skip in _SKIP_PATTERNS)
    ]
    return list(set(clean))


def _discover_contact_link_urls(html: str, root_url: str) -> list[str]:
    """Same-origin links whose path or anchor text suggests a contact page."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    parsed_root = urlparse(root_url)
    root_key = _host_key(parsed_root.netloc)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urljoin(root_url + "/", href)
        p = urlparse(abs_url)
        if p.scheme not in ("http", "https"):
            continue
        if _host_key(p.netloc) != root_key:
            continue
        path_l = (p.path or "").lower()
        text_l = (a.get_text() or "").strip().lower()
        blob = f"{path_l} {text_l}"
        score = sum(3 for kw in _LINK_KEYWORDS if kw in blob)
        if score == 0:
            continue
        norm = abs_url.split("#")[0].rstrip("/")
        if norm in seen:
            continue
        seen.add(norm)
        scored.append((score, norm))

    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:_MAX_DISCOVERED_LINKS]]


def _site_domain_for_scoring(website_url: str) -> str | None:
    n = normalize_http_url(website_url)
    if not n:
        return None
    host = (urlparse(n).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def pick_best_email(emails: list[str], website: str) -> str | None:
    """
    Pick the most likely business contact email from extracted candidates.
    """
    if not emails:
        return None
    site_dom = _site_domain_for_scoring(website)
    prefixes = (
        "contacto", "contact", "info", "hola", "hello", "ventas", "sales",
        "comercial", "admin", "oficina", "recepcion",
    )
    public_hosts = (
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
        "live.com", "msn.com", "proton.me", "protonmail.com",
    )

    def score_tuple(addr: str) -> tuple[int, int, str]:
        a = addr.strip()
        if "@" not in a:
            return (-999, 0, a)
        local, _, dom = a.partition("@")
        dom_l = dom.lower()
        local_l = local.lower()
        pfx = 0
        for i, pr in enumerate(prefixes):
            if local_l == pr or local_l.startswith(pr + "."):
                pfx = 10 + (len(prefixes) - i)
                break
        dom_match = 0
        if site_dom and (dom_l == site_dom or dom_l.endswith("." + site_dom)):
            dom_match = 5
        pen = 0
        if site_dom and any(dom_l == ph or dom_l.endswith("." + ph) for ph in public_hosts):
            pen = 3
        return (pfx + dom_match - pen, -len(a), a.lower())

    return max(emails, key=lambda e: score_tuple(e))


def _playwright_fetch_sync(url: str) -> str:
    """Blocking fetch via Chromium (optional dependency)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            return page.content() or ""
        finally:
            browser.close()


async def _fetch_page_playwright(url: str) -> str:
    if not settings.email_scraper_use_playwright:
        return ""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _playwright_fetch_sync(url))
    except ImportError:
        logger.warning("EMAIL_SCRAPER_USE_PLAYWRIGHT=1 but playwright is not installed")
        return ""
    except Exception as exc:
        logger.debug("playwright fetch failed for %s: %s", url, exc)
        return ""


async def find_email_in_website(url: str) -> list[str]:
    """
    Visit a business website and extract email addresses.

    Acquires a proxy via proxy_manager for each HTTP request.

    Strategy:
    1. Try provided URL, then site root
    2. Follow same-origin contact-like links (limited)
    3. Try common contact paths
    4. Optional: Playwright on root if enabled and still empty

    Returns deduplicated list of emails (unordered; use pick_best_email).
    """
    normalized = normalize_http_url(url)
    if not normalized:
        return []

    parsed = urlparse(normalized)
    root_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    target_url = normalized.rstrip("/")

    first_candidates = [target_url]
    if root_url != target_url:
        first_candidates.append(root_url)

    n_requests = 0
    _NO_PROXY = "__NO_PROXY__"

    async def _fetch_one(page_url: str) -> str:
        nonlocal n_requests
        if n_requests >= _MAX_FETCH_REQUESTS:
            return ""
        n_requests += 1
        proxy = await proxy_manager.wait_for_available()
        if proxy is None and proxy_manager._stats:
            logger.warning("find_email: no proxy available for %s", page_url)
            return _NO_PROXY
        html = await _fetch_page(page_url, proxy)
        if html:
            await proxy_manager.report_success(proxy)
        return html

    root_page_html = ""

    for candidate in first_candidates:
        html = await _fetch_one(candidate)
        if html == _NO_PROXY:
            return []
        if candidate.rstrip("/") == root_url.rstrip("/"):
            root_page_html = html
        emails = _extract_emails(html)
        if emails:
            return emails

    if not root_page_html and n_requests < _MAX_FETCH_REQUESTS:
        # Root may not have been fetched if target == root but fetch failed empty
        pass

    for extra_url in _discover_contact_link_urls(root_page_html, root_url):
        if n_requests >= _MAX_FETCH_REQUESTS:
            break
        html = await _fetch_one(extra_url)
        if html == _NO_PROXY:
            return []
        emails = _extract_emails(html)
        if emails:
            return emails

    for path in _CONTACT_PATHS:
        if n_requests >= _MAX_FETCH_REQUESTS:
            break
        html = await _fetch_one(root_url + path)
        if html == _NO_PROXY:
            return []
        emails = _extract_emails(html)
        if emails:
            return emails

    if settings.email_scraper_use_playwright:
        pw_html = await _fetch_page_playwright(root_url)
        if pw_html:
            emails = _extract_emails(pw_html)
            if emails:
                return emails

    logger.debug("find_email: no emails found for %s", root_url)
    return []
