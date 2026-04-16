import asyncio
import logging
import math
import random

import curl_cffi.requests as curl_requests

from backend.config.settings import settings
from backend.proxy.proxy_manager import proxy_manager
from backend.scraper.maps_parser import (
    extract_preview_url_from_html,
    hex_cid_to_decimal,
    parse_cids_from_maps_response,
    parse_maps_response,
    parse_place_from_html,
    parse_place_from_preview_json,
)

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.google.com/search"
_MAPS_PLACE_URL = "https://www.google.com/maps"

_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_PLACE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

# Bypass Google GDPR consent page ("Antes de ir a Google Maps").
# Without these cookies, Google redirects place page requests to the consent
# screen and returns an HTML page whose <title> is "Antes de ir a Google Maps".
_GOOGLE_CONSENT_COOKIES = {
    "CONSENT": "YES+cb.20210720-07-p0.en+FX+410",
    "SOCS": "CAESHAgBEhJnd3NfMjAyNDA5MTAtMF9SQzIaAnplIAEaBgiA3pO1Bg",
}


def _radius_to_zoom(radius_km: float) -> int:
    """Convert search radius in km to an approximate Google Maps zoom level."""
    return max(9, round(15 - math.log2(max(1.0, radius_km))))


_MAPS_PREVIEW_URL = "https://www.google.com/maps/preview/place"

_PREVIEW_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "*/*",
    "Referer": "https://www.google.com/maps/",
}


class MapsFetchError(RuntimeError):
    """Error operativo (proxy/red/bloqueo) al consultar Google Maps."""

    def __init__(self, message: str, *, kind: str = "unknown", retryable: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


async def _fetch_place_details(hex_cid: str) -> dict | None:
    """
    Fetch full business details for a single place by its hex CID.

    Two-step process:
      1. Fetch the Google Maps place HTML page (/maps?cid={decimal}).
         The response embeds a <link> element pointing to the JSON preview API
         with the exact business coordinates embedded in the pb parameter.
      2. Fetch the preview/place JSON endpoint → full structured data
         (name, address, phone, website, rating, category).

    Falls back to title extraction from the HTML if the preview step fails.
    Returns a business dict or None on failure.
    """
    decimal = hex_cid_to_decimal(hex_cid)
    if not decimal:
        logger.debug("_fetch_place_details: invalid hex_cid %s", hex_cid)
        return None

    proxy = await proxy_manager.wait_for_available()
    if proxy is None and proxy_manager._stats:
        logger.warning("_fetch_place_details: no proxy available for cid=%s", hex_cid)
        return None

    proxies = {"https": proxy, "http": proxy} if proxy else None

    # --- Step 1: Fetch place page HTML to get the preview URL ---
    try:
        loop = asyncio.get_running_loop()
        html_response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                _MAPS_PLACE_URL,
                params={"cid": decimal, "hl": "es", "gl": "es"},
                headers=_PLACE_HEADERS,
                cookies=_GOOGLE_CONSENT_COOKIES,
                proxies=proxies,
                impersonate="chrome131",
                timeout=15,
                allow_redirects=True,
            ),
        )
    except Exception as exc:
        logger.debug("_fetch_place_details: HTML fetch error cid=%s: %s", hex_cid, exc)
        if proxy:
            await proxy_manager.report_error(proxy)
        return None

    if html_response.status_code == 429:
        await proxy_manager.report_error(proxy)
        return None
    if html_response.status_code != 200:
        logger.debug("_fetch_place_details: HTML status %d for cid=%s",
                     html_response.status_code, hex_cid)
        await proxy_manager.report_error(proxy)
        return None
    if "Antes de ir a Google Maps" in html_response.text or "Before you continue" in html_response.text:
        logger.warning("_fetch_place_details: consent page for cid=%s — check cookies", hex_cid)
        return None

    html = html_response.text
    preview_url = extract_preview_url_from_html(html)

    if not preview_url:
        # Last-resort fallback: try to get at least the name from the title
        logger.debug("_fetch_place_details: no preview link for cid=%s, using title fallback", hex_cid)
        return parse_place_from_html(html, hex_cid)

    # --- Step 2: Fetch the preview/place JSON endpoint ---
    try:
        preview_proxy = await proxy_manager.wait_for_available()
        if preview_proxy is None and proxy_manager._stats:
            return None
        preview_proxies = {"https": preview_proxy, "http": preview_proxy} if preview_proxy else None

        json_response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                preview_url,
                headers=_PREVIEW_HEADERS,
                cookies=_GOOGLE_CONSENT_COOKIES,
                proxies=preview_proxies,
                impersonate="chrome131",
                timeout=15,
            ),
        )
    except Exception as exc:
        logger.debug("_fetch_place_details: preview fetch error cid=%s: %s", hex_cid, exc)
        return parse_place_from_html(html, hex_cid)

    if json_response.status_code != 200:
        logger.debug("_fetch_place_details: preview status %d for cid=%s",
                     json_response.status_code, hex_cid)
        return parse_place_from_html(html, hex_cid)

    raw = json_response.text
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip("\n")

    business = parse_place_from_preview_json(raw, hex_cid)
    if business:
        await proxy_manager.report_success(proxy)
        logger.debug("_fetch_place_details: resolved '%s' via preview JSON", business.get("business_name"))
    else:
        logger.debug("_fetch_place_details: preview JSON parse failed for cid=%s, using title fallback", hex_cid)
        business = parse_place_from_html(html, hex_cid)

    return business


async def _fetch_cid_list(
    query: str,
    location: str,
    start: int = 0,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 10.0,
) -> list[str]:
    """
    Fetch the tbm=map JSON response and extract hex CIDs.

    When lat/lng are provided, adds the ``ll`` parameter to center the Google
    Maps search on the given coordinates with a zoom level derived from radius_km.

    Returns up to 20 hex CID strings. Empty list on error.
    """
    proxy = await proxy_manager.wait_for_available()
    if proxy is None and proxy_manager._stats:
        logger.warning("_fetch_cid_list: no proxy available, skipping")
        return []

    # Build query string: combine keyword + location text (if any)
    search_query = f"{query} {location}".strip() if location else query

    params: dict[str, str] = {
        "tbm": "map",
        "hl": "es",
        "gl": "es",
        "q": search_query,
        "num": "20",
        "start": str(start),
    }

    # Pin the map to explicit coordinates when available
    if lat is not None and lng is not None:
        zoom = _radius_to_zoom(radius_km)
        params["ll"] = f"@{lat},{lng},{zoom}z"
        logger.debug(
            "_fetch_cid_list: using coords lat=%.5f lng=%.5f zoom=%d radius=%.1fkm",
            lat, lng, zoom, radius_km,
        )

    proxies = {"https": proxy, "http": proxy} if proxy else None

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                _SEARCH_URL,
                params=params,
                headers=_SEARCH_HEADERS,
                cookies=_GOOGLE_CONSENT_COOKIES,
                proxies=proxies,
                impersonate="chrome131",
                timeout=20,
            ),
        )

        if response.status_code == 429:
            await proxy_manager.report_error(proxy)
            raise MapsFetchError("Maps rate limited (429)", kind="rate_limited", retryable=True)

        if response.status_code != 200:
            logger.warning("_fetch_cid_list: status %d for '%s'", response.status_code, search_query)
            await proxy_manager.report_error(proxy)
            raise MapsFetchError(
                f"Maps unexpected status {response.status_code}",
                kind="bad_status",
                retryable=True,
            )

        raw = response.text
        if raw.startswith(")]}'"):
            raw = raw[4:].lstrip("\n")

        # Try old full-detail format first (FORMAT A)
        businesses = parse_maps_response(raw)
        if businesses:
            await proxy_manager.report_success(proxy)
            logger.debug("_fetch_cid_list: FORMAT A hit — %d businesses directly", len(businesses))
            # Return a special sentinel so search_maps knows to use these directly
            return [("__FORMAT_A__", businesses)]  # type: ignore[list-item]

        # New CID-only format (FORMAT B)
        cids = parse_cids_from_maps_response(raw)
        if cids:
            await proxy_manager.report_success(proxy)
            logger.debug("_fetch_cid_list: FORMAT B — %d CIDs for '%s' start=%d", len(cids), search_query, start)
        else:
            logger.debug("_fetch_cid_list: no data in either format for '%s'", search_query)
            # Puede ser 'sin resultados' real. No lo tratamos como error operativo.

        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))
        return cids

    except Exception as exc:
        logger.error("_fetch_cid_list('%s', start=%d): error=%s", search_query, start, exc, exc_info=True)
        if isinstance(exc, MapsFetchError):
            raise
        if proxy:
            await proxy_manager.report_error(proxy)
        raise MapsFetchError(str(exc), kind="connection", retryable=True) from exc


async def search_maps(
    query: str,
    location: str,
    start: int = 0,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 10.0,
) -> list[dict]:
    """
    Return normalized business dicts for a single page of Google Maps results.

    Two-step process:
      1. Fetch CID list from tbm=map (FORMAT B) or full data directly (FORMAT A)
      2. If FORMAT B: fetch place details for each CID in parallel

    Args:
        query: What to search (e.g. "dentistas")
        location: Where to search as text (e.g. "Valencia"), used when no coords
        start: Pagination offset (0, 20, 40…)
        lat: Latitude to center the search map
        lng: Longitude to center the search map
        radius_km: Search radius in km (used for zoom level)

    Returns:
        List of business dicts. Empty list on error or no proxy available.
    """
    cid_list = await _fetch_cid_list(query, location, start=start, lat=lat, lng=lng, radius_km=radius_km)

    if not cid_list:
        return []

    # FORMAT A fast-path: full businesses returned directly
    if len(cid_list) == 1 and isinstance(cid_list[0], tuple) and cid_list[0][0] == "__FORMAT_A__":
        return cid_list[0][1]

    # FORMAT B: fetch details for each CID concurrently
    logger.info("search_maps: fetching details for %d places (FORMAT B)…", len(cid_list))
    semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

    async def fetch_with_semaphore(cid: str) -> dict | None:
        async with semaphore:
            result = await _fetch_place_details(cid)
            await asyncio.sleep(random.uniform(
                settings.request_delay_min * 0.5,
                settings.request_delay_max * 0.5,
            ))
            return result

    tasks = [fetch_with_semaphore(cid) for cid in cid_list]
    results = await asyncio.gather(*tasks)

    businesses = [b for b in results if b is not None and b.get("business_name")]
    logger.debug("search_maps('%s %s', start=%d): %d/%d places resolved",
                 query, location, start, len(businesses), len(cid_list))
    return businesses


async def search_maps_paginated(
    query: str,
    location: str,
    max_results: int = 50,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 10.0,
) -> list[dict]:
    """
    Paginate through Google Maps results up to max_results.

    Stops early if a page returns fewer than 20 results (no more pages).
    """
    all_results: list[dict] = []
    start = 0

    while len(all_results) < max_results:
        batch: list[dict] = []
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                batch = await search_maps(query, location, start=start, lat=lat, lng=lng, radius_km=radius_km)
                last_error = None
                break
            except MapsFetchError as exc:
                last_error = exc
                if not exc.retryable:
                    break
                backoff = 0.6 * (2**attempt) + random.uniform(0.0, 0.25)
                logger.warning(
                    "search_maps_paginated: retry %d/3 after MapsFetchError(kind=%s): %s",
                    attempt + 1,
                    getattr(exc, "kind", "unknown"),
                    exc,
                )
                await asyncio.sleep(backoff)

        if last_error is not None and not batch:
            raise last_error
        if not batch:
            break

        all_results.extend(batch)
        start += 20

        if len(batch) < 20:
            break

    return all_results[:max_results]
