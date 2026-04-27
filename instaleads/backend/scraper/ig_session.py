import json
import logging
import os
from urllib.parse import unquote

from cryptography.fernet import Fernet, InvalidToken
from instagrapi import Client

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

SESSION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "session", "session.json.enc"
)
SESSION_PATH = os.path.abspath(SESSION_PATH)

# Module-level cached client so callers don't re-login on every call
_client: Client | None = None
_client_username: str | None = None
_client_proxy_url: str | None = None


def _get_fernet() -> Fernet:
    key = Settings.IG_SESSION_KEY
    if not key:
        raise RuntimeError("IG_SESSION_KEY is not set in .env")
    return Fernet(key.encode())


def _save_session(cl: Client):
    data = cl.get_settings()
    encrypted = _get_fernet().encrypt(json.dumps(data).encode())
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    with open(SESSION_PATH, "wb") as f:
        f.write(encrypted)
    logger.info("Instagram session saved to disk (encrypted)")


def _load_session(cl: Client) -> bool:
    """Try to load session from disk. Returns True if session is still valid."""
    if not os.path.exists(SESSION_PATH):
        return False
    try:
        with open(SESSION_PATH, "rb") as f:
            encrypted = f.read()
        data = json.loads(_get_fernet().decrypt(encrypted).decode())
        cl.set_settings(data)
        cl.get_timeline_feed()
        logger.info("Session loaded from disk — login skipped")
        return True
    except InvalidToken:
        logger.warning("Session file could not be decrypted — will re-login")
    except Exception as e:
        logger.warning("Session on disk invalid or expired: %s — will re-login", e)
    return False


def _read_saved_session_settings() -> dict | None:
    if not os.path.exists(SESSION_PATH):
        return None
    try:
        with open(SESSION_PATH, "rb") as f:
            encrypted = f.read()
        return json.loads(_get_fernet().decrypt(encrypted).decode())
    except Exception:
        return None


def _extract_session_username(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    auth = data.get("authorization_data")
    if isinstance(auth, dict):
        username = auth.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
    cookies = data.get("cookies")
    if isinstance(cookies, dict):
        username = cookies.get("ds_user")
        if isinstance(username, str) and username.strip():
            return username.strip()
    return None


def _normalize_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return None
    if "://" not in proxy_url:
        proxy_url = f"http://{proxy_url}"
    if not (
        proxy_url.startswith("http://")
        or proxy_url.startswith("https://")
        or proxy_url.startswith("socks5://")
        or proxy_url.startswith("socks5h://")
    ):
        raise RuntimeError("Invalid proxy URL format. Use http(s)://user:pass@host:port")
    return proxy_url


def _normalize_sessionid(raw_sessionid: str | None) -> str:
    value = (raw_sessionid or "").strip()
    if not value:
        return ""
    # Accept paste formats like: "sessionid=abc123; Path=/; ..."
    if "sessionid=" in value.lower():
        parts = value.split(";")
        for part in parts:
            part = part.strip()
            if part.lower().startswith("sessionid="):
                value = part.split("=", 1)[1].strip()
                break
    # Remove accidental quotes from copy/paste
    value = value.strip("\"'")
    # Handle URL-encoded cookies (e.g. %3A)
    value = unquote(value)
    return value


def get_authenticated_client(username: str, password: str, proxy_url: str | None = None) -> Client:
    global _client, _client_username, _client_proxy_url
    normalized_proxy = _normalize_proxy_url(proxy_url)

    if (
        _client is not None
        and _client_username == username
        and _client_proxy_url == normalized_proxy
    ):
        return _client

    # Reset cached client when account/proxy changes.
    _client = None
    _client_username = None
    _client_proxy_url = None

    cl = Client()
    cl.delay_range = [3, 7]
    if normalized_proxy:
        cl.set_proxy(normalized_proxy)

    if _load_session(cl):
        _client = cl
        _client_username = _extract_session_username(_read_saved_session_settings()) or username
        _client_proxy_url = normalized_proxy
        return cl

    try:
        cl.login(username, password)
        _save_session(cl)
        _client = cl
        _client_username = username
        _client_proxy_url = normalized_proxy
        return cl
    except Exception as e:
        raise RuntimeError(f"Instagram login failed: {e}") from e


def import_session_by_sessionid(
    username: str, sessionid: str, proxy_url: str | None = None
) -> dict:
    global _client, _client_username, _client_proxy_url
    normalized_proxy = _normalize_proxy_url(proxy_url)
    clean_sessionid = _normalize_sessionid(sessionid)
    if not clean_sessionid:
        raise RuntimeError("Sessionid is required.")

    cl = Client()
    cl.delay_range = [3, 7]
    if normalized_proxy:
        cl.set_proxy(normalized_proxy)

    try:
        cl.login_by_sessionid(clean_sessionid)
        cl.get_timeline_feed()
        _save_session(cl)
        info = cl.account_info()
        final_username = getattr(info, "username", None) or username
        _client = cl
        _client_username = final_username
        _client_proxy_url = normalized_proxy
        return {"username": final_username}
    except Exception as e:
        if "Exceeded 30 redirects" in str(e):
            raise RuntimeError(
                "Instagram session import failed: redirect loop. "
                "Sessionid inválido/caducado o cookie copiada en formato incorrecto."
            ) from e
        raise RuntimeError(f"Instagram session import failed: {e}") from e


def clear_session():
    global _client, _client_username, _client_proxy_url
    _client = None
    _client_username = None
    _client_proxy_url = None
    if os.path.exists(SESSION_PATH):
        os.remove(SESSION_PATH)
        logger.info("Session file removed from disk")


def session_info() -> dict:
    if not os.path.exists(SESSION_PATH):
        return {"logged_in": False, "username": None, "session_age_hours": None}
    try:
        stat = os.stat(SESSION_PATH)
        import time
        username = _extract_session_username(_read_saved_session_settings())
        age_hours = round((time.time() - stat.st_mtime) / 3600, 1)
        return {
            "logged_in": True,
            "username": username or Settings.IG_USERNAME,
            "session_age_hours": age_hours,
        }
    except Exception:
        return {"logged_in": False, "username": None, "session_age_hours": None}
