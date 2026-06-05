import asyncio
import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.parse import quote_plus
import httpx
from httpx import Response

from backend.config.settings import Settings
from backend.scraper import tiktok_serp

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - handled dynamically at runtime
    async_playwright = None

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional runtime dep
    curl_requests = None

logger = logging.getLogger(__name__)

HANDLE_RE = re.compile(r"^/@([A-Za-z0-9._-]{2,40})")
HANDLE_IN_FULL_URL_RE = re.compile(
    r"tiktok\.com/@([A-Za-z0-9._-]{2,40})(?:/|\?|#|$)", re.IGNORECASE
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
SCRIPT_REHYDRATE_RE = re.compile(
    r'__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.*?\})\s*;',
    re.DOTALL,
)
class TikTokMockClient:
    """
    Cliente local determinista para desarrollo.
    Simula discovery/enrichment sin depender de endpoints privados de TikTok.
    """

    async def discover(
        self,
        mode: str,
        target: str,
        max_results: int,
        exclude_handles: set[str] | None = None,
    ) -> list[dict]:
        excl = {str(x).lower().lstrip("@") for x in (exclude_handles or set()) if x}
        seed = int(hashlib.md5(f"{mode}:{target}".encode("utf-8")).hexdigest()[:8], 16)
        rnd = random.Random(seed)
        token = re.sub(r"[^a-zA-Z0-9]+", "", target.lower())[:12] or "creator"
        out: list[dict] = []
        idx = 0
        cap = max(max_results * 8, max_results + 10)
        while len(out) < max_results and idx < cap:
            idx += 1
            handle = f"{token}.{idx}"
            if handle.lower() in excl:
                continue
            uid = f"{token}_{idx:03d}"
            out.append(
                {
                    "source_type": mode,
                    "source_value": target,
                    "author_id": f"author_{uid}",
                    "author_handle": handle,
                    "video_id": f"video_{uid}",
                    "description": f"{target} creator {idx}",
                    "hashtags": [target.split()[0].replace("#", "")[:20] or "leadgen"],
                    "view_count": rnd.randint(500, 400000),
                    "like_count": rnd.randint(50, 30000),
                    "comment_count": rnd.randint(5, 4000),
                    "share_count": rnd.randint(1, 6000),
                    "created_at": "2026-01-01T00:00:00Z",
                }
            )
        return out

    async def enrich_author(self, author_id: str, handle: str) -> dict:
        num = int(hashlib.md5(author_id.encode("utf-8")).hexdigest()[:6], 16)
        domain = handle.replace(".", "").replace("_", "")[:16] or "creator"
        email = f"contacto@{domain}.com" if num % 3 == 0 else None
        phone = f"+34 6{num % 100000000:08d}" if num % 17 == 0 else None
        website = f"https://{domain}.site" if num % 4 == 0 else None
        bio = (
            f"Perfil de {handle}. "
            + (f"Contacto: {email}. " if email else "")
            + (f"Web: {website}. " if website else "")
        ).strip()
        return {
            "author_id": author_id,
            "handle": handle,
            "nickname": handle.replace(".", " ").title(),
            "bio": bio,
            "followers": 500 + (num % 500000),
            "following": 100 + (num % 4000),
            "likes": 1000 + (num % 4000000),
            "verified": bool(num % 9 == 0),
            "email": email,
            "phone": phone,
            "website": website,
        }


def _discovery_tag_slug(mode: str, target: str) -> str:
    """Slug estable para /tag/ y mirrors Jina (evita `nicho|ciudad` literal en la URL del tag)."""
    t = (target or "").strip()
    if mode != "hashtag":
        return t
    niche, _loc = tiktok_serp.parse_niche_location(t)
    base = (niche or t).strip().lstrip("#").split("|")[0].strip()
    return base or "tiktok"


def _handle_from_discovery_href(href: str | None) -> str | None:
    """TikTok suele usar `/@user` o URL absoluta; el scroll en /tag/ devuelve sobre todo absolutas."""
    if not href:
        return None
    h = href.strip()
    m = HANDLE_RE.match(h)
    if m:
        return m.group(1).strip()
    m2 = HANDLE_IN_FULL_URL_RE.search(h)
    if m2:
        return m2.group(1).strip()
    return None


def _norm_handle_key(h: str | None) -> str:
    if not h:
        return ""
    return str(h).strip().lstrip("@").lower()


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _http_enrich_has_contact_signal(profile: dict) -> bool:
    """True si el enrich por HTTP ya aporta email (objetivo del job).

    Intencionalmente ignora phone y website: en TikTok casi todos los perfiles
    tienen un bioLink (website), lo que activaría el retorno anticipado y saltaría
    Playwright aunque el email solo sea visible en el DOM renderizado.
    """
    e = profile.get("email")
    return bool(e and str(e).strip())


def _parse_compact_number(value: str | int | float | None) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().upper().replace(",", ".")
    mult = 1
    if text.endswith("K"):
        mult = 1_000
        text = text[:-1]
    elif text.endswith("M"):
        mult = 1_000_000
        text = text[:-1]
    elif text.endswith("B"):
        mult = 1_000_000_000
        text = text[:-1]
    try:
        return int(float(text) * mult)
    except Exception:
        return _safe_int(re.sub(r"[^\d]", "", text), 0)


def _normalize_email_candidate(raw: str | None) -> str | None:
    """Evita que el regex coma texto colado tras el TLD (p. ej. .com.Mira)."""
    if not raw:
        return None
    s = raw.strip().rstrip(".,;:")
    parts = s.split("@")
    if len(parts) != 2:
        return s if "@" in s else None
    local, domain = parts[0], parts[1]
    segs = [x for x in domain.split(".") if x]
    common_tlds = {
        "com",
        "es",
        "org",
        "net",
        "io",
        "co",
        "uk",
        "eu",
        "de",
        "fr",
        "cat",
        "info",
        "biz",
        "pro",
    }
    while len(segs) >= 2:
        tld = segs[-1].lower()
        if len(tld) <= 6 and tld.isalpha() and tld in common_tlds:
            return f"{local}@{'.'.join(segs)}"
        if len(tld) <= 4 and tld.isalpha() and tld not in common_tlds:
            segs.pop()
            continue
        if len(tld) > 6 or not tld.isalpha():
            segs.pop()
            continue
        return f"{local}@{'.'.join(segs)}"
    return None


def _extract_from_text(text: str) -> tuple[str | None, str | None, str | None]:
    email_match = EMAIL_RE.search(text or "")
    phone_match = PHONE_RE.search(text or "")
    website_match = re.search(r"https?://[^\s)]+", text or "", re.IGNORECASE)
    raw_email = email_match.group(0) if email_match else None
    return (
        _normalize_email_candidate(raw_email),
        phone_match.group(0) if phone_match else None,
        website_match.group(0) if website_match else None,
    )


class TikTokLiveClient:
    _proxy_stats: dict[str, dict[str, int]] = {}
    _proxy_cooldown_until: dict[str, float] = {}
    _user_agent_profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "accept-language": "en-US,en;q=0.9",
            "platform": "Windows",
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "accept-language": "en-US,en;q=0.9",
            "platform": "macOS",
        },
        {
            "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "accept-language": "en-US,en;q=0.9",
            "platform": "Linux",
        },
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
            "sec-ch-ua": None,
            "sec-ch-ua-mobile": None,
            "sec-ch-ua-platform": None,
            "accept-language": "en-US,en;q=0.5",
            "platform": "Windows",
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
            "sec-ch-ua": None,
            "sec-ch-ua-mobile": None,
            "sec-ch-ua-platform": None,
            "accept-language": "es-ES,es;q=0.8,en;q=0.5",
            "platform": "macOS",
        },
    ]
    _ua_profile_index = 0
    _common_viewports = [
        {"width": 1920, "height": 1080},  # Full HD
        {"width": 1366, "height": 768},   # Laptop común
        {"width": 1440, "height": 900},   # MacBook Air
        {"width": 2560, "height": 1440},  # 2K
        {"width": 1536, "height": 864},   # Laptop HD+
    ]

    @classmethod
    def _get_current_ua_profile(cls) -> dict:
        """Devuelve el perfil de UA actual y rota al siguiente."""
        profile = cls._user_agent_profiles[cls._ua_profile_index]
        cls._ua_profile_index = (cls._ua_profile_index + 1) % len(cls._user_agent_profiles)
        return profile

    @classmethod
    def _build_headers_from_profile(cls, profile: dict, base_headers: dict = None) -> dict:
        """Construye headers completos desde un perfil de UA."""
        headers = base_headers.copy() if base_headers else {}
        headers["User-Agent"] = profile["ua"]
        headers["Accept-Language"] = profile["accept-language"]
        
        # Client Hints (solo si el perfil los tiene - Firefox no)
        if profile["sec-ch-ua"]:
            headers["sec-ch-ua"] = profile["sec-ch-ua"]
            headers["sec-ch-ua-mobile"] = profile["sec-ch-ua-mobile"]
            headers["sec-ch-ua-platform"] = profile["sec-ch-ua-platform"]
        
        return headers

    @staticmethod
    def _human_delay_ms(mean_ms: int = 2000) -> int:
        """Genera delay con distribución lognormal (más humano que uniform)."""
        mu = 7.5
        sigma = 0.5
        delay = int(random.lognormvariate(mu, sigma))
        return max(800, min(delay, 6000))

    @staticmethod
    async def _human_scroll(page, num_scrolls: int = 5):
        """Scroll progresivo con delays variables (comportamiento humano)."""
        viewport_height = await page.evaluate("window.innerHeight")
        
        for i in range(num_scrolls):
            scroll_amount = random.randint(viewport_height // 2, viewport_height)
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            
            delay = random.randint(800, 2500)
            await page.wait_for_timeout(delay)
            
            if random.random() < 0.1:
                await page.wait_for_timeout(random.randint(3000, 5000))

    def __init__(self):
        self._last_discovery_raw_count = 0
        self._wait_turn_cb = None
        self._wait_turn_enrich_cb = None
        self._enrich_phase = False
        self._event_cb = None
        self._cookie_jar: dict = {}
        self._referer_chain: list[str] = []
        self._metrics = {
            "requests_total": 0,
            "blocked_count": 0,
            "captcha_count": 0,
            "proxy_switches": 0,
            "source_attempts": 0,
            "source_success": 0,
        }
        self._last_proxy_used: str | None = None

    def set_runtime_hooks(self, wait_turn_cb=None, wait_turn_enrich_cb=None, event_cb=None):
        self._wait_turn_cb = wait_turn_cb
        self._wait_turn_enrich_cb = wait_turn_enrich_cb
        self._event_cb = event_cb

    async def _wait_turn_for_request(self):
        # Durante enrich: `_run_job` ya aplicó `wait_turn` o throttle antes de cada perfil.
        # No repetir aquí por cada GET interno (rotación de proxies / HTTP-first), o el
        # tiempo por perfil se multiplica de forma brutal en ENRICH_CONCURRENCY=1.
        if self._enrich_phase:
            if self._wait_turn_enrich_cb is not None:
                await self._wait_turn_enrich_cb()
            return
        if self._wait_turn_cb is not None:
            await self._wait_turn_cb()
            return
        delay = random.uniform(
            Settings.TIKTOKLEADS_DISCOVERY_MIN_DELAY,
            Settings.TIKTOKLEADS_DISCOVERY_MAX_DELAY,
        )
        await asyncio.sleep(delay)

    async def _log_event(self, stage: str, level: str, message: str):
        if self._event_cb is not None:
            await self._event_cb(stage, level, message)
        else:
            logf = logger.warning if level in {"warning", "error"} else logger.info
            logf("%s: %s", stage, message)

    def _record_proxy_result(self, proxy: str | None, ok: bool):
        if not proxy:
            return
        stats = self.__class__._proxy_stats.setdefault(proxy, {"ok": 0, "fail": 0})
        key = "ok" if ok else "fail"
        stats[key] += 1
        # Si falla mucho, lo vetamos temporalmente con cooldown multiplicado.
        if not ok and stats["fail"] >= max(3, stats["ok"] + 2):
            multiplier = Settings.TIKTOKLEADS_PROXY_COOLDOWN_MULTIPLIER_ON_FAIL
            cooldown = Settings.TIKTOKLEADS_PROXY_COOLDOWN_SECONDS * multiplier
            self.__class__._proxy_cooldown_until[proxy] = time.monotonic() + cooldown

    def _update_cookies_from_response(self, resp: Response | None):
        """Extrae y persiste cookies de respuestas exitosas."""
        if resp and resp.status_code < 400 and hasattr(resp, 'cookies'):
            for name, value in resp.cookies.items():
                self._cookie_jar[name] = value

    def _get_next_referer(self, current_url: str) -> str | None:
        """Devuelve Referer apropiado basado en historial."""
        if not self._referer_chain:
            return "https://www.google.com/"
        return self._referer_chain[-1] if self._referer_chain else None

    def _update_referer_chain(self, url: str):
        """Actualiza historial de URLs para Referer chain."""
        self._referer_chain.append(url)
        if len(self._referer_chain) > 5:
            self._referer_chain.pop(0)

    def _candidate_proxies(self, max_proxy_tries: int | None = None) -> list[str | None]:
        """Proxies ordenados por tasa de éxito; conexión directa al final si está habilitada.

        Antes se intentaba primero directo y un solo proxy: con listas rotatorias eso
        desaprovechaba IPs con mejor historial y quemaba el intento en el orden fijo.
        """
        limit = max_proxy_tries
        if limit is None:
            limit = Settings.TIKTOKLEADS_PROXY_FALLBACK_MAX
        limit = max(1, int(limit))

        now = time.monotonic()
        pool = [p.strip() for p in Settings.TIKTOKLEADS_PROXY_LIST if p and str(p).strip()]
        if not pool:
            return [None]

        cls = self.__class__
        ranked: list[tuple[tuple[float, int], str]] = []

        for p in pool:
            if cls._proxy_cooldown_until.get(p, 0) > now:
                continue
            stats = cls._proxy_stats.get(p, {})
            ok = int(stats.get("ok", 0))
            fail = int(stats.get("fail", 0))
            total = ok + fail
            if total > 5 and ok == 0:
                continue
            if total == 0:
                score = 0.45
            else:
                score = ok / total
            ranked.append(((score, ok), p))

        ranked.sort(key=lambda item: (item[0][0], item[0][1]), reverse=True)
        chosen = [p for _, p in ranked[:limit]]

        if not chosen:
            return [None]

        if Settings.TIKTOKLEADS_HTTP_FALLBACK_DIRECT_LAST:
            chosen.append(None)
        return chosen

    @staticmethod
    def _playwright_proxy_config(proxy_url: str) -> dict | None:
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url)
        if not parsed.scheme or not parsed.hostname:
            return None
        server = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            server = f"{server}:{parsed.port}"
        cfg = {"server": server}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg

    @classmethod
    def proxy_stats(cls) -> list[dict]:
        now = time.monotonic()
        report = []
        for proxy, stats in cls._proxy_stats.items():
            ok = int(stats.get("ok", 0))
            fail = int(stats.get("fail", 0))
            total = ok + fail
            cooldown_left = max(0, int(cls._proxy_cooldown_until.get(proxy, 0) - now))
            report.append(
                {
                    "proxy": proxy,
                    "ok": ok,
                    "fail": fail,
                    "success_rate": round((ok / total), 3) if total else 0.0,
                    "cooldown_left_s": cooldown_left,
                }
            )
        report.sort(key=lambda r: (r["success_rate"], r["ok"], -r["fail"]), reverse=True)
        return report

    @classmethod
    def load_proxy_stats(cls, rows: list[dict]):
        cls._proxy_stats = {}
        cls._proxy_cooldown_until = {}
        now = time.monotonic()
        for row in rows:
            proxy = row.get("proxy")
            if not proxy:
                continue
            ok = int(row.get("ok", 0))
            fail = int(row.get("fail", 0))
            cls._proxy_stats[proxy] = {"ok": ok, "fail": fail}
            cooldown_left = int(row.get("cooldown_left_s", 0))
            if cooldown_left > 0:
                cls._proxy_cooldown_until[proxy] = now + cooldown_left

    def metrics(self) -> dict:
        attempts = max(1, self._metrics["source_attempts"])
        return {
            "requests_total": int(self._metrics["requests_total"]),
            "blocked_count": int(self._metrics["blocked_count"]),
            "captcha_count": int(self._metrics["captcha_count"]),
            "proxy_switches": int(self._metrics["proxy_switches"]),
            "source_success_rate": round(self._metrics["source_success"] / attempts, 3),
        }

    async def _launch_browser(self, playwright):
        launch_attempts = [
            {
                "headless": Settings.TIKTOKLEADS_PLAYWRIGHT_HEADLESS,
                "channel": "chrome",
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-site-isolation-trials",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-features=VizDisplayCompositor",
                ],
            },
            {
                "headless": Settings.TIKTOKLEADS_PLAYWRIGHT_HEADLESS,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-site-isolation-trials",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-features=VizDisplayCompositor",
                ],
            },
        ]
        last_error = None
        for cfg in launch_attempts:
            try:
                return await playwright.chromium.launch(**cfg)
            except Exception as exc:  # pragma: no cover
                last_error = exc
        raise RuntimeError(f"No se pudo lanzar navegador Playwright: {last_error}")

    @staticmethod
    def is_available() -> bool:
        """Extracción real siempre intentable (HTTP/SERP); Playwright es opcional."""
        return True

    @staticmethod
    def playwright_installed() -> bool:
        return async_playwright is not None

    def _search_url(self, mode: str, target: str) -> str:
        token = target.strip()
        if mode == "hashtag":
            normalized = _discovery_tag_slug("hashtag", token)
            return f"https://www.tiktok.com/tag/{quote_plus(normalized)}"
        q = tiktok_serp.web_search_q_for_target(mode, target)
        return f"https://www.tiktok.com/search?q={quote_plus(q)}"

    def _extract_handles(self, text: str) -> list[str]:
        return tiktok_serp.extract_handles_from_html_with_uddg(text)

    def _make_discovery_item(self, mode: str, target: str, handle: str) -> dict:
        return {
            "source_type": mode,
            "source_value": target,
            "author_id": f"live_{handle.lower()}",
            "author_handle": handle,
            "video_id": None,
            "description": "",
            "hashtags": [target.replace("#", "").split()[0][:30] or "tiktok"],
            "view_count": 0,
            "like_count": 0,
            "comment_count": 0,
            "share_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _extract_profile_from_universal_data(self, html: str) -> dict:
        out = {
            "author_id": None,
            "handle": None,
            "nickname": None,
            "bio": "",
            "followers": 0,
            "following": 0,
            "likes": 0,
            "verified": False,
            "website": None,
            "email": None,
            "phone": None,
        }
        match = SCRIPT_REHYDRATE_RE.search(html or "")
        if not match:
            return out
        try:
            data = json.loads(match.group(1))
        except Exception:
            return out
        user_info = {}
        stats = {}
        try:
            user_detail = (data.get("__DEFAULT_SCOPE__", {}) or {}).get("webapp.user-detail", {})
            user_info = (user_detail.get("userInfo", {}) or {}).get("user", {}) or {}
            stats = (user_detail.get("userInfo", {}) or {}).get("stats", {}) or {}
        except Exception:
            pass
        out["author_id"] = user_info.get("id")
        out["handle"] = user_info.get("uniqueId")
        out["nickname"] = user_info.get("nickname")
        out["bio"] = user_info.get("signature") or ""
        out["followers"] = _parse_compact_number(stats.get("followerCount"))
        out["following"] = _parse_compact_number(stats.get("followingCount"))
        out["likes"] = _parse_compact_number(stats.get("heartCount"))
        out["verified"] = bool(user_info.get("verified"))
        bio_link = (user_info.get("bioLink") or {}).get("link")
        out["website"] = bio_link
        email, phone, website = _extract_from_text((out["bio"] or "") + " " + (html or "")[:3000])
        out["email"] = email
        out["phone"] = phone
        out["website"] = out["website"] or website
        return out

    def _looks_blocked(self, status_code: int, body: str) -> tuple[bool, bool]:
        low = (body or "").lower()
        blocked = status_code in {401, 403, 429} or any(
            k in low for k in ("access denied", "blocked", "/challenge", "verify", "captcha")
        )
        captcha = "captcha" in low or "verify" in low
        return blocked, captcha

    async def _do_get(self, client: httpx.AsyncClient, url: str, proxy: str | None, **kwargs) -> Response | None:
        self._metrics["requests_total"] += 1
        await self._wait_turn_for_request()
        # Incluir cookies si hay en el jar
        if self._cookie_jar:
            kwargs.setdefault('cookies', {}).update(self._cookie_jar)
        if curl_requests is not None:
            try:
                async with curl_requests.AsyncSession(impersonate="chrome131", timeout=30) as session:
                    resp = await asyncio.wait_for(
                        session.get(
                            url,
                            proxies={"http": proxy, "https": proxy} if proxy else None,
                            **kwargs,
                        ),
                        timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT,
                    )
                    return Response(
                        status_code=resp.status_code,
                        headers=resp.headers,
                        content=resp.content or b"",
                        request=httpx.Request("GET", url),
                    )
            except Exception as exc:
                logger.debug("curl GET failed (%s): %s", url, exc)
        try:
            return await asyncio.wait_for(
                client.get(url, proxy=proxy, timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT, **kwargs),
                timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT + 1,
            )
        except Exception as exc:
            logger.debug("httpx GET failed (%s): %s", url, exc)
            return None

    async def _do_post(self, client: httpx.AsyncClient, url: str, proxy: str | None, **kwargs) -> Response | None:
        self._metrics["requests_total"] += 1
        await self._wait_turn_for_request()
        # Incluir cookies si hay en el jar
        if self._cookie_jar:
            kwargs.setdefault('cookies', {}).update(self._cookie_jar)
        if curl_requests is not None:
            try:
                async with curl_requests.AsyncSession(impersonate="chrome131", timeout=30) as session:
                    resp = await asyncio.wait_for(
                        session.post(
                            url,
                            proxies={"http": proxy, "https": proxy} if proxy else None,
                            **kwargs,
                        ),
                        timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT,
                    )
                    return Response(
                        status_code=resp.status_code,
                        headers=resp.headers,
                        content=resp.content or b"",
                        request=httpx.Request("POST", url),
                    )
            except Exception as exc:
                logger.debug("curl POST failed (%s): %s", url, exc)
        try:
            return await asyncio.wait_for(
                client.post(url, proxy=proxy, timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT, **kwargs),
                timeout=Settings.TIKTOKLEADS_SOURCE_TIMEOUT + 1,
            )
        except Exception as exc:
            logger.debug("httpx POST failed (%s): %s", url, exc)
            return None

    async def _http_get_with_proxy_fallback(self, client: httpx.AsyncClient, url: str, **kwargs):
        """Intenta con proxy rotativo y, si falla, reintenta en directo."""
        proxies_to_try = self._candidate_proxies()
        for proxy in proxies_to_try:
            try:
                if proxy != self._last_proxy_used:
                    self._metrics["proxy_switches"] += 1
                    self._last_proxy_used = proxy
                resp = await self._do_get(client, url, proxy, **kwargs)
                if not resp:
                    self._record_proxy_result(proxy, False)
                    continue
                blocked, captcha = self._looks_blocked(resp.status_code, resp.text)
                if blocked:
                    self._metrics["blocked_count"] += 1
                if captcha:
                    self._metrics["captcha_count"] += 1
                self._record_proxy_result(proxy, resp.status_code < 400 and not blocked)
                if resp.status_code < 500 and not blocked:
                    self._update_cookies_from_response(resp)
                    return resp
            except Exception as exc:
                logger.debug("GET source failed (%s): %s", url, exc)
                self._record_proxy_result(proxy, False)
                continue
        return None

    async def _http_post_with_proxy_fallback(self, client: httpx.AsyncClient, url: str, **kwargs):
        """Intenta POST con proxy rotativo y fallback directo."""
        proxies_to_try = self._candidate_proxies()
        for proxy in proxies_to_try:
            try:
                if proxy != self._last_proxy_used:
                    self._metrics["proxy_switches"] += 1
                    self._last_proxy_used = proxy
                resp = await self._do_post(client, url, proxy, **kwargs)
                if not resp:
                    self._record_proxy_result(proxy, False)
                    continue
                blocked, captcha = self._looks_blocked(resp.status_code, resp.text)
                if blocked:
                    self._metrics["blocked_count"] += 1
                if captcha:
                    self._metrics["captcha_count"] += 1
                self._record_proxy_result(proxy, resp.status_code < 400 and not blocked)
                if resp.status_code < 500 and not blocked:
                    self._update_cookies_from_response(resp)
                    return resp
            except Exception as exc:
                logger.debug("POST source failed (%s): %s", url, exc)
                self._record_proxy_result(proxy, False)
                continue
        return None

    async def _discover_via_http(
        self,
        mode: str,
        target: str,
        limit: int,
        exclude_norm: frozenset[str] | None = None,
    ) -> list[dict]:
        started_at = time.monotonic()

        def out_of_time() -> bool:
            return (time.monotonic() - started_at) >= Settings.TIKTOKLEADS_DISCOVERY_BUDGET_SECONDS

        profile = self._get_current_ua_profile()
        headers = self._build_headers_from_profile(profile, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        })
        referer = self._get_next_referer("https://www.tiktok.com/")
        if referer:
            headers["Referer"] = referer
        
        primary = Settings.TIKTOKLEADS_DISCOVERY_PRIMARY
        if primary not in ("serp", "tiktok_web", "both"):
            primary = "serp"

        t_stripped = target.strip()
        urls = [self._search_url(mode, target)]
        if mode == "hashtag":
            slug = _discovery_tag_slug("hashtag", t_stripped)
            if slug:
                urls.append(f"https://r.jina.ai/http://www.tiktok.com/tag/{quote_plus(slug)}")
        search_q = tiktok_serp.web_search_q_for_target(mode, t_stripped)
        urls.append(
            f"https://r.jina.ai/http://www.tiktok.com/search?q={quote_plus(search_q.lstrip('#'))}"
        )
        queries = tiktok_serp.build_dork_queries(mode, target)

        found: list[dict] = []
        seen: set[str] = set(exclude_norm or ())

        async def wrap_post(
            client: httpx.AsyncClient, url: str, *, headers: dict, data: dict
        ):
            self._metrics["source_attempts"] += 1
            resp = await self._http_post_with_proxy_fallback(
                client, url, headers=headers, data=data
            )
            if resp and resp.status_code < 400:
                blocked, _ = self._looks_blocked(resp.status_code, resp.text)
                if not blocked:
                    self._metrics["source_success"] += 1
            return resp

        async def wrap_get(
            client: httpx.AsyncClient, url: str, *, headers: dict, params: dict | None = None
        ):
            self._metrics["source_attempts"] += 1
            kw: dict = {"headers": headers}
            if params is not None:
                kw["params"] = params
            resp = await self._http_get_with_proxy_fallback(client, url, **kw)
            if resp and resp.status_code < 400:
                blocked, _ = self._looks_blocked(resp.status_code, resp.text)
                if not blocked:
                    self._metrics["source_success"] += 1
            return resp

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:

            async def consume_tiktok_jina() -> None:
                for url in urls:
                    if out_of_time() or len(found) >= limit:
                        return
                    try:
                        self._metrics["source_attempts"] += 1
                        resp = await self._http_get_with_proxy_fallback(
                            client, url, headers=headers
                        )
                        if not resp or resp.status_code >= 400:
                            await self._log_event(
                                "discovery_source",
                                "warning",
                                json.dumps(
                                    {"source": url, "status": getattr(resp, "status_code", None)}
                                ),
                            )
                            continue
                        html = resp.text
                        blocked, _ = self._looks_blocked(resp.status_code, html)
                        if not blocked:
                            self._metrics["source_success"] += 1
                        for handle in self._extract_handles(html):
                            nk = _norm_handle_key(handle)
                            if not nk or nk in seen:
                                continue
                            seen.add(nk)
                            found.append(self._make_discovery_item(mode, target, handle))
                            if len(found) >= limit:
                                return
                    except Exception as exc:
                        await self._log_event(
                            "discovery_source",
                            "warning",
                            json.dumps({"source": url, "error": str(exc)}),
                        )

            async def consume_serp_queries() -> None:
                for query in queries:
                    if out_of_time() or len(found) >= limit:
                        return
                    try:
                        handles = await tiktok_serp.scrape_serp_handles_for_query(
                            wrap_post,
                            wrap_get,
                            client,
                            query,
                            headers,
                            self._log_event,
                        )
                    except Exception as exc:
                        await self._log_event(
                            "discovery_source",
                            "warning",
                            json.dumps({"query": query[:80], "error": str(exc)}),
                        )
                        continue
                    for handle in handles:
                        nk = _norm_handle_key(handle)
                        if not nk or nk in seen:
                            continue
                        seen.add(nk)
                        found.append(self._make_discovery_item(mode, target, handle))
                        if len(found) >= limit:
                            return

            if primary == "serp":
                await consume_serp_queries()
                if len(found) < limit:
                    await consume_tiktok_jina()
            elif primary == "tiktok_web":
                await consume_tiktok_jina()
                if len(found) < limit:
                    await consume_serp_queries()
            else:  # "both"
                await consume_serp_queries()
                if len(found) < limit:
                    await consume_tiktok_jina()

        if not found:
            raise RuntimeError("No se pudieron extraer handles reales vía HTTP.")
        return found

    async def _enrich_via_http(
        self, author_id: str, handle: str, *, jina_fallback: bool = True
    ) -> dict:
        url = f"https://www.tiktok.com/@{quote_plus(handle)}"
        alt_url = f"https://r.jina.ai/http://www.tiktok.com/@{quote_plus(handle)}"
        profile = self._get_current_ua_profile()
        headers = self._build_headers_from_profile(profile, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await self._http_get_with_proxy_fallback(client, url, headers=headers)
            html = resp.text if resp and resp.status_code < 400 else ""
            if not html and jina_fallback:
                resp2 = await self._http_get_with_proxy_fallback(client, alt_url, headers=headers)
                html = resp2.text if resp2 and resp2.status_code < 400 else ""
        parsed = self._extract_profile_from_universal_data(html)
        bio_match = re.search(r'<meta name="description" content="([^"]+)"', html)
        fallback_bio = bio_match.group(1) if bio_match else ""
        bio = parsed.get("bio") or fallback_bio
        email, phone, website = _extract_from_text((bio or "") + " " + html[:3000])
        return {
            "author_id": parsed.get("author_id") or author_id,
            "handle": parsed.get("handle") or handle,
            "nickname": parsed.get("nickname") or handle,
            "bio": bio[:500],
            "followers": int(parsed.get("followers") or 0),
            "following": int(parsed.get("following") or 0),
            "likes": int(parsed.get("likes") or 0),
            "verified": bool(parsed.get("verified")) or ("verified" in html.lower()),
            "email": parsed.get("email") or email,
            "phone": parsed.get("phone") or phone,
            "website": parsed.get("website") or website,
        }

    async def _discover_related_from_profile_http(self, seed_handle: str, limit: int) -> list[dict]:
        if not seed_handle:
            return []
        profile = self._get_current_ua_profile()
        headers = self._build_headers_from_profile(profile, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })
        urls = [
            f"https://www.tiktok.com/@{quote_plus(seed_handle)}",
            f"https://r.jina.ai/http://www.tiktok.com/@{quote_plus(seed_handle)}",
        ]
        related: list[dict] = []
        seen: set[str] = {seed_handle.lower()}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            for src in urls:
                if len(related) >= limit:
                    break
                resp = await self._http_get_with_proxy_fallback(client, src, headers=headers)
                if not resp or resp.status_code >= 400:
                    continue
                for handle in self._extract_handles(resp.text):
                    h = handle.strip().lstrip("@")
                    if not h or h.lower() in seen:
                        continue
                    seen.add(h.lower())
                    related.append(self._make_discovery_item("profile_related", f"@{seed_handle}", h))
                    if len(related) >= limit:
                        break
        return related

    async def _discover_keyword_seeds_via_http(self, target: str, limit: int) -> list[str]:
        token = target.replace("#", "").replace("@", "").strip()
        if not token:
            return []
        first = token.split()[0]
        profile = self._get_current_ua_profile()
        headers = self._build_headers_from_profile(profile, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })
        urls = [
            f"https://www.tiktok.com/tag/{quote_plus(first)}",
            f"https://r.jina.ai/http://www.tiktok.com/tag/{quote_plus(first)}",
            f"https://r.jina.ai/http://www.tiktok.com/search?q={quote_plus(token)}",
        ]
        out: list[str] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            for src in urls:
                if len(out) >= limit:
                    break
                self._metrics["source_attempts"] += 1
                resp = await self._http_get_with_proxy_fallback(client, src, headers=headers)
                if not resp or resp.status_code >= 400:
                    await self._log_event(
                        "discovery_seed",
                        "warning",
                        json.dumps({"source": src, "status": getattr(resp, "status_code", None)}),
                    )
                    continue
                self._metrics["source_success"] += 1
                for h in self._extract_handles(resp.text):
                    clean = h.strip().lstrip("@")
                    if not clean or clean.lower() in seen:
                        continue
                    seen.add(clean.lower())
                    out.append(clean)
                    if len(out) >= limit:
                        break
        return out

    async def discover(
        self,
        mode: str,
        target: str,
        max_results: int,
        exclude_handles: set[str] | None = None,
    ) -> list[dict]:
        limit = max(1, min(max_results, Settings.TIKTOKLEADS_MAX_DISCOVERY_RESULTS))
        exclude_norm = frozenset(
            nk for x in (exclude_handles or set()) if (nk := _norm_handle_key(x))
        )
        handles_by_norm: dict[str, dict] = {}

        def add_item(item: dict | None) -> bool:
            if not item:
                return False
            nk = _norm_handle_key(item.get("author_handle"))
            if not nk or nk in exclude_norm or nk in handles_by_norm:
                return False
            handles_by_norm[nk] = item
            return True

        try:
            for item in await self._discover_via_http(mode, target, limit, exclude_norm=exclude_norm):
                add_item(item)
                if len(handles_by_norm) >= limit:
                    break
        except Exception as exc:
            logger.warning("Discovery HTTP fallido para target='%s': %s", target, exc)

        if mode == "keyword" and len(handles_by_norm) < limit:
            try:
                seed_limit = max(2, min(6, limit))
                seeds = await self._discover_keyword_seeds_via_http(target, seed_limit)
                for seed in seeds:
                    if _norm_handle_key(seed) in exclude_norm:
                        continue
                    add_item(self._make_discovery_item("keyword_seed", target, seed))
                for seed in seeds:
                    if len(handles_by_norm) >= limit:
                        break
                    for item in await self._discover_related_from_profile_http(
                        seed, limit - len(handles_by_norm)
                    ):
                        add_item(item)
                        if len(handles_by_norm) >= limit:
                            break
            except Exception as exc:
                logger.warning("Discovery keyword-hybrid fallido para target='%s': %s", target, exc)

        if mode == "hashtag" and len(handles_by_norm) < limit:
            try:
                seed_limit = max(2, min(8, limit))
                slug = _discovery_tag_slug("hashtag", target)
                seeds = await self._discover_keyword_seeds_via_http(slug, seed_limit)
                for seed in seeds:
                    if _norm_handle_key(seed) in exclude_norm:
                        continue
                    add_item(self._make_discovery_item("hashtag_seed", target, seed))
                for seed in seeds:
                    if len(handles_by_norm) >= limit:
                        break
                    for item in await self._discover_related_from_profile_http(
                        seed, limit - len(handles_by_norm)
                    ):
                        add_item(item)
                        if len(handles_by_norm) >= limit:
                            break
            except Exception as exc:
                logger.warning("Discovery hashtag-hybrid fallido para target='%s': %s", target, exc)

        if len(handles_by_norm) >= limit:
            self._last_discovery_raw_count = len(handles_by_norm)
            return list(handles_by_norm.values())[:limit]

        if async_playwright is not None:
            url = self._search_url(mode, target)
            max_scrolls = max(1, Settings.TIKTOKLEADS_MAX_SCROLLS)
            try:
                async with async_playwright() as p:
                    browser = await self._launch_browser(p)
                    context_kwargs = {
                        "locale": "es-ES",
                        "user_agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "viewport": random.choice(TikTokLiveClient._common_viewports),
                    }
                    if Settings.TIKTOKLEADS_USE_PROXY_FOR_PLAYWRIGHT:
                        pw_proxy = (
                            Settings.TIKTOKLEADS_PLAYWRIGHT_PROXY
                            or Settings.TIKTOKLEADS_HTTP_PROXY
                        )
                        proxy_cfg = self._playwright_proxy_config(pw_proxy) if pw_proxy else None
                        if proxy_cfg:
                            context_kwargs["proxy"] = proxy_cfg

                    context = await browser.new_context(**context_kwargs)
                    
                    # 2b) Stealth: Solo fallback básico (menos detectable que playwright-stealth completo)
                    await context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """)
                    
                    page = await context.new_page()
                    
                    page.set_default_timeout(Settings.TIKTOKLEADS_NAV_TIMEOUT_MS)
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(self._human_delay_ms(Settings.TIKTOKLEADS_PAGE_WAIT_MS))

                    for _ in range(max_scrolls):
                        hrefs = await page.eval_on_selector_all(
                            "a[href]",
                            "els => els.map(e => e.getAttribute('href') || '')",
                        )
                        tag_label = (
                            _discovery_tag_slug(mode, target)
                            if mode == "hashtag"
                            else (target.replace("#", "").split()[0][:30] or "tiktok")
                        )
                        for href in hrefs:
                            handle = _handle_from_discovery_href(href)
                            if not handle:
                                continue
                            nk = _norm_handle_key(handle)
                            if not nk or nk in exclude_norm or nk in handles_by_norm:
                                continue
                            author_id = f"live_{nk}"
                            handles_by_norm[nk] = {
                                "source_type": mode,
                                "source_value": target,
                                "author_id": author_id,
                                "author_handle": handle,
                                "video_id": None,
                                "description": "",
                                "hashtags": [tag_label],
                                "view_count": 0,
                                "like_count": 0,
                                "comment_count": 0,
                                "share_count": 0,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            }
                            if len(handles_by_norm) >= limit:
                                break
                        if len(handles_by_norm) >= limit:
                            break
                        await self._human_scroll(page, num_scrolls=1)

                    await context.close()
                    await browser.close()
            except Exception as exc:
                logger.warning("Discovery Playwright fallido para target='%s': %s", target, exc)

        self._last_discovery_raw_count = len(handles_by_norm)
        if not handles_by_norm:
            raise RuntimeError("No se encontraron handles reales en TikTok para ese target.")
        return list(handles_by_norm.values())[:limit]

    async def enrich_author(self, author_id: str, handle: str) -> dict:
        self._enrich_phase = True
        profile_url = f"https://www.tiktok.com/@{quote_plus(handle)}"
        try:
            if async_playwright is None:
                return await self._enrich_via_http(author_id, handle)

            if Settings.TIKTOKLEADS_ENRICH_HTTP_FIRST:
                # Sin Jina aquí: si no hay señal de contacto, Playwright cargará la página;
                # evita dos peticiones HTTP (tiktok+Jina) antes del navegador.
                http_prof = await self._enrich_via_http(
                    author_id, handle, jina_fallback=False
                )
                if _http_enrich_has_contact_signal(http_prof):
                    return http_prof

            try:
                async with async_playwright() as p:
                    browser = await self._launch_browser(p)
                    context = await browser.new_context(
                        locale="es-ES",
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                        ),
                        viewport=random.choice(TikTokLiveClient._common_viewports),
                    )
                    
                    # 2) Stealth: Solo fallback básico (menos detectable que playwright-stealth completo)
                    await context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """)
                    
                    page = await context.new_page()
                    
                    page.set_default_timeout(Settings.TIKTOKLEADS_NAV_TIMEOUT_MS)
                    await page.goto(profile_url, wait_until="domcontentloaded")
                    settle_ms = int(Settings.TIKTOKLEADS_ENRICH_PLAYWRIGHT_SETTLE_MS)
                    if settle_ms > 0:
                        await page.wait_for_timeout(self._human_delay_ms(settle_ms))

                    html = await page.content()
                    parsed = self._extract_profile_from_universal_data(html)
                    title = await page.title()
                    body_text = await page.evaluate(
                        "document.body ? document.body.innerText : ''"
                    )
                    description = await page.get_attribute(
                        "meta[name='description']", "content"
                    )
                    bio = (parsed.get("bio") or description or "")[:500]
                    bio_source = " ".join(filter(None, [bio, title, body_text[:2000]]))
                    email, phone, website = _extract_from_text(bio_source)
                    if parsed.get("email"):
                        email = email or parsed.get("email")
                    if parsed.get("phone"):
                        phone = phone or parsed.get("phone")
                    if parsed.get("website"):
                        website = website or parsed.get("website")

                    followers = int(parsed.get("followers") or 0)
                    following = int(parsed.get("following") or 0)
                    likes = int(parsed.get("likes") or 0)
                    if followers <= 0:
                        stats_candidates = re.findall(
                            r"(\d+(?:[.,]\d+)?[KMB]?)", body_text or ""
                        )
                        if stats_candidates:
                            followers = _parse_compact_number(stats_candidates[0])
                            if len(stats_candidates) > 1:
                                following = _parse_compact_number(stats_candidates[1])
                            if len(stats_candidates) > 2:
                                likes = _parse_compact_number(stats_candidates[2])

                    await context.close()
                    await browser.close()

                return {
                    "author_id": parsed.get("author_id") or author_id,
                    "handle": parsed.get("handle") or handle,
                    "nickname": parsed.get("nickname") or handle,
                    "bio": bio,
                    "followers": followers,
                    "following": following,
                    "likes": likes,
                    "verified": bool(parsed.get("verified"))
                    or ("verified" in (body_text or "").lower()),
                    "email": email,
                    "phone": phone,
                    "website": website,
                }
            except Exception as exc:
                logger.warning("Enrichment Playwright fallido para @%s: %s", handle, exc)
                return await self._enrich_via_http(author_id, handle)
        finally:
            self._enrich_phase = False
