import asyncio
import itertools
import logging
import re
from typing import AsyncGenerator

import httpx

from backend.config.settings import Settings
from backend.scraper.ig_deduplicator import Deduplicator
from backend.scraper.ig_profile import get_profile
from backend.scraper.ig_rate_limiter import DailyLimitReached
from backend.storage import database as db

logger = logging.getLogger(__name__)

INSTAGRAM_USERNAME_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]{2,30})"
)

_SKIP_SLUGS = {
    "p", "reel", "explore", "stories", "tv", "accounts", "about",
    "privacy", "legal", "help", "press", "api", "direct", "share",
    "reels", "tags", "locations", "ar", "challenges", "startpage",
}

_BASE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# --- Search engine ---

STARTPAGE_URL = "https://www.startpage.com/do/search"
STARTPAGE_HEADERS = {
    "User-Agent": _BASE_UA,
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# Round-robin iterator over configured proxies (reused across requests)
_proxy_cycle = itertools.cycle(Settings.IG_PROXY_LIST) if Settings.IG_PROXY_LIST else None


_NICHE_SYNONYMS: dict[str, list[str]] = {
    # Fitness / salud física
    "entrenador personal":   ["personal trainer", "fitness coach", "preparador fisico", "entrenador", "coach fitness"],
    "entrenador":            ["personal trainer", "fitness coach", "entrenamiento personal"],
    "preparador fisico":     ["personal trainer", "entrenador personal", "fitness coach"],
    # Nutrición
    "nutricionista":         ["nutritionist", "dietista", "dietitian", "dietista nutricionista", "nutricion"],
    "dietista":              ["nutritionist", "nutricionista", "dietitian", "dietetica"],
    "nutricion":             ["nutritionist", "dietista", "nutricionista", "alimentacion saludable"],
    # Psicología / mente
    "psicólogo":             ["psychologist", "psicologa", "terapeuta", "psicoterapia", "terapia psicologica"],
    "psicologia":            ["psychologist", "psicologa", "terapeuta", "psicoterapia"],
    "terapeuta":             ["therapist", "terapia", "psicologo", "coach"],
    "coach":                 ["life coach", "coaching", "mentor", "coach online", "coaching personal"],
    # Salud / medicina
    "medico":                ["doctor", "médico", "clinica médica", "medicina"],
    "dentista":              ["dentist", "clinica dental", "odontólogo", "odontologia"],
    "veterinario":           ["veterinary", "veterinaria", "clinica veterinaria", "veterinarios"],
    "fisioterapeuta":        ["physiotherapist", "fisioterapia", "rehabilitacion", "fisio"],
    "logopeda":              ["speech therapist", "logopedia", "terapeuta del habla"],
    "podólogo":              ["podologist", "podologia", "clinica podologica"],
    "dermatólogo":           ["dermatologist", "dermatologia", "clinica dermatologica"],
    "oculista":              ["ophthalmologist", "oftalmologia", "clinica visual"],
    "acupuntura":            ["acupuncture", "acupuntor", "medicina tradicional china"],
    # Abogado / legal
    "abogado":               ["lawyer", "abogada", "bufete", "asesor juridico", "abogados"],
    "abogada":               ["lawyer", "abogado", "bufete", "asesor legal"],
    "asesor fiscal":         ["tax advisor", "gestor fiscal", "fiscalista", "asesoria fiscal"],
    "gestor":                ["gestor administrativo", "asesoria", "gestoria"],
    # Diseño / arte / foto
    "fotografo":             ["photographer", "fotografia profesional", "fotografa"],
    "fotografia":            ["photography", "fotografo profesional", "fotografía"],
    "diseñador":             ["designer", "diseño grafico", "diseñadora", "graphic designer"],
    "diseño":                ["designer", "diseño grafico", "diseño web", "design"],
    "ilustrador":            ["illustrator", "ilustración", "artista digital"],
    "arquitecto":            ["architect", "arquitectura", "arquitecta", "interiorismo"],
    "interiorismo":          ["interior design", "interiorista", "decoracion de interiores"],
    # Marketing / digital
    "marketing":             ["marketing digital", "social media", "community manager", "agencia marketing"],
    "community manager":     ["social media manager", "marketing digital", "redes sociales"],
    "seo":                   ["posicionamiento web", "seo specialist", "marketing digital"],
    # Belleza / estética
    "peluquero":             ["hairdresser", "peluqueria", "estilista", "peluquera", "salon de belleza"],
    "peluqueria":            ["hairdresser", "salon de belleza", "estilista", "hair salon"],
    "esteticista":           ["aesthetician", "estetica", "beauty", "centro de estetica"],
    "estetica":              ["aesthetician", "centro de estetica", "beauty salon"],
    "tatuador":              ["tattoo artist", "tatuaje", "tattoo studio", "tattoo"],
    # Fitness wellness
    "yoga":                  ["yoga instructor", "profesor yoga", "instructora yoga", "yoga online"],
    "pilates":               ["pilates instructor", "profesor pilates", "clases pilates"],
    "natacion":              ["swimming coach", "natacion", "entrenador natacion"],
    # Otros servicios
    "fontanero":             ["plumber", "fontaneria", "instalaciones"],
    "electricista":          ["electrician", "instalacion electrica", "electricidad"],
    "carpintero":            ["carpenter", "carpinteria", "muebles a medida"],
    "pintor":                ["painter", "pintura de interiores", "pintor de pisos"],
    "organizador":           ["home organizer", "organizacion del hogar", "personal organizer"],
    "wedding planner":       ["organizador bodas", "wedding planner", "bodas"],
}


def _get_synonyms(niche: str) -> list[str]:
    """Return synonym list for a niche (case-insensitive lookup), or empty list."""
    niche_lower = niche.lower().strip()
    for key, synonyms in _NICHE_SYNONYMS.items():
        if key in niche_lower or niche_lower in key:
            return synonyms
    return []


def _build_queries(niche: str, location: str) -> list[str]:
    """Build a diverse set of DDG queries for a niche+location pair.

    Organized in tiers from highest expected email yield to broadest fallback.
    Returns a deduplicated ordered list.
    """
    queries: list[str] = []

    # --- Tier 1: email-forward — DDG indexes pages that mention IG URL + email ---
    queries += [
        f"instagram {niche} {location} @gmail.com",
        f"instagram {niche} {location} @hotmail.com",
        f"instagram {niche} {location} @outlook.com",
        f'"{niche}" "{location}" instagram email',
        f"instagram {niche} {location} email contacto",
        f"instagram {niche} {location} info@",
        f"instagram {niche} {location} correo electronico",
        f"instagram {niche} {location} mailto:",
    ]

    # --- Tier 2: direct profile discovery ---
    queries += [
        f"instagram {niche} {location}",
        f"instagram.com {niche} {location}",
        f'site:instagram.com "{niche}" "{location}"',
        f"instagram {niche} {location} perfil",
        f"instagram {niche} {location} linktr.ee",
        f"instagram {niche} {location} linktree",
    ]

    # --- Tier 3: business intent signals ---
    queries += [
        f"instagram {niche} {location} presupuesto",
        f"instagram {niche} {location} consulta",
        f"instagram {niche} {location} reservas",
        f"instagram {niche} {location} cita",
        f"instagram {niche} {location} servicios",
        f"instagram {niche} {location} profesional",
        f"instagram {niche} {location} online",
        f"instagram {niche} {location} whatsapp",
    ]

    # --- Tier 4: synonym variants (email-forward + discovery) ---
    for syn in _get_synonyms(niche):
        queries += [
            f"instagram {syn} {location} @gmail.com",
            f"instagram {syn} {location} email contacto",
            f"instagram {syn} {location}",
        ]

    # --- Tier 5: broader geographic fallback ---
    queries += [
        f"instagram {niche} email {location}",
        f'"{niche}" instagram email España',
        f"instagram {niche} España @gmail.com",
    ]

    return list(dict.fromkeys(queries))  # preserve order, remove duplicates


def _parse_usernames(html: str) -> list[str]:
    candidates = INSTAGRAM_USERNAME_RE.findall(html)
    return list(dict.fromkeys(u for u in candidates if u.lower() not in _SKIP_SLUGS))


def _startpage_request(query: str, page: int) -> str:
    params: dict = {"q": query}
    if page > 1:
        params["page"] = str(page)

    proxy_url = next(_proxy_cycle) if _proxy_cycle else None

    with httpx.Client(proxy=proxy_url, follow_redirects=True, timeout=15) as client:
        r = client.get(STARTPAGE_URL, params=params, headers=STARTPAGE_HEADERS)

    if "captcha" in r.text[:500].lower() or "<title" in r.text[:200].lower() and "captcha" in r.text[:500].lower():
        raise RuntimeError("startpage_captcha")
    return r.text


_serp_captcha_count = 0


async def _scrape_startpage(query: str, page: int = 1) -> list[str]:
    """Fetch one page from Startpage (page=1,2,3 supported)."""
    global _serp_captcha_count
    loop = asyncio.get_running_loop()
    try:
        html = await loop.run_in_executor(None, _startpage_request, query, page)
    except RuntimeError as e:
        if "startpage_captcha" in str(e):
            _serp_captcha_count += 1
            logger.warning("Startpage captcha (total=%d) — IP bloqueada, usando proxy si disponible", _serp_captcha_count)
        else:
            logger.warning("Startpage scrape failed (page=%d): %s", page, e)
        return []
    except Exception as e:
        logger.warning("Startpage scrape failed (page=%d): %s", page, e)
        return []
    _serp_captcha_count = 0  # reset on success
    usernames = _parse_usernames(html)
    logger.debug("Startpage '%s' p%d → %d usernames", query[:50], page, len(usernames))
    return usernames


async def _scrape_serp_all_pages(query: str, max_pages: int = 5) -> list[str]:
    """Scrape Startpage pages 1-3 per query via httpx with proxy rotation.

    If Startpage returns a captcha, the page is skipped (returns empty list).
    ~8-12 unique usernames per page when not blocked.
    """
    seen: set[str] = set()
    all_usernames: list[str] = []

    for page in range(1, min(max_pages, 4)):  # max 3 pages from Startpage
        if page > 1:
            await asyncio.sleep(1.5)
        results = await _scrape_startpage(query, page)
        new_count = 0
        for u in results:
            if u not in seen:
                seen.add(u)
                all_usernames.append(u)
                new_count += 1
        if not results or new_count == 0:
            break

    logger.debug("SERP '%s' total unique: %d", query[:50], len(all_usernames))
    return all_usernames


async def _process_username(username: str, dedup: Deduplicator) -> dict | None:
    """Fetch and evaluate one profile. Returns profile with email, or None.

    Raises DailyLimitReached if the daily quota is exhausted.
    Marks the username as seen in the deduplicator for all outcomes.
    """
    if dedup.should_skip(username):
        return None

    dedup.mark_seen(username)

    try:
        profile = await get_profile(username)
    except DailyLimitReached:
        raise

    if profile is None:
        return None

    if profile.get("private"):
        await db.insert_ig_skipped(username, profile.get("instagram_id"), "private")
        return None

    if not profile.get("email"):
        await db.insert_ig_skipped(username, profile.get("instagram_id"), "no_email")
        return None

    return profile


async def search_and_extract(
    niche: str,
    location: str,
    max_results: int,
    job_id: str,
) -> AsyncGenerator[dict, None]:
    """Async generator: yields one profile dict per profile found WITH email.

    Profiles without email or already in DB are silently discarded.
    Processes up to IG_CONCURRENCY profiles concurrently to maximize throughput
    while respecting the rate limiter's inter-request delay.
    """
    dedup = Deduplicator()
    await dedup.load_from_db()

    queries = _build_queries(niche, location)
    logger.info("Job %s: %d queries generadas para '%s %s'", job_id[:8], len(queries), niche, location)

    emails_found = 0
    profiles_checked = 0
    concurrency = Settings.IG_CONCURRENCY

    for query_idx, query in enumerate(queries):
        if emails_found >= max_results:
            break

        usernames = await _scrape_serp_all_pages(query)
        candidates = [u for u in usernames if not dedup.should_skip(u)]

        if not candidates:
            logger.debug("Query #%d '%s': sin candidatos nuevos", query_idx + 1, query[:60])
            if query_idx < len(queries) - 1:
                await asyncio.sleep(1.0)
            continue

        logger.debug(
            "Query #%d '%s': %d candidatos nuevos",
            query_idx + 1, query[:60], len(candidates),
        )

        # Process candidates in concurrent batches
        i = 0
        while i < len(candidates) and emails_found < max_results:
            batch = candidates[i : i + concurrency]
            i += concurrency

            results = await asyncio.gather(
                *[_process_username(u, dedup) for u in batch],
                return_exceptions=True,
            )

            daily_limit_hit = False
            for username, result in zip(batch, results):
                if isinstance(result, DailyLimitReached):
                    logger.info("Job %s: cuota diaria alcanzada — %d emails encontrados", job_id[:8], emails_found)
                    daily_limit_hit = True
                    break
                if isinstance(result, Exception):
                    logger.warning("Error procesando @%s: %s", username, result)
                    continue

                profiles_checked += 1

                if result is not None:
                    await db.upsert_ig_lead(
                        result,
                        job_id=job_id,
                        source_type="dorking",
                        source_value=f"{niche}|{location}",
                    )
                    emails_found += 1
                    logger.info(
                        "Job %s: %d/%d emails (%d perfiles analizados)",
                        job_id[:8], emails_found, max_results, profiles_checked,
                    )
                    yield result

            await db.update_job_progress(job_id, emails_found, emails_found, profiles_checked)

            if daily_limit_hit:
                return

        if emails_found < max_results and query_idx < len(queries) - 1:
            await asyncio.sleep(1.0)

    logger.info(
        "Job %s: búsqueda completada — %d/%d emails en %d perfiles analizados",
        job_id[:8], emails_found, max_results, profiles_checked,
    )
