# main.py
# Punto de entrada del scraper de LinkedIn.
#
# Modos:
#   --mode index   → Fase A: recopila todos los slugs de conexiones y los
#                    guarda en contact_queue como 'pending'. Rápido.
#   --mode enrich  → Fase B (defecto): toma slugs 'pending' de la queue,
#                    visita cada perfil, extrae datos completos y los guarda
#                    en la tabla contacts. Respetar límites anti-ban.
#
# Controles de seguridad activos en ambos modos:
#   · Cooldown tras bloqueo (429 / on_block)
#   · Franja horaria (SCRAPE_WINDOW_START – SCRAPE_WINDOW_END)
#   · Presupuesto diario (MAX_CONTACTS_PER_DAY)
#   · Intervalo mínimo entre ejecuciones (MIN_HOURS_BETWEEN_RUNS)

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
from dotenv import load_dotenv

from scraper import (
    init_client,
    get_current_username,
    scrape_profile_and_connections,
    collect_all_slugs,
    _enrich_connection_from_profile,
    _create_driver_with_cookies,
    session_file_for,
)
from db import (
    insert_run,
    queue_slugs,
    get_pending_slugs,
    requeue_errors,
    upsert_contact,
    mark_queue_done,
    mark_queue_error,
    get_daily_count,
    get_queue_stats,
    contact_exists,
    contact_has_core_fields,
    contact_has_contact_details,
    contact_has_suspicious_geo_fields,
    days_since_last_scrape,
    register_account,
    update_account_last_run,
    get_account_proxy,
)
from notifications import (
    notify_session_expired,
    notify_block,
    notify_daily_summary,
    notify_index_complete,
    notify_auto_login_ok,
    notify_auto_login_needs_verification,
    notify_auto_login_failed,
)
from log_config import setup_logging

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuración desde .env ───────────────────────────────────────────────────

COOLDOWN_HOURS_AFTER_429 = int(os.getenv("COOLDOWN_HOURS_AFTER_429", "48"))
MIN_HOURS_BETWEEN_RUNS = int(os.getenv("MIN_HOURS_BETWEEN_RUNS", "0"))
MAX_CONTACTS_PER_RUN = max(
    1,
    min(
        int(os.getenv("MAX_CONTACTS_PER_RUN", os.getenv("MAX_CONTACTS", "20"))),
        50,
    ),
)
MAX_CONTACTS_PER_DAY = max(1, int(os.getenv("MAX_CONTACTS_PER_DAY", "80")))
SCRAPE_WINDOW_START = int(os.getenv("SCRAPE_WINDOW_START", "0"))  # hora (0-23)
SCRAPE_WINDOW_END = int(os.getenv("SCRAPE_WINDOW_END", "23"))  # hora (0-23)
# Días mínimos antes de refrescar un contacto ya scrapeado.
# Si tiene datos de hace menos de CONTACT_REFRESH_DAYS, se salta sin visitar el perfil.
CONTACT_REFRESH_DAYS = max(1, int(os.getenv("CONTACT_REFRESH_DAYS", "30")))


def _state_file(name: str) -> str:
    # Keep limiter state next to DB so it is stable in Docker and service restarts.
    from db import DB_PATH as _DB_PATH
    return str(Path(_DB_PATH).resolve().parent / name)


COOLDOWN_FILE = _state_file(".linkedin_429_cooldown")
COOLDOWN_COUNT_FILE = _state_file(".linkedin_429_count")
LAST_RUN_FILE = _state_file(".linkedin_last_run")


# ── Re-login automático ────────────────────────────────────────────────────────

def _try_auto_relogin(account: Optional[str]) -> bool:
    """
    Intenta renovar la sesión automáticamente usando las credenciales cifradas
    guardadas en la BD.

    Flujo:
      1. Comprueba si hay credenciales guardadas para esta cuenta.
      2. Notifica por Telegram que la sesión caducó y que se intentará re-login.
      3. Llama a login_with_credentials (abre Chrome, rellena email/contraseña).
      4. Notifica el resultado: OK / verificación requerida / error.

    Devuelve True si el re-login fue exitoso, False en cualquier otro caso.
    No lanza excepciones: todos los errores se loggean y notifican.
    """
    from db import (
        has_saved_credentials,
        get_account_credentials,
        get_account_proxy as _get_proxy_db,
    )
    from scraper import login_with_credentials

    label = account or "cuenta principal"

    # Sin credenciales guardadas → aviso manual
    if not has_saved_credentials(account):
        logger.warning("_try_auto_relogin [%s]: sin credenciales guardadas", label)
        notify_session_expired(account, auto_retry=False)
        return False

    # Hay credenciales → avisar que se intentará automáticamente
    notify_session_expired(account, auto_retry=True)
    logger.info("_try_auto_relogin [%s]: iniciando login automático", label)

    try:
        creds = get_account_credentials(account)
        if not creds:
            logger.error(
                "_try_auto_relogin [%s]: no se pudieron descifrar credenciales", label
            )
            notify_auto_login_failed(account, "No se pudieron descifrar las credenciales.")
            return False

        proxy = _get_proxy_db(account) if account else None
        # headless=True: el re-login automático corre sin ventana visible.
        # Si LinkedIn pide verificación, se notifica por Telegram y el usuario
        # lo completa manualmente desde la vista.
        result = login_with_credentials(
            account or "",
            creds["email"],
            creds["password"],
            proxy=proxy,
            headless=True,
        )

        if result["status"] == "ok":
            logger.info("_try_auto_relogin [%s]: OK", label)
            notify_auto_login_ok(account)
            return True

        if result["status"] == "needs_verification":
            logger.warning("_try_auto_relogin [%s]: requiere verificación", label)
            notify_auto_login_needs_verification(account, result.get("message"))
            return False

        # wrong_credentials u otro error
        logger.error(
            "_try_auto_relogin [%s]: %s — %s",
            label,
            result["status"],
            result.get("message"),
        )
        notify_auto_login_failed(account, result.get("message"))
        return False

    except Exception as exc:
        logger.exception("_try_auto_relogin [%s]: excepción inesperada: %s", label, exc)
        notify_auto_login_failed(account, str(exc))
        return False


# ── Controles de seguridad ─────────────────────────────────────────────────────

def _check_cooldown() -> bool:
    """True = estamos en cooldown (no ejecutar)."""
    if not os.path.isfile(COOLDOWN_FILE):
        return False
    try:
        with open(COOLDOWN_FILE) as f:
            until = float(f.read().strip())
    except (ValueError, OSError):
        _remove_file(COOLDOWN_FILE)
        return False
    if time.time() < until:
        return True
    _remove_file(COOLDOWN_FILE)
    return False


def _write_cooldown() -> int:
    """Escribe el cooldown con backoff exponencial. Retorna las horas efectivas."""
    count = 1
    try:
        if os.path.isfile(COOLDOWN_COUNT_FILE):
            with open(COOLDOWN_COUNT_FILE) as f:
                count = max(1, int(f.read().strip()))
    except (ValueError, OSError):
        pass

    hours = min(4 * (2 ** (count - 1)), 48)  # 4h → 8h → 16h → 48h (techo)

    try:
        with open(COOLDOWN_COUNT_FILE, "w") as f:
            f.write(str(count + 1))
    except OSError:
        pass

    until = time.time() + hours * 3600
    try:
        with open(COOLDOWN_FILE, "w") as f:
            f.write(str(until))
    except OSError:
        pass

    logger.info("Cooldown activado: %dh (bloqueo #%d)", hours, count)
    return hours


def _reset_cooldown_counter() -> None:
    """Reinicia el contador de bloqueos tras un run exitoso."""
    _remove_file(COOLDOWN_COUNT_FILE)


def _check_min_interval() -> bool:
    """True = no han pasado MIN_HOURS_BETWEEN_RUNS desde la última ejecución."""
    if MIN_HOURS_BETWEEN_RUNS <= 0:
        return False
    now = time.time()
    if os.path.isfile(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE) as f:
                last = float(f.read().strip())
            if now - last < MIN_HOURS_BETWEEN_RUNS * 3600:
                return True
        except (ValueError, OSError):
            pass
    try:
        with open(LAST_RUN_FILE, "w") as f:
            f.write(str(now))
    except OSError:
        pass
    return False


def _check_time_window() -> bool:
    """
    True = estamos FUERA de la franja horaria permitida y no debemos ejecutar.
    La franja va de SCRAPE_WINDOW_START a SCRAPE_WINDOW_END (hora local).
    """
    if SCRAPE_WINDOW_START == 0 and SCRAPE_WINDOW_END == 23:
        return False  # sin restricción horaria
    hour = datetime.now().hour
    if SCRAPE_WINDOW_START <= SCRAPE_WINDOW_END:
        return not (SCRAPE_WINDOW_START <= hour < SCRAPE_WINDOW_END)
    # Franja que cruza medianoche (ej. 22-6): es inusual pero soportado
    return not (hour >= SCRAPE_WINDOW_START or hour < SCRAPE_WINDOW_END)


def _check_daily_budget(username: str) -> bool:
    """True = ya se ha alcanzado el presupuesto diario (no ejecutar más)."""
    count = get_daily_count(username)
    return count >= MAX_CONTACTS_PER_DAY


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── Helpers de usuario ────────────────────────────────────────────────────────

def extract_username(url: str) -> str:
    match = re.search(r"linkedin\.com/in/([^/?]+)", url)
    if not match:
        raise ValueError(f"URL inválida: {url}")
    return match.group(1).rstrip("/")


def get_username(account) -> str:
    username = get_current_username(account)
    if username:
        return username
    url = os.getenv("LINKEDIN_PROFILE_URL", "").strip()
    if url:
        return extract_username(url)
    print("No se pudo detectar tu usuario automáticamente.")
    url = input("🔗 Pega la URL de tu perfil de LinkedIn: ").strip()
    return extract_username(url)


def get_username_non_interactive(session, account_slug: Optional[str] = None) -> str:
    """
    Resuelve el username de LinkedIn en modo no interactivo (cron / servidor).

    Orden de prioridad:
      1. Detección automática desde la sesión activa (más fiable).
      2. account_slug — el slug con el que se registró la cuenta; siempre
         disponible cuando se lanza un run con --account=<slug>.
      3. LINKEDIN_PROFILE_URL en .env (fallback legacy).
      4. ValueError — solo si ninguna fuente lo puede resolver.
    """
    username = get_current_username(session)
    if username:
        return username
    if account_slug:
        logger.debug(
            "get_username_non_interactive: auto-detección fallida, "
            "usando account_slug '%s' como fallback",
            account_slug,
        )
        return account_slug
    url = os.getenv("LINKEDIN_PROFILE_URL", "").strip()
    if url:
        return extract_username(url)
    raise ValueError(
        "No se pudo determinar el username de LinkedIn. "
        "Asegúrate de registrar la cuenta con su slug correcto desde la vista."
    )


# ── Comprobaciones comunes ────────────────────────────────────────────────────

def _run_safety_checks(username: str, interactive: bool) -> None:
    """
    Lanza RuntimeError (modo no interactivo) o sys.exit(0) (interactivo)
    si algún control de seguridad impide ejecutar.
    """

    def _abort(msg: str) -> None:
        logger.warning(msg)
        print(f"⚠️  {msg}")
        if interactive:
            sys.exit(0)
        raise RuntimeError(msg)

    if _check_cooldown():
        try:
            with open(COOLDOWN_FILE) as f:
                until_dt = datetime.fromtimestamp(float(f.read())).strftime(
                    "%Y-%m-%d %H:%M"
                )
        except (ValueError, OSError):
            until_dt = f"{COOLDOWN_HOURS_AFTER_429} h"
        _abort(
            "Cooldown activo: LinkedIn bloqueó en una ejecución anterior. "
            f"No se ejecutará hasta: {until_dt}"
        )

    if _check_min_interval():
        _abort(f"Solo se permite una ejecución cada {MIN_HOURS_BETWEEN_RUNS} h.")

    # Respetar franja horaria permitida.
    # _check_time_window() devuelve True cuando estamos FUERA de ventana.
    if username and _check_time_window():
        _abort(
            "Fuera de la franja horaria permitida. "
            f"Se permite ejecutar entre {SCRAPE_WINDOW_START}:00 y {SCRAPE_WINDOW_END}:00."
        )


    if username and _check_daily_budget(username):
        _abort(
            "Presupuesto diario alcanzado: ya se han procesado "
            f"{get_daily_count(username)}/{MAX_CONTACTS_PER_DAY} "
            f"contactos hoy para '{username}'."
        )


# ── Modo INDEX ────────────────────────────────────────────────────────────────

def run_index(
    interactive: bool = True,
    account: Optional[str] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> None:
    """
    Fase A: recopila todos los slugs de conexiones y los encola en contact_queue.
    No visita perfiles individuales → rápido y de bajo riesgo.

    account: slug de LinkedIn de la cuenta a usar (None = cuenta por defecto).
    """
    setup_logging()
    logger.info("run_index iniciado%s", f" [{account}]" if account else "")
    print("🗂️  Modo INDEX: recopilando índice de conexiones...\n")
    if progress_callback:
        progress_callback({
            "phase": "init",
            "label": "Inicializando",
            "detail": "Validando límites y sesión...",
            "current": 1,
            "total": 10,
        })

    _run_safety_checks(username="", interactive=interactive)

    proxy = get_account_proxy(account) if account else None
    try:
        session = init_client(account=account, proxy=proxy)
    except RuntimeError:
        # Sesión caducada en modo no interactivo → intentar re-login automático
        relogin_ok = _try_auto_relogin(account)
        if relogin_ok:
            # La sesión se renovó: reintentar init_client con las cookies nuevas
            try:
                session = init_client(account=account, proxy=proxy)
            except RuntimeError:
                logger.error("run_index [%s]: fallo tras re-login automático", account)
                raise
        else:
            raise

    username = (
        get_username(session)
        if interactive
        else get_username_non_interactive(session, account_slug=account)
    )
    logger.info("run_index: usuario=%s, proxy=%s", username, bool(proxy))
    if progress_callback:
        progress_callback({
            "phase": "running",
            "label": "Conectado a LinkedIn",
            "detail": f"Sesión activa para @{username}. Recopilando slugs...",
            "current": 3,
            "total": 10,
        })

    slugs = collect_all_slugs(session, proxy=proxy)

    if not slugs:
        print("ℹ️  No se encontraron slugs. Revisa la sesión.")
        logger.warning("run_index: 0 slugs recopilados")
        if progress_callback:
            progress_callback({
                "phase": "done",
                "label": "Sin resultados",
                "detail": "No se encontraron conexiones para indexar.",
                "current": 10,
                "total": 10,
                "queue_pending": 0,
                "queue_done": 0,
                "queue_error": 0,
            })
        return

    if progress_callback:
        progress_callback({
            "phase": "running",
            "label": "Guardando índice",
            "detail": f"Se encontraron {len(slugs)} slugs. Guardando en cola...",
            "current": 8,
            "total": 10,
        })

    nuevos = queue_slugs(username, slugs)
    stats = get_queue_stats(username)
    logger.info("run_index: %d slugs totales, %d nuevos encolados", len(slugs), nuevos)
    notify_index_complete(account or username, len(slugs), nuevos)
    if progress_callback:
        progress_callback({
            "phase": "done",
            "label": "Indexación completada",
            "detail": f"Conexiones encontradas: {len(slugs)} · nuevas en cola: {nuevos}",
            "current": 10,
            "total": 10,
            "queue_pending": stats.get("pending", 0),
            "queue_done": stats.get("done", 0),
            "queue_error": stats.get("error", 0),
        })
    print(
        f"✅ Índice actualizado: {len(slugs)} conexiones encontradas, "
        f"{nuevos} nuevas encoladas."
    )
    print(
        "   Cola actual → pending: {pending}, done: {done}, error: {error}, total: {total}".format(
            **stats
        )
    )


# ── Modo ENRICH ───────────────────────────────────────────────────────────────

def run_enrich(
    interactive: bool = True,
    max_contacts_override: int | None = None,
    account: Optional[str] = None,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> None:
    """
    Fase B: toma slugs 'pending' de la cola y visita cada perfil para extraer
    datos completos. Guarda los resultados en la tabla contacts y marca cada
    slug como 'done' o 'error' en la queue.

    Lógica de skip inteligente: si un contacto ya existe y fue scrapeado hace
    menos de CONTACT_REFRESH_DAYS días, se marca done sin visitarlo (ahorra
    peticiones y reduce el riesgo de bloqueo).

    account: slug de LinkedIn de la cuenta a usar (None = cuenta por defecto).
    """
    setup_logging()
    logger.info("run_enrich iniciado%s", f" [{account}]" if account else "")
    print("👥 Modo ENRICH: enriqueciendo contactos pendientes...\n")
    if progress_callback:
        progress_callback({
            "phase": "init",
            "label": "Inicializando",
            "detail": "Comprobando sesión y límites anti-bloqueo...",
            "current": 0,
            "total": 1,
        })

    proxy = get_account_proxy(account) if account else None
    try:
        session = init_client(account=account, proxy=proxy)
    except RuntimeError:
        # Sesión caducada en modo no interactivo → intentar re-login automático
        relogin_ok = _try_auto_relogin(account)
        if relogin_ok:
            try:
                session = init_client(account=account, proxy=proxy)
            except RuntimeError:
                logger.error("run_enrich [%s]: fallo tras re-login automático", account)
                raise
        else:
            raise

    username = (
        get_username(session)
        if interactive
        else get_username_non_interactive(session, account_slug=account)
    )
    logger.info(
        "run_enrich: usuario=%s, proxy=%s, refresh_days=%d",
        username,
        bool(proxy),
        CONTACT_REFRESH_DAYS,
    )

    _run_safety_checks(username=username, interactive=interactive)

    # Recuperar más slugs de los que finalmente visitaremos; algunos se saltarán
    # por el skip inteligente, así que pedimos el doble para aprovechar el presupuesto.
    daily_used = get_daily_count(username)
    remaining_budget = max(0, MAX_CONTACTS_PER_DAY - daily_used)
    fetch_limit = min(
        (max_contacts_override or MAX_CONTACTS_PER_RUN) * 2,
        remaining_budget * 2,
        200,  # nunca pedir más de 200 a la vez
    )

    if remaining_budget <= 0:
        print(f"ℹ️  Presupuesto diario agotado ({MAX_CONTACTS_PER_DAY} contactos/día).")
        if progress_callback:
            progress_callback({
                "phase": "done",
                "label": "Presupuesto agotado",
                "detail": f"Límite diario alcanzado ({MAX_CONTACTS_PER_DAY}/día).",
                "current": 1,
                "total": 1,
            })
        return

    requeued = requeue_errors(username)
    if requeued:
        logger.info("run_enrich: %d slugs con error re-enolados como pending", requeued)


    slugs = get_pending_slugs(username, limit=fetch_limit)
    if not slugs:
        stats = get_queue_stats(username)
        print(
            "ℹ️  No hay contactos pendientes en la cola. "
            f"(done: {stats['done']}, total: {stats['total']})"
        )
        print("   Ejecuta '--mode index' para reindexar las conexiones.")
        if progress_callback:
            progress_callback({
                "phase": "done",
                "label": "Sin pendientes",
                "detail": "No hay contactos pendientes. Ejecuta primero INDEX si hace falta.",
                "current": 1,
                "total": 1,
                "queue_pending": stats.get("pending", 0),
                "queue_done": stats.get("done", 0),
                "queue_error": stats.get("error", 0),
            })
        return

    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    new_count = updated_count = skipped_count = error_count = visited = 0
    strategy_errors = {"requests": 0, "voyager": 0, "overlay": 0}
    run_limit = max_contacts_override or MAX_CONTACTS_PER_RUN
    effective_total = min(run_limit, remaining_budget, len(slugs))
    if progress_callback:
        progress_callback({
            "phase": "running",
            "label": "Enriqueciendo perfiles",
            "detail": f"Objetivo: {effective_total} perfiles (cola disponible: {len(slugs)})",
            "current": 0,
            "total": max(1, effective_total),
            "new_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "strategy_errors": strategy_errors,
        })

    # En servidores con ≤1 GB RAM, Chrome acumula memoria entre perfiles y crashea.
    # Reiniciamos proactivamente cada N perfiles para liberar RAM antes del OOM.
    # En local puedes desactivarlo con DRIVER_RESTART_EVERY=0 para que NO cierre/abra Chrome.
    # Default aumentado a 8 para reducir detección de bot por cierres/aperturas frecuentes.
    try:
        DRIVER_RESTART_EVERY = int(os.getenv("DRIVER_RESTART_EVERY", "8"))
    except ValueError:
        DRIVER_RESTART_EVERY = 8
    if DRIVER_RESTART_EVERY < 0:
        DRIVER_RESTART_EVERY = 8

    def _make_fresh_driver():
        drv = _create_driver_with_cookies(session, proxy=proxy)
        if not drv:
            logger.error("run_enrich: no se pudo crear el WebDriver")
        return drv

    driver = _make_fresh_driver()
    if not driver:
        logger.warning("run_enrich: no se pudo abrir navegador, usando fallback requests-only")
        print("⚠️  No se pudo abrir el navegador. Continuando en modo requests-only.")
        if progress_callback:
            progress_callback({
                "phase": "running",
                "label": "Modo degradado",
                "detail": "Browser no disponible, usando extracción requests/voyager.",
                "current": 0,
                "total": max(1, effective_total),
                "new_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "strategy_errors": strategy_errors,
            })

    import random

    try:
        for slug in slugs:
            # Parar si ya alcanzamos el límite de visitas reales de esta ejecución
            # o si agotamos el presupuesto diario
            if visited >= run_limit or visited >= remaining_budget:
                break

            # ── Skip inteligente ──────────────────────────────────────────────
            if contact_exists(username, slug):
                days = days_since_last_scrape(username, slug)
                core_complete = contact_has_core_fields(username, slug)
                details_complete = contact_has_contact_details(username, slug)
                suspicious_geo = contact_has_suspicious_geo_fields(username, slug)
                if (
                    days is not None
                    and days < CONTACT_REFRESH_DAYS
                    and core_complete
                    and details_complete
                    and not suspicious_geo
                ):
                    mark_queue_done(username, slug)
                    skipped_count += 1
                    logger.debug("skip %s (hace %.1f días)", slug, days)
                    if progress_callback:
                        progress_callback({
                            "phase": "running",
                            "label": "Enriqueciendo perfiles",
                            "detail": f"Saltado por freshness: {slug}",
                            "current": visited,
                            "total": max(1, effective_total),
                            "new_count": new_count,
                            "updated_count": updated_count,
                            "skipped_count": skipped_count,
                            "error_count": error_count,
                            "eta_seconds": int(max(0, (effective_total - visited) * 6.5)),
                        })
                    continue  # no cuenta contra el presupuesto ni visita el perfil

            # ── Reinicio proactivo de Chrome cada N perfiles ──────────────────
            if (
                driver
                and
                DRIVER_RESTART_EVERY
                and visited > 0
                and visited % DRIVER_RESTART_EVERY == 0
            ):
                logger.info(
                    "run_enrich: reiniciando Chrome tras %d perfiles para liberar RAM...",
                    visited,
                )
                try:
                    driver.quit()
                except Exception:
                    pass
                time.sleep(4)  # dejar al SO recuperar memoria antes de abrir Chrome
                driver = _make_fresh_driver()
                if not driver:
                    logger.error(
                        "run_enrich: no se pudo recrear el WebDriver, continuando requests-only"
                    )

            # ── Delay anti-bot entre perfiles ─────────────────────────────────
            if visited > 0:
                delay = random.uniform(2.0, 5.0)
                time.sleep(delay)

            # ── Visita real del perfil ────────────────────────────────────────
            print(f"   [{visited + 1}/{run_limit}] {slug}", end="\r", flush=True)
            try:
                data = _enrich_connection_from_profile(driver, slug, session=session)
                if data.get("_meta_contact_source") == "overlay":
                    # Track fallback path usage; helps diagnose Voyager failures.
                    strategy_errors["voyager"] += 1
                result = upsert_contact(username, data)
                mark_queue_done(username, slug)
                visited += 1
                if result == "inserted":
                    new_count += 1
                else:
                    updated_count += 1
                if progress_callback:
                    progress_callback({
                        "phase": "running",
                        "label": "Enriqueciendo perfiles",
                        "detail": f"[{visited}/{effective_total}] {slug}",
                        "current": visited,
                        "total": max(1, effective_total),
                        "new_count": new_count,
                        "updated_count": updated_count,
                        "skipped_count": skipped_count,
                        "error_count": error_count,
                        "strategy_errors": strategy_errors,
                        "eta_seconds": int(max(0, (effective_total - visited) * 6.5)),
                    })
            except Exception as exc:
                logger.warning("run_enrich: error en %s: %s", slug, exc)
                err_text = str(exc).lower()
                if "voyager" in err_text:
                    strategy_errors["voyager"] += 1
                elif "overlay" in err_text or "contact" in err_text:
                    strategy_errors["overlay"] += 1
                else:
                    strategy_errors["requests"] += 1
                mark_queue_error(username, slug, str(exc))
                error_count += 1
                visited += 1  # también cuenta: se hizo una petición
                if progress_callback:
                    progress_callback({
                        "phase": "running",
                        "label": "Enriqueciendo perfiles",
                        "detail": f"Error en {slug}: {exc}",
                        "current": visited,
                        "total": max(1, effective_total),
                        "new_count": new_count,
                        "updated_count": updated_count,
                        "skipped_count": skipped_count,
                        "error_count": error_count,
                        "strategy_errors": strategy_errors,
                        "eta_seconds": int(max(0, (effective_total - visited) * 6.5)),
                    })
                # Si el renderer de Chrome crasheó, recrear el driver inmediatamente
                exc_str = str(exc).lower()
                if any(
                    kw in exc_str
                    for kw in (
                        "renderer",
                        "session info",
                        "no such session",
                        "invalid session",
                    )
                ):
                    logger.warning("run_enrich: renderer crash — recreando WebDriver...")
                    if driver:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                    time.sleep(4)
                    driver = _make_fresh_driver()
                    if not driver:
                        logger.error(
                            "run_enrich: no se pudo recrear el WebDriver, continuando requests-only"
                        )

            # Pausa anti-detección (no pausar tras el último)
            if visited < run_limit and visited < remaining_budget:
                pause = random.uniform(4.0, 9.0)
                logger.debug("Pausa %.1fs antes del siguiente perfil", pause)
                time.sleep(pause)

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    print()  # nueva línea tras el \r
    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    total_scraped = new_count + updated_count
    insert_run(
        username=username,
        started_at=started_at,
        finished_at=finished_at,
        contacts_scraped=total_scraped,
        contacts_new=new_count,
        contacts_updated=updated_count,
    )
    update_account_last_run(username)
    stats = get_queue_stats(username)
    logger.info(
        "run_enrich finalizado: new=%d updated=%d skipped=%d error=%d",
        new_count,
        updated_count,
        skipped_count,
        error_count,
    )
    notify_daily_summary(
        account=account or username,
        new_count=new_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        error_count=error_count,
        queue_pending=stats.get("pending", 0),
    )
    if progress_callback:
        progress_callback({
            "phase": "done",
            "label": "Enriquecimiento completado",
            "detail": (
                f"Nuevos: {new_count} · actualizados: {updated_count} · "
                f"saltados: {skipped_count} · errores: {error_count}"
            ),
            "current": max(1, effective_total),
            "total": max(1, effective_total),
            "new_count": new_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "queue_pending": stats.get("pending", 0),
            "queue_done": stats.get("done", 0),
            "queue_error": stats.get("error", 0),
            "strategy_errors": strategy_errors,
            "eta_seconds": 0,
        })
    print(
        f"✅ Enriquecimiento completado: {new_count} nuevos, "
        f"{updated_count} actualizados, {skipped_count} saltados (frescos), "
        f"{error_count} errores."
    )
    print(
        f"   Cola → pending: {stats['pending']}, done: {stats['done']}, "
        f"error: {stats['error']}, total: {stats['total']}"
    )

    on_block = getattr(session, "on_block", False)
    if on_block:
        effective_hours = _write_cooldown()
        notify_block(account=account or username, cooldown_hours=effective_hours)
        print(f"\n⚠️  LinkedIn limitó la sesión. Cooldown de {effective_hours}h activado.")
    else:
        _reset_cooldown_counter()


# ── Modo LEGACY (compatibilidad con el flujo original) ────────────────────────

def run_scrape(
    interactive: bool = True,
    dry_run: bool = False,
    max_contacts_override: int | None = None,
    account: Optional[str] = None,
) -> None:
    """
    Flujo original (perfil + conexiones como CSV).
    Se mantiene para compatibilidad con invocaciones directas y tests existentes.

    account: slug de LinkedIn de la cuenta a usar (None = cuenta por defecto).
    """
    setup_logging()
    if not interactive:
        print("Scraping de conexiones (modo no interactivo)\n")
        logger.info("run_scrape iniciado (modo no interactivo)")
    else:
        print("Scraping de conexiones de tu cuenta (perfil + contactos)\n")
        logger.info("run_scrape iniciado (modo interactivo)")

    if _check_cooldown():
        try:
            with open(COOLDOWN_FILE) as f:
                until_dt = datetime.fromtimestamp(float(f.read())).strftime(
                    "%Y-%m-%d %H:%M"
                )
        except (ValueError, OSError):
            until_dt = f"{COOLDOWN_HOURS_AFTER_429} h"
        msg = f"Cooldown activo hasta: {until_dt}"
        logger.warning(msg)
        print(f"⚠️  {msg}")
        if interactive:
            sys.exit(0)
        raise RuntimeError(msg)

    if _check_min_interval():
        msg = f"Solo se permite una ejecución cada {MIN_HOURS_BETWEEN_RUNS} h."
        logger.warning(msg)
        print(f"⚠️  {msg}")
        if interactive:
            sys.exit(0)
        raise RuntimeError(msg)

    if dry_run:
        logger.info("Dry-run: cooldown e intervalo OK")
        print("✅ Dry-run: cooldown e intervalo OK. No se ha ejecutado el scrape.")
        return

    proxy = get_account_proxy(account) if account else None
    try:
        session = init_client(account=account, proxy=proxy)
    except RuntimeError:
        notify_session_expired(account)
        raise
    username = get_username(session) if interactive else get_username_non_interactive(session)
    max_contacts = (
        max(1, max_contacts_override)
        if max_contacts_override is not None
        else MAX_CONTACTS_PER_RUN
    )
    logger.info("Usuario: %s, max_contacts: %s", username, max_contacts)

    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    os.makedirs("output", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")

    perfil, conexiones = scrape_profile_and_connections(session, username, max_contacts)

    finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    contacts_count = len(conexiones) if not conexiones.empty else 0
    on_block = getattr(session, "on_block", False)
    if conexiones.empty and not on_block:
        logger.warning("0 conexiones sin bloqueo. Revisar sesión.")

    insert_run(
        username=username,
        started_at=started_at,
        finished_at=finished_at,
        contacts_scraped=contacts_count,
        contacts_new=0,
        contacts_updated=0,
    )

    df_perfil = pd.DataFrame([perfil])
    f_perfil = f"output/perfil_{username}_{timestamp}.csv"
    df_perfil.to_csv(f_perfil, index=False, encoding="utf-8-sig")
    print(f"\n✅ Perfil guardado en: {f_perfil}")

    if not conexiones.empty:
        f_conexiones = f"output/conexiones_{username}_{timestamp}.csv"
        conexiones.to_csv(f_conexiones, index=False, encoding="utf-8-sig")
        print(f"✅ {len(conexiones)} conexiones guardadas en: {f_conexiones}")
        cols = [
            c
            for c in ["name", "position", "company", "location", "emails"]
            if c in conexiones.columns
        ]
        if cols:
            print(conexiones[cols].head(10))
    else:
        print("ℹ️  No se obtuvieron conexiones")

    if on_block:
        _write_cooldown()
        notify_block(account=account or username, cooldown_hours=COOLDOWN_HOURS_AFTER_429)
        print(
            f"\n⚠️  LinkedIn limitó la sesión. Cooldown de {COOLDOWN_HOURS_AFTER_429} h activado."
        )

    update_account_last_run(username)
    logger.info("run_scrape finalizado: %d conexiones, on_block=%s", contacts_count, on_block)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper de conexiones de LinkedIn.")
    parser.add_argument(
        "--mode",
        choices=["index", "enrich", "legacy"],
        default="legacy",
        help=(
            "index  → recopilar slugs de conexiones en la cola (Fase A).\n"
            "enrich → enriquecer contactos pendientes con datos completos (Fase B).\n"
            "legacy → flujo original perfil+CSV (defecto)."
        ),
    )
    parser.add_argument(
        "--max-contacts",
        type=int,
        default=None,
        metavar="N",
        help="Límite de contactos por ejecución (sobrescribe MAX_CONTACTS_PER_RUN).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo comprobar controles de seguridad; no conectar ni scrapear.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="No abrir navegador si la sesión caduca (útil en cron/servidor).",
    )
    parser.add_argument(
        "--account",
        type=str,
        default=None,
        metavar="SLUG",
        help=(
            "Cuenta LinkedIn a usar (slug, ej. 'miquel-roca-mascaros'). "
            "La sesión se cargará desde sessions/{slug}.pkl. "
            "Sin este argumento se usa la sesión por defecto (session.pkl)."
        ),
    )
    args = parser.parse_args()

    if args.no_browser:
        os.environ["LINKEDIN_NO_BROWSER"] = "1"
    interactive = not args.no_browser

    if args.dry_run:
        run_scrape(interactive=interactive, dry_run=True, account=args.account)
        return

    if args.mode == "index":
        run_index(interactive=interactive, account=args.account)
    elif args.mode == "enrich":
        run_enrich(
            interactive=interactive,
            max_contacts_override=args.max_contacts,
            account=args.account,
        )
    else:
        run_scrape(
            interactive=interactive,
            dry_run=False,
            max_contacts_override=args.max_contacts,
            account=args.account,
        )


if __name__ == "__main__":
    main()
