"""
Instagram username discovery for Mode A (unauthenticated).

Discovery strategy (tried in order):
  1. Google Custom Search JSON API  — requires GOOGLE_API_KEY + GOOGLE_CSE_ID in .env
  2. Hashtag-based Instagram fetch  — no API key needed, always works as fallback

Google CSE setup (one-time, free):
  1. Go to https://programmablesearchengine.google.com/ → Create
     Site to search: www.instagram.com
  2. Copy the "Search engine ID"
  3. Go to https://console.cloud.google.com/ → APIs → Custom Search API → Enable
  4. Create credentials (API key)
  5. Add to instaleads/.env:
       GOOGLE_API_KEY=your_key
       GOOGLE_CSE_ID=your_cse_id
  Free tier: 100 queries/day. Each call fetches 10 results, so 100 calls = 1000 results/day.
"""

import asyncio
import logging
import os
import random
import re
import unicodedata

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})(?:[/?#\"'\s<]|$)")

_SYSTEM_PATHS = {
    "p", "reel", "reels", "explore", "stories", "tv", "accounts",
    "about", "blog", "help", "legal", "privacy", "safety",
    "directory", "features", "developer", "graphql", "api",
    "static", "s", "web", "lite", "ar", "web_profile_info",
    "null", "instagram",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-ES,es;q=0.9",
}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_usernames(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for u in raw:
        u = u.lower().rstrip(".")
        if u and u not in _SYSTEM_PATHS and u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _to_ascii(text: str) -> str:
    """Remove accents: fotógrafo → fotografo."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _build_hashtag_candidates(niche: str, location: str) -> list[str]:
    """
    Generate Instagram hashtag candidates from niche+location.
    These are tried as usernames and also as hashtags for discovery.
    """
    n = _to_ascii(niche.lower().replace(" ", ""))
    l = _to_ascii(location.lower().replace(" ", ""))
    return list(dict.fromkeys([
        f"{n}{l}",
        f"{n}_{l}",
        f"{n}.{l}",
        f"{l}{n}",
        f"{l}_{n}",
        f"{n}en{l}",
        f"{n}de{l}",
        f"foto{l}" if "foto" in n else f"{n}{l}studio",
        f"{n}",
        f"{l}{n}foto" if "foto" in n else f"{l}{n}",
    ]))


def _build_google_queries(niche: str, location: str) -> list[str]:
    """Build Google CSE queries for niche+location Instagram search."""
    n_ascii = _to_ascii(niche)
    return [
        f'site:instagram.com "{niche}" "{location}" "@gmail.com" OR "@hotmail.com"',
        f'site:instagram.com "{niche}" "{location}" "email" OR "contacto"',
        f'site:instagram.com "{n_ascii}" "{location}"',
        f'site:instagram.com "{niche}" "{location}"',
    ]


# ── Strategy 1: Google Custom Search API ────────────────────────────────────

async def _search_google_cse(
    session: AsyncSession,
    query: str,
    api_key: str,
    cse_id: str,
    start: int = 1,
) -> list[str]:
    """One Google CSE request, returns extracted usernames."""
    try:
        resp = await session.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "num": 10,
                "start": start,
            },
            headers=_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning("Google CSE returned %d: %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        items = data.get("items", [])
        raw: list[str] = []
        for item in items:
            link = item.get("link", "")
            matches = _USERNAME_RE.findall(link + " " + item.get("snippet", ""))
            raw.extend(matches)
        return _clean_usernames(raw)
    except Exception as exc:
        logger.warning("Google CSE request failed: %s", exc)
        return []


async def _discover_via_google(
    niche: str, location: str, max_results: int
) -> list[str]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    cse_id = os.getenv("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        logger.warning("Google CSE not configured (GOOGLE_API_KEY / GOOGLE_CSE_ID missing in .env) — skipping")
        return []

    logger.info("Discovering usernames via Google CSE for '%s %s'", niche, location)
    logger.debug("Using Google CSE ID: %s...", cse_id[:10])
    all_usernames: list[str] = []
    seen: set[str] = set()

    async with AsyncSession(impersonate="chrome131") as session:
        queries = _build_google_queries(niche, location)
        logger.debug("Google CSE queries to try: %d", len(queries))

        for query_idx, query in enumerate(queries):
            if len(all_usernames) >= max_results:
                break
            logger.debug("Google CSE query %d/%d: %s", query_idx + 1, len(queries), query[:60])

            for start in [1, 11, 21]:  # pages 1-3
                if len(all_usernames) >= max_results:
                    break
                usernames = await _search_google_cse(session, query, api_key, cse_id, start=start)
                if usernames:
                    logger.debug("Google CSE page returned %d usernames", len(usernames))
                    for u in usernames:
                        if u not in seen:
                            seen.add(u)
                            all_usernames.append(u)
                else:
                    logger.debug("Google CSE page returned 0 results — moving to next query")
                    break
                await asyncio.sleep(random.uniform(1.0, 2.5))
            await asyncio.sleep(random.uniform(2.0, 4.0))

    if all_usernames:
        logger.info("✅ Google CSE found %d usernames", len(all_usernames))
    else:
        logger.warning("⚠️ Google CSE found 0 usernames — will try fallback strategies")
    return all_usernames


# ── Strategy 2: DuckDuckGo web search (no API key needed) ───────────────────

def _build_search_queries(niche: str, location: str) -> list[str]:
    """Build search queries for web scraping engines."""
    n_ascii = _to_ascii(niche)
    queries = [
        f'site:instagram.com "{niche}" "{location}" email',
        f'site:instagram.com "{niche}" "{location}"',
        f'site:instagram.com {n_ascii} {location}',
    ]
    if not location:
        queries = [
            f'site:instagram.com "{niche}" email',
            f'site:instagram.com "{niche}"',
        ]
    return queries


async def _discover_via_duckduckgo(
    niche: str, location: str, max_results: int
) -> list[str]:
    """
    Scrape DuckDuckGo HTML for Instagram profiles matching niche+location.
    No API key required. Uses curl_cffi browser impersonation with User-Agent rotation,
    retry logic, and optional Bing fallback.
    """
    queries = _build_search_queries(niche, location)
    all_usernames: list[str] = []
    seen: set[str] = set()

    logger.info("Discovering usernames via DuckDuckGo for '%s %s'", niche, location)

    async with AsyncSession(impersonate="chrome131") as session:
        for query in queries:
            if len(all_usernames) >= max_results:
                break

            # Retry logic with backoff
            for attempt in range(1, 4):  # 3 attempts
                if len(all_usernames) >= max_results:
                    break
                try:
                    user_agent = random.choice(_USER_AGENTS)
                    resp = await session.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": query},
                        headers={
                            "User-Agent": user_agent,
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "es-ES,es;q=0.9",
                            "Accept-Encoding": "gzip, deflate",
                        },
                        timeout=20,
                    )
                    if resp.status_code == 200:
                        matches = _USERNAME_RE.findall(resp.text)
                        clean = _clean_usernames(matches)
                        for u in clean:
                            if u not in seen:
                                seen.add(u)
                                all_usernames.append(u)
                        logger.debug(
                            "DDG query '%s...' returned %d usernames (total: %d)",
                            query[:50], len(clean), len(all_usernames),
                        )
                        break  # Success, move to next query
                    elif resp.status_code in (429, 202):  # Rate limited or blocked
                        wait_time = (2 ** attempt) * random.uniform(1, 3)  # Exponential backoff
                        logger.warning(
                            "DDG returned %d for query (attempt %d/3) — waiting %.1fs",
                            resp.status_code, attempt, wait_time
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning("DuckDuckGo returned %d for query: %s", resp.status_code, query[:60])
                        break
                except Exception as exc:
                    logger.warning("DuckDuckGo search failed (attempt %d/3) for '%s': %s", attempt, query[:60], exc)
                    if attempt < 3:
                        await asyncio.sleep(2 ** attempt)

            # Delay between queries to avoid rate limiting
            await asyncio.sleep(random.uniform(4.0, 8.0))

    if all_usernames:
        logger.info("✅ DuckDuckGo found %d usernames for '%s %s'", len(all_usernames), niche, location)
    else:
        logger.warning("⚠️ DuckDuckGo found 0 usernames — will try Bing fallback")
        all_usernames = await _discover_via_bing(niche, location, max_results)

    return all_usernames[:max_results]


# ── Strategy 2b: Bing fallback (if DuckDuckGo fails) ───────────────────────

async def _discover_via_bing(
    niche: str, location: str, max_results: int
) -> list[str]:
    """
    Fallback: Scrape Bing HTML for Instagram profiles.
    Used if DuckDuckGo is rate-limited or blocked.
    """
    queries = _build_search_queries(niche, location)
    all_usernames: list[str] = []
    seen: set[str] = set()

    logger.info("Falling back to Bing for '%s %s'", niche, location)

    async with AsyncSession(impersonate="chrome131") as session:
        for query in queries:
            if len(all_usernames) >= max_results:
                break
            try:
                user_agent = random.choice(_USER_AGENTS)
                resp = await session.get(
                    "https://www.bing.com/search",
                    params={"q": query},
                    headers={
                        "User-Agent": user_agent,
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "es-ES,es;q=0.9",
                        "Accept-Encoding": "gzip, deflate",
                    },
                    timeout=20,
                )
                if resp.status_code == 200:
                    matches = _USERNAME_RE.findall(resp.text)
                    clean = _clean_usernames(matches)
                    for u in clean:
                        if u not in seen:
                            seen.add(u)
                            all_usernames.append(u)
                    logger.debug("Bing query returned %d usernames", len(clean))
                else:
                    logger.warning("Bing returned %d for query", resp.status_code)
                await asyncio.sleep(random.uniform(3.0, 6.0))
            except Exception as exc:
                logger.warning("Bing search failed for '%s': %s", query[:60], exc)

    logger.info("Bing found %d usernames for '%s %s'", len(all_usernames), niche, location)
    return all_usernames[:max_results]


# ── Strategy 3: Hashtag-based fallback ──────────────────────────────────────

async def _fetch_profile_exists(session: AsyncSession, username: str) -> bool:
    """Check if an Instagram username actually exists (web_profile_info)."""
    try:
        resp = await session.get(
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            headers={
                "User-Agent": _HEADERS["User-Agent"],
                "Accept": "*/*",
                "Accept-Language": "es-ES,es;q=0.9",
                "x-ig-app-id": "936619743392459",
                "Referer": "https://www.instagram.com/",
            },
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("data", {}).get("user"))
        return False
    except Exception:
        return False


async def _discover_via_hashtags(
    niche: str, location: str, max_results: int
) -> list[str]:
    """
    Fallback: generate username candidates from niche+location patterns
    and verify which ones actually exist on Instagram.
    """
    candidates = _build_hashtag_candidates(niche, location)
    logger.info(
        "Hashtag fallback: checking %d username candidates for '%s %s'",
        len(candidates), niche, location,
    )
    found: list[str] = []
    async with AsyncSession(impersonate="chrome131") as session:
        for username in candidates:
            if len(found) >= max_results:
                break
            exists = await _fetch_profile_exists(session, username)
            if exists:
                found.append(username)
                logger.debug("Hashtag candidate @%s exists", username)
            await asyncio.sleep(random.uniform(2.0, 4.0))

    logger.info("Hashtag fallback found %d existing accounts", len(found))
    return found


# ── Public entry point ────────────────────────────────────────────────────────

# ── Strategy 3: Hashtag API via instagrapi ────────────────────────────────────

async def _discover_via_hashtag_api(niche: str, location: str, max_results: int) -> list[str]:
    """
    Use instagrapi to find users via hashtag search.
    Generates relevant hashtags and extracts usernames from recent posts.
    """
    from backend.instagram import ig_session

    if not ig_session.is_logged_in():
        logger.debug("Hashtag API requires authenticated session — skipping")
        return []

    from backend.instagram.ig_rate_limiter import auth_limiter

    logger.info("Discovering usernames via hashtag API for '%s %s'", niche, location)
    all_usernames: list[str] = []
    seen: set[str] = set()

    try:
        cl = ig_session.get_client()
        hashtags = _build_hashtag_candidates(niche, location)

        for hashtag in hashtags[:5]:  # Try top 5 hashtags
            if len(all_usernames) >= max_results:
                break

            try:
                await auth_limiter.wait()
                medias = cl.hashtag_medias_recent(hashtag, amount=30)

                for media in medias:
                    if len(all_usernames) >= max_results:
                        break
                    username = media.user.username
                    if username and username not in seen:
                        seen.add(username)
                        all_usernames.append(username)
                        logger.debug("Found via hashtag #%s: @%s", hashtag, username)

            except Exception as exc:
                logger.debug("Error querying hashtag #%s: %s", hashtag, exc)
                continue

    except Exception as exc:
        logger.warning("Hashtag API discovery failed: %s", exc)

    logger.info("Hashtag API found %d usernames", len(all_usernames))
    return all_usernames


# ── Strategy 4: Location API via instagrapi ────────────────────────────────────

async def _discover_via_location_api(niche: str, location: str, max_results: int) -> list[str]:
    """
    Use instagrapi to find users by location.
    Searches location ID and extracts usernames from recent posts.
    """
    from backend.instagram import ig_session

    if not ig_session.is_logged_in():
        logger.debug("Location API requires authenticated session — skipping")
        return []

    from backend.instagram.ig_rate_limiter import auth_limiter

    logger.info("Discovering usernames via location API for '%s'", location)
    all_usernames: list[str] = []
    seen: set[str] = set()

    if not location.strip():
        logger.debug("Location not provided — skipping location API")
        return []

    try:
        cl = ig_session.get_client()

        # Search for location ID
        await auth_limiter.wait()
        locations = cl.search_location(location)

        if not locations:
            logger.debug("No location found for '%s'", location)
            return []

        # Use the first location result
        location_id = locations[0].pk

        # Get recent medias at this location
        await auth_limiter.wait()
        medias = cl.location_medias_recent(location_id, amount=50)

        niche_lower = niche.lower()
        for media in medias:
            if len(all_usernames) >= max_results:
                break

            username = media.user.username
            caption = (media.caption or "").lower()
            user_bio = (media.user.biography or "").lower()

            # Filter by niche keywords in caption or bio
            if username and username not in seen:
                if niche_lower in caption or niche_lower in user_bio or not niche_lower:
                    seen.add(username)
                    all_usernames.append(username)
                    logger.debug("Found at location %s: @%s", location_id, username)

    except Exception as exc:
        logger.warning("Location API discovery failed: %s", exc)

    logger.info("Location API found %d usernames", len(all_usernames))
    return all_usernames


async def find_usernames(target: str, max_results: int = 50) -> list[str]:
    """
    Find Instagram usernames related to the target.
    Uses a 5-strategy cascade:
      1. Google CSE (best quality, no auth needed, requires API key)
      2. DuckDuckGo web scraping with Bing fallback (if DDG rate-limited)
      3. Hashtag API via instagrapi (if session available)
      4. Location API via instagrapi (if session available)
      5. Hashtag pattern fallback (worst quality, no auth needed)

    Args:
        target: "niche|location" string (e.g. "fotografo|valencia")
                or a plain search term.
        max_results: Max usernames to return.

    Returns:
        Deduplicated list of Instagram usernames.
    """
    if "|" in target:
        parts = target.split("|", 1)
        niche = parts[0].strip()
        location = parts[1].strip()
    else:
        # Treat plain string as niche only, no location filter
        niche = target.strip()
        location = ""

    logger.info("=" * 70)
    logger.info("🔍 DORKING DISCOVERY PIPELINE: niche='%s' | location='%s' | max=%d", niche, location, max_results)
    logger.info("=" * 70)

    # Strategy 1: Google CSE (best quality, requires API key)
    logger.info("[1/5] Attempting Google CSE discovery...")
    usernames = await _discover_via_google(niche, location, max_results)
    seen = set(usernames)

    # Strategy 2: DuckDuckGo web scraping (no API key needed, always available)
    if len(usernames) < max_results * 0.5:
        logger.info("[2/5] Google CSE insufficient (%d/%d) — trying DuckDuckGo...", len(usernames), max_results)
        ddg_results = await _discover_via_duckduckgo(niche, location, max_results - len(usernames))
        added = 0
        for u in ddg_results:
            if u not in seen:
                seen.add(u)
                usernames.append(u)
                added += 1
        logger.info("     DuckDuckGo added %d new usernames (total: %d)", added, len(usernames))
    else:
        logger.info("[2/5] Skipping DuckDuckGo (Google CSE sufficient)")

    # Strategy 3: Hashtag API if session available
    if len(usernames) < max_results * 0.5:
        logger.info("[3/5] Under 50%% quota — trying Hashtag API...")
        hashtag_results = await _discover_via_hashtag_api(niche, location, max_results - len(usernames))
        added = 0
        for u in hashtag_results:
            if u not in seen:
                seen.add(u)
                usernames.append(u)
                added += 1
        logger.info("     Hashtag API added %d new usernames (total: %d)", added, len(usernames))
    else:
        logger.info("[3/5] Skipping Hashtag API (quota sufficient)")

    # Strategy 4: Location API if session available and location given
    if location and len(usernames) < max_results:
        logger.info("[4/5] Trying Location API for '%s'...", location)
        location_results = await _discover_via_location_api(niche, location, max_results - len(usernames))
        added = 0
        for u in location_results:
            if u not in seen:
                seen.add(u)
                usernames.append(u)
                added += 1
        logger.info("     Location API added %d new usernames (total: %d)", added, len(usernames))
    else:
        logger.info("[4/5] Skipping Location API (%s)", "no location" if not location else "quota met")

    # Strategy 5: Hashtag pattern fallback if still very few results
    if len(usernames) < 5:
        logger.info("[5/5] Very few results (%d) — using Hashtag Pattern Fallback...", len(usernames))
        fallback_results = await _discover_via_hashtags(niche, location, max_results - len(usernames))
        added = 0
        for u in fallback_results:
            if u not in seen:
                seen.add(u)
                usernames.append(u)
                added += 1
        logger.info("     Hashtag Fallback added %d usernames (total: %d)", added, len(usernames))
    else:
        logger.info("[5/5] Skipping Hashtag Fallback (have %d usernames)", len(usernames))

    logger.info("=" * 70)
    logger.info("✅ DISCOVERY COMPLETE: Found %d usernames for target '%s'", len(usernames), target)
    logger.info("=" * 70)
    return usernames[:max_results]
