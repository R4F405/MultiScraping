"""
Lightweight email discovery from business websites.

Covers: homepage, common /contact paths, same-origin links that look like contact
pages, mailto/data attributes, simple obfuscations. Does NOT cover: emails only
in images, sites behind heavy bot/Captcha, or JS-only SPAs without server HTML
(use optional Playwright when installed + EMAIL_SCRAPER_USE_PLAYWRIGHT=1).
"""

import asyncio
import json
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
    "/soporte",
    "/support",
    "/atencion-al-cliente",
    "/atencion-cliente",
    "/customer-service",
    "/legal",
    "/aviso-legal",
    "/privacy",
    "/politica-privacidad",
]

_LINK_KEYWORDS = (
    "contact", "contacto", "kontakt", "about", "nosotros", "equipo",
    "impressum", "ubicacion", "ubication", "location", "legal", "aviso",
)

_DEEP_LINK_KEYWORDS = (
    "contact", "contacto", "about", "nosotros", "equipo",
    "legal", "aviso", "privacy", "privacidad", "cookies",
    "terminos", "condiciones", "faq", "soporte", "support",
)

# Hard cap on HTTP fetches per business (proxies + politeness).
_MAX_FETCH_REQUESTS = 12
_MAX_DISCOVERED_LINKS = 6
_MAX_DEEP_DISCOVERED_LINKS = 12

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

_NON_BUSINESS_HOSTS = _SOCIAL_HOSTS | {
    # Link-in-bio / profile hubs
    "linktr.ee",
    "beacons.ai",
    "solo.to",
    "bio.site",
    # Generic profile/catalog/listing platforms (usually not business-owned sites)
    "google.com",
    "forms.gle",
    "docs.google.com",
    "g.page",
    "goo.gl",
    "maps.google.com",
    "tripadvisor.com",
    "yelp.com",
    "trustpilot.com",
    "foursquare.com",
    "páginasamarillas.es",
    "paginasamarillas.es",
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

    return host in _NON_BUSINESS_HOSTS or any(host.endswith("." + h) for h in _NON_BUSINESS_HOSTS)


async def _fetch_page(url: str, proxy: str | None) -> tuple[str, bool, str | None]:
    """
    Fetch a URL and return (html, used_proxy_successfully).

    If a proxy attempt fails (network/tunnel/non-200), retry once with direct
    connection. This keeps email extraction resilient when proxy pools are flaky.
    """

    async def _attempt(proxies: dict[str, str] | None, *, via_proxy: bool) -> tuple[str, bool, str | None]:
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
                return response.text, via_proxy, None
            logger.debug(
                "_fetch_page %s via %s → status %d",
                url,
                "proxy" if via_proxy else "direct",
                response.status_code,
            )
            return "", False, f"status_{response.status_code}"
        except Exception as exc:
            err = str(exc).lower()
            reason = "network_error"
            if any(k in err for k in ("ssl", "tls", "certificate", "secure")):
                reason = "ssl_error"
            elif "timeout" in err:
                reason = "timeout"
            logger.debug(
                "_fetch_page error for %s via %s: %s",
                url,
                "proxy" if via_proxy else "direct",
                exc,
            )
            return "", False, reason

    if proxy:
        proxied_html, used_proxy, proxy_reason = await _attempt({"https": proxy, "http": proxy}, via_proxy=True)
        if proxied_html:
            return proxied_html, used_proxy, None
        logger.debug("_fetch_page: proxy failed for %s, retrying direct", url)

    direct_html, used_proxy, direct_reason = await _attempt(None, via_proxy=False)
    if direct_html:
        return direct_html, used_proxy, None
    final_reason = direct_reason or (proxy_reason if proxy else None)
    return direct_html, used_proxy, final_reason


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


def _extract_jsonld_emails(source: str) -> list[str]:
    """
    Extract emails from JSON-LD blocks (schema.org Organization/LocalBusiness, etc.).
    """
    if not source:
        return []

    found: list[str] = []
    try:
        soup = BeautifulSoup(source, "html.parser")
    except Exception:
        return []

    def _walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k.lower() == "email" and isinstance(v, str):
                    for e in EMAIL_REGEX.findall(v):
                        found.append(e)
                else:
                    _walk(v)
        elif isinstance(value, list):
            for item in value:
                _walk(item)
        elif isinstance(value, str):
            # Some sites embed full strings containing email-like values.
            for e in EMAIL_REGEX.findall(value):
                found.append(e)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        payload = script.string or script.get_text() or ""
        payload = payload.strip()
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except Exception:
            # Invalid JSON-LD is common; ignore quietly.
            continue
        _walk(parsed)

    return found


def _extract_js_concat_emails(source: str) -> list[str]:
    """
    Catch simple inline JS concatenations like:
    "info" + "@" + "empresa.com" or 'hola'+'@'+'dominio.es'
    """
    out: list[str] = []
    pattern = re.compile(
        r"""["']([a-zA-Z0-9._%+\-]+)["']\s*\+\s*["']@["']\s*\+\s*["']([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})["']"""
    )
    for m in pattern.finditer(source or ""):
        candidate = f"{m.group(1)}@{m.group(2)}"
        if EMAIL_REGEX.fullmatch(candidate):
            out.append(candidate.lower())
    return out


def _extract_emails(page_html: str) -> list[str]:
    """Extract and filter emails from HTML string."""
    source = html_lib.unescape(page_html or "")
    found = list(EMAIL_REGEX.findall(source))
    found.extend(_extract_obfuscated_emails(source))
    found.extend(_extract_data_attribute_emails(source))
    found.extend(_extract_jsonld_emails(source))
    found.extend(_extract_js_concat_emails(source))
    # Also catch explicit mailto links, including URL-encoded addresses.
    mailto_hits = re.findall(r"mailto:([^\"'?\s>]+)", source, flags=re.IGNORECASE)
    found.extend(unquote(hit) for hit in mailto_hits if hit)
    normalized: list[str] = []
    for e in found:
        candidate = unquote((e or "").strip()).strip(" \t\r\n<>()[]{}'\";,")
        candidate = re.sub(r"^(?:%20)+", "", candidate, flags=re.IGNORECASE)
        if not candidate:
            continue
        if not EMAIL_REGEX.fullmatch(candidate):
            continue
        if any(skip in candidate.lower() for skip in _SKIP_PATTERNS):
            continue
        normalized.append(candidate.lower())
    return list(set(normalized))


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
        aria_l = str(a.get("aria-label") or "").strip().lower()
        title_l = str(a.get("title") or "").strip().lower()
        class_l = " ".join(str(c).lower() for c in (a.get("class") or []))
        id_l = str(a.get("id") or "").strip().lower()
        blob = f"{path_l} {text_l} {aria_l} {title_l} {class_l} {id_l}"
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


def _discover_deep_link_urls(html: str, root_url: str) -> list[str]:
    """
    Discover additional same-origin pages that may contain contact metadata
    (privacy/legal/contact pages, form pages, etc.).
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []

    root_key = _host_key(urlparse(root_url).netloc)
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
        if any(ext in path_l for ext in (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip")):
            continue

        text_l = (a.get_text() or "").strip().lower()
        aria_l = str(a.get("aria-label") or "").strip().lower()
        title_l = str(a.get("title") or "").strip().lower()
        blob = f"{path_l} {text_l} {aria_l} {title_l}"

        score = 0
        score += sum(2 for kw in _DEEP_LINK_KEYWORDS if kw in blob)
        if "/contact" in path_l or "contacto" in path_l:
            score += 4
        if "privacy" in path_l or "privacidad" in path_l or "legal" in path_l:
            score += 3
        if len(path_l.split("/")) <= 4:
            score += 1
        if score == 0:
            continue

        norm = abs_url.split("#")[0].rstrip("/")
        if norm in seen:
            continue
        seen.add(norm)
        scored.append((score, norm))

    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:_MAX_DEEP_DISCOVERED_LINKS]]


def _detect_form_vendor(html: str) -> str | None:
    src = (html or "").lower()
    if not src:
        return None
    if "fluentform" in src or "fluent-form" in src:
        return "fluentform"
    if "wpcf7" in src or "contact-form-7" in src:
        return "contact-form-7"
    if "hubspot" in src or "hs-form" in src:
        return "hubspot"
    if "typeform" in src:
        return "typeform"
    if "formspree" in src:
        return "formspree"
    if "<form" in src:
        return "html_form"
    return None


def _form_vendor_rank(vendor: str | None) -> int:
    """
    Rank form vendor specificity so generic html_form can be upgraded
    when a more specific provider is discovered in later pages.
    """
    if not vendor:
        return 0
    if vendor == "html_form":
        return 1
    # known specific providers
    return 2


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
    low_quality_locals = ("noreply", "no-reply", "donotreply", "do-not-reply")

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
        if any(token in local_l for token in low_quality_locals):
            pen += 6
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


async def find_email_in_website_diagnostics(url: str) -> dict:
    """
    Visit a business website and extract email addresses.

    Acquires a proxy via proxy_manager for each HTTP request.

    Strategy:
    1. Try provided URL, then site root
    2. Follow same-origin contact-like links (limited)
    3. Try common contact paths
    4. Optional: Playwright on root if enabled and still empty

    Returns diagnostic payload with extracted emails and failure context.
    """
    normalized = normalize_http_url(url)
    if not normalized:
        return {"emails": [], "reason": "invalid_url", "visited_urls": [], "fetch_failures": []}

    parsed = urlparse(normalized)
    root_url = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    target_url = normalized.rstrip("/")

    first_candidates = [target_url]
    if root_url != target_url:
        first_candidates.append(root_url)

    n_requests = 0
    _NO_PROXY = "__NO_PROXY__"
    force_direct = settings.email_scraper_force_direct
    visited_urls: list[str] = []
    fetch_failures: list[dict] = []
    form_vendor: str | None = None

    async def _fetch_one(page_url: str) -> str:
        nonlocal n_requests
        if n_requests >= _MAX_FETCH_REQUESTS:
            return ""
        n_requests += 1
        visited_urls.append(page_url)
        proxy = None
        if not force_direct:
            proxy = await proxy_manager.wait_for_available()
            if proxy is None and proxy_manager._stats:
                logger.warning("find_email: no proxy available for %s", page_url)
                return _NO_PROXY
        html, used_proxy, reason = await _fetch_page(page_url, proxy)
        if not html and reason:
            fetch_failures.append({"url": page_url, "reason": reason})
        if html and used_proxy:
            await proxy_manager.report_success(proxy)
        nonlocal form_vendor
        if html:
            detected_vendor = _detect_form_vendor(html)
            if _form_vendor_rank(detected_vendor) > _form_vendor_rank(form_vendor):
                form_vendor = detected_vendor
        return html

    root_page_html = ""

    for candidate in first_candidates:
        html = await _fetch_one(candidate)
        if html == _NO_PROXY:
            return {"emails": [], "reason": "no_proxy_available", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}
        if candidate.rstrip("/") == root_url.rstrip("/"):
            root_page_html = html
        emails = _extract_emails(html)
        if emails:
            return {"emails": emails, "reason": "found", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}

    if not root_page_html and n_requests < _MAX_FETCH_REQUESTS:
        # Root may not have been fetched if target == root but fetch failed empty
        pass

    for extra_url in _discover_contact_link_urls(root_page_html, root_url):
        if n_requests >= _MAX_FETCH_REQUESTS:
            break
        html = await _fetch_one(extra_url)
        if html == _NO_PROXY:
            return {"emails": [], "reason": "no_proxy_available", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}
        emails = _extract_emails(html)
        if emails:
            return {"emails": emails, "reason": "found", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}

    for path in _CONTACT_PATHS:
        if n_requests >= _MAX_FETCH_REQUESTS:
            break
        html = await _fetch_one(root_url + path)
        if html == _NO_PROXY:
            return {"emails": [], "reason": "no_proxy_available", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}
        emails = _extract_emails(html)
        if emails:
            return {"emails": emails, "reason": "found", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}

    # Deep mode: broaden crawl to same-origin legal/privacy/about pages.
    for deep_url in _discover_deep_link_urls(root_page_html, root_url):
        if n_requests >= _MAX_FETCH_REQUESTS:
            break
        html = await _fetch_one(deep_url)
        if html == _NO_PROXY:
            return {"emails": [], "reason": "no_proxy_available", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}
        emails = _extract_emails(html)
        if emails:
            return {"emails": emails, "reason": "found", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}

    if settings.email_scraper_use_playwright:
        pw_html = await _fetch_page_playwright(root_url)
        if pw_html:
            emails = _extract_emails(pw_html)
            if emails:
                return {"emails": emails, "reason": "found_via_playwright", "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}

    logger.debug("find_email: no emails found for %s", root_url)
    if any(f.get("reason") == "ssl_error" for f in fetch_failures):
        reason = "ssl_or_insecure_site"
    elif fetch_failures:
        reason = "unreachable_or_blocked"
    else:
        reason = "no_visible_email"
    return {"emails": [], "reason": reason, "visited_urls": visited_urls, "fetch_failures": fetch_failures, "form_vendor": form_vendor}


async def find_email_in_website(url: str) -> list[str]:
    result = await find_email_in_website_diagnostics(url)
    return result.get("emails", [])
