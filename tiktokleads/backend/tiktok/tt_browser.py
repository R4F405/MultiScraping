"""
Playwright browser session for TikTok scraping.

Strategy:
  - Single persistent browser context (reutiliza cookies entre requests)
  - Intercept XHR responses to capture API JSON before page render
  - Headless configurable via TIKTOK_HEADLESS env var
  - Proxy support via TIKTOK_PROXY_URL env var
"""
import asyncio
import json
import logging
import os

from playwright.async_api import BrowserContext, async_playwright

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['es-ES', 'es', 'en-US', 'en']});
    window.chrome = { runtime: {} };
"""

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_USER_AGENT_POOL = [
    _USER_AGENT,
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
]

_playwright_instance = None
_browser = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()
_proxy_index = 0
_ua_index = 0


async def _page_looks_blocked(page) -> bool:
    """Best-effort check for anti-bot / verification pages."""
    try:
        html = (await page.content()).lower()
    except Exception:
        return False
    markers = (
        "captcha",
        "verify",
        "robot",
        "security check",
        "unusual traffic",
        "are you human",
    )
    return any(m in html for m in markers)


def _current_proxy() -> str:
    if not settings.proxy_urls:
        return ""
    return settings.proxy_urls[_proxy_index % len(settings.proxy_urls)]


def _current_user_agent() -> str:
    return _USER_AGENT_POOL[_ua_index % len(_USER_AGENT_POOL)]


async def _save_storage_state(ctx: BrowserContext) -> None:
    path = settings.session_state_path
    if not path:
        return
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        await ctx.storage_state(path=path)
    except Exception as exc:
        logger.debug("Could not save storage state: %s", exc)


async def _warmup_session(page) -> None:
    if not settings.warmup_enabled:
        return
    try:
        await page.goto(settings.warmup_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(1.0)
    except Exception as exc:
        logger.debug("Warmup failed: %s", exc)


async def rotate_browser_identity(reason: str) -> None:
    """Rotate proxy/user-agent and force a fresh browser context."""
    global _proxy_index, _ua_index, _context, _browser
    async with _lock:
        _proxy_index += 1
        _ua_index += 1
        logger.warning(
            "Rotating browser identity (%s) -> proxy_idx=%d ua_idx=%d",
            reason,
            _proxy_index,
            _ua_index,
        )
        if _context:
            try:
                await _context.close()
            except Exception:
                pass
        if _browser:
            try:
                await _browser.close()
            except Exception:
                pass
        _context = None
        _browser = None


async def get_context() -> BrowserContext:
    """Get or create persistent browser context. Thread-safe."""
    global _playwright_instance, _browser, _context
    async with _lock:
        if _context is not None:
            return _context

        logger.info("Initializing Playwright browser (headless=%s)", settings.headless)
        _playwright_instance = await async_playwright().start()

        launch_opts: dict = {
            "headless": settings.headless,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        proxy = _current_proxy()
        if proxy:
            launch_opts["proxy"] = {"server": proxy}
            logger.info("Using proxy index %d", _proxy_index % max(1, len(settings.proxy_urls)))

        _browser = await _playwright_instance.chromium.launch(**launch_opts)
        context_kwargs: dict = {
            "user_agent": _current_user_agent(),
            "viewport": {"width": 1280, "height": 800},
            "locale": "es-ES",
            "extra_http_headers": {
                "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        }
        if settings.session_state_path and os.path.exists(settings.session_state_path):
            context_kwargs["storage_state"] = settings.session_state_path

        _context = await _browser.new_context(**context_kwargs)
        await _context.add_init_script(_STEALTH_SCRIPT)
        logger.info("Browser context ready")
        return _context


async def close_browser() -> None:
    """Close all Playwright resources. Called on app shutdown."""
    global _playwright_instance, _browser, _context
    async with _lock:
        if _context:
            try:
                await _context.close()
            except Exception:
                pass
            _context = None
        if _browser:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None
        if _playwright_instance:
            try:
                await _playwright_instance.stop()
            except Exception:
                pass
            _playwright_instance = None
        logger.info("Browser closed")


async def intercept_search_xhr(keyword: str, max_results: int = 50) -> list[dict]:
    """
    Navigate to TikTok user search and intercept XHR responses.

    Captures /api/search/general/full/ responses which contain creator data
    (secUid, uniqueId, nickname, followerCount) without needing X-Bogus.

    Returns list of creator dicts.
    """
    ctx = await get_context()
    page = await ctx.new_page()
    collected: list[dict] = []
    captured = asyncio.Event()

    async def handle_response(response):
        if "api/search/general/full" not in response.url:
            return
        try:
            body = await response.json()
        except Exception:
            return

        # Extract user items from search results
        user_list = body.get("user_list") or []
        for item in user_list:
            user_info = item.get("user_info") or {}
            uid = user_info.get("uid") or item.get("uid", "")
            unique_id = user_info.get("unique_id") or item.get("unique_id", "")
            sec_uid = user_info.get("sec_uid") or item.get("sec_uid", "")
            nickname = user_info.get("nickname") or item.get("nickname", "")
            follower_count = (
                user_info.get("follower_count")
                or user_info.get("fans_count")
                or item.get("follower_count", 0)
            )
            if unique_id:
                collected.append({
                    "uid": uid,
                    "unique_id": unique_id,
                    "sec_uid": sec_uid,
                    "nickname": nickname,
                    "follower_count": follower_count,
                })
        if collected:
            captured.set()

    page.on("response", handle_response)

    try:
        await _warmup_session(page)
        search_url = f"https://www.tiktok.com/search/user?q={keyword}"
        logger.info("Navigating to TikTok search: %s", search_url)
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for first XHR capture or timeout
        try:
            await asyncio.wait_for(captured.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.warning("No XHR captured for keyword '%s' within 15s", keyword)
            if await _page_looks_blocked(page):
                raise RuntimeError("tiktok_challenge_detected: anti-bot verification page shown")

        # Scroll to paginate if more results needed
        if len(collected) < max_results:
            for _ in range(min(5, max_results // 10)):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2.0)
                if len(collected) >= max_results:
                    break

        logger.info("Search for '%s' captured %d creators", keyword, len(collected))
        await _save_storage_state(ctx)
    except Exception as exc:
        logger.error("Error during TikTok search for '%s': %s", keyword, exc)
        if "tiktok_challenge_detected" in str(exc):
            raise
    finally:
        await page.close()

    # Deduplicate by unique_id
    seen_ids: set[str] = set()
    result = []
    for creator in collected:
        uid = creator["unique_id"]
        if uid not in seen_ids:
            seen_ids.add(uid)
            result.append(creator)

    return result[:max_results]


async def fetch_profile_rehydration(username: str) -> dict | None:
    """
    Visit https://www.tiktok.com/@{username} and extract profile data
    from __UNIVERSAL_DATA_FOR_REHYDRATION__ script tag.

    Returns dict with: uniqueId, nickname, signature, followerCount,
                       verified, bioLink (string or None)
    """
    ctx = await get_context()
    page = await ctx.new_page()

    try:
        profile_url = f"https://www.tiktok.com/@{username}"
        logger.debug("Fetching profile rehydration for @%s", username)
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(1.5)

        # Try __UNIVERSAL_DATA_FOR_REHYDRATION__ first
        data = await page.evaluate("""() => {
            const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
            if (el) {
                try { return JSON.parse(el.textContent); } catch(e) { return null; }
            }
            return null;
        }""")

        if data:
            user_info = _extract_user_from_rehydration(data, username)
            if user_info:
                return user_info

        # Fallback: try SIGI_STATE
        data = await page.evaluate("""() => {
            const el = document.getElementById('SIGI_STATE');
            if (el) {
                try { return JSON.parse(el.textContent); } catch(e) { return null; }
            }
            return null;
        }""")

        if data:
            user_info = _extract_user_from_sigi(data, username)
            if user_info:
                return user_info

        logger.warning("Could not extract profile data for @%s", username)
        return None

    except Exception as exc:
        logger.error("Error fetching profile for @%s: %s", username, exc)
        return None
    finally:
        await page.close()


def _extract_user_from_rehydration(data: dict, username: str) -> dict | None:
    """Extract user dict from __UNIVERSAL_DATA_FOR_REHYDRATION__ JSON."""
    try:
        scope = data.get("__DEFAULT_SCOPE__", {})
        user_detail = scope.get("webapp.user-detail", {})
        user_info = user_detail.get("userInfo", {})
        user = user_info.get("user", {})
        stats = user_info.get("stats", {})
        if not user:
            return None
        return _build_profile(user, stats)
    except Exception as exc:
        logger.debug("_extract_user_from_rehydration failed for @%s: %s", username, exc)
        return None


def _extract_user_from_sigi(data: dict, username: str) -> dict | None:
    """Extract user dict from SIGI_STATE JSON."""
    try:
        user_module = data.get("UserModule", {})
        users = user_module.get("users", {})
        # Try exact match first, then case-insensitive
        user = users.get(username) or users.get(username.lower())
        if not user:
            # Try first value if only one user in the dict
            values = list(users.values())
            user = values[0] if values else None
        if not user:
            return None

        stats_module = data.get("UserModule", {}).get("stats", {})
        stats = stats_module.get(username) or stats_module.get(username.lower()) or {}
        return _build_profile(user, stats)
    except Exception as exc:
        logger.debug("_extract_user_from_sigi failed for @%s: %s", username, exc)
        return None


def _build_profile(user: dict, stats: dict) -> dict:
    """Build normalized profile dict from raw TikTok user data."""
    bio_link_obj = user.get("bioLink") or {}
    bio_link = bio_link_obj.get("link") if isinstance(bio_link_obj, dict) else str(bio_link_obj) if bio_link_obj else None

    follower_count = (
        stats.get("followerCount")
        or user.get("followerCount")
        or user.get("fans_count")
        or 0
    )

    return {
        "uniqueId": user.get("uniqueId", ""),
        "nickname": user.get("nickname", ""),
        "signature": user.get("signature", ""),
        "followerCount": follower_count,
        "verified": bool(user.get("verified", False)),
        "bioLink": bio_link,
    }
