"""Encrypted key–value store for runtime settings.

Lets settings that used to live in plaintext ``.env`` files (Instagram session
id, proxy list, …) be edited from the web panel and persisted **encrypted at
rest** in a small SQLite database instead.

Resolution order for any setting is: value saved here (DB) → environment
variable → hard-coded default. The ``.env`` therefore keeps working as a
fallback and nothing breaks for instances that never touch the panel.

Encryption uses Fernet (AES-128-CBC + HMAC-SHA256). The key comes from
``CONFIG_ENCRYPTION_KEY`` when set; otherwise it is generated once and stored
next to the database with ``0600`` perms — keeping the secret out of ``.env``
by default while still surviving restarts (the data dir is a Docker volume).
"""

import logging
import os
import sqlite3
import threading

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class SettingsStore:
    def __init__(self, db_path: str, key_path: str):
        self._db_path = db_path
        self._key_path = key_path
        self._lock = threading.Lock()
        self._fernet: Fernet | None = None
        self._ensure_db()

    # ── encryption key ────────────────────────────────────────────────────────
    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            raw = os.getenv("CONFIG_ENCRYPTION_KEY", "").strip()
            key = raw.encode() if raw else self._load_or_create_key_file()
            self._fernet = Fernet(key)
        return self._fernet

    def _load_or_create_key_file(self) -> bytes:
        if os.path.exists(self._key_path):
            with open(self._key_path, "rb") as f:
                return f.read().strip()
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(self._key_path), exist_ok=True)
        with open(self._key_path, "wb") as f:
            f.write(key)
        try:
            os.chmod(self._key_path, 0o600)
        except OSError:
            pass
        logger.info("Generated new config encryption key at %s", self._key_path)
        return key

    # ── storage ───────────────────────────────────────────────────────────────
    def _ensure_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS app_config ("
                "key TEXT PRIMARY KEY, "
                "value_enc TEXT NOT NULL, "
                "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )

    def get(self, key: str) -> str | None:
        """Decrypted value saved in the DB, or None if unset/undecryptable."""
        with self._lock, sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT value_enc FROM app_config WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        try:
            return self._get_fernet().decrypt(row[0].encode()).decode()
        except (InvalidToken, ValueError):
            logger.warning("Could not decrypt config key '%s' (encryption key changed?)", key)
            return None

    def has(self, key: str) -> bool:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM app_config WHERE key = ?", (key,)
            ).fetchone()
        return row is not None

    def set(self, key: str, value: str) -> None:
        token = self._get_fernet().encrypt(value.encode()).decode()
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO app_config (key, value_enc, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value_enc = excluded.value_enc, updated_at = CURRENT_TIMESTAMP",
                (key, token),
            )

    def delete(self, key: str) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM app_config WHERE key = ?", (key,))


def _default_data_dir() -> str:
    """Directory of the service's main SQLite DB (a persistent Docker volume)."""
    from backend.config.settings import Settings

    return os.path.dirname(os.path.abspath(Settings.DB_PATH))


_data_dir = _default_data_dir()
store = SettingsStore(
    db_path=os.path.join(_data_dir, "config.db"),
    key_path=os.path.join(_data_dir, ".config_key"),
)
