import json
import logging
import os
import time

from cryptography.fernet import Fernet

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_client = None  # instagrapi.Client instance
_logged_username: str | None = None
_login_time: float | None = None  # Unix timestamp of last successful login
_last_login_error: str | None = None


def _classify_login_error(exc: Exception) -> str:
    _, message = _classify_login_error_typed(exc)
    return message


def _classify_login_error_typed(exc: Exception) -> tuple[str, str]:
    """Return (error_type, human_message). error_type: challenge|phone|2fa|credentials|ip_blocked|account_issue|unknown."""
    msg = str(exc)
    low = msg.lower()

    # IP/Network issues
    if "blacklist" in low or "ip address" in low or "ip is added" in low:
        return (
            "ip_blocked",
            "Tu IP está bloqueada por Instagram. Espera 24-48h o usa un proxy residencial. "
            "Si tienes acceso a otra red/VPN, prueba desde allí.",
        )
    if "can't reach our servers" in low or "network" in low or "connection" in low:
        return (
            "network",
            "Error de conexión a Instagram. Verifica tu conexión a Internet y vuelve a intentarlo.",
        )

    # Account compromise/suspicious activity
    if "compromised" in low or "unusual activity" in low or "suspicious" in low:
        return (
            "account_issue",
            "Instagram detectó actividad sospechosa en tu cuenta. Cambia la contraseña desde https://instagram.com/accounts/login/ "
            "en una navegador normal, luego inténtalo aquí.",
        )
    if "disabled" in low or "action blocked" in low:
        return (
            "account_issue",
            "Tu cuenta está deshabilitada o bloqueada por Instagram. Abre https://www.instagram.com/accounts/suspended/ "
            "para más información.",
        )

    # Phone/2FA verification required
    if "submit_phone" in low:
        return (
            "phone",
            "Instagram requiere verificación por teléfono. Abre la app de Instagram, inicia sesión y completa la verificación por SMS o llamada. "
            "Luego pulsa 'Reconectar' aquí.",
        )
    if "two_factor" in low or "2fa" in low or "two-factor" in low:
        return (
            "2fa",
            "Esta cuenta tiene 2FA habilitado. Abre la app de Instagram, inicia sesión y completa la verificación en dos pasos. "
            "Luego pulsa 'Reconectar' aquí.",
        )

    # Challenge (captcha, security check, etc)
    if "challenge" in low or "checkpoint" in low or "verify" in low:
        return (
            "challenge",
            "Instagram solicita verificación de seguridad (puede ser captcha). Abre la app, inicia sesión y completa el proceso. "
            "Luego pulsa 'Reconectar' aquí.",
        )

    # Credentials
    if "bad password" in low or "incorrect password" in low or "wrong password" in low:
        return "credentials", "Contraseña incorrecta. Verifica que esté bien escrita."
    if "bad username" in low or "user.*not.*found" in low or "does not exist" in low or "no user" in low:
        return "credentials", "Usuario no encontrado. Verifica el nombre de usuario (es case-sensitive)."
    if "invalid.*credentials" in low or "invalid.*login" in low:
        return "credentials", "Usuario o contraseña incorrectos."

    # Generic unknown
    return (
        "unknown",
        f"Error desconocido al iniciar sesión: {msg[:150]}. "
        "Si el usuario y contraseña son correctos, intenta desde la app de Instagram en otro dispositivo, "
        "luego pulsa 'Reconectar' aquí.",
    )


def _get_fernet() -> Fernet:
    """Return a Fernet instance, generating and persisting the key if needed."""
    key = settings.session_key.strip()
    if not key:
        # Check if key is stored in a key file next to session file
        key_file = settings.session_file + ".key"
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                key = f.read().decode()
        else:
            key = Fernet.generate_key().decode()
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            with open(key_file, "wb") as f:
                f.write(key.encode())
            logger.info("Generated new session encryption key: %s", key_file)
    return Fernet(key.encode())


def is_logged_in() -> bool:
    return _client is not None


def get_session_info() -> dict:
    """Return session status with username and age in hours."""
    if _client is None:
        return {"logged_in": False, "username": None, "session_age_hours": None}
    age_hours = None
    if _login_time is not None:
        age_hours = round((time.time() - _login_time) / 3600, 1)
    return {"logged_in": True, "username": _logged_username, "session_age_hours": age_hours}


def get_last_login_error() -> str | None:
    return _last_login_error


def get_client():
    """Return the active instagrapi client or raise if not logged in."""
    if _client is None:
        raise RuntimeError("No active Instagram session. Login first.")
    return _client


async def login(username: str, password: str) -> bool:
    """Login to Instagram and save encrypted session."""
    global _client, _logged_username, _login_time, _last_login_error
    try:
        from instagrapi import Client  # lazy import

        cl = Client()

        # Apply proxy if configured
        if settings.proxy_url:
            cl.set_proxy(settings.proxy_url)
            logger.info("Using proxy for instagrapi client: %s", settings.proxy_url.split("://")[1] if "://" in settings.proxy_url else settings.proxy_url)

        cl.login(username, password)
        _client = cl
        _logged_username = username
        _login_time = time.time()
        _last_login_error = None
        _save_session(cl)
        logger.info("Instagram login successful for %s", username)
        return True
    except Exception as exc:
        logger.error("Instagram login failed: %s", exc)
        _last_login_error = _classify_login_error(exc)
        return False


async def load_session() -> bool:
    """Try to restore session from encrypted file. Returns True if successful."""
    global _client
    session_file = settings.session_file
    if not os.path.exists(session_file):
        return False
    try:
        fernet = _get_fernet()
        with open(session_file, "rb") as f:
            encrypted = f.read()
        data = json.loads(fernet.decrypt(encrypted).decode())

        from instagrapi import Client  # lazy import

        cl = Client()

        # Apply proxy if configured
        if settings.proxy_url:
            cl.set_proxy(settings.proxy_url)
            logger.info("Using proxy for restored session: %s", settings.proxy_url.split("://")[1] if "://" in settings.proxy_url else settings.proxy_url)

        cl.set_settings(data)
        cl.get_timeline_feed()  # Verify session is still valid
        _client = cl
        _login_time = os.path.getmtime(session_file)
        try:
            _logged_username = cl.username
        except Exception:
            pass
        logger.info("Restored Instagram session from %s", session_file)
        return True
    except Exception as exc:
        logger.warning("Could not restore session: %s", exc)
        _client = None
        return False


async def logout() -> None:
    global _client, _logged_username, _login_time
    if _client:
        try:
            _client.logout()
        except Exception:
            pass
        _client = None
    _logged_username = None
    _login_time = None
    # Remove session file from disk
    session_file = settings.session_file
    if os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            pass


def _save_session(cl) -> None:
    session_file = settings.session_file
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
    fernet = _get_fernet()
    data = json.dumps(cl.get_settings()).encode()
    encrypted = fernet.encrypt(data)
    with open(session_file, "wb") as f:
        f.write(encrypted)
    logger.debug("Session saved to %s", session_file)


# ── Multi-account session helpers ─────────────────────────────────────────────

def _account_session_path(username: str) -> str:
    """Return the encrypted session file path for a pool account."""
    return os.path.join(settings.sessions_dir, f"{username}.json.enc")


def _account_credentials_path(username: str) -> str:
    """Return the encrypted credentials file path for a pool account."""
    return os.path.join(settings.sessions_dir, f"{username}.creds.enc")


def save_account_credentials(username: str, password: str) -> None:
    """Save encrypted credentials for auto-relogin."""
    path = _account_credentials_path(username)
    os.makedirs(settings.sessions_dir, exist_ok=True)
    fernet = _get_fernet()
    with open(path, "wb") as f:
        f.write(fernet.encrypt(password.encode()))
    logger.debug("Credentials saved for %s", username)


def load_account_credentials(username: str) -> str | None:
    """Load encrypted credentials. Returns password or None if not saved."""
    path = _account_credentials_path(username)
    if not os.path.exists(path):
        return None
    try:
        fernet = _get_fernet()
        with open(path, "rb") as f:
            return fernet.decrypt(f.read()).decode()
    except Exception as exc:
        logger.warning("Could not load credentials for %s: %s", username, exc)
        return None


def delete_account_credentials(username: str) -> None:
    """Remove saved credentials for a pool account."""
    path = _account_credentials_path(username)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


async def login_account(
    username: str,
    password: str,
    proxy_url: str | None = None,
    save_credentials: bool = True,
) -> tuple[object | None, str | None, str | None]:
    """Login an account for the pool.

    Returns (client, error_type, message).
    On success: (client, None, None).
    On failure: (None, error_type, message) where error_type is
        'challenge' | 'phone' | '2fa' | 'credentials' | 'unknown'.
    """
    try:
        from instagrapi import Client  # lazy import

        cl = Client()
        if proxy_url:
            cl.set_proxy(proxy_url)
        elif settings.proxy_url:
            cl.set_proxy(settings.proxy_url)

        cl.login(username, password)

        # Persist encrypted session
        os.makedirs(settings.sessions_dir, exist_ok=True)
        session_path = _account_session_path(username)
        fernet = _get_fernet()
        with open(session_path, "wb") as f:
            f.write(fernet.encrypt(json.dumps(cl.get_settings()).encode()))

        # Persist encrypted credentials for auto-relogin
        if save_credentials:
            save_account_credentials(username, password)

        logger.info("Pool account login successful: %s", username)
        return cl, None, None
    except Exception as exc:
        error_type, message = _classify_login_error_typed(exc)
        logger.error("Pool account login failed for %s (%s): %s", username, error_type, exc)
        return None, error_type, message


async def load_account_session(
    username: str, proxy_url: str | None = None
) -> tuple[object | None, str | None, str | None]:
    """Restore an encrypted session for a pool account.

    On session expiry, attempts auto-relogin using saved credentials.
    Returns (client, error_type, message) — same shape as login_account().
    """
    session_path = _account_session_path(username)
    if os.path.exists(session_path):
        try:
            fernet = _get_fernet()
            with open(session_path, "rb") as f:
                data = json.loads(fernet.decrypt(f.read()).decode())

            from instagrapi import Client  # lazy import

            cl = Client()
            if proxy_url:
                cl.set_proxy(proxy_url)
            elif settings.proxy_url:
                cl.set_proxy(settings.proxy_url)

            cl.set_settings(data)
            cl.get_timeline_feed()  # Verify session still valid
            logger.info("Pool account session restored: %s", username)
            return cl, None, None
        except Exception as exc:
            logger.warning(
                "Session restore failed for %s: %s — trying auto-relogin", username, exc
            )

    # Session missing or expired — try auto-relogin with saved credentials
    password = load_account_credentials(username)
    if password:
        logger.info("Auto-relogin attempt for %s", username)
        return await login_account(username, password, proxy_url=proxy_url, save_credentials=True)

    logger.warning("No saved credentials for %s — cannot auto-relogin", username)
    return None, "no_credentials", "Sesión expirada y no hay credenciales guardadas para reconectar."


async def logout_account(username: str, delete_credentials: bool = True) -> None:
    """Clear the session (and optionally credentials) for a pool account."""
    session_path = _account_session_path(username)
    if os.path.exists(session_path):
        try:
            os.remove(session_path)
        except Exception:
            pass
    if delete_credentials:
        delete_account_credentials(username)
    logger.info("Pool account session cleared: %s", username)
