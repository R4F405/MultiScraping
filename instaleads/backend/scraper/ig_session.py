import json
import logging
import os

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


def get_authenticated_client(username: str, password: str) -> Client:
    global _client

    if _client is not None:
        return _client

    cl = Client()
    cl.delay_range = [3, 7]

    if _load_session(cl):
        _client = cl
        return cl

    try:
        cl.login(username, password)
        _save_session(cl)
        _client = cl
        return cl
    except Exception as e:
        raise RuntimeError(f"Instagram login failed: {e}") from e


def clear_session():
    global _client
    _client = None
    if os.path.exists(SESSION_PATH):
        os.remove(SESSION_PATH)
        logger.info("Session file removed from disk")


def session_info() -> dict:
    if not os.path.exists(SESSION_PATH):
        return {"logged_in": False, "username": None, "session_age_hours": None}
    try:
        stat = os.stat(SESSION_PATH)
        import time
        age_hours = round((time.time() - stat.st_mtime) / 3600, 1)
        return {
            "logged_in": True,
            "username": Settings.IG_USERNAME,
            "session_age_hours": age_hours,
        }
    except Exception:
        return {"logged_in": False, "username": None, "session_age_hours": None}
