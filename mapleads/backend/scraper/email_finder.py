import asyncio
import logging
import re

import curl_cffi.requests as curl_requests

from backend.proxy.proxy_manager import proxy_manager

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_PATTERNS = [
    "@2x", "@3x", "sentry", "example.com", "schema.org",
    "wixpress.com", "w3.org", "demolink.org", "your-domain",
    "yourdomain", "domain.com", "email@",
]

_CONTACT_PATHS = ["/contacto", "/contact", "/about", "/sobre-nosotros", "/contactanos"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


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


def _extract_emails(html: str) -> list[str]:
    """Extract and filter emails from HTML string."""
    found = EMAIL_REGEX.findall(html)
    clean = [
        e for e in found
        if not any(skip in e.lower() for skip in _SKIP_PATTERNS)
    ]
    return list(set(clean))


async def find_email_in_website(url: str) -> list[str]:
    """
    Visit a business website and extract email addresses.

    Acquires a proxy via proxy_manager for each HTTP request.

    Strategy:
    1. Try homepage
    2. If no emails, try common contact page paths
    3. Filter false positives (image refs, JS libs, etc.)

    Returns deduplicated list of emails. Empty list if none found.
    """
    if not url:
        return []

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    base_url = url.rstrip("/")

    # 1. Try homepage
    proxy = await proxy_manager.wait_for_available()
    if proxy is None and proxy_manager._stats:
        logger.warning("find_email: no proxy available for %s", base_url)
        return []

    html = await _fetch_page(base_url, proxy)
    emails = _extract_emails(html)

    if emails:
        await proxy_manager.report_success(proxy)
        logger.debug("find_email: %d emails on homepage of %s", len(emails), base_url)
        return emails

    # 2. Try contact pages (each gets a fresh proxy slot)
    for path in _CONTACT_PATHS:
        proxy = await proxy_manager.wait_for_available()
        if proxy is None and proxy_manager._stats:
            break

        html = await _fetch_page(base_url + path, proxy)
        emails = _extract_emails(html)
        if emails:
            await proxy_manager.report_success(proxy)
            logger.debug("find_email: %d emails on %s%s", len(emails), base_url, path)
            return emails

    logger.debug("find_email: no emails found for %s", base_url)
    return []
