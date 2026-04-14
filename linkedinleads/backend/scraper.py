# scraper.py
# Scraping de LinkedIn usando Playwright (sin StaffSpy).
# El login se gestiona con cookies persistidas en session.pkl.

import atexit
import json
import logging
import os
import pickle
import random
import re
import sys
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

import requests as _requests

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, BrowserContext, Playwright
from playwright_stealth import Stealth as _Stealth

_stealth = _Stealth()

load_dotenv()

_log = logging.getLogger(__name__)


# ── Configuración ──────────────────────────────────────────────────────────────

def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


BROWSER_PROFILE_WAIT      = _get_env_int("BROWSER_PROFILE_WAIT", 10)
SLEEP_BETWEEN_CONNECTIONS = _get_env_float("SLEEP_BETWEEN_CONNECTIONS", 6.0)
MAX_CONTACTS_CAP          = _get_env_int("MAX_CONTACTS_CAP", 50)
SESSION_FILE              = "session.pkl"
SESSIONS_DIR              = os.getenv("SESSIONS_DIR", "sessions")
# Muestra el navegador si HEADLESS=false en .env (útil para depuración)
HEADLESS                  = os.getenv("HEADLESS", "true").lower() != "false"

# LinkedIn redirige invite-manager/ a catch-up/ — usamos la URL real
_CONNECTIONS_URL        = "https://www.linkedin.com/mynetwork/invite-connect/connections/"
_CONNECTIONS_SEARCH_URL = "https://www.linkedin.com/search/results/people/?network=%5B%22F%22%5D&origin=MEMBER_PROFILE_CANNED_SEARCH"

# Nombres de env para límites del index (leídos en tiempo de ejecución en collect_all_slugs).
INDEX_ENV_MAX_CONTACTS      = "INDEX_MAX_CONTACTS"
INDEX_ENV_MAX_SCROLL_ROUNDS = "INDEX_MAX_SCROLL_ROUNDS"
INDEX_ENV_USE_RECENTLY_ADDED = "INDEX_USE_RECENTLY_ADDED"


# ── Sesión ─────────────────────────────────────────────────────────────────────

class LinkedInSession:
    """Contenedor ligero de la sesión de LinkedIn (cookies + estado)."""

    def __init__(self, cookies: List[Dict], username: Optional[str] = None):
        # cookies: lista de dicts {name, value, domain, path}
        self._cookies: List[Dict] = cookies
        self.on_block: bool = False
        # Username detectado durante init_client (slug de LinkedIn, ej. "miquel-roca-mascaros")
        self.username: Optional[str] = username

    @property
    def cookies(self) -> List[Dict]:
        return self._cookies


def _load_cookies(path: str = SESSION_FILE) -> Optional[List[Dict]]:
    """
    Carga cookies desde session.pkl.
    Soporta el formato antiguo (requests.cookies.RequestsCookieJar)
    y el nuevo (lista de dicts).
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = pickle.load(f)
        cookies = data.get("cookies", [])
        # Formato antiguo: RequestsCookieJar (iterable de objetos con .name, .value…)
        if not isinstance(cookies, list):
            converted = []
            for c in cookies:
                converted.append({
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain or ".linkedin.com",
                    "path": c.path or "/",
                })
            return converted
        return cookies
    except Exception as e:
        _log.warning("No se pudo cargar %s: %s", path, e)
        return None


def _save_cookies(cookies: List[Dict], path: str = SESSION_FILE) -> None:
    """Guarda la lista de cookies en session.pkl."""
    try:
        with open(path, "wb") as f:
            pickle.dump({"cookies": cookies}, f)
        _log.info("Cookies guardadas en %s", path)
    except Exception as e:
        _log.warning("No se pudo guardar cookies en %s: %s", path, e)


def _driver_cookies_to_list(driver) -> List[Dict]:
    """Extrae todas las cookies del driver como lista de dicts."""
    return [
        {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ".linkedin.com"),
            "path": c.get("path", "/"),
        }
        for c in driver.context.cookies()
    ]


# ── WebDriver ──────────────────────────────────────────────────────────────────

# User-Agent de un Chrome reciente en macOS (actualizar si la versión queda obsoleta)
_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)

# Script que se inyecta en cada nueva página para ocultar que es un navegador automatizado.
# Cubre las comprobaciones más habituales de LinkedIn y otras webs anti-bot.
_STEALTH_SCRIPT = """
    // Ocultar navigator.webdriver (señal primaria de Selenium/WebDriver)
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    // Simular plugins de un navegador real (headless los tiene vacíos)
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            {name: 'Chrome PDF Plugin'},
            {name: 'Chrome PDF Viewer'},
            {name: 'Native Client'}
        ]
    });

    // Idiomas típicos de un usuario de habla hispana en macOS
    Object.defineProperty(navigator, 'languages', {
        get: () => ['es-ES', 'es', 'en-US', 'en']
    });

    // window.chrome existe en Chrome real pero no en headless por defecto
    if (!window.chrome) {
        window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
    }

    // Ocultar el flag "HeadlessChrome" del user-agent que Chrome inyecta internamente
    const origUA = navigator.userAgent;
    Object.defineProperty(navigator, 'userAgent', {
        get: () => origUA.replace('HeadlessChrome', 'Chrome')
    });
"""


# ── Playwright singleton ───────────────────────────────────────────────────────

_pw_instance: Optional[Playwright] = None


def _get_pw() -> Playwright:
    global _pw_instance
    if _pw_instance is None:
        _pw_instance = sync_playwright().start()
    return _pw_instance


def _cleanup_pw() -> None:
    global _pw_instance
    if _pw_instance:
        try:
            _pw_instance.stop()
        except Exception:
            pass
        _pw_instance = None


atexit.register(_cleanup_pw)


def _new_page(headless: bool = True, proxy: Optional[str] = None) -> "Page":
    """
    Crea un nuevo browser Playwright + context + page.
    Parcha page.quit() para cerrar el browser al terminar.
    """
    pw = _get_pw()
    launch_kwargs = _make_browser_launch_kwargs(headless=headless)
    context_kwargs: dict = {"user_agent": _CHROME_UA, "viewport": {"width": 1280, "height": 800}}
    if proxy:
        p = _parse_proxy(proxy)
        context_kwargs["proxy"] = {"server": f"http://{p['host']}:{p['port']}"}
        if p["user"] and p["password"]:
            context_kwargs["proxy"]["username"] = p["user"]
            context_kwargs["proxy"]["password"] = p["password"]
        _log.info("Proxy configurado: %s:%s", p["host"], p["port"])
    browser = pw.chromium.launch(**launch_kwargs)
    context = browser.new_context(**context_kwargs)
    page = context.new_page()
    page.set_default_timeout(45000)
    # Patch quit() para compatibilidad con linkedin_main.py que llama driver.quit()
    page.quit = browser.close
    return page


def _parse_proxy(proxy_str: str) -> dict:
    """
    Parsea un proxy en cualquiera de estos formatos:
      - host:port
      - http://host:port
      - http://user:pass@host:port
      - user:pass@host:port

    Devuelve un dict con claves: host, port, user (opcional), password (opcional).
    """
    s = proxy_str.strip()
    if s.startswith("http://") or s.startswith("https://"):
        s = s.split("://", 1)[1]
    user = password = None
    if "@" in s:
        credentials, hostport = s.rsplit("@", 1)
        if ":" in credentials:
            user, password = credentials.split(":", 1)
        else:
            user = credentials
    else:
        hostport = s
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, "8080"
    return {"host": host, "port": port, "user": user, "password": password}


def _make_browser_launch_kwargs(headless: bool = True) -> dict:
    """
    Devuelve kwargs para chromium.launch() con flags optimizados para RAM baja.
    El proxy se configura a nivel de contexto en _new_page() — no aquí.
    """
    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,800",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-plugins",
        "--disable-images",
        "--no-zygote",
        "--no-first-run",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--hide-scrollbars",
        "--mute-audio",
        "--safebrowsing-disable-auto-update",
        "--js-flags=--max-old-space-size=256",
        "--disk-cache-size=1",
        "--media-cache-size=1",
    ]
    return {"headless": headless, "args": args}


def _apply_stealth(page: "Page") -> None:
    """
    Aplica playwright-stealth + script propio para ocultar la automatización.
    Debe llamarse justo después de crear la page y antes de la primera navegación.
    """
    try:
        _stealth.apply_stealth_sync(page)
        page.add_init_script(_STEALTH_SCRIPT)
    except Exception as e:
        _log.warning("No se pudo aplicar stealth: %s", e)


def _detect_username_from_driver(driver) -> Optional[str]:
    """
    Intenta extraer el username (slug) del usuario logueado usando el driver activo.
    Navega a /in/me (LinkedIn redirige al perfil real) y extrae el slug de la URL.
    También intenta extraerlo del HTML del feed como fallback.
    """
    try:
        driver.goto("https://www.linkedin.com/in/me")
        time.sleep(random.uniform(2.5, 4.0))
        url = driver.url
        m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
        if m:
            slug = m.group(1).rstrip("/").lower()
            if slug and slug not in ("me", "login", "feed") and len(slug) > 2:
                _log.info("Username detectado via /in/me: %s", slug)
                return slug
    except Exception as e:
        _log.debug("_detect_username_from_driver (/in/me) falló: %s", e)

    # Fallback: buscar el enlace al propio perfil en el HTML actual
    try:
        driver.goto("https://www.linkedin.com/feed/")
        time.sleep(random.uniform(2.0, 3.0))
        html = driver.content()
        # LinkedIn inyecta el perfil del usuario en el HTML del feed
        for pat in [
            r'"publicIdentifier"\s*:\s*"([a-z0-9][a-z0-9\-]{2,})"',
            r'linkedin\.com/in/([a-z0-9][a-z0-9\-]{2,})(?:/|")',
        ]:
            m = re.search(pat, html)
            if m:
                slug = m.group(1).rstrip("/").lower()
                if slug and len(slug) > 2:
                    _log.info("Username detectado via HTML del feed: %s", slug)
                    return slug
    except Exception as e:
        _log.debug("_detect_username_from_driver (feed fallback) falló: %s", e)

    return None


def _is_logged_in(driver) -> bool:
    """Comprueba si el driver actual está autenticado en LinkedIn."""
    url = driver.url
    return not any(kw in url for kw in ("authwall", "/login", "checkpoint", "uas/login", "signup"))


def _is_soft_blocked(driver) -> bool:
    """
    Detecta bloqueos suaves que NO cambian la URL:
    páginas de error, captcha, verificación de seguridad, rate-limit.
    Devuelve True si se detecta alguno.
    """
    try:
        title = (driver.title() or "").lower()
        if any(kw in title for kw in ("security verification", "captcha", "verification")):
            return True
        body_el = driver.query_selector("body")
        if not body_el:
            return False
        text = body_el.inner_text().lower()
        soft_block_phrases = [
            "something went wrong",
            "algo salió mal",
            "too many redirects",
            "this page is unavailable",
            "we couldn't load this page",
            "please verify you are a human",
            "security check",
            "unusual activity",
        ]
        return any(phrase in text for phrase in soft_block_phrases)
    except Exception:
        return False


def _inject_cookies(driver, cookies: List[Dict]) -> None:
    """
    Inyecta cookies de LinkedIn en el contexto del driver (Playwright Page).
    Solo se inyectan cookies de dominios linkedin.com — el resto son tracking
    de terceros que Playwright rechaza con domain+url simultáneos.
    Playwright requiere usar SOLO domain+path O solo url, nunca ambos.
    """
    ok = 0
    errors = 0
    for c in cookies:
        domain = c.get("domain") or ".linkedin.com"
        if "linkedin.com" not in domain:
            continue  # ignorar cookies de terceros (doubleclick, adnxs, etc.)
        if not domain.startswith("."):
            domain = "." + domain
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": domain,
            "path": c.get("path", "/"),
        }
        try:
            driver.context.add_cookies([cookie])
            ok += 1
        except Exception as e:
            _log.debug("Cookie '%s' rechazada: %s", c.get("name"), e)
            errors += 1
    _log.info("_inject_cookies: %d inyectadas, %d rechazadas", ok, errors)
    print(f"[inject_cookies] {ok} cookies LinkedIn inyectadas, {errors} rechazadas")


# ── Login / init_client ────────────────────────────────────────────────────────

def _is_interactive() -> bool:
    """True si el proceso tiene terminal (puede mostrar un navegador al usuario)."""
    return sys.stdin.isatty()


def session_file_for(account: Optional[str] = None) -> str:
    """
    Devuelve la ruta al archivo de sesión para la cuenta indicada.
    - Sin cuenta: usa session.pkl (comportamiento original).
    - Con cuenta: usa sessions/{account}.pkl creando el directorio si hace falta.
    """
    if not account:
        return SESSION_FILE
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", account)
    return os.path.join(SESSIONS_DIR, f"{safe}.pkl")


def init_client(account: Optional[str] = None, proxy: Optional[str] = None) -> LinkedInSession:
    """
    Inicializa la sesión de LinkedIn usando Selenium puro.

    account: nombre de la cuenta (slug de LinkedIn). Si se indica, las cookies
             se guardan en sessions/{account}.pkl en lugar de session.pkl.
    proxy:   proxy para esta cuenta. Formato 'host:port' o 'user:pass@host:port'.
             Si es None, no se usa proxy.

    Flujo:
    1. Carga cookies del archivo de sesión correspondiente (si existen).
    2. Abre Chrome headless (con proxy si aplica), inyecta cookies y comprueba sesión.
    3. Si la sesión ha caducado (o no hay cookies):
       a. En modo interactivo: abre Chrome visible para que el usuario haga login.
       b. En modo no interactivo (cron / --no-browser): lanza RuntimeError.
    4. Guarda las cookies nuevas y devuelve la sesión.
    """
    session_path = session_file_for(account)
    account_label = f" [{account}]" if account else ""
    proxy_label = f" (proxy: {proxy.split('@')[-1] if proxy and '@' in proxy else proxy})" if proxy else ""
    print(f"🔐 Conectando a LinkedIn{account_label}{proxy_label}...")
    no_browser = os.environ.get("LINKEDIN_NO_BROWSER", "").strip() in ("1", "true", "yes")
    cookies = _load_cookies(session_path)

    # Paso 1: comprobar si las cookies guardadas siguen siendo válidas.
    # En servidores con poca RAM usamos una validación ligera (eager loading + timeout corto)
    # para evitar que Chrome se quede sin memoria cargando LinkedIn completo.
    if cookies:
        import stat as _stat

        # Optimización para RAM baja: si el pkl tiene menos de 6h de antigüedad lo
        # consideramos válido directamente sin abrir Chrome (LinkedIn no caduca tan rápido).
        pkl_age_hours = 999
        try:
            pkl_mtime = os.path.getmtime(session_path)
            pkl_age_hours = (time.time() - pkl_mtime) / 3600
        except Exception:
            pass

        if pkl_age_hours < 6:
            _log.info(
                "Sesión reciente (%.1fh). Usando cookies directamente sin validar con Chrome.",
                pkl_age_hours,
            )
            print(f"✅ Sesión activa (fichero reciente, {pkl_age_hours:.1f}h).")
            # Intentar extraer username del nombre del archivo
            username = account if account else None
            return LinkedInSession(cookies, username=username)

        driver = None
        try:
            driver = _new_page(headless=True, proxy=proxy)
            _apply_stealth(driver)
            _inject_cookies(driver, cookies)
            driver.goto("https://www.linkedin.com/feed/")
            driver.wait_for_load_state("domcontentloaded")
            time.sleep(random.uniform(1.5, 2.5))
            if _is_logged_in(driver):
                username = _detect_username_from_driver(driver)
                if username:
                    _log.info("Username detectado durante init_client: %s", username)
                fresh_cookies = _driver_cookies_to_list(driver)
                _save_cookies(fresh_cookies, session_path)
                _log.info("Sesión válida cargada desde %s", session_path)
                print(f"✅ Sesión activa{f' ({username})' if username else ''}.")
                return LinkedInSession(fresh_cookies, username=username)
            else:
                _log.info("Cookies caducadas o sesión inválida, necesario re-login")
                print("ℹ️  La sesión guardada ha caducado. Necesitas volver a iniciar sesión.")
        except Exception as e:
            _log.warning("Error comprobando sesión existente: %s", e)
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
    else:
        print("ℹ️  No hay sesión guardada. Necesitas iniciar sesión en LinkedIn.")

    # Paso 2: login manual (modo interactivo)
    if not _is_interactive() or no_browser:
        if no_browser:
            _log.warning("Sesión inválida y LINKEDIN_NO_BROWSER=1: no se abre navegador.")
        msg = (
            "⚠️  No hay sesión válida y no se puede abrir el navegador "
            "(modo no interactivo o --no-browser).\n"
            "   Ejecuta el script manualmente para volver a iniciar sesión."
        )
        print(msg)
        raise RuntimeError(msg)

    print("   Abriendo Chrome para que inicies sesión en LinkedIn...")
    print("   (Completa el login, incluida cualquier verificación en dos pasos o captcha.)")
    driver = None
    try:
        driver = _new_page(headless=False, proxy=proxy)
        _apply_stealth(driver)
        driver.goto("https://www.linkedin.com/login")
        input("\n   Pulsa Enter cuando hayas iniciado sesión y estés en LinkedIn (Feed o perfil)...\n")
        if not _is_logged_in(driver):
            print("⚠️  No parece que hayas completado el login. Vuelve a ejecutar el script.")
            raise RuntimeError("Login no completado")
        # Detectar username y recoger cookies mientras el driver está activo
        username = _detect_username_from_driver(driver)
        if username:
            _log.info("Username detectado tras login: %s", username)
        fresh_cookies = _driver_cookies_to_list(driver)
        _save_cookies(fresh_cookies, session_path)
        _log.info("Login completado, cookies guardadas en %s", session_path)
        print(f"✅ Login completado y sesión guardada{f' ({username})' if username else ''}.")
        return LinkedInSession(fresh_cookies, username=username)
    except RuntimeError:
        raise
    except Exception as e:
        print(
            "\n⚠️  El login no se completó. Si LinkedIn mostró captcha o verificación, "
            "complétala antes de pulsar Enter. Vuelve a ejecutar el script si hace falta."
        )
        raise RuntimeError(f"Error durante el login: {e}") from e
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── Helpers de diagnóstico para la página de challenge ────────────────────────

def _dump_challenge_page(driver, account: str) -> None:
    """Guarda HTML y screenshot de la página de challenge para diagnóstico."""
    try:
        html = driver.content()
        path_html = f"challenge_debug_{account}.html"
        with open(path_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[login] 📄 HTML del challenge guardado en: {path_html}")
    except Exception as e:
        print(f"[login] No se pudo guardar HTML: {e}")
    try:
        path_png = f"challenge_debug_{account}.png"
        driver.screenshot(path=path_png)
        print(f"[login] 📸 Screenshot guardado en: {path_png}")
    except Exception as e:
        print(f"[login] No se pudo hacer screenshot: {e}")


def _try_click_challenge_continue(driver) -> None:
    """
    Intenta clicar botones de 'Continuar' o 'Submit' que LinkedIn muestra en la
    página de challenge después de que el usuario acepta la notificación móvil.
    """
    selectors = [
        "button[type='submit']",
        "button#challenge-btn",
        "button.btn__primary--large",
        "button[data-litms-control-urn*='challenge']",
        "input[type='submit']",
    ]
    for sel in selectors:
        try:
            el = driver.query_selector(sel)
            if el and el.is_visible():
                print(f"[login] 🔘 Clicando botón challenge: {sel}")
                el.click()
                return
        except Exception:
            continue


# ── Login automático con credenciales ─────────────────────────────────────────

def login_with_credentials(
    account: str,
    email: str,
    password: str,
    proxy: Optional[str] = None,
    headless: bool = False,
) -> dict:
    """
    Realiza el login automatizado en LinkedIn con email y contraseña.

    headless=False (por defecto) → Chrome visible. Recomendado para el primer
      login desde la vista, donde el usuario puede completar 2FA o captcha.
    headless=True → Chrome oculto. Usado en re-login automático desde el cron.
      Si LinkedIn pide verificación, se retorna "needs_verification" y se notifica
      por Telegram para que el usuario lo haga manualmente.

    Retorna un dict con una de estas claves "status":
      "ok"                 → sesión guardada en sessions/{account}.pkl
      "needs_verification" → LinkedIn pide 2FA / email-code / captcha
      "wrong_credentials"  → email o contraseña incorrectos
      "error"              → error inesperado (mensaje en "message")
    """
    session_path = session_file_for(account)
    driver = None
    try:
        driver = _new_page(headless=headless, proxy=proxy)
        _apply_stealth(driver)
        driver.goto("https://www.linkedin.com/login")
        time.sleep(random.uniform(2.0, 3.5))

        # Rellenar email tecla a tecla (delay en ms por carácter)
        driver.click("#username")
        driver.type("#username", email, delay=random.randint(40, 110))
        time.sleep(random.uniform(0.4, 0.9))

        # Rellenar contraseña tecla a tecla
        driver.click("#password")
        driver.type("#password", password, delay=random.randint(40, 100))
        time.sleep(random.uniform(0.4, 0.8))

        # Clic en "Sign in"
        driver.click("button[type='submit']")
        print(f"[login] Clic en submit enviado para {account}")

        # Polling de hasta 90 segundos esperando que el login se complete.
        # Necesario porque LinkedIn puede pedir verificación por notificación móvil
        # (push "¿Eres tú?") y el usuario tarda varios segundos en aceptarla.
        _log.info("Login: esperando redirección tras submit para cuenta %s...", account)
        deadline = time.time() + 90
        current_url = driver.url
        elapsed = 0
        while time.time() < deadline:
            time.sleep(2)
            elapsed += 2
            current_url = driver.url
            print(f"[login] t={elapsed}s  URL={current_url}")

            # Éxito: redirigió al feed o perfil
            if _is_logged_in(driver):
                print(f"[login] ✅ Sesión activa detectada — guardando cookies")
                username = _detect_username_from_driver(driver)
                fresh_cookies = _driver_cookies_to_list(driver)
                _save_cookies(fresh_cookies, session_path)
                _log.info("Login OK para cuenta %s → %s", account, session_path)
                return {"status": "ok", "detected_username": username or account}

            # Error inmediato: credenciales incorrectas (sigue en /login con mensaje de error)
            if "/login" in current_url or "uas/login" in current_url:
                try:
                    page_src = driver.content().lower()
                except Exception:
                    page_src = ""
                if any(kw in page_src for kw in ("incorrect", "wrong", "invalid", "error")):
                    print(f"[login] ❌ Credenciales incorrectas detectadas")
                    return {
                        "status": "wrong_credentials",
                        "message": "Email o contraseña incorrectos. Revisa los datos e inténtalo de nuevo.",
                    }
                # Sigue en login sin mensaje de error: esperamos más
                continue

            # LinkedIn pide verificación (checkpoint, código por email, notificación móvil…).
            verification_keywords = ("checkpoint", "verification", "challenge", "captcha", "pin")
            if any(kw in current_url.lower() for kw in verification_keywords):
                # En la primera detección, volcar HTML + screenshot para diagnóstico
                if elapsed == 2:
                    _dump_challenge_page(driver, account)
                # Intentar clicar cualquier botón "Continuar/Submit" que aparezca tras aceptar en móvil
                _try_click_challenge_continue(driver)
                continue

        # Se agotó el tiempo de espera sin detectar éxito ni error claro
        _log.warning("Login: timeout 90s para %s. URL final: %s", account, current_url)
        return {
            "status": "needs_verification",
            "message": (
                f"LinkedIn no completó el login en 90s (URL final: {current_url}). "
                "Si LinkedIn pidió un código por email/SMS, introdúcelo y vuelve a intentarlo. "
                "Si enviaron notificación al móvil, acepta y vuelve a añadir la cuenta."
            ),
        }

    except Exception as exc:
        _log.error("Error en login_with_credentials para %s: %s", account, exc)
        return {"status": "error", "message": str(exc)}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── Username ───────────────────────────────────────────────────────────────────

def get_current_username(session: LinkedInSession) -> Optional[str]:
    """
    Devuelve el username detectado durante init_client si ya está en la sesión.
    Si no, abre un driver headless como fallback para intentar extraerlo.
    """
    # Camino rápido: username ya detectado durante init_client (sin abrir otro Chrome)
    if session.username:
        _log.info("Username disponible desde la sesión: %s", session.username)
        return session.username

    # Fallback: abrir un driver headless con las cookies e intentar detectarlo
    driver = None
    try:
        driver = _new_page(headless=True)
        _apply_stealth(driver)
        _inject_cookies(driver, session.cookies)
        driver.goto("https://www.linkedin.com")
        driver.wait_for_load_state("domcontentloaded")
        time.sleep(random.uniform(1.5, 2.5))
        username = _detect_username_from_driver(driver)
        if username:
            session.username = username  # cachear para futuros accesos
        return username
    except Exception as e:
        _log.warning("get_current_username (fallback driver) falló: %s", e)
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ── Utilidades de parseo ───────────────────────────────────────────────────────

def _find_in_dict(obj, *keys: str) -> Optional[str]:
    """Busca la primera clave (de una lista) que exista en un dict con valor string."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj:
            val = obj[key]
            if isinstance(val, str) and val:
                return val
    return None


def _deep_find_value(obj, target_key: str) -> Optional[str]:
    """Recorre recursivamente dict/list y devuelve el primer valor string para target_key."""
    if isinstance(obj, dict):
        if target_key in obj:
            v = obj[target_key]
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            found = _deep_find_value(v, target_key)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find_value(item, target_key)
            if found:
                return found
    return None


def _parse_person_from_json_ld(parsed: dict) -> Optional[Dict]:
    """Extrae campos de Person desde un bloque JSON-LD de LinkedIn."""
    person = None
    if isinstance(parsed, dict) and "@graph" in parsed:
        for item in parsed.get("@graph", []):
            if isinstance(item, dict) and item.get("@type") == "Person":
                person = item
                break
    elif isinstance(parsed, dict) and parsed.get("@type") == "Person":
        person = parsed
    if not person:
        return None

    addr = person.get("address") or {}
    if isinstance(addr, dict):
        loc_parts = [
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("addressCountry"),
        ]
        location = ", ".join(filter(None, loc_parts)) or None
    else:
        location = None

    works_for = person.get("worksFor")
    company = None
    if isinstance(works_for, list) and works_for:
        company = works_for[0].get("name") if isinstance(works_for[0], dict) else None
    elif isinstance(works_for, dict):
        company = works_for.get("name")

    # Foto de perfil (LinkedIn la incluye en JSON-LD como campo "image")
    image = person.get("image")
    profile_photo = None
    if isinstance(image, dict):
        profile_photo = image.get("contentUrl") or image.get("url")
    elif isinstance(image, str):
        profile_photo = image

    return {
        "name": person.get("name"),
        "first_name": person.get("givenName"),
        "last_name": person.get("familyName"),
        "position": person.get("headline"),
        "company": company,
        "location": location,
        "profile_photo": profile_photo,
    }


def _extract_person_from_any_script(html: str) -> Optional[Dict]:
    """Busca en el HTML scripts JSON-LD con datos de Person."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        if script.string:
            try:
                row = _parse_person_from_json_ld(json.loads(script.string))
                if row and (row.get("name") or row.get("position") or row.get("company")):
                    return row
            except (json.JSONDecodeError, TypeError):
                pass
    for match in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
        content = match.group(1).strip()
        if "Person" not in content and "headline" not in content and "givenName" not in content:
            continue
        try:
            parsed = json.loads(content)
            items = parsed if isinstance(parsed, list) else [parsed]
            for item in items:
                    if isinstance(item, dict):
                        row = _parse_person_from_json_ld(item)
                        if row and (row.get("name") or row.get("position") or row.get("company")):
                            return row
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _extract_person_from_meta(html: str) -> Optional[Dict]:
    """
    Fallback de baja fragilidad: intenta extraer nombre/cargo/empresa desde meta tags
    y título de la página del perfil.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        out = {
            "name": None,
            "first_name": None,
            "last_name": None,
            "position": None,
            "company": None,
            "location": None,
            "profile_photo": None,
        }

        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
            # Formato típico: "Nombre - Cargo - Empresa | LinkedIn"
            left = title.split("|")[0].strip()
            parts = [p.strip() for p in left.split(" - ") if p.strip()]
            if parts:
                out["name"] = parts[0]
            if len(parts) >= 2:
                out["position"] = parts[1]
            if len(parts) >= 3:
                out["company"] = parts[2]

        if not out["name"] and soup.title and soup.title.string:
            raw_title = soup.title.string.strip()
            left = raw_title.split("|")[0].strip()
            parts = [p.strip() for p in left.split(" - ") if p.strip()]
            if parts:
                out["name"] = parts[0]
            if len(parts) >= 2:
                out["position"] = parts[1]
            if len(parts) >= 3:
                out["company"] = parts[2]

        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            out["profile_photo"] = og_image["content"].strip() or None

        if out["name"] and not out["first_name"]:
            name_parts = out["name"].split()
            if name_parts:
                out["first_name"] = name_parts[0]
                out["last_name"] = " ".join(name_parts[1:]) if len(name_parts) > 1 else None

        if out["name"] or out["position"] or out["company"]:
            return out
    except Exception:
        pass
    return None


def _merge_profile_rows(*rows: Optional[Dict]) -> Optional[Dict]:
    """
    Fusiona múltiples fuentes de perfil priorizando la primera no vacía por campo.
    """
    keys = ("name", "first_name", "last_name", "position", "company", "location", "profile_photo")
    merged = {k: None for k in keys}

    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in keys:
            v = row.get(k)
            if isinstance(v, str):
                v = v.strip()
            if v and not merged[k]:
                merged[k] = v

    if not any(merged.values()):
        return None

    if merged["name"] and not merged["first_name"]:
        parts = merged["name"].split()
        if parts:
            merged["first_name"] = parts[0]
            if len(parts) > 1:
                merged["last_name"] = " ".join(parts[1:])
    return merged


def _row_has_minimum_profile_data(row: Optional[Dict]) -> bool:
    if not isinstance(row, dict):
        return False
    name = (row.get("name") or "").strip() if isinstance(row.get("name"), str) else ""
    position = (row.get("position") or "").strip() if isinstance(row.get("position"), str) else ""
    company = (row.get("company") or "").strip() if isinstance(row.get("company"), str) else ""
    location = (row.get("location") or "").strip() if isinstance(row.get("location"), str) else ""
    # Mínimo esperado: nombre + al menos otro dato de perfil
    return bool(name and (position or company or location))


def _name_from_slug(slug: str) -> Optional[str]:
    raw = (slug or "").strip().strip("/")
    if not raw:
        return None
    raw = unquote(raw)
    # Quitar sufijos numéricos largos típicos de LinkedIn
    raw = re.sub(r"-\d{5,}$", "", raw)
    parts = [p for p in re.split(r"[-_]+", raw) if p and not p.isdigit()]
    if not parts:
        return None
    # Evitar generar "nombres" absurdos de 1 carácter repetido
    if len(parts) == 1 and len(parts[0]) < 3:
        return None
    return " ".join(p.capitalize() for p in parts[:4])


def _extract_person_from_dom(driver) -> Optional[Dict]:
    """Extrae nombre, headline, ubicación y empresa desde el DOM de LinkedIn."""
    try:
        out = {
            "name": None, "first_name": None, "last_name": None,
            "position": None, "company": None, "location": None,
        }
        name_el = driver.query_selector_all(
            "h1.text-heading-xlarge, h1.inline.t-24, main h1, section h1"
        )
        if name_el:
            out["name"] = (name_el[0].inner_text() or "").strip() or None
        headline_el = driver.query_selector_all(
            "div.text-body-medium.break-words, div.inline.t-14, "
            "div.pv-text-details__left-panel div.text-body-medium, "
            "main div[data-view-name='profile-card'] div.text-body-medium"
        )
        if headline_el:
            out["position"] = (headline_el[0].inner_text() or "").strip() or None
        for sel in (
            "span.text-body-small.inline.t-black--light",
            "div.text-body-small.inline.t-black--light",
            "span.inline.t-black--light.break-words",
        ):
            loc_el = driver.query_selector_all(sel)
            if loc_el:
                txt = (loc_el[0].inner_text() or "").strip()
                if txt and len(txt) < 200 and not txt.startswith("http"):
                    out["location"] = txt
                    break
        # Intentar extraer empresa del top-card (LinkedIn nuevo diseño)
        if not out.get("company"):
            for sel in (
                # Selector para el nombre de empresa en la sección de experiencia del top-card
                "div.inline-show-more-text--is-collapsed span[aria-hidden='true']",
                "div[data-field='experience_position_title'] ~ div span[aria-hidden='true']",
                "a[data-field='experience_company_logo'] span[aria-hidden='true']",
                ".pv-text-details__right-panel .t-14.t-normal span[aria-hidden='true']",
                "main a[href*='/company/'] span[aria-hidden='true']",
                "main a[href*='/company/']",
            ):
                els = driver.query_selector_all(sel)
                if els:
                    txt = (els[0].inner_text() or "").strip()
                    if txt and len(txt) < 100 and not txt.startswith("http"):
                        out["company"] = txt
                        break
        # Fallback: extraer empresa del headline si tiene formato "Cargo en Empresa"
        if not out.get("company") and out["position"] and " at " in out["position"]:
            out["company"] = out["position"].split(" at ")[-1].strip()
        if not out.get("company") and out["position"] and " en " in out["position"]:
            out["company"] = out["position"].split(" en ")[-1].strip()

        if out.get("name") and not out.get("first_name"):
            name_parts = out["name"].split()
            if name_parts:
                out["first_name"] = name_parts[0]
                out["last_name"] = " ".join(name_parts[1:]) if len(name_parts) > 1 else None
        if out.get("name") or out.get("position") or out.get("location") or out.get("company"):
            return out
    except Exception:
        pass
    return None


def _is_valid_phone(text: str) -> bool:
    """
    Verifica si un texto es un número de teléfono válido.
    - Sin letras ni acentos (excluye "Móvil", "Trabajo", etc.)
    - Sin puntos (los separan de versiones como 1.13.42781)
    - Al menos 7 dígitos
    - Solo contiene dígitos, espacios, guiones, paréntesis y el prefijo +
    """
    if not text or len(text) > 25:
        return False
    if re.search(r'[a-zA-ZÀ-ÿ]', text):
        return False
    if "." in text:  # versiones (1.13.42781) u otros formatos no telefónicos
        return False
    digits = re.sub(r'\D', '', text)
    return len(digits) >= 7 and bool(re.match(r'^[\+\d][\d\s\-\(\)]+$', text))


def _extract_contact_info_from_overlay(driver, slug: str) -> Dict:
    """
    Navega al overlay de información de contacto del perfil y extrae email y teléfono.

    Estructura real del overlay de LinkedIn:
      <h3 class="pv-contact-info__header …">\\n  Teléfono\\n</h3>
      <ul class="list-style-none">
        <li>
          <span class="t-14 t-black t-normal">653329820</span>
          <span class="t-14 t-black--light t-normal">(Trabajo)</span>
        </li>
      </ul>

    Para el email: enlaces <a href="mailto:…"> (fiable).
    Para el teléfono: XPath al <h3> que contenga "Teléfono"/"Phone" → <ul> siguiente
    → <span class="t-14 t-black t-normal"> (el que NO tiene t-black--light).
    """
    result: Dict = {"emails": None, "phones": None}
    try:
        overlay_url = f"https://www.linkedin.com/in/{slug}/overlay/contact-info/"
        driver.goto(overlay_url)
        try:
            driver.wait_for_selector(
                "div.pv-contact-info, h3.pv-contact-info__header, a[href^='mailto:']",
                timeout=8000,
            )
        except Exception:
            time.sleep(3)

        # ── Emails: enlaces mailto: ───────────────────────────────────────────────
        emails = []
        for a in driver.query_selector_all("a[href^='mailto:']"):
            href = a.get_attribute("href") or ""
            addr = href.replace("mailto:", "").strip()
            if addr and "@" in addr and "linkedin.com" not in addr and addr not in emails:
                emails.append(addr)

        # ── Teléfonos: XPath al h3 con texto "Teléfono"/"Phone" ──────────────────
        # La estructura del overlay tiene el h3 y la ul como HERMANOS dentro del mismo
        # contenedor. El h3 tiene espacios/saltos alrededor del texto, por eso se usa
        # normalize-space() en lugar de text()=.
        phones = []
        phone_xpath = (
            "//h3[contains(normalize-space(.), 'Teléfono') "
            "or contains(normalize-space(.), 'Phone') "
            "or contains(normalize-space(.), 'Tel')]"
        )
        for h3 in driver.locator(f"xpath={phone_xpath}").all():
            try:
                # El <ul> con los números está como hermano siguiente del <h3>
                ul = h3.query_selector("xpath=following-sibling::ul[1]")
                if not ul:
                    continue
                # Span con la clase t-black t-normal = el número (no la etiqueta "Móvil")
                for span in ul.query_selector_all(
                    "span.t-14.t-black.t-normal, span.t-black.t-normal"
                ):
                    text = (span.inner_text() or "").strip()
                    if _is_valid_phone(text) and text not in phones:
                        phones.append(text)
            except Exception:
                pass

        # Fallback: si XPath no encontró nada, buscar en BeautifulSoup con regex sin anclas
        if not phones:
            soup = BeautifulSoup(driver.content(), "html.parser")
            for header in soup.find_all(
                string=re.compile(r"Tel[eé]fono|Phone|Tel\b", re.IGNORECASE)
            ):
                # Navegar hasta el contenedor padre que tenga hermanos con el número
                node = header.parent
                for _ in range(5):
                    if node is None:
                        break
                    sibling = node.find_next_sibling("ul")
                    if sibling:
                        for span in sibling.find_all("span"):
                            text = span.get_text(strip=True)
                            if _is_valid_phone(text) and text not in phones:
                                phones.append(text)
                        break
                    node = node.parent

        # Fallback final: regex sobre texto completo del overlay
        if not phones:
            body_text = ""
            try:
                body = driver.query_selector("body")
                body_text = (body.inner_text() or "") if body else ""
            except Exception:
                body_text = ""

            if body_text:
                for line in body_text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    # Captura teléfonos internacionales y nacionales comunes
                    m = re.findall(r"\+?\d[\d\s\-\(\)]{6,}\d", line)
                    for cand in m:
                        cand = re.sub(r"\s+", " ", cand).strip()
                        if _is_valid_phone(cand) and cand not in phones:
                            phones.append(cand)

        if emails:
            result["emails"] = "; ".join(emails)
        if phones:
            result["phones"] = "; ".join(phones[:3])

    except Exception as e:
        _log.debug("_extract_contact_info_from_overlay (%s) falló: %s", slug, e)

    return result


def _extract_extra_from_dom(driver) -> Dict:
    """
    Extrae del DOM del perfil los campos que no están en JSON-LD:
    profile_photo, followers, connections, premium, creator, open_to_work.
    """
    result: Dict = {
        "profile_photo": None,
        "followers": None,
        "connections": None,
        "premium": None,
        "creator": None,
        "open_to_work": None,
    }
    try:
        # ── Foto de perfil ────────────────────────────────────────────────────
        for sel in [
            "img.pv-top-card-profile-picture__image-v2",
            "img.profile-photo-edit__preview",
            "img[class*='profile-picture']",
            "button.pv-top-card-profile-picture__edit-overlay img",
        ]:
            els = driver.query_selector_all(sel)
            if els:
                src = els[0].get_attribute("src") or ""
                if src and "ghost" not in src and "static" not in src:
                    result["profile_photo"] = src
                    break

        # ── Conexiones y seguidores ───────────────────────────────────────────
        # LinkedIn muestra "X seguidores" y "X contactos" en el perfil
        body_el = driver.query_selector("body")
        page_text = (body_el.inner_text() or "") if body_el else ""
        for pattern, key in [
            (r'([\d,\.]+\s*(?:K|M)?)\s*(?:followers|seguidores)', "followers"),
            (r'([\d,\.]+\+?)\s*(?:connections?|contactos)', "connections"),
        ]:
            m = re.search(pattern, page_text, re.IGNORECASE)
            if m:
                result[key] = m.group(1).strip()

        # ── Premium ───────────────────────────────────────────────────────────
        premium_els = driver.query_selector_all(
            "li-icon[type*='premium'], .premium-icon, [aria-label*='Premium'], [class*='premium-badge']"
        )
        result["premium"] = len(premium_els) > 0 or None

        # ── Creator ───────────────────────────────────────────────────────────
        creator_els = driver.query_selector_all("[class*='creator-badge'], [aria-label*='Creator']")
        if not creator_els:
            creator_els = [el for el in driver.query_selector_all("span.t-14")
                           if "creator" in (el.inner_text() or "").lower()]
        result["creator"] = len(creator_els) > 0 or None

        # ── Open to work ──────────────────────────────────────────────────────
        otw_els = driver.query_selector_all(
            "#open-to-work-overlay-text, [class*='open-to-work'], [aria-label*='Open to work']"
        )
        if not otw_els:
            otw_els = [el for el in driver.query_selector_all("span.t-14, div.t-14")
                       if "open to work" in (el.inner_text() or "").lower()
                       or "abierto a trabajar" in (el.inner_text() or "").lower()]
        result["open_to_work"] = len(otw_els) > 0 or None

    except Exception as e:
        _log.debug("_extract_extra_from_dom falló: %s", e)

    return result


def _extract_internal_id_from_html(html: str, public_id: Optional[str] = None) -> Optional[str]:
    """Extrae el id interno (ACoA…) del perfil desde el HTML renderizado."""
    if not html:
        return None
    acoa_pat = re.compile(r'(ACoA[A-Za-z0-9_-]{22,})')
    if public_id:
        public_escaped = re.escape(public_id)
        for m in re.finditer(rf'publicIdentifier["\']?\s*:\s*["\']?{public_escaped}', html):
            start = max(0, m.start() - 500)
            end = min(len(html), m.end() + 3000)
            chunk = html[start:end]
            aco = acoa_pat.search(chunk)
            if aco:
                return aco.group(1)
        for m in re.finditer(public_escaped, html):
            start = m.start()
            end = min(len(html), m.end() + 2500)
            chunk = html[start:end]
            aco = acoa_pat.search(chunk)
            if aco:
                return aco.group(1)
        return None
    patterns = [
        r'urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]{20,})',
        r'"profileId"\s*:\s*"(ACoA[A-Za-z0-9_-]{20,})"',
        r'entityUrn["\']?\s*:\s*["\']?urn:li:fsd_profile:(ACoA[A-Za-z0-9_-]+)',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


# ── Driver con cookies ─────────────────────────────────────────────────────────

def _create_driver_with_cookies(
    session: LinkedInSession,
    headless: Optional[bool] = None,
    proxy: Optional[str] = None,
):
    """
    Crea una Playwright Page con las cookies de la sesión ya inyectadas.
    Si headless=None usa la variable de entorno HEADLESS (por defecto True).
    proxy: 'host:port' o 'user:pass@host:port' para enrutar el tráfico por un proxy.
    Aplica playwright-stealth antes de la primera navegación.
    Devuelve la page lista para navegar, o None si no se puede crear.
    """
    use_headless = HEADLESS if headless is None else headless
    try:
        page = _new_page(headless=use_headless, proxy=proxy)
    except Exception as e:
        _log.error("No se pudo crear el browser Playwright: %s", e)
        return None
    try:
        _apply_stealth(page)
        _inject_cookies(page, session.cookies)
        page.goto("https://www.linkedin.com")
        page.wait_for_load_state("domcontentloaded")
        time.sleep(random.uniform(0.8, 1.2))
        return page
    except Exception as e:
        _log.error("Error inicializando page con cookies: %s", e)
        try:
            page.quit()
        except Exception:
            pass
        return None


# ── Scraping de perfil ─────────────────────────────────────────────────────────

def _scrape_profile_via_browser(
    session: LinkedInSession, url: str, public_id: str, driver=None
) -> Tuple[Optional[Dict], list]:
    """
    Carga el perfil con Selenium y extrae datos desde JSON-LD y/o el DOM.
    Si se proporciona `driver`, lo usa sin cerrarlo al acabar.
    Si no, crea uno propio y lo cierra al finalizar.
    Devuelve (dict_perfil_normalizado, []).
    """
    owned = driver is None
    if owned:
        driver = _create_driver_with_cookies(session)
    if not driver:
        return None, []
    try:
        driver.goto(url)
        try:
            driver.wait_for_selector("body", timeout=BROWSER_PROFILE_WAIT * 1000)
        except Exception:
            pass
        # Segunda carga para que la SPA inyecte el JSON-LD
        driver.reload()
        time.sleep(3)
        try:
            driver.wait_for_selector("body", timeout=BROWSER_PROFILE_WAIT * 1000)
        except Exception:
            pass
        html = driver.content()
        for _ in range(BROWSER_PROFILE_WAIT):
            if "application/ld+json" in html or ('"givenName"' in html and '"headline"' in html):
                break
            time.sleep(1)
            html = driver.content()

        row = _extract_person_from_any_script(html)
        if not row:
            row = _extract_person_from_dom(driver)

        if row and (row.get("name") or row.get("position") or row.get("company")):
            return {
                "profile_id": public_id,
                "name": row.get("name"),
                "first_name": row.get("first_name"),
                "last_name": row.get("last_name"),
                "position": row.get("position"),
                "company": row.get("company"),
                "location": row.get("location"),
                "emails": None,
                "phones": None,
                "is_connection": None,
                "followers": None,
                "connections": None,
                "profile_link": url,
                "profile_photo": row.get("profile_photo"),
                "premium": None,
                "creator": None,
                "open_to_work": None,
            }, []
        return None, []
    finally:
        if owned:
            try:
                driver.quit()
            except Exception:
                pass


# ── Scraping de conexiones ─────────────────────────────────────────────────────

def _build_connection_dict(slug: str, name: Optional[str], position: Optional[str]) -> Dict:
    """Construye el dict normalizado de una conexión."""
    return {
        "profile_id": slug,
        "name": name,
        "first_name": None,
        "last_name": None,
        "position": position,
        "company": None,
        "location": None,
        "emails": None,
        "phones": None,
        "is_connection": True,
        "followers": None,
        "connections": None,
        "profile_link": f"https://www.linkedin.com/in/{slug}/",
        "profile_photo": None,
        "premium": None,
        "creator": None,
        "open_to_work": None,
    }


def _extract_connection_cards_from_driver(driver) -> list:
    """
    Extrae las tarjetas de conexión visibles en el DOM.
    Prueba en orden:
    1. Selectores específicos de /mynetwork/ (li.mn-connection-card)
    2. Selectores de /search/results/people/
    3. Fallback genérico: todos los a[href*="/in/"]
    """
    results = []
    seen_slugs: set = set()

    # 1) Página de conexiones (/mynetwork/)
    cards = driver.query_selector_all("li.mn-connection-card")
    if not cards:
        cards = driver.query_selector_all("li[class*='connection-card']")

    if cards:
        for card in cards:
            try:
                link_els = card.query_selector_all("a.mn-connection-card__link, a[href*='/in/']")
                if not link_els:
                    continue
                href = link_els[0].get_attribute("href") or ""
                m = re.search(r"linkedin\.com/in/([^/?#]+)", href)
                if not m:
                    continue
                slug = m.group(1).rstrip("/").lower()
                if not slug or len(slug) < 2 or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                name = None
                for sel in ["span.mn-connection-card__name", "span[class*='name']", ".actor-name"]:
                    els = card.query_selector_all(sel)
                    if els:
                        txt = (els[0].inner_text() or "").strip()
                        if txt:
                            name = txt
                            break

                position = None
                for sel in [
                    "span.mn-connection-card__occupation",
                    "p.mn-connection-card__occupation",
                    "p[class*='occupation']",
                    "span[class*='occupation']",
                ]:
                    els = card.query_selector_all(sel)
                    if els:
                        txt = (els[0].inner_text() or "").strip()
                        if txt:
                            position = txt
                            break

                results.append(_build_connection_dict(slug, name, position))
            except Exception:
                continue
        return results

    # 2) Página de búsqueda (/search/results/people/)
    search_cards = driver.query_selector_all(
        "li.reusable-search__result-container, li[class*='result-container']"
    )
    if search_cards:
        for card in search_cards:
            try:
                link_els = card.query_selector_all("a[href*='/in/']")
                if not link_els:
                    continue
                href = link_els[0].get_attribute("href") or ""
                m = re.search(r"linkedin\.com/in/([^/?#]+)", href)
                if not m:
                    continue
                slug = m.group(1).rstrip("/").lower()
                if not slug or len(slug) < 2 or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                name = None
                for sel in ["span[class*='actor-name']", "span.t-16", "span[aria-hidden='true']"]:
                    els = card.query_selector_all(sel)
                    if els:
                        txt = (els[0].inner_text() or "").strip()
                        if txt and len(txt) < 80 and "\n" not in txt:
                            name = txt
                            break

                position = None
                for sel in [
                    "div.entity-result__primary-subtitle",
                    "div[class*='primary-subtitle']",
                    "div[class*='subtitle']",
                ]:
                    els = card.query_selector_all(sel)
                    if els:
                        txt = (els[0].inner_text() or "").strip()
                        if txt:
                            position = txt
                            break

                results.append(_build_connection_dict(slug, name, position))
            except Exception:
                continue
        return results

    # 3) Fallback genérico
    links = driver.query_selector_all("a[href*='/in/']")
    for a in links:
        try:
            href = a.get_attribute("href") or ""
            m = re.search(r"linkedin\.com/in/([^/?#]+)", href)
            if not m:
                continue
            slug = m.group(1).rstrip("/").lower()
            if not slug or len(slug) < 2 or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            name = (a.inner_text() or "").strip() or None
            if name and len(name) > 120:
                name = None
            results.append(_build_connection_dict(slug, name, None))
        except Exception:
            continue
    return results


def _collect_connection_slugs(driver, max_contacts: int) -> List[str]:
    """
    Navega por la página de conexiones y la página de búsqueda de primer grado
    para recopilar slugs únicos hasta alcanzar max_contacts.
    No extrae datos de cada perfil aqui - eso lo hace _enrich_connection_from_profile.
    """
    seen: set = set()
    slugs: List[str] = []

    def _extract_slugs_from_page() -> int:
        """Extrae slugs de los enlaces /in/ visibles en la página actual. Devuelve cuántos nuevos."""
        new = 0
        links = driver.query_selector_all("a[href*='/in/']")
        for a in links:
            if len(slugs) >= max_contacts:
                break
            try:
                href = a.get_attribute("href") or ""
                m = re.search(r"linkedin\.com/in/([^/?#]+)", href)
                if not m:
                    continue
                slug = m.group(1).rstrip("/").lower()
                # Excluir slugs que sean del propio usuario, menús u otras secciones
                if not slug or len(slug) < 2 or slug in seen:
                    continue
                seen.add(slug)
                slugs.append(slug)
                new += 1
            except Exception:
                continue
        return new

    # Intentar primero la página de conexiones (/mynetwork/invite-connect/connections/)
    _log.info("Slug collection: cargando %s", _CONNECTIONS_URL)
    driver.goto(_CONNECTIONS_URL)
    time.sleep(random.uniform(3.0, 5.0))

    no_progress = 0
    for scroll_i in range(max(20, max_contacts // 5 + 10)):
        if len(slugs) >= max_contacts:
            break
        prev = len(slugs)
        _extract_slugs_from_page()
        if len(slugs) == prev:
            no_progress += 1
            if no_progress >= 4:
                break
            time.sleep(random.uniform(1.5, 3.0))
        else:
            no_progress = 0

        steps = random.randint(2, 4)
        for _ in range(steps):
            driver.evaluate(f"window.scrollBy(0, {random.randint(300, 600)});")
            time.sleep(random.uniform(0.2, 0.5))
        time.sleep(random.uniform(1.0, 2.0))

    if len(slugs) < max_contacts:
        # Fallback: búsqueda de conexiones de primer grado
        _log.info("Slug collection: fallback a búsqueda (%d/%d hasta ahora)", len(slugs), max_contacts)
        driver.goto(_CONNECTIONS_SEARCH_URL)
        time.sleep(random.uniform(3.5, 5.5))
        for _ in range(max(8, max_contacts // 8 + 5)):
            if len(slugs) >= max_contacts:
                break
            _extract_slugs_from_page()
            driver.evaluate("window.scrollBy(0, 700);")
            time.sleep(random.uniform(1.5, 3.0))

    _log.info("Slugs recopilados: %d", len(slugs))
    return slugs[:max_contacts]


def _fetch_profile_html_via_requests(slug: str, session: "LinkedInSession") -> Optional[str]:
    """
    Obtiene el HTML de un perfil de LinkedIn usando requests (sin Chrome).
    Mucho más ligero en RAM que Selenium. Devuelve el HTML o None si falla.
    LinkedIn hace SSR del JSON-LD con los datos básicos del perfil para los crawlers.
    """
    url = f"https://www.linkedin.com/in/{slug}/"
    cookies_dict = {c["name"]: c["value"] for c in session.cookies if "linkedin.com" in c.get("domain", "")}
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.linkedin.com/mynetwork/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        from proxy_pool import proxy_pool as _proxy_pool
        proxy_str = _proxy_pool.get_next()
        proxies_dict = {"http": proxy_str, "https": proxy_str} if proxy_str else None

        resp = _requests.get(url, cookies=cookies_dict, headers=headers, timeout=20, allow_redirects=True, proxies=proxies_dict)
        if resp.status_code == 200 and "linkedin.com" in resp.url:
            html = resp.text
            # Verificar que no nos redirigió al login
            if any(kw in html for kw in ('"givenName"', 'application/ld+json', 'profile-top-card')):
                return html
            _log.debug("fetch_requests %s: HTML sin datos de perfil (posible authwall)", slug)
        else:
            _log.debug("fetch_requests %s: status=%d url=%s", slug, resp.status_code, resp.url)
    except Exception as e:
        _log.debug("fetch_requests %s: error=%s", slug, e)
    return None


def _enrich_connection_from_profile(driver, slug: str, session: Optional["LinkedInSession"] = None) -> Dict:
    """
    Extrae todos los campos de un perfil de conexión.

    Estrategia híbrida para minimizar uso de RAM en servidores con ≤1 GB:
    1. Intenta obtener el HTML del perfil via requests (sin Chrome) → extrae datos básicos
    2. Solo si requests falla, carga el perfil con Chrome
    3. Usa Chrome únicamente para el overlay de contacto (email/teléfono)
    """
    clean_slug = unquote((slug or "").strip()).strip("/")
    url = f"https://www.linkedin.com/in/{clean_slug}/"
    try:
        row = None
        extra: Dict = {}
        used_chrome_for_profile = False

        # ── 1. Intentar obtener HTML via requests (sin Chrome) ─────────────────
        if session is not None:
            _log.info("enrich %s: intentando via requests...", slug)
            html = _fetch_profile_html_via_requests(slug, session)
            if html:
                script_row = _extract_person_from_any_script(html)
                meta_row = _extract_person_from_meta(html)
                row = _merge_profile_rows(script_row, meta_row)
                if row:
                    _log.info("enrich %s: datos via requests OK — name=%s", slug, row.get("name"))
                else:
                    _log.info("enrich %s: requests OK pero JSON-LD sin datos de persona", slug)
            else:
                _log.info("enrich %s: requests no devolvió HTML válido", slug)

        # ── 2. Fallback: cargar perfil con Chrome si requests no trajo datos
        #     suficientes para completar nombre + metadatos base ────────────────
        if not _row_has_minimum_profile_data(row):
            _log.info("enrich %s: fallback a Chrome para cargar perfil", slug)
            driver.goto(url)
            try:
                driver.wait_for_selector("body", timeout=BROWSER_PROFILE_WAIT * 1000)
            except Exception:
                pass
            for _ in range(10):
                html = driver.content()
                if "application/ld+json" in html or '"givenName"' in html:
                    break
                time.sleep(1)
            html = driver.content()
            script_row = _extract_person_from_any_script(html)
            dom_row = _extract_person_from_dom(driver)
            meta_row = _extract_person_from_meta(html)
            row = _merge_profile_rows(row, script_row, dom_row, meta_row)
            extra = _extract_extra_from_dom(driver)
            used_chrome_for_profile = True
            _log.info("enrich %s: Chrome perfil — name=%s", slug, row.get("name") if row else None)

        # ── 3. Overlay de contacto via Chrome (email/teléfono) ─────────────────
        # Si no usamos Chrome para el perfil, necesitamos navegar a la URL primero
        # para que el driver esté en el dominio correcto antes de abrir el overlay.
        if not used_chrome_for_profile:
            _log.info("enrich %s: navegando a perfil para overlay de contacto", slug)
            driver.goto(url)
            try:
                driver.wait_for_selector("body", timeout=15000)
            except Exception as e:
                _log.warning("enrich %s: timeout cargando perfil para overlay: %s", slug, e)

        time.sleep(random.uniform(1.0, 2.0))
        _log.info("enrich %s: extrayendo overlay de contacto...", slug)
        contact = _extract_contact_info_from_overlay(driver, clean_slug)
        _log.info("enrich %s: overlay OK — emails=%s phones=%s", slug, contact.get("emails"), contact.get("phones"))

        # ── 4. Construir resultado completo ────────────────────────────────────
        result = _build_connection_dict(
            clean_slug,
            row.get("name") if row else None,
            row.get("position") if row else None,
        )
        if row:
            result["first_name"] = row.get("first_name")
            result["last_name"] = row.get("last_name")
            result["company"] = row.get("company")
            result["location"] = row.get("location")
            result["profile_photo"] = row.get("profile_photo") or extra.get("profile_photo")
        else:
            result["profile_photo"] = extra.get("profile_photo")

        result["emails"] = contact.get("emails")
        result["phones"] = contact.get("phones")

        # ── Email enrichment si overlay no dio email ──────────────────────────
        if not result.get("emails") and result.get("company"):
            try:
                from email_enrichment import enrich_email_if_missing
                enriched = enrich_email_if_missing(
                    company=result["company"],
                    first_name=result.get("first_name") or "",
                    last_name=result.get("last_name") or "",
                )
                if enriched:
                    result["emails"] = enriched
                    _log.info("enrich %s: email via enrichment: %s", slug, enriched)
            except Exception as e:
                _log.debug("Email enrichment falló para %s: %s", slug, e)

        result["followers"] = extra.get("followers")
        result["connections"] = extra.get("connections")
        result["premium"] = extra.get("premium")
        result["creator"] = extra.get("creator")
        result["open_to_work"] = extra.get("open_to_work")
        result["is_connection"] = True
        result["profile_link"] = url

        if not result.get("name"):
            guessed = _name_from_slug(clean_slug)
            if guessed:
                result["name"] = guessed

        if not result.get("name") and not result.get("position") and not result.get("company"):
            _log.warning(
                "enrich %s: perfil sin metadatos (name/position/company vacíos). "
                "emails=%s phones=%s",
                clean_slug,
                bool(result.get("emails")),
                bool(result.get("phones")),
            )

        _log.info(
            "Enriquecido %s: name=%s company=%s email=%s phone=%s (chrome_perfil=%s)",
            clean_slug, result.get("name"), result.get("company"),
            result.get("emails"), result.get("phones"), used_chrome_for_profile,
        )
        return result

    except Exception as e:
        _log.warning("Error enriqueciendo %s: %s", slug, e)
        raise


def scrape_connections_selenium(
    session: LinkedInSession, max_contacts: int, driver=None
) -> pd.DataFrame:
    """
    Scrapea las conexiones de LinkedIn usando Selenium con las cookies de sesión.

    Si se proporciona `driver`, lo usa sin cerrarlo al acabar (driver compartido).
    Si no, crea uno propio y lo cierra al finalizar.

    Estrategia en dos fases:
    1. Recopilación de slugs: navega por /mynetwork/invite-connect/connections/ con scroll
       para obtener todos los slugs de conexiones. Si no consigue suficientes, usa
       la búsqueda de primer grado como fallback.
    2. Enriquecimiento: visita el perfil de cada conexión para extraer nombre,
       posición, empresa, ubicación, etc. (datos que la lista no muestra).

    Si detecta authwall, login o soft-block, marca session.on_block y devuelve vacío.
    """
    _log.info("Iniciando scraping de conexiones con Selenium (máx. %d)...", max_contacts)
    owned = driver is None
    if owned:
        driver = _create_driver_with_cookies(session)
    if not driver:
        _log.error("Selenium: no se pudo crear el WebDriver")
        return pd.DataFrame()

    try:
        # ── Comprobación inicial de sesión ─────────────────────────────────────
        driver.goto("https://www.linkedin.com/feed/")
        time.sleep(random.uniform(2.0, 3.5))
        current_url = driver.url
        if any(kw in current_url for kw in ("authwall", "/login", "checkpoint", "uas/login")):
            _log.warning("Selenium: redirigido a '%s', sesión no válida", current_url)
            session.on_block = True
            return pd.DataFrame()
        if _is_soft_blocked(driver):
            _log.warning("Selenium: soft-block detectado en el feed")
            print("⚠️  LinkedIn muestra captcha/verificación. Espera unos minutos.")
            session.on_block = True
            return pd.DataFrame()

        # ── FASE 1: recopilar slugs ─────────────────────────────────────────────
        print(f"   Fase 1/2: recopilando slugs de {max_contacts} conexiones...")
        slugs = _collect_connection_slugs(driver, max_contacts)
        print(f"   Fase 1/2: {len(slugs)} slugs obtenidos.")

        if not slugs:
            _log.warning("Selenium: 0 slugs encontrados")
            return pd.DataFrame()

        # ── FASE 2: enriquecer cada perfil ──────────────────────────────────────
        print(f"   Fase 2/2: visitando perfiles para extraer datos completos...")
        enriched: List[Dict] = []
        for i, slug in enumerate(slugs):
            # Comprobar soft-block periódicamente
            if _is_soft_blocked(driver):
                _log.warning("Selenium: soft-block durante enriquecimiento en perfil %d/%d", i + 1, len(slugs))
                print(f"\n⚠️  LinkedIn mostró verificación/captcha en perfil {i+1}. Se detiene el scraping.")
                session.on_block = True
                break

            print(f"   Perfil {i + 1}/{len(slugs)}: {slug}", end="\r", flush=True)
            conn = _enrich_connection_from_profile(driver, slug)
            enriched.append(conn)

            # Pausa anti-detección entre visitas a perfiles
            if i < len(slugs) - 1:
                pause = random.uniform(4.0, 9.0)
                time.sleep(pause)

        print()  # nueva línea tras el \r de progreso
        _log.info("Selenium: %d conexiones enriquecidas", len(enriched))

        if not enriched:
            return pd.DataFrame()
        return pd.DataFrame(enriched)

    except Exception as e:
        _log.error("Error en scrape_connections_selenium: %s", e)
        return pd.DataFrame()
    finally:
        if owned:
            try:
                driver.quit()
            except Exception:
                pass


# ── API pública del módulo ─────────────────────────────────────────────────────

def scrape_connections(
    session: LinkedInSession, max_contacts: int, driver=None
) -> pd.DataFrame:
    """Scrapea las conexiones de tu cuenta usando Selenium."""
    print(f"\n👥 Scrapeando conexiones (máx. {max_contacts})...")
    return scrape_connections_selenium(session, max_contacts, driver=driver)


def scrape_profile_and_connections(
    session: LinkedInSession, username: str, max_contacts: int
) -> Tuple[Dict, pd.DataFrame]:
    """
    Orquestador: scrapea el perfil propio + conexiones con un único driver Chrome.
    Abre el navegador una sola vez, lo reutiliza en todo el proceso y lo cierra al final.
    Devuelve (dict_perfil, DataFrame_conexiones).
    """
    profile_url = f"https://www.linkedin.com/in/{username}/"
    perfil: Optional[Dict] = None

    # Abrir un único driver para todo el proceso (perfil + conexiones)
    driver = _create_driver_with_cookies(session)
    if not driver:
        _log.error("No se pudo crear el WebDriver para scrape_profile_and_connections")
        return {
            "profile_id": username, "name": None, "first_name": None,
            "last_name": None, "position": None, "company": None,
            "location": None, "emails": None, "phones": None,
            "is_connection": None, "followers": None, "connections": None,
            "profile_link": profile_url, "profile_photo": None,
            "premium": None, "creator": None, "open_to_work": None,
            "scrape_error": "No se pudo crear el WebDriver",
        }, pd.DataFrame()

    try:
        # ── Perfil ────────────────────────────────────────────────────────────
        print(f"\n📋 Scrapeando perfil: {username}")
        try:
            result = _scrape_profile_via_browser(session, profile_url, username, driver=driver)
            if result and result[0]:
                perfil = result[0]
        except Exception as exc:
            _log.warning("Error scrapeando perfil '%s': %s", username, exc)

        if perfil is None:
            _log.warning("No se pudo obtener el perfil '%s'. Continuando con las conexiones.", username)
            print(f"⚠️  No se pudo obtener el perfil '{username}'. Continuando con las conexiones...")
            perfil = {
                "profile_id": username,
                "name": None, "first_name": None, "last_name": None,
                "position": None, "company": None, "location": None,
                "emails": None, "phones": None, "is_connection": None,
                "followers": None, "connections": None,
                "profile_link": profile_url, "profile_photo": None,
                "premium": None, "creator": None, "open_to_work": None,
                "scrape_error": "No se pudo obtener el perfil",
            }

        # ── Pausa entre perfil y conexiones ───────────────────────────────────
        pause = random.uniform(4.0, 8.0)
        _log.debug("Pausa de %.1fs entre perfil y conexiones (anti-detección)", pause)
        time.sleep(pause)

        # ── Conexiones (mismo driver, sin cerrar y reabrir Chrome) ────────────
        conexiones = scrape_connections(session, max_contacts, driver=driver)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return perfil, conexiones


# ── Fase A: recopilar índice de slugs ──────────────────────────────────────────

def collect_all_slugs(session: LinkedInSession, proxy: Optional[str] = None) -> List[str]:
    """
    Fase A del scraping en producción: recorre la página de conexiones y la
    búsqueda de primer grado para recopilar TODOS los slugs disponibles
    sin visitar ningún perfil individual (rápido, sin enriquecimiento).

    proxy: proxy a usar para esta sesión ('host:port' o 'user:pass@host:port').
    Devuelve la lista de slugs únicos encontrados.
    """
    index_max_contacts = max(20, min(int(os.getenv(INDEX_ENV_MAX_CONTACTS, "100")), 1000))
    index_max_scroll   = max(5, min(int(os.getenv(INDEX_ENV_MAX_SCROLL_ROUNDS, "25")), 120))
    index_recently    = os.getenv(INDEX_ENV_USE_RECENTLY_ADDED, "true").lower() == "true"
    _log.info(
        "collect_all_slugs: iniciando (max=%d, scroll_rounds=%d, recently_added=%s)",
        index_max_contacts, index_max_scroll, index_recently,
    )
    driver = _create_driver_with_cookies(session, proxy=proxy)
    if not driver:
        _log.error("collect_all_slugs: no se pudo crear el WebDriver")
        return []

    # Slugs propios a excluir (el perfil del usuario logueado y strings no-slug)
    OWN_SLUG = (session.username or "").lower()
    EXCLUDED = {"me", "login", "feed", "jobs", "messaging", "notifications",
                "search", "mynetwork", "in", "company", "school", ""}

    seen: set = set()
    slugs: List[str] = []

    def _harvest_page() -> int:
        """Extrae slugs de todos los enlaces /in/ visibles en la página actual."""
        new = 0
        links = driver.query_selector_all("a[href*='/in/']")
        for a in links:
            if len(slugs) >= index_max_contacts:
                break
            try:
                href = a.get_attribute("href") or ""
                m = re.search(r"linkedin\.com/in/([^/?#]+)", href)
                if not m:
                    continue
                slug = m.group(1).rstrip("/").lower()
                if not slug or len(slug) < 2 or slug in seen:
                    continue
                if slug in EXCLUDED or slug == OWN_SLUG:
                    continue
                seen.add(slug)
                slugs.append(slug)
                new += 1
            except Exception:
                continue
        return new

    def _click_next_search_page() -> bool:
        """
        En la búsqueda de personas (network=F) LinkedIn pagina (10 resultados/página).
        Este helper intenta clicar el botón Siguiente/Next. Devuelve True si navegó.
        """
        xpaths = [
            "//button[contains(@aria-label,'Next') and not(@disabled)]",
            "//button[contains(@aria-label,'Siguiente') and not(@disabled)]",
            "//button[contains(.,'Next') and not(@disabled)]",
            "//button[contains(.,'Siguiente') and not(@disabled)]",
            "//li[contains(@class,'artdeco-pagination__indicator--number')]/following-sibling::li//button[contains(@aria-label,'Next') and not(@disabled)]",
        ]
        for xp in xpaths:
            try:
                btns = driver.locator(f"xpath={xp}").all()
                for b in btns:
                    if b.is_visible() and b.is_enabled():
                        b.click()
                        time.sleep(random.uniform(2.0, 3.5))
                        return True
            except Exception:
                continue
        return False

    try:
        # ── ÚNICA FUENTE: búsqueda de primer grado con paginación ──────────────
        _log.info("collect_all_slugs: cargando búsqueda de primer grado (paginada)")
        driver.goto(_CONNECTIONS_SEARCH_URL)
        time.sleep(random.uniform(3.5, 5.5))

        final_url = driver.url
        print(f"[collect_slugs] URL tras goto: {final_url}")
        if any(kw in final_url for kw in ("authwall", "/login", "checkpoint")):
            _log.warning("collect_all_slugs: sesión no válida, redirigido a login")
            print(f"[collect_slugs] ❌ Sesión no válida — redirigido a {final_url}")
            session.on_block = True
            return []

        # Máximo páginas = límite de contactos / 10 (resultados por página) con margen
        max_pages = max(1, min(200, (index_max_contacts // 10) + 5))
        page = 1
        no_progress = 0
        while page <= max_pages and len(slugs) < index_max_contacts:
            prev = len(slugs)
            _harvest_page()
            all_links = driver.query_selector_all("a[href*='/in/']")
            print(f"[collect_slugs] page {page}: {len(all_links)} links /in/ encontrados, +{len(slugs)-prev} slugs nuevos (total={len(slugs)})")
            _log.info("collect_all_slugs: page %d -> +%d (total=%d)", page, len(slugs) - prev, len(slugs))
            if len(slugs) == prev:
                no_progress += 1
            else:
                no_progress = 0

            # Si 2 páginas seguidas no aportan slugs, probablemente se acabó
            if no_progress >= 2:
                break

            if len(slugs) >= index_max_contacts:
                break

            # Ir a la siguiente página
            if not _click_next_search_page():
                break
            page += 1
            # Pausa anti-detección entre páginas
            time.sleep(random.uniform(1.5, 3.0))

        _log.info("collect_all_slugs: %d slugs totales recopilados", len(slugs))

    except Exception as e:
        _log.error("collect_all_slugs: error inesperado: %s", e)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return slugs[:index_max_contacts]
