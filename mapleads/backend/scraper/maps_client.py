"""
HTTP client for Google Maps internal search (tbm=map).

Primary flow (1 request per page of 20 full results):
  GET /search?tbm=map&q={query}&pb={PB}
  The ``pb`` protobuf-style parameter pins the map viewport (altitude/lng/lat)
  and the result offset (``8i{offset}``), enabling real pagination — Google
  ignores ``start``/``num`` on tbm=map. The response embeds full business
  data (name, address, phone, website, rating, CID, coordinates) inline.

Viewport resolution:
  - Explicit lat/lng from the caller → altitude derived from radius_km.
  - Otherwise a one-off bootstrap request (plain ``q=``) geocodes the query
    and returns the viewport Google chose (cached per location).

Fallback flow (first page only, 2 extra requests per place):
  plain ``q=`` → hex CIDs → /maps?cid= HTML → /maps/preview/place JSON.
  Kept for resilience if Google ever rejects the ``pb`` template.
"""

import asyncio
import logging
import math
import random

import curl_cffi.requests as curl_requests

from backend.config.settings import settings
from backend.proxy.proxy_manager import proxy_manager
from backend.scraper.maps_parser import (
    extract_preview_url_from_html,
    extract_viewport_from_maps_response,
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
    **_SEARCH_HEADERS,
    "Referer": "https://www.google.com/",
}

_PREVIEW_HEADERS = {
    **_SEARCH_HEADERS,
    "Accept": "*/*",
    "Referer": "https://www.google.com/maps/",
}

# Bypass Google GDPR consent page ("Antes de ir a Google Maps").
# Without these cookies, Google redirects requests to the consent screen.
_GOOGLE_CONSENT_COOKIES = {
    "CONSENT": "YES+cb.20210720-07-p0.en+FX+410",
    "SOCS": "CAESHAgBEhJnd3NfMjAyNDA5MTAtMF9SQzIaAnplIAEaBgiA3pO1Bg",
}

_MAPS_PREVIEW_URL = "https://www.google.com/maps/preview/place"

# pb template for paginated tbm=map searches (reverse-engineered from the
# Google Maps web app; same template documented by SerpApi). Placeholders:
#   {altitude} — viewport altitude in meters (see _zoom_to_altitude)
#   {lng}/{lat} — viewport center
#   {offset}   — result offset for pagination (0, 20, 40…), segment 8i
# The 7i20 segment requests 20 results per page.
_PB_TEMPLATE = (
    "!4m8!1m3!1d{altitude}!2d{lng}!3d{lat}!3m2!1i1024!2i768!4f13.1!7i20!8i{offset}!10b1!12m25"
    "!1m1!18b1!2m3!5m1!6e2!20e3!6m16!4b1!23b1!26i1!27i1!41i2!45b1!49b1!63m0!67b1!73m0!74i150000"
    "!75b1!89b1!105b1!109b1!110m0!10b1!16b1!19m4!2m3!1i360!2i120!4i8!20m65!2m2!1i203!2i100!3m2"
    "!2i4!5b1!6m6!1m2!1i86!2i86!1m2!1i408!2i240!7m50!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2"
    "!2b0!3e3!1m3!1e3!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e3!2b1!3e2!1m3!1e10!2b0!3e3!1m3!1e10!2b1"
    "!3e2!1m3!1e9!2b1!3e2!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!2b1!4b1!9b0!22m3"
    "!1s!2z!7e81!24m55!1m15!13m7!2b1!3b1!4b1!6i1!8b1!9b1!20b0!18m6!3b1!4b1!5b1!6b1!13b0!14b0"
    "!2b1!5m5!2b1!3b1!5b1!6b1!7b1!10m1!8e3!14m1!3b1!17b1!20m4!1e3!1e6!1e14!1e15!24b1!25b1!26b1"
    "!29b1!30m1!2b1!36b1!43b1!52b1!54m1!1b1!55b1!56m2!1b1!3b1!65m5!3m4!1m3!1m2!1i224!2i298!89b1"
    "!26m4!2m3!1i80!2i92!4i8!30m28!1m6!1m2!1i0!2i0!2m2!1i458!2i768!1m6!1m2!1i974!2i0!2m2!1i1024"
    "!2i768!1m6!1m2!1i0!2i0!2m2!1i1024!2i20!1m6!1m2!1i0!2i748!2m2!1i1024!2i768!34m16!2b1!3b1!4b1"
    "!6b1!8m4!1b1!3b1!4b1!6b1!9b1!12b1!14b1!20b1!23b1!25b1!26b1!37m1!1e81!42b1!46m1!1e9!47m0"
    "!49m1!3b1!50m53!1m49!2m7!1u3!4s!5e1!9s!10m2!3m1!1e1!2m7!1u2!4s!5e1!9s!10m2!2m1!1e1!2m7"
    "!1u16!4s!5e1!9s!10m2!16m1!1e1!2m7!1u16!4s!5e1!9s!10m2!16m1!1e2!3m11!1u16!2m4!1m2!16m1!1e1"
    "!2s!2m4!1m2!16m1!1e2!2s!3m1!1u2!3m1!1u3!4BIAE!2e2!3m1!3b1!59B!65m0!69i540"
)

_EARTH_RADIUS_M = 6371010
_TILE_SIZE = 256
_SCREEN_PIXEL_HEIGHT = 768

# Viewport fallback when geocoding fails: Spain-wide (the query text still
# contains the location, so Google recenters; the viewport only biases).
_DEFAULT_VIEWPORT = (40.4168, -3.7038, 6.0)  # lat, lng, zoom

# Bootstrap viewport cache: location text → (lat, lng, altitude)
_viewport_cache: dict[str, tuple[float, float, float]] = {}
_VIEWPORT_CACHE_MAX = 10000


class MapsFetchError(RuntimeError):
    """Error operativo (proxy/red/bloqueo) al consultar Google Maps."""

    def __init__(self, message: str, *, kind: str = "unknown", retryable: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


def _radius_to_zoom(radius_km: float) -> int:
    """Convert search radius in km to an approximate Google Maps zoom level."""
    return max(9, round(15 - math.log2(max(1.0, radius_km))))


def _zoom_to_altitude(zoom: float, lat: float) -> float:
    """Convert zoom level + latitude to the viewport altitude the pb expects."""
    return (27.3611 * _EARTH_RADIUS_M * _SCREEN_PIXEL_HEIGHT * math.cos(lat * math.pi / 180)) / (
        (2 ** zoom) * _TILE_SIZE
    )


def _curl_kwargs() -> dict:
    """
    Transport options for curl_cffi.

    MAPS_IMPERSONATE: browser TLS fingerprint profile (default chrome131);
    set to "none" to disable (e.g. dev environments whose egress proxy
    rejects impersonated ClientHellos). MAPS_CA_BUNDLE: custom CA path for
    TLS-intercepting proxies.
    """
    kwargs: dict = {}
    imp = (settings.maps_impersonate or "").strip()
    if imp and imp.lower() not in ("none", "off", "0", "false"):
        kwargs["impersonate"] = imp
    if settings.maps_ca_bundle:
        kwargs["verify"] = settings.maps_ca_bundle
    return kwargs


async def _http_get(url: str, *, params: dict, headers: dict, timeout: int = 20):
    """
    Proxy-managed GET against Google. Returns the curl_cffi response.

    Raises MapsFetchError on connection errors, 429 and unexpected statuses;
    reports proxy success/error to the proxy manager.
    """
    proxy = await proxy_manager.wait_for_available()
    if proxy is None and proxy_manager._stats:
        raise MapsFetchError("No proxy available", kind="no_proxy", retryable=True)

    proxies = {"https": proxy, "http": proxy} if proxy else None

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: curl_requests.get(
                url,
                params=params,
                headers=headers,
                cookies=_GOOGLE_CONSENT_COOKIES,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                **_curl_kwargs(),
            ),
        )
    except Exception as exc:
        if proxy:
            await proxy_manager.report_error(proxy)
        raise MapsFetchError(str(exc), kind="connection", retryable=True) from exc

    if response.status_code == 429:
        await proxy_manager.report_error(proxy)
        raise MapsFetchError("Maps rate limited (429)", kind="rate_limited", retryable=True)

    if response.status_code != 200:
        await proxy_manager.report_error(proxy)
        raise MapsFetchError(
            f"Maps unexpected status {response.status_code}",
            kind="bad_status",
            retryable=True,
        )

    text = response.text
    if "Antes de ir a Google" in text[:4000] or "Before you continue" in text[:4000]:
        await proxy_manager.report_error(proxy)
        raise MapsFetchError("Google consent page — check consent cookies", kind="consent", retryable=True)
    if "unusual traffic" in text[:4000] or "/sorry/" in str(response.url):
        await proxy_manager.report_error(proxy)
        raise MapsFetchError("Google anti-bot interstitial (sorry page)", kind="blocked", retryable=True)

    await proxy_manager.report_success(proxy)
    return response


def _strip_xssi(raw: str) -> str:
    if raw.startswith(")]}'"):
        return raw[4:].lstrip("\n")
    return raw


def _cache_viewport(key: str, viewport: tuple[float, float, float]) -> None:
    if len(_viewport_cache) >= _VIEWPORT_CACHE_MAX:
        _viewport_cache.pop(next(iter(_viewport_cache)))
    _viewport_cache[key] = viewport


async def _resolve_viewport(
    search_query: str,
    location: str,
    lat: float | None,
    lng: float | None,
    radius_km: float,
) -> tuple[float, float, float]:
    """
    Resolve the (lat, lng, altitude) viewport for a search.

    Explicit coordinates win; otherwise geocode via a bootstrap plain-q
    request (cached per location text). Falls back to a country-wide
    viewport — the query text still carries the location for Google.
    """
    if lat is not None and lng is not None:
        zoom = _radius_to_zoom(radius_km)
        return lat, lng, _zoom_to_altitude(zoom, lat)

    cache_key = (location or search_query).strip().lower()
    cached = _viewport_cache.get(cache_key)
    if cached:
        return cached

    try:
        response = await _http_get(
            _SEARCH_URL,
            params={"tbm": "map", "hl": "es", "gl": "es", "q": search_query},
            headers=_SEARCH_HEADERS,
        )
        viewport = extract_viewport_from_maps_response(_strip_xssi(response.text))
        if viewport:
            vp_lat, vp_lng, altitude = viewport
            result = (vp_lat, vp_lng, altitude)
            _cache_viewport(cache_key, result)
            logger.debug(
                "_resolve_viewport: geocoded '%s' → lat=%.5f lng=%.5f alt=%.0f",
                search_query, vp_lat, vp_lng, altitude,
            )
            return result
        logger.warning("_resolve_viewport: no viewport in bootstrap response for '%s'", search_query)
    except MapsFetchError as exc:
        logger.warning("_resolve_viewport: bootstrap failed for '%s': %s", search_query, exc)

    d_lat, d_lng, d_zoom = _DEFAULT_VIEWPORT
    return d_lat, d_lng, _zoom_to_altitude(d_zoom, d_lat)


async def _fetch_place_details(hex_cid: str) -> dict | None:
    """
    Fallback flow: fetch full details for a single place by hex CID.

    1. /maps?cid={decimal} HTML → embedded /maps/preview/place URL
    2. preview endpoint JSON → full structured data
    Falls back to <title> extraction if the preview step fails.
    """
    decimal = hex_cid_to_decimal(hex_cid)
    if not decimal:
        logger.debug("_fetch_place_details: invalid hex_cid %s", hex_cid)
        return None

    try:
        html_response = await _http_get(
            _MAPS_PLACE_URL,
            params={"cid": decimal, "hl": "es", "gl": "es"},
            headers=_PLACE_HEADERS,
            timeout=15,
        )
    except MapsFetchError as exc:
        logger.debug("_fetch_place_details: HTML fetch failed cid=%s: %s", hex_cid, exc)
        return None

    html = html_response.text
    preview_url = extract_preview_url_from_html(html)

    if not preview_url:
        logger.debug("_fetch_place_details: no preview link for cid=%s, using title fallback", hex_cid)
        return parse_place_from_html(html, hex_cid)

    try:
        json_response = await _http_get(preview_url, params={}, headers=_PREVIEW_HEADERS, timeout=15)
    except MapsFetchError as exc:
        logger.debug("_fetch_place_details: preview fetch failed cid=%s: %s", hex_cid, exc)
        return parse_place_from_html(html, hex_cid)

    business = parse_place_from_preview_json(_strip_xssi(json_response.text), hex_cid)
    if business:
        logger.debug("_fetch_place_details: resolved '%s' via preview JSON", business.get("business_name"))
    else:
        logger.debug("_fetch_place_details: preview JSON parse failed for cid=%s, using title fallback", hex_cid)
        business = parse_place_from_html(html, hex_cid)

    return business


async def _search_maps_via_cids(search_query: str) -> list[dict]:
    """
    Fallback: plain ``q=`` search → hex CIDs → per-CID detail fetches.

    Only useful for the first page — Google ignores pagination parameters
    on plain-q tbm=map requests.
    """
    response = await _http_get(
        _SEARCH_URL,
        params={"tbm": "map", "hl": "es", "gl": "es", "q": search_query},
        headers=_SEARCH_HEADERS,
    )
    cids = parse_cids_from_maps_response(_strip_xssi(response.text))
    if not cids:
        return []

    logger.info("_search_maps_via_cids: fetching details for %d places…", len(cids))
    semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

    async def fetch_with_semaphore(cid: str) -> dict | None:
        async with semaphore:
            result = await _fetch_place_details(cid)
            await asyncio.sleep(random.uniform(
                settings.request_delay_min * 0.5,
                settings.request_delay_max * 0.5,
            ))
            return result

    results = await asyncio.gather(*[fetch_with_semaphore(cid) for cid in cids])
    return [b for b in results if b is not None and b.get("business_name")]


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

    Args:
        query: What to search (e.g. "dentistas")
        location: Where to search as text (e.g. "Valencia"); appended to the
            query unless it is already contained in it.
        start: Pagination offset (0, 20, 40…)
        lat/lng: Optional coordinates to center the search viewport
        radius_km: Search radius in km (drives the viewport zoom)

    Returns:
        List of business dicts (up to 20). Empty list when the page has no
        results. Raises MapsFetchError on operational failures.
    """
    location = (location or "").strip()
    if location and location.lower() in query.lower():
        search_query = query
    else:
        search_query = f"{query} {location}".strip()

    vp_lat, vp_lng, altitude = await _resolve_viewport(search_query, location, lat, lng, radius_km)

    pb = _PB_TEMPLATE.format(
        altitude=f"{altitude:.6f}",
        lng=f"{vp_lng:.7f}",
        lat=f"{vp_lat:.7f}",
        offset=start,
    )
    response = await _http_get(
        _SEARCH_URL,
        params={
            "tbm": "map",
            "authuser": "0",
            "hl": "es",
            "gl": "es",
            "q": search_query,
            "pb": pb,
        },
        headers=_SEARCH_HEADERS,
    )

    businesses = parse_maps_response(_strip_xssi(response.text))
    if businesses:
        logger.debug(
            "search_maps('%s', start=%d): %d businesses via pb search",
            search_query, start, len(businesses),
        )
        await asyncio.sleep(random.uniform(settings.request_delay_min, settings.request_delay_max))
        return businesses

    # pb yielded nothing. On the first page, try the CID fallback flow in
    # case Google rejected the pb template but still answers plain queries.
    if start == 0:
        logger.info("search_maps('%s'): pb search empty — trying CID fallback", search_query)
        businesses = await _search_maps_via_cids(search_query)
        if businesses:
            logger.info("search_maps('%s'): CID fallback resolved %d businesses", search_query, len(businesses))
        return businesses

    logger.debug("search_maps('%s', start=%d): no more results", search_query, start)
    return []


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

    Stops when a page returns fewer than 20 results, or when a full page
    contributes no unseen place_ids (Google starts repeating results near
    the end of the available set — usually ~200 per viewport).
    """
    all_results: list[dict] = []
    seen_ids: set[str] = set()
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

        new_in_batch = 0
        for business in batch:
            pid = str(business.get("place_id") or "").strip()
            if pid:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
            new_in_batch += 1
            all_results.append(business)

        start += 20

        if len(batch) < 20 or new_in_batch == 0:
            break

    return all_results[:max_results]
