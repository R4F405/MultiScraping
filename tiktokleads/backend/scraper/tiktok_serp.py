"""
Discovery vía SERP (dorking) sin cuentas TikTok: consultas en capas, DDG paginado,
Startpage y fusión paralela — patrón alineado con instaleads/ig_dorking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup
from httpx import Response

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

HANDLE_ANY_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9._-]{2,40})", re.IGNORECASE)
SKIP_HANDLES = {
    "explore",
    "discover",
    "tag",
    "music",
    "about",
    "legal",
    "privacy",
    "business",
    "foryou",
    "following",
    "startpagesearch",
}

DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://html.duckduckgo.com/",
}

STARTPAGE_URL = "https://www.startpage.com/do/search"
STARTPAGE_HEADERS_BASE = {
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

BING_SEARCH_URL = "https://www.bing.com/search"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def ddg_headers() -> dict[str, str]:
    h = dict(DUCKDUCKGO_HEADERS_BASE)
    h["User-Agent"] = _UA
    return h


def startpage_headers() -> dict[str, str]:
    h = dict(STARTPAGE_HEADERS_BASE)
    h["User-Agent"] = _UA
    return h


def parse_niche_location(target: str) -> tuple[str, str | None]:
    raw = (target or "").strip()
    if "|" in raw:
        niche, loc = raw.split("|", 1)
        niche, loc = niche.strip(), loc.strip()
        if niche and loc:
            return niche, loc
    return raw, None


def web_search_q_for_target(mode: str, target: str) -> str:
    """Query legible para tiktok.com/search (evita enviar 'nicho|ciudad' literal al buscador web)."""
    raw = (target or "").strip().lstrip("#")
    niche, loc = parse_niche_location(raw)
    if loc:
        return f"{niche} {loc}".strip()
    return niche or raw


def build_dork_queries(mode: str, target: str) -> list[str]:
    """Consultas en tiers (site:tiktok.com/@, señales de negocio, geo, sinónimos simples)."""
    niche, location = parse_niche_location(target)
    token = niche.replace("#", "").replace("@", "").strip()
    if not token:
        return []

    geo_bits: list[str] = []
    if location:
        geo_bits = [location, "España", "Spain"]
    else:
        geo_bits = ["españa", "valencia", "madrid"]

    queries: list[str] = []

    # Tier 1: perfil TikTok directo
    queries += [
        f"site:tiktok.com/@ {token}",
        f"tiktok {token} site:tiktok.com/@",
        f'"{token}" tiktok.com/@',
        f"tiktok.com/@ {token}",
    ]

    # Tier 2: intención negocio / creador
    queries += [
        f"site:tiktok.com/@ {token} colaboración",
        f"site:tiktok.com/@ {token} presupuesto",
        f"site:tiktok.com/@ {token} email",
        f"site:tiktok.com/@ {token} whatsapp",
        f"site:tiktok.com/@ {token} linktr.ee",
        f"site:tiktok.com/@ {token} linktree",
        f"site:tiktok.com/@ {token} contacto",
    ]

    # Tier 3: geo (niche+location o fallback)
    for g in geo_bits[:3]:
        queries.append(f'site:tiktok.com/@ "{token}" {g}')

    if mode == "hashtag":
        ht = token
        queries += [
            f"site:tiktok.com/@ {ht} creator",
            f"site:tiktok.com/@ {ht} recetas",
            f"tiktok #{ht} site:tiktok.com/@",
        ]
    elif mode == "keyword":
        queries += [
            f"site:tiktok.com/@ {token} servicios",
            f"site:tiktok.com/@ {token} profesional",
        ]

    # Tier 4: primera palabra extra (sinónimo léxico mínimo)
    first = token.split()[0] if token.split() else token
    if first and first.lower() != token.lower():
        queries.append(f"site:tiktok.com/@ {first} {token}")

    return list(dict.fromkeys(q for q in queries if q.strip()))


def extract_handles_from_html(html: str) -> list[str]:
    out: list[str] = []
    for handle in HANDLE_ANY_RE.findall(html or ""):
        clean = handle.strip().strip("/").lstrip("@")
        if not clean:
            continue
        if clean.lower() in SKIP_HANDLES:
            continue
        out.append(clean)
    for match in re.findall(
        r"tiktok\.com%2F%40([A-Za-z0-9._-]{2,40})", html or "", re.IGNORECASE
    ):
        clean = match.strip().lstrip("@")
        if clean and clean.lower() not in SKIP_HANDLES:
            out.append(clean)
    return list(dict.fromkeys(out))


def extract_handles_from_html_with_uddg(html: str) -> list[str]:
    """Handles en HTML + URLs codificadas uddg= de resultados DDG."""
    seen: set[str] = set()
    ordered: list[str] = []
    for h in extract_handles_from_html(html):
        key = h.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(h)
    for uddg in re.findall(r"uddg=([^&\"'>]+)", html or ""):
        decoded = unquote(uddg)
        for h in extract_handles_from_html(decoded):
            key = h.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(h)
    return ordered


def extract_ddg_next_params(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html or "", "html.parser")
    form = soup.select_one("div.nav-link form")
    if not form:
        return {}
    return {
        str(inp.get("name")): inp.get("value", "") or ""
        for inp in form.find_all("input")
        if inp.get("name")
    }


SerpPost = Callable[..., Awaitable[Response | None]]
SerpGet = Callable[..., Awaitable[Response | None]]
LogEvent = Callable[..., Awaitable[None]]


async def _scrape_duckduckgo_all_pages(
    post_fn: SerpPost,
    client: httpx.AsyncClient,
    query: str,
    headers: dict[str, str],
    max_pages: int,
    log_event: LogEvent | None,
) -> list[str]:
    seen: set[str] = set()
    all_handles: list[str] = []
    page_params: dict[str, str] | None = None

    for page in range(max_pages):
        if page > 0:
            await asyncio.sleep(2.0)
        data = page_params if page_params else {"q": query}
        try:
            resp = await post_fn(
                client,
                DUCKDUCKGO_URL,
                headers={**ddg_headers(), **headers},
                data=data,
            )
        except Exception as exc:
            logger.warning("DuckDuckGo scrape failed (page=%d): %s", page + 1, exc)
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {"engine": "ddg_html", "query": query[:80], "page": page + 1, "error": str(exc)}
                    ),
                )
            break

        if not resp or resp.status_code != 200:
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {
                            "engine": "ddg_html",
                            "query": query[:80],
                            "page": page + 1,
                            "status": getattr(resp, "status_code", None),
                        }
                    ),
                )
            break

        text = resp.text
        low = text[:1200].lower()
        if "please try again" in low:
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {"engine": "ddg_html", "query": query[:80], "page": page + 1, "error": "captcha"}
                    ),
                )
            break

        handles = extract_handles_from_html_with_uddg(text)
        new_count = 0
        for h in handles:
            key = h.lower()
            if key not in seen:
                seen.add(key)
                all_handles.append(h)
                new_count += 1

        if log_event:
            await log_event(
                "discovery_source",
                "info",
                json.dumps(
                    {
                        "engine": "ddg_html",
                        "query": query[:80],
                        "page": page + 1,
                        "handles_found": new_count,
                    }
                ),
            )

        if not handles or new_count == 0:
            break

        page_params = extract_ddg_next_params(text)
        if not page_params:
            break

    return all_handles


async def _scrape_startpage_all_pages(
    get_fn: SerpGet,
    client: httpx.AsyncClient,
    query: str,
    headers: dict[str, str],
    max_pages: int,
    log_event: LogEvent | None,
) -> list[str]:
    seen: set[str] = set()
    all_handles: list[str] = []
    merged_headers = {**startpage_headers(), **headers}

    for page in range(1, min(max_pages + 1, 5)):
        if page > 1:
            await asyncio.sleep(1.5)
        params: dict[str, str] = {"q": query}
        if page > 1:
            params["page"] = str(page)
        try:
            resp = await get_fn(
                client, STARTPAGE_URL, headers=merged_headers, params=params
            )
        except Exception as exc:
            logger.warning("Startpage scrape failed (page=%d): %s", page, exc)
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {"engine": "startpage", "query": query[:80], "page": page, "error": str(exc)}
                    ),
                )
            break

        if not resp:
            break
        text = resp.text
        if "captcha" in text[:800].lower():
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {"engine": "startpage", "query": query[:80], "page": page, "error": "captcha"}
                    ),
                )
            break

        if resp.status_code != 200:
            if log_event:
                await log_event(
                    "discovery_source",
                    "warning",
                    json.dumps(
                        {
                            "engine": "startpage",
                            "query": query[:80],
                            "page": page,
                            "status": resp.status_code,
                        }
                    ),
                )
            break

        handles = extract_handles_from_html_with_uddg(text)
        new_count = 0
        for h in handles:
            key = h.lower()
            if key not in seen:
                seen.add(key)
                all_handles.append(h)
                new_count += 1

        if log_event:
            await log_event(
                "discovery_source",
                "info",
                json.dumps(
                    {
                        "engine": "startpage",
                        "query": query[:80],
                        "page": page,
                        "handles_found": new_count,
                    }
                ),
            )

        if not handles or new_count == 0:
            break

    return all_handles


async def _scrape_bing_one_page(
    get_fn: SerpGet,
    client: httpx.AsyncClient,
    query: str,
    headers: dict[str, str],
    log_event: LogEvent | None,
) -> list[str]:
    try:
        resp = await get_fn(
            client, BING_SEARCH_URL, headers=headers, params={"q": query}
        )
    except Exception as exc:
        if log_event:
            await log_event(
                "discovery_source",
                "warning",
                json.dumps({"engine": "bing", "query": query[:80], "page": 1, "error": str(exc)}),
            )
        return []

    if not resp or resp.status_code != 200:
        if log_event:
            await log_event(
                "discovery_source",
                "warning",
                json.dumps(
                    {
                        "engine": "bing",
                        "query": query[:80],
                        "page": 1,
                        "status": getattr(resp, "status_code", None),
                    }
                ),
            )
        return []

    handles = extract_handles_from_html_with_uddg(resp.text)
    if log_event:
        await log_event(
            "discovery_source",
            "info",
            json.dumps(
                {"engine": "bing", "query": query[:80], "page": 1, "handles_found": len(handles)}
            ),
        )
    return handles


async def scrape_serp_handles_for_query(
    post_fn: SerpPost,
    get_fn: SerpGet,
    client: httpx.AsyncClient,
    query: str,
    headers: dict[str, str],
    log_event: LogEvent | None = None,
) -> list[str]:
    """
    DDG HTML (paginado) + Startpage (paginado) en paralelo; Bing una página extra.
    Si un motor falla, el merge sigue con el resto.
    """
    max_ddg = max(1, Settings.TIKTOKLEADS_SERP_DDG_MAX_PAGES)
    max_sp = max(1, Settings.TIKTOKLEADS_SERP_STARTPAGE_MAX_PAGES)

    ddg_task = _scrape_duckduckgo_all_pages(
        post_fn, client, query, headers, max_ddg, log_event
    )
    sp_task = _scrape_startpage_all_pages(
        get_fn, client, query, headers, max_sp, log_event
    )

    results = await asyncio.gather(ddg_task, sp_task, return_exceptions=True)

    seen: set[str] = set()
    merged: list[str] = []
    for engine_results in results:
        if isinstance(engine_results, Exception):
            logger.warning("SERP engine error for query '%s': %s", query[:50], engine_results)
            continue
        for h in engine_results:
            key = h.lower()
            if key not in seen:
                seen.add(key)
                merged.append(h)

    if Settings.TIKTOKLEADS_SERP_INCLUDE_BING:
        try:
            bing_handles = await _scrape_bing_one_page(get_fn, client, query, headers, log_event)
            for h in bing_handles:
                key = h.lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(h)
        except Exception as exc:
            logger.warning("Bing SERP error: %s", exc)

    return merged
