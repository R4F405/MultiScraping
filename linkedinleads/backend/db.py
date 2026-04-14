# db.py
# Base de datos SQLite del scraper.
# Tablas:
#   runs          → historial de ejecuciones (usado por viewer_app)
#   contact_queue → slugs pendientes de enriquecer por cuenta
#   contacts      → datos completos de cada contacto por cuenta

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = os.environ.get("DB_PATH") or str(DATA_DIR / "contacts.db")

# ── Esquemas ───────────────────────────────────────────────────────────────────

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT    NOT NULL,
    started_at        TEXT    NOT NULL,
    finished_at       TEXT    NOT NULL,
    contacts_scraped  INTEGER NOT NULL DEFAULT 0,
    contacts_new      INTEGER NOT NULL DEFAULT 0,
    contacts_updated  INTEGER NOT NULL DEFAULT 0
);
"""

# Cola de slugs a enriquecer. Un slug puede estar en varios usernames distintos.
# status: pending → done | error
QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL,
    slug          TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'pending',
    queued_at     TEXT    NOT NULL,
    processed_at  TEXT,
    error_msg     TEXT,
    UNIQUE (username, slug)
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON contact_queue (username, status);
"""

# Datos completos de cada contacto. Upsert por (username, profile_id).
CONTACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL,
    profile_id       TEXT    NOT NULL,
    name             TEXT,
    first_name       TEXT,
    last_name        TEXT,
    position         TEXT,
    company          TEXT,
    location         TEXT,
    emails           TEXT,
    phones           TEXT,
    profile_link     TEXT,
    profile_photo    TEXT,
    premium          INTEGER,
    creator          INTEGER,
    open_to_work     INTEGER,
    followers        TEXT,
    connections      TEXT,
    first_scraped_at TEXT    NOT NULL,
    last_scraped_at  TEXT    NOT NULL,
    UNIQUE (username, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_contacts_username ON contacts (username);
"""

# Registro de cuentas LinkedIn activas en el sistema.
# status: active | paused | removed
ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT    NOT NULL UNIQUE,
    display_name TEXT,
    email        TEXT,
    session_file TEXT    NOT NULL,
    proxy        TEXT,
    added_at     TEXT    NOT NULL,
    last_run_at  TEXT,
    status       TEXT    NOT NULL DEFAULT 'active'
);
"""

# Control persistente de cadencia por cuenta y modo (index/enrich).
TRIGGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS trigger_limits (
    key           TEXT PRIMARY KEY,
    last_trigger  REAL NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# Migraciones suaves (se aplican en ensure_tables y fallan silenciosamente si ya existen)
_ACCOUNTS_MIGRATION_PROXY     = "ALTER TABLE accounts ADD COLUMN proxy TEXT;"
_ACCOUNTS_MIGRATION_EMAIL     = "ALTER TABLE accounts ADD COLUMN email TEXT;"
_ACCOUNTS_MIGRATION_ENCPWD    = "ALTER TABLE accounts ADD COLUMN encrypted_password TEXT;"


# ── Helpers internos ───────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # mejor rendimiento con lecturas concurrentes
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── Inicialización ─────────────────────────────────────────────────────────────

def ensure_tables() -> None:
    """Crea el directorio data/ y todas las tablas si no existen."""
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(RUNS_SCHEMA)
    conn.executescript(QUEUE_SCHEMA)
    conn.executescript(CONTACTS_SCHEMA)
    conn.executescript(ACCOUNTS_SCHEMA)
    conn.executescript(TRIGGER_SCHEMA)
    # Migraciones suaves: se ignoran si la columna ya existe
    for migration in (
        _ACCOUNTS_MIGRATION_PROXY,
        _ACCOUNTS_MIGRATION_EMAIL,
        _ACCOUNTS_MIGRATION_ENCPWD,
    ):
        try:
            conn.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass
    conn.close()


# Alias de compatibilidad con el código anterior (viewer_app usa ensure_runs_table)
def ensure_runs_table() -> None:
    ensure_tables()


# ── Tabla runs ─────────────────────────────────────────────────────────────────

def insert_run(
    username: str,
    started_at: str,
    finished_at: str,
    contacts_scraped: int = 0,
    contacts_new: int = 0,
    contacts_updated: int = 0,
) -> None:
    """Registra una ejecución del scraper."""
    ensure_tables()
    conn = _connect()
    conn.execute(
        """INSERT INTO runs
           (username, started_at, finished_at, contacts_scraped, contacts_new, contacts_updated)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, started_at, finished_at, contacts_scraped, contacts_new, contacts_updated),
    )
    conn.commit()
    conn.close()


# ── Tabla contact_queue ────────────────────────────────────────────────────────

def queue_slugs(username: str, slugs: List[str]) -> int:
    """
    Añade slugs a la cola como 'pending' para un username.
    Ignora los slugs que ya estén en la cola (UNIQUE username+slug).
    Devuelve el número de slugs nuevos insertados.
    """
    ensure_tables()
    now = _now_iso()
    conn = _connect()
    inserted = 0
    for slug in slugs:
        cur = conn.execute(
            """INSERT OR IGNORE INTO contact_queue (username, slug, status, queued_at)
               VALUES (?, ?, 'pending', ?)""",
            (username, slug, now),
        )
        inserted += cur.rowcount
    conn.commit()
    conn.close()
    return inserted


def requeue_pending(username: str, slugs: List[str]) -> int:
    """
    Reactiva slugs que ya estaban en la cola (status = 'pending').
    Útil cuando queremos refrescar datos de contactos ya scrapeados.
    Devuelve cuántos se han reactivado.
    """
    ensure_tables()
    now = _now_iso()
    conn = _connect()
    updated = 0
    for slug in slugs:
        cur = conn.execute(
            """UPDATE contact_queue
               SET status = 'pending', queued_at = ?, processed_at = NULL, error_msg = NULL
               WHERE username = ? AND slug = ?""",
            (now, username, slug),
        )
        updated += cur.rowcount
    conn.commit()
    conn.close()
    return updated


def get_pending_slugs(username: str, limit: int = 20) -> List[str]:
    """
    Devuelve hasta `limit` slugs con status='pending' para ese username,
    ordenados por queued_at (los más antiguos primero → FIFO).
    """
    ensure_tables()
    conn = _connect()
    rows = conn.execute(
        """SELECT slug FROM contact_queue
           WHERE username = ? AND status = 'pending'
           ORDER BY queued_at ASC
           LIMIT ?""",
        (username, limit),
    ).fetchall()
    conn.close()
    return [r["slug"] for r in rows]


def mark_queue_done(username: str, slug: str) -> None:
    """Marca un slug como procesado correctamente."""
    conn = _connect()
    conn.execute(
        """UPDATE contact_queue
           SET status = 'done', processed_at = ?
           WHERE username = ? AND slug = ?""",
        (_now_iso(), username, slug),
    )
    conn.commit()
    conn.close()


def mark_queue_error(username: str, slug: str, error_msg: str = "") -> None:
    """Marca un slug como fallido para poder revisarlo o reintentarlo."""
    conn = _connect()
    conn.execute(
        """UPDATE contact_queue
           SET status = 'error', processed_at = ?, error_msg = ?
           WHERE username = ? AND slug = ?""",
        (_now_iso(), error_msg[:500], username, slug),
    )
    conn.commit()
    conn.close()


def get_queue_stats(username: str) -> Dict[str, int]:
    """
    Devuelve un resumen del estado de la cola: cuántos pending, done y error.
    Ejemplo: {"pending": 120, "done": 45, "error": 2, "total": 167}
    """
    ensure_tables()
    conn = _connect()
    rows = conn.execute(
        """SELECT status, COUNT(*) as n
           FROM contact_queue
           WHERE username = ?
           GROUP BY status""",
        (username,),
    ).fetchall()
    conn.close()
    stats = {"pending": 0, "done": 0, "error": 0}
    for r in rows:
        stats[r["status"]] = r["n"]
    stats["total"] = sum(stats.values())
    return stats


# ── Tabla contacts ─────────────────────────────────────────────────────────────

def upsert_contact(username: str, data: Dict) -> str:
    """
    Inserta o actualiza los datos de un contacto.
    - Si no existe (username, profile_id): INSERT con first_scraped_at = ahora.
    - Si ya existe: UPDATE de todos los campos excepto first_scraped_at.
    Devuelve 'inserted' o 'updated'.
    """
    ensure_tables()
    now = _now_iso()
    profile_id = data.get("profile_id", "")
    conn = _connect()

    existing = conn.execute(
        "SELECT id, first_scraped_at FROM contacts WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    ).fetchone()

    def _bool(v) -> Optional[int]:
        if v is None:
            return None
        return 1 if v else 0

    params = (
        username,
        profile_id,
        data.get("name"),
        data.get("first_name"),
        data.get("last_name"),
        data.get("position"),
        data.get("company"),
        data.get("location"),
        data.get("emails"),
        data.get("phones"),
        data.get("profile_link"),
        data.get("profile_photo"),
        _bool(data.get("premium")),
        _bool(data.get("creator")),
        _bool(data.get("open_to_work")),
        str(data.get("followers")) if data.get("followers") is not None else None,
        str(data.get("connections")) if data.get("connections") is not None else None,
    )

    if existing is None:
        conn.execute(
            """INSERT INTO contacts
               (username, profile_id, name, first_name, last_name, position, company,
                location, emails, phones, profile_link, profile_photo,
                premium, creator, open_to_work, followers, connections,
                first_scraped_at, last_scraped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            params + (now, now),
        )
        result = "inserted"
    else:
        conn.execute(
            """UPDATE contacts SET
               name=?, first_name=?, last_name=?, position=?, company=?,
               location=?, emails=?, phones=?, profile_link=?, profile_photo=?,
               premium=?, creator=?, open_to_work=?, followers=?, connections=?,
               last_scraped_at=?
               WHERE username=? AND profile_id=?""",
            (
                data.get("name"), data.get("first_name"), data.get("last_name"),
                data.get("position"), data.get("company"), data.get("location"),
                data.get("emails"), data.get("phones"),
                data.get("profile_link"), data.get("profile_photo"),
                _bool(data.get("premium")), _bool(data.get("creator")),
                _bool(data.get("open_to_work")),
                str(data.get("followers")) if data.get("followers") is not None else None,
                str(data.get("connections")) if data.get("connections") is not None else None,
                now, username, profile_id,
            ),
        )
        result = "updated"

    conn.commit()
    conn.close()
    return result


def get_daily_count(username: str) -> int:
    """
    Número de contactos marcados como 'done' en la cola HOY (UTC) para ese username.
    Se usa para controlar el presupuesto diario de scraping.
    """
    ensure_tables()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _connect()
    row = conn.execute(
        """SELECT COUNT(*) as n FROM contact_queue
           WHERE username = ? AND status = 'done'
           AND processed_at LIKE ?""",
        (username, f"{today}%"),
    ).fetchone()
    conn.close()
    return row["n"] if row else 0


def contact_exists(username: str, profile_id: str) -> bool:
    """True si el contacto ya está en la tabla contacts."""
    ensure_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM contacts WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    ).fetchone()
    conn.close()
    return row is not None


def contact_has_core_fields(username: str, profile_id: str) -> bool:
    """
    True si el contacto ya tiene metadatos de perfil suficientes para no re-scrapear
    en runs recientes.
    """
    ensure_tables()
    conn = _connect()
    row = conn.execute(
        """SELECT name, position, company, location
           FROM contacts
           WHERE username = ? AND profile_id = ?""",
        (username, profile_id),
    ).fetchone()
    conn.close()
    if row is None:
        return False

    def _has(v) -> bool:
        return isinstance(v, str) and bool(v.strip())

    # Consideramos "completo" si tiene al menos 2 campos core poblados.
    filled = sum(
        1 for k in ("name", "position", "company", "location")
        if _has(row[k])
    )
    return filled >= 2


def days_since_last_scrape(username: str, profile_id: str) -> Optional[float]:
    """
    Días transcurridos desde el último scraping de este contacto.
    Devuelve None si el contacto no existe todavía en la tabla contacts.
    """
    ensure_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT last_scraped_at FROM contacts WHERE username = ? AND profile_id = ?",
        (username, profile_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    try:
        last_dt = datetime.fromisoformat(row["last_scraped_at"].replace("Z", "+00:00"))
        now_dt = datetime.now(timezone.utc)
        return (now_dt - last_dt).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


# ── Consultas paginadas de contactos ──────────────────────────────────────────

# Columnas permitidas para ORDER BY (evita inyección SQL)
_ALLOWED_SORT_COLS = {
    "name", "first_name", "last_name", "position", "company",
    "location", "emails", "phones", "followers", "connections",
    "last_scraped_at", "first_scraped_at",
}


def _contacts_where(
    username: str,
    search: str = "",
    filter_mode: str = "all",
    run_from: Optional[str] = None,
    run_to: Optional[str] = None,
) -> tuple:
    """
    Construye la cláusula WHERE y la lista de parámetros para consultas
    sobre la tabla contacts. Devuelve (clause_str, params_list).
    """
    clauses: List[str] = []
    params: list = []

    # username vacío => vista agregada "todas las cuentas"
    if username and username.strip():
        clauses.append("username = ?")
        params.append(username.strip())

    if search:
        like = f"%{search}%"
        clauses.append(
            "(name LIKE ? OR company LIKE ? OR position LIKE ? "
            "OR location LIKE ? OR emails LIKE ?)"
        )
        params.extend([like, like, like, like, like])

    if filter_mode == "email":
        clauses.append("emails IS NOT NULL AND emails != ''")
    elif filter_mode == "phone":
        clauses.append("phones IS NOT NULL AND phones != ''")
    elif filter_mode == "email_phone":
        clauses.append(
            "emails IS NOT NULL AND emails != '' "
            "AND phones IS NOT NULL AND phones != ''"
        )

    if run_from:
        clauses.append("last_scraped_at >= ?")
        params.append(run_from)
    if run_to:
        clauses.append("last_scraped_at <= ?")
        params.append(run_to)

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def count_contacts_filtered(
    username: str,
    search: str = "",
    filter_mode: str = "all",
    run_from: Optional[str] = None,
    run_to: Optional[str] = None,
) -> int:
    """
    Cuenta los contactos de una cuenta que cumplen los filtros dados.
    Útil para calcular el número de páginas en la paginación.
    """
    ensure_tables()
    conn = _connect()
    where, params = _contacts_where(username, search, filter_mode, run_from, run_to)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM contacts WHERE {where}", params
    ).fetchone()
    conn.close()
    return int(row["n"]) if row else 0


def get_contacts_paginated(
    username: str,
    page: int = 1,
    per_page: int = 50,
    search: str = "",
    filter_mode: str = "all",
    sort_col: str = "last_scraped_at",
    sort_order: str = "desc",
    run_from: Optional[str] = None,
    run_to: Optional[str] = None,
) -> List[Dict]:
    """
    Devuelve una página de contactos con filtros, búsqueda y ordenación.

    Parámetros:
      page        → número de página, empieza en 1
      per_page    → filas por página (1–200)
      search      → texto libre; busca en name, company, position, location, emails
      filter_mode → "all" | "email" | "phone" | "email_phone"
      sort_col    → columna por la que ordenar (validada contra _ALLOWED_SORT_COLS)
      sort_order  → "asc" | "desc"
      run_from    → ISO timestamp: filtrar contactos scrapeados desde esta fecha
      run_to      → ISO timestamp: filtrar contactos scrapeados hasta esta fecha
    """
    col   = sort_col if sort_col in _ALLOWED_SORT_COLS else "last_scraped_at"
    order = "ASC" if sort_order.lower() == "asc" else "DESC"
    limit  = min(max(1, per_page), 200)
    offset = (max(1, page) - 1) * limit

    ensure_tables()
    conn = _connect()
    where, params = _contacts_where(username, search, filter_mode, run_from, run_to)
    rows = conn.execute(
        f"SELECT * FROM contacts WHERE {where} ORDER BY {col} {order} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Tabla accounts ────────────────────────────────────────────────────────────

def get_account_proxy(username: str) -> Optional[str]:
    """
    Devuelve el proxy configurado para esta cuenta, o None si no tiene.
    Formato esperado: 'host:port' o 'user:pass@host:port'
    """
    ensure_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT proxy FROM accounts WHERE username = ? AND status = 'active'",
        (username,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row["proxy"] or None


def register_account(
    username: str,
    session_file: str,
    display_name: str = "",
    proxy: str = "",
    email: str = "",
) -> str:
    """
    Registra una cuenta LinkedIn.
    Si ya existe, la reactiva y actualiza session_file, display_name y email.
    El proxy solo se actualiza si se pasa un valor no vacío.
    Devuelve 'inserted' o 'updated'.
    """
    ensure_tables()
    now = _now_iso()
    conn = _connect()
    existing = conn.execute(
        "SELECT id FROM accounts WHERE username = ?", (username,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """INSERT INTO accounts
               (username, display_name, email, session_file, proxy, added_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (username, display_name or username, email or None, session_file, proxy or None, now),
        )
        result = "inserted"
    else:
        conn.execute(
            """UPDATE accounts
               SET session_file = ?, display_name = ?, email = ?, proxy = ?, status = 'active'
               WHERE username = ?""",
            (session_file, display_name or username, email or None, proxy or None, username),
        )
        result = "updated"
    conn.commit()
    conn.close()
    return result


def get_contacts(username: str, limit: Optional[int] = None) -> List[Dict]:
    """
    Devuelve los contactos enriquecidos de una cuenta, ordenados por último scraping.
    """
    ensure_tables()
    conn = _connect()
    q = "SELECT * FROM contacts WHERE username = ? ORDER BY last_scraped_at DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q, (username,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_account_proxy(username: str, proxy: str) -> bool:
    """
    Actualiza el proxy de una cuenta.
    Devuelve True si la cuenta existía, False si no.
    """
    ensure_tables()
    conn = _connect()
    cur = conn.execute(
        "UPDATE accounts SET proxy = ? WHERE username = ?",
        (proxy or None, username),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_accounts(include_inactive: bool = False) -> List[Dict]:
    """
    Devuelve todas las cuentas registradas.
    Si include_inactive=False (defecto), solo devuelve las activas.
    """
    ensure_tables()
    conn = _connect()
    if include_inactive:
        rows = conn.execute("SELECT * FROM accounts ORDER BY added_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE status = 'active' ORDER BY added_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_account_last_run(username: str) -> None:
    """Actualiza last_run_at de la cuenta al momento actual."""
    conn = _connect()
    conn.execute(
        "UPDATE accounts SET last_run_at = ? WHERE username = ?",
        (_now_iso(), username),
    )
    conn.commit()
    conn.close()


def deactivate_account(username: str) -> bool:
    """
    Marca la cuenta como 'removed' (no la borra para preservar historial).
    Devuelve True si existía, False si no.
    """
    ensure_tables()
    conn = _connect()
    cur = conn.execute(
        "UPDATE accounts SET status = 'removed' WHERE username = ?", (username,)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ── trigger_limits (cadencia persistente) ─────────────────────────────────────

def get_last_trigger_epoch(key: str) -> float:
    """
    Devuelve epoch seconds del último trigger para `key`, o 0.0 si no existe.
    """
    ensure_tables()
    conn = _connect()
    row = conn.execute(
        "SELECT last_trigger FROM trigger_limits WHERE key = ?",
        (key,),
    ).fetchone()
    conn.close()
    if not row:
        return 0.0
    try:
        return float(row["last_trigger"])
    except (TypeError, ValueError):
        return 0.0


def set_last_trigger_epoch(key: str, ts: float) -> None:
    """
    Guarda/actualiza el último trigger para `key` en epoch seconds.
    """
    ensure_tables()
    conn = _connect()
    conn.execute(
        """INSERT INTO trigger_limits (key, last_trigger, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
             last_trigger = excluded.last_trigger,
             updated_at = excluded.updated_at
        """,
        (key, float(ts), _now_iso()),
    )
    conn.commit()
    conn.close()


# ── Credenciales cifradas ──────────────────────────────────────────────────────

def _get_cipher():
    """
    Devuelve un objeto Fernet inicializado con CREDENTIAL_KEY del entorno.
    Si la variable no está configurada, lanza ValueError.
    La clave debe ser una clave Fernet válida de 32 bytes en base64-urlsafe.
    Para generar una nueva: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """
    from cryptography.fernet import Fernet
    key = os.environ.get("CREDENTIAL_KEY", "").strip()
    if not key:
        raise ValueError(
            "CREDENTIAL_KEY no está configurada en .env. "
            "Genera una con: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def save_account_credentials(username: str, password: str) -> bool:
    """
    Cifra y guarda la contraseña de una cuenta.
    El email ya está en la columna 'email' de accounts — solo necesitamos la contraseña.
    Requiere CREDENTIAL_KEY en .env.
    Devuelve True si se guardó, False si la cuenta no existe o no hay clave configurada.
    """
    try:
        cipher = _get_cipher()
        encrypted = cipher.encrypt(password.encode()).decode()
        ensure_tables()
        conn = _connect()
        cur = conn.execute(
            "UPDATE accounts SET encrypted_password = ? WHERE username = ?",
            (encrypted, username),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except (ValueError, Exception):
        return False


def get_account_credentials(username: str) -> Optional[Dict]:
    """
    Recupera y descifra las credenciales guardadas para una cuenta.
    Devuelve {"email": "...", "password": "..."} o None si no hay credenciales
    guardadas o no se pueden descifrar.
    """
    try:
        ensure_tables()
        conn = _connect()
        row = conn.execute(
            "SELECT email, encrypted_password FROM accounts WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        if not row or not row["encrypted_password"] or not row["email"]:
            return None
        cipher = _get_cipher()
        password = cipher.decrypt(row["encrypted_password"].encode()).decode()
        return {"email": row["email"], "password": password}
    except Exception:
        return None


def has_saved_credentials(username: str) -> bool:
    """True si la cuenta tiene contraseña cifrada guardada Y tiene email configurado."""
    try:
        ensure_tables()
        conn = _connect()
        row = conn.execute(
            "SELECT email, encrypted_password FROM accounts WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()
        return bool(row and row["email"] and row["encrypted_password"])
    except Exception:
        return False
