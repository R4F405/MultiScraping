import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    TIKTOKLEADS_PROXY_LIST: list[str] = [
        p.strip() for p in os.getenv("TIKTOKLEADS_PROXY_LIST", "").split(",") if p.strip()
    ]
    TIKTOKLEADS_DELAY_MIN: float = float(os.getenv("TIKTOKLEADS_DELAY_MIN", "2.8"))
    TIKTOKLEADS_DELAY_MAX: float = float(os.getenv("TIKTOKLEADS_DELAY_MAX", "6.5"))
    TIKTOKLEADS_BACKOFF_INITIAL: int = int(os.getenv("TIKTOKLEADS_BACKOFF_INITIAL", "12"))
    TIKTOKLEADS_BACKOFF_MULTIPLIER: int = int(os.getenv("TIKTOKLEADS_BACKOFF_MULTIPLIER", "2"))
    TIKTOKLEADS_BACKOFF_MAX: int = int(os.getenv("TIKTOKLEADS_BACKOFF_MAX", "360"))
    TIKTOKLEADS_MAX_RETRIES: int = int(os.getenv("TIKTOKLEADS_MAX_RETRIES", "2"))
    TIKTOKLEADS_ENABLE_MOCK_CLIENT: bool = (
        os.getenv("TIKTOKLEADS_ENABLE_MOCK_CLIENT", "true").lower() == "true"
    )
    # Solo "live" en producto; "mock" solo vía API explícita (tests).
    TIKTOKLEADS_ENGINE: str = os.getenv("TIKTOKLEADS_ENGINE", "live").lower()
    TIKTOKLEADS_PLAYWRIGHT_HEADLESS: bool = (
        os.getenv("TIKTOKLEADS_PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    )
    TIKTOKLEADS_NAV_TIMEOUT_MS: int = int(os.getenv("TIKTOKLEADS_NAV_TIMEOUT_MS", "45000"))
    TIKTOKLEADS_MAX_SCROLLS: int = int(os.getenv("TIKTOKLEADS_MAX_SCROLLS", "6"))
    TIKTOKLEADS_MAX_DISCOVERY_RESULTS: int = int(
        os.getenv("TIKTOKLEADS_MAX_DISCOVERY_RESULTS", "120")
    )
    TIKTOKLEADS_PAGE_WAIT_MS: int = int(os.getenv("TIKTOKLEADS_PAGE_WAIT_MS", "1600"))
    TIKTOKLEADS_USE_PROXY_FOR_PLAYWRIGHT: bool = (
        os.getenv("TIKTOKLEADS_USE_PROXY_FOR_PLAYWRIGHT", "false").lower() == "true"
    )
    TIKTOKLEADS_HTTP_PROXY: str = os.getenv("TIKTOKLEADS_HTTP_PROXY", "").strip()
    TIKTOKLEADS_PLAYWRIGHT_PROXY: str = os.getenv("TIKTOKLEADS_PLAYWRIGHT_PROXY", "").strip()
    TIKTOKLEADS_PROXY_COOLDOWN_SECONDS: int = int(
        os.getenv("TIKTOKLEADS_PROXY_COOLDOWN_SECONDS", "300")
    )
    TIKTOKLEADS_PROXY_COOLDOWN_MULTIPLIER_ON_FAIL: int = int(
        os.getenv("TIKTOKLEADS_PROXY_COOLDOWN_MULTIPLIER_ON_FAIL", "2")
    )
    # Cuántos proxies distintos probar por URL (ordenados por historial de aciertos).
    TIKTOKLEADS_PROXY_FALLBACK_MAX: int = max(
        1, int(os.getenv("TIKTOKLEADS_PROXY_FALLBACK_MAX", "4"))
    )
    # Tras agotar proxies, intentar conexión directa al final (útil si la lista va mala).
    TIKTOKLEADS_HTTP_FALLBACK_DIRECT_LAST: bool = (
        os.getenv("TIKTOKLEADS_HTTP_FALLBACK_DIRECT_LAST", "true").lower() == "true"
    )
    TIKTOKLEADS_DISCOVERY_MIN_DELAY: float = float(
        os.getenv("TIKTOKLEADS_DISCOVERY_MIN_DELAY", "1.2")
    )
    TIKTOKLEADS_DISCOVERY_MAX_DELAY: float = float(
        os.getenv("TIKTOKLEADS_DISCOVERY_MAX_DELAY", "2.8")
    )
    TIKTOKLEADS_SOURCE_TIMEOUT: float = float(os.getenv("TIKTOKLEADS_SOURCE_TIMEOUT", "10.0"))
    TIKTOKLEADS_DISCOVERY_BUDGET_SECONDS: int = int(
        os.getenv("TIKTOKLEADS_DISCOVERY_BUDGET_SECONDS", "180")
    )
    # serp: dorking SERP primero; tiktok_web: TikTok/Jina antes que SERP paginado; both: ambos en orden clásico
    TIKTOKLEADS_DISCOVERY_PRIMARY: str = os.getenv(
        "TIKTOKLEADS_DISCOVERY_PRIMARY", "serp"
    ).lower()
    TIKTOKLEADS_SERP_DDG_MAX_PAGES: int = int(os.getenv("TIKTOKLEADS_SERP_DDG_MAX_PAGES", "3"))
    TIKTOKLEADS_SERP_STARTPAGE_MAX_PAGES: int = int(
        os.getenv("TIKTOKLEADS_SERP_STARTPAGE_MAX_PAGES", "3")
    )
    TIKTOKLEADS_SERP_INCLUDE_BING: bool = (
        os.getenv("TIKTOKLEADS_SERP_INCLUDE_BING", "true").lower() == "true"
    )
    # Prioriza candidatos discovery con señales de contacto antes del enrich.
    TIKTOKLEADS_RANK_DISCOVERY_ITEMS: bool = (
        os.getenv("TIKTOKLEADS_RANK_DISCOVERY_ITEMS", "true").lower() == "true"
    )
    # Mínimo de perfiles distintos a intentar: max(max_results del cliente, email_goal × este factor), hasta 500.
    TIKTOKLEADS_EMAIL_GOAL_PROFILES_PER_EMAIL: int = int(
        os.getenv("TIKTOKLEADS_EMAIL_GOAL_PROFILES_PER_EMAIL", "90")
    )
    # Objetivo de emails: tamaño de ronda discovery y límites de reintento
    TIKTOKLEADS_EMAIL_GOAL_DISCOVERY_CHUNK: int = int(
        os.getenv("TIKTOKLEADS_EMAIL_GOAL_DISCOVERY_CHUNK", "52")
    )
    TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS: int = int(
        os.getenv("TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS", "24")
    )
    # 0 = sin límite explícito (solo timeouts internos del cliente)
    TIKTOKLEADS_ENRICH_TIMEOUT_SECONDS: float = float(
        os.getenv("TIKTOKLEADS_ENRICH_TIMEOUT_SECONDS", "120")
    )
    # Si true y hay Playwright: una petición HTTP a tiktok.com antes del navegador; si ya hay
    # email/tel/web, evita Playwright. Por defecto false: en muchos perfiles el contacto solo
    # aparece en el DOM renderizado y HTTP+PW sería más lento que solo PW.
    TIKTOKLEADS_ENRICH_HTTP_FIRST: bool = (
        os.getenv("TIKTOKLEADS_ENRICH_HTTP_FIRST", "false").lower() == "true"
    )
    # Espera tras domcontentloaded en enrich por Playwright (ms). 0 = omitir.
    TIKTOKLEADS_ENRICH_PLAYWRIGHT_SETTLE_MS: int = max(
        0, int(os.getenv("TIKTOKLEADS_ENRICH_PLAYWRIGHT_SETTLE_MS", "1200"))
    )
    # Enrich paralelo: 1 = comportamiento secuencial actual. Subir aumenta carga en TikTok/proxies.
    TIKTOKLEADS_ENRICH_CONCURRENCY: int = max(
        1, int(os.getenv("TIKTOKLEADS_ENRICH_CONCURRENCY", "1"))
    )
    _burst_raw = os.getenv("TIKTOKLEADS_ENRICH_BUCKET_BURST", "").strip()
    TIKTOKLEADS_ENRICH_BUCKET_BURST: float | None = (
        float(_burst_raw) if _burst_raw else None
    )
    # Rondas seguidas con enrich real y sin nuevo correo → cerrar job (evita running infinito).
    # Rondas donde todos los candidatos son dedup no cuentan (ver routes._run_job).
    TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS: int = int(
        os.getenv("TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS", "12")
    )
    # Dedup cross-job: reintentar perfiles sin email tras N días (como Instagram ig_skipped)
    TIKTOKLEADS_DEDUP_STALENESS_DAYS: int = int(os.getenv("TIKTOKLEADS_DEDUP_STALENESS_DAYS", "3"))
    _db_path_env = (os.getenv("TIKTOKLEADS_DB_PATH") or "").strip()
    DB_PATH: str = (
        _db_path_env
        if _db_path_env
        else os.path.join(os.path.dirname(__file__), "..", "..", "data", "tiktokleads.db")
    )
