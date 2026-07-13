"""
Authenticated Instagram session management.

Instagram's private web API (``/api/v1/...``) — including the followers list
endpoint — requires an authenticated session. Since mid-2024 even the
"public" ``web_profile_info`` endpoint returns 429/login_required for
datacenter IPs without a session, so authentication is effectively
mandatory for any reliable scraping.

The session is provided as a ``sessionid`` cookie (the value you can copy
from a logged-in browser's DevTools → Application → Cookies, or that a
login flow produces). Configuration sources, in priority order:

  1. ``IG_SESSIONID`` env var (optionally ``IG_DS_USER_ID`` / ``IG_CSRFTOKEN``).
  2. ``IG_SESSION_FILE`` env var → JSON file ``{"sessionid": "...", ...}``.

The ``sessionid`` cookie value has the shape ``{ds_user_id}%3A...%3A...``,
so the account user-id can be parsed from it when not given explicitly.
"""

import json
import logging
import os
from urllib.parse import unquote

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

IG_APP_ID = Settings.IG_APP_ID

_BASE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _parse_ds_user_id(sessionid: str) -> str | None:
    """Extract the account's numeric user id from a sessionid cookie value."""
    if not sessionid:
        return None
    decoded = unquote(sessionid)
    head = decoded.split(":", 1)[0]
    return head if head.isdigit() else None


class IgSession:
    """Holds the cookies/headers needed for authenticated Instagram calls."""

    def __init__(
        self,
        sessionid: str,
        *,
        csrftoken: str | None = None,
        ds_user_id: str | None = None,
        mid: str | None = None,
    ) -> None:
        self.sessionid = (sessionid or "").strip()
        # Instagram accepts the csrftoken as any value as long as cookie and
        # header agree; use a stable placeholder when none is supplied.
        self.csrftoken = (csrftoken or "").strip() or "missing"
        self.ds_user_id = (ds_user_id or "").strip() or _parse_ds_user_id(self.sessionid)
        self.mid = (mid or "").strip() or None

    @property
    def authenticated(self) -> bool:
        return bool(self.sessionid)

    def cookies(self) -> dict:
        jar = {
            "sessionid": self.sessionid,
            "csrftoken": self.csrftoken,
        }
        if self.ds_user_id:
            jar["ds_user_id"] = self.ds_user_id
        if self.mid:
            jar["mid"] = self.mid
        return jar

    def headers(self) -> dict:
        return {
            "x-ig-app-id": IG_APP_ID,
            "x-csrftoken": self.csrftoken,
            "x-requested-with": "XMLHttpRequest",
            "User-Agent": _BASE_UA,
            "Accept": "*/*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": "https://www.instagram.com/",
            "Origin": "https://www.instagram.com",
        }


def _load_from_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Support both a flat dict and instagrapi-style {"authorization_data": {...}}
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        logger.warning("IG_SESSION_FILE %s not found", path)
    except Exception as exc:
        logger.warning("Could not read IG_SESSION_FILE %s: %s", path, exc)
    return None


def load_session() -> IgSession | None:
    """Build an :class:`IgSession` from configuration, or None if unconfigured.

    The session id is read from the encrypted settings store first (set via the
    web panel), falling back to the ``IG_SESSIONID`` env var / ``.env``.
    """
    from backend.config.settings_store import store

    sessionid = (store.get("IG_SESSIONID") or os.getenv("IG_SESSIONID", "")).strip()
    csrftoken = (store.get("IG_CSRFTOKEN") or os.getenv("IG_CSRFTOKEN", "")).strip()
    ds_user_id = (store.get("IG_DS_USER_ID") or os.getenv("IG_DS_USER_ID", "")).strip()

    if not sessionid:
        session_file = os.getenv("IG_SESSION_FILE", "").strip()
        if session_file:
            data = _load_from_file(session_file)
            if data:
                sessionid = str(data.get("sessionid", "")).strip()
                csrftoken = csrftoken or str(data.get("csrftoken", "")).strip()
                ds_user_id = ds_user_id or str(data.get("ds_user_id", "")).strip()

    if not sessionid:
        return None

    session = IgSession(sessionid, csrftoken=csrftoken or None, ds_user_id=ds_user_id or None)
    logger.info(
        "Instagram session loaded (ds_user_id=%s)",
        session.ds_user_id or "unknown",
    )
    return session


# Module-level singleton, refreshable for tests / hot config reload.
_session: IgSession | None = None
_loaded = False


def get_session() -> IgSession | None:
    global _session, _loaded
    if not _loaded:
        _session = load_session()
        _loaded = True
    return _session


def reload_session() -> IgSession | None:
    global _session, _loaded
    _session = load_session()
    _loaded = True
    return _session
