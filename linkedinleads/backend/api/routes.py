"""
FastAPI routes para el LinkedIn Scraper.

Endpoints:
  GET  /api/linkedin/health
  GET  /api/linkedin/accounts
  POST /api/linkedin/accounts
  DELETE /api/linkedin/accounts/{username}
  GET  /api/linkedin/accounts/{username}/stats
  POST /api/linkedin/search          → lanza index/enrich en background
  GET  /api/linkedin/status          → estado del job en curso
  GET  /api/linkedin/jobs
  GET  /api/linkedin/leads
  GET  /api/linkedin/leads/export
"""

import io
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.api.schemas import (
    AccountAddRequest,
    AccountResponse,
    HealthResponse,
    JobResponse,
    JobStatusResponse,
    LeadResponse,
    SearchRequest,
)
from backend.config.settings import DB_PATH, SESSIONS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/linkedin")

# ── Estado del job en curso (thread-safe) ─────────────────────────────────────

@dataclass
class JobState:
    running: bool = False
    mode: Optional[str] = None
    account: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    progress: dict[str, Any] = field(default_factory=dict)


_job_lock = threading.Lock()
_job_state = JobState()

# Compatibilidad con tests/consumidores antiguos.
_job_running = False
_job_mode: Optional[str] = None
_job_account: Optional[str] = None
_job_error: Optional[str] = None
_job_started_at: Optional[str] = None
_job_finished_at: Optional[str] = None
_job_progress: dict[str, Any] = {}

_login_status_lock = threading.Lock()
_login_status: dict[str, dict[str, Any]] = {}

# Control de cadencia: evita spam de ejecuciones
_MIN_ENRICH_INTERVAL = 20 * 60   # 20 min entre enrich del mismo usuario
_MIN_INDEX_INTERVAL = 60 * 60    # 60 min entre index


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _db_exists() -> bool:
    return Path(DB_PATH).exists()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_percent(current: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (current / total) * 100.0)), 1)


def _sync_legacy_job_globals() -> None:
    global _job_running, _job_mode, _job_account, _job_error, _job_started_at, _job_finished_at, _job_progress
    _job_running = _job_state.running
    _job_mode = _job_state.mode
    _job_account = _job_state.account
    _job_error = _job_state.error
    _job_started_at = _job_state.started_at
    _job_finished_at = _job_state.finished_at
    _job_progress = dict(_job_state.progress or {})


def _update_job_progress(
    *,
    phase: Optional[str] = None,
    label: Optional[str] = None,
    detail: Optional[str] = None,
    current: Optional[int] = None,
    total: Optional[int] = None,
    new_count: Optional[int] = None,
    updated_count: Optional[int] = None,
    skipped_count: Optional[int] = None,
    error_count: Optional[int] = None,
    queue_pending: Optional[int] = None,
    queue_done: Optional[int] = None,
    queue_error: Optional[int] = None,
    eta_seconds: Optional[int] = None,
) -> None:
    with _job_lock:
        p = dict(_job_state.progress or {})
        if phase is not None:
            p["phase"] = phase
        if label is not None:
            p["label"] = label
        if detail is not None:
            p["detail"] = detail
        if current is not None:
            p["current"] = max(0, int(current))
        if total is not None:
            p["total"] = max(0, int(total))
        if new_count is not None:
            p["new_count"] = max(0, int(new_count))
        if updated_count is not None:
            p["updated_count"] = max(0, int(updated_count))
        if skipped_count is not None:
            p["skipped_count"] = max(0, int(skipped_count))
        if error_count is not None:
            p["error_count"] = max(0, int(error_count))
        if queue_pending is not None:
            p["queue_pending"] = max(0, int(queue_pending))
        if queue_done is not None:
            p["queue_done"] = max(0, int(queue_done))
        if queue_error is not None:
            p["queue_error"] = max(0, int(queue_error))
        if eta_seconds is not None:
            p["eta_seconds"] = max(0, int(eta_seconds))

        cur = int(p.get("current", 0))
        tot = int(p.get("total", 0))
        if tot > 0:
            p["percent"] = _safe_percent(cur, tot)
        _job_state.progress = p
        _sync_legacy_job_globals()


def _set_login_status(account_key: str, status: str, message: str, **extra: Any) -> None:
    with _login_status_lock:
        _login_status[account_key] = {
            "account": account_key,
            "status": status,
            "message": message,
            "updated_at": _utc_now_iso(),
            **extra,
        }


def _adopt_legacy_job_globals_if_needed() -> None:
    """Compatibilidad: algunos tests tocan variables legacy directamente."""
    if (
        _job_running != _job_state.running
        or _job_mode != _job_state.mode
        or _job_account != _job_state.account
    ):
        _job_state.running = _job_running
        _job_state.mode = _job_mode
        _job_state.account = _job_account
        _job_state.error = _job_error
        _job_state.started_at = _job_started_at
        _job_state.finished_at = _job_finished_at
        _job_state.progress = dict(_job_progress or {})


def _session_status(username: str) -> dict:
    session_file = str(SESSIONS_DIR / f"{username}.pkl")
    p = Path(session_file)
    if not p.exists():
        # Intentar también session.pkl raíz para cuenta por defecto
        root = Path(DB_PATH).parent.parent / "session.pkl"
        if root.exists():
            p = root
        else:
            return {"session_exists": False, "session_age_days": None, "session_ok": False}
    try:
        age_days = (time.time() - p.stat().st_mtime) / 86400
        session_ok = age_days < 75
        return {
            "session_exists": True,
            "session_age_days": round(age_days, 1),
            "session_ok": session_ok,
        }
    except Exception:
        return {"session_exists": True, "session_age_days": None, "session_ok": None}


def _cooldown_remaining(account: str) -> dict:
    """Segundos restantes de cooldown para index y enrich de una cuenta (0 = disponible)."""
    try:
        from backend.db import get_last_trigger_epoch
        now = time.time()
        index_last = get_last_trigger_epoch(f"{account}:index")
        enrich_last = get_last_trigger_epoch(f"{account}:enrich")
        return {
            "index_cooldown_remaining": max(0, int(_MIN_INDEX_INTERVAL - (now - index_last))),
            "enrich_cooldown_remaining": max(0, int(_MIN_ENRICH_INTERVAL - (now - enrich_last))),
        }
    except Exception:
        return {"index_cooldown_remaining": 0, "enrich_cooldown_remaining": 0}


def _account_stats(username: str) -> dict:
    try:
        from backend.db import get_queue_stats, get_daily_count
        queue = get_queue_stats(username)
        daily = get_daily_count(username)
        contacts_total = 0
        if _db_exists():
            conn = _db_conn()
            row = conn.execute(
                "SELECT COUNT(*) as n FROM contacts WHERE username = ?", (username,)
            ).fetchone()
            conn.close()
            contacts_total = row["n"] if row else 0
        return {
            "queue_pending": queue.get("pending", 0),
            "queue_done": queue.get("done", 0),
            "queue_error": queue.get("error", 0),
            "queue_total": queue.get("total", 0),
            "contacts_total": contacts_total,
            "daily_count": daily,
        }
    except Exception:
        return {
            "queue_pending": 0, "queue_done": 0, "queue_error": 0,
            "queue_total": 0, "contacts_total": 0, "daily_count": 0,
        }


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> Any:
    accounts_count = 0
    if _db_exists():
        try:
            conn = _db_conn()
            row = conn.execute(
                "SELECT COUNT(*) as n FROM accounts WHERE status = 'active'"
            ).fetchone()
            conn.close()
            accounts_count = row["n"] if row else 0
        except Exception:
            pass
    import os as _os
    from backend.api.schemas import _MAX_CONTACTS_CAP
    _default = max(1, int(_os.getenv("MAX_CONTACTS_PER_RUN", "20")))
    return {
        "status": "ok",
        "db_exists": _db_exists(),
        "accounts_count": accounts_count,
        "max_contacts_cap": _MAX_CONTACTS_CAP,
        "max_contacts_default": _default,
    }


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def stats() -> Any:
    if not _db_exists():
        return {"total_contacts": 0, "with_email": 0, "with_phone": 0}
    try:
        conn = _db_conn()
        row = conn.execute("SELECT COUNT(*) as n FROM contacts").fetchone()
        total = row["n"] if row else 0
        row_email = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE emails IS NOT NULL AND emails != ''"
        ).fetchone()
        with_email = row_email["n"] if row_email else 0
        row_phone = conn.execute(
            "SELECT COUNT(*) as n FROM contacts WHERE phones IS NOT NULL AND phones != ''"
        ).fetchone()
        with_phone = row_phone["n"] if row_phone else 0
        conn.close()
        return {"total_contacts": total, "with_email": with_email, "with_phone": with_phone}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Accounts ──────────────────────────────────────────────────────────────────

@router.get("/accounts")
async def list_accounts() -> Any:
    try:
        from backend.db import get_all_accounts_with_stats
        accounts = get_all_accounts_with_stats(include_inactive=False)
        result = []
        for acc in accounts:
            session_info = _session_status(acc["username"])
            cooldown = _cooldown_remaining(acc["username"])
            proxy_raw = acc.get("proxy") or ""
            proxy_masked = proxy_raw.split("@")[-1] if "@" in proxy_raw else proxy_raw or None
            result.append({
                **acc,
                "proxy": proxy_masked,
                **session_info,
                **cooldown,
            })
        return result
    except Exception as exc:
        logger.exception("list_accounts error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/accounts/login-status")
async def login_status(account: Optional[str] = Query(default=None)) -> Any:
    with _login_status_lock:
        if account:
            return _login_status.get(account, {"account": account, "status": "unknown"})
        return list(_login_status.values())


@router.post("/accounts")
async def add_account(body: AccountAddRequest) -> Any:
    """
    Registra una nueva cuenta e inicia sesión en LinkedIn en background.
    La contraseña NO se almacena en la BD en claro; se cifra con Fernet.
    """
    import re as _re

    if not body.email:
        raise HTTPException(status_code=400, detail="El campo 'email' es obligatorio.")
    if not body.password:
        raise HTTPException(status_code=400, detail="El campo 'password' es obligatorio.")

    email = body.email.strip()
    password = body.password.strip()
    username = (body.username or "").strip()
    display_name = (body.display_name or "").strip()
    proxy = (body.proxy or "").strip() or None

    if proxy and not _re.match(r"^.+:\d+$", proxy.split("@")[-1]):
        raise HTTPException(
            status_code=400,
            detail="Formato de proxy inválido. Usa: user:pass@host:port o host:port",
        )

    account_key = username or email.split("@")[0].replace(".", "-")
    _set_login_status(account_key, "started", "Login iniciado en background.")
    t = threading.Thread(target=_do_add_account, args=(username, email, password, display_name, proxy), daemon=True)
    t.start()
    return {"status": "login_started", "message": "Login iniciado en background.", "account": account_key}


def _do_add_account(
    username: str,
    email: str,
    password: str,
    display_name: str,
    proxy: Optional[str],
) -> None:
    """Ejecuta el login de LinkedIn en un hilo separado."""
    import sys

    # Aseguramos que el backend/ esté en path para que scraper.py y db.py se importen bien
    backend_dir = str(Path(__file__).resolve().parent.parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    temp_slug = username or email.split("@")[0].replace(".", "-")
    _set_login_status(temp_slug, "running", "Intentando autenticación en LinkedIn...")
    try:
        from backend.scraper import login_with_credentials
        from backend.db import register_account, ensure_tables, save_account_credentials

        ensure_tables()

        session_file = str(SESSIONS_DIR / f"{temp_slug}.pkl")

        result = login_with_credentials(
            temp_slug,
            email,
            password,
            proxy=proxy,
            headless=True,
        )

        if result.get("status") != "ok":
            logger.error("add_account login failed: %s", result.get("message"))
            _set_login_status(temp_slug, "failed", result.get("message") or "Login fallido")
            print(f"[add_account] ❌ Login fallido: {result.get('message')} (status={result.get('status')})")
            return

        print(f"[add_account] ✅ Login OK — result={result}")

        # Detectar username real desde la sesión
        try:
            import pickle
            with open(session_file, "rb") as f:
                sess = pickle.load(f)
            real_username = getattr(sess, "username", "") or temp_slug
            print(f"[add_account] session cargada — username detectado: {real_username!r}")
        except Exception as e:
            print(f"[add_account] ⚠️  No se pudo cargar sesión ({e}), usando temp_slug: {temp_slug!r}")
            real_username = temp_slug

        # Si hay username real diferente, renombrar el .pkl
        if real_username and real_username != temp_slug:
            new_session_file = str(SESSIONS_DIR / f"{real_username}.pkl")
            try:
                import shutil
                shutil.move(session_file, new_session_file)
                session_file = new_session_file
            except Exception:
                pass

        print(f"[add_account] Registrando cuenta '{real_username}' en DB → {session_file}")
        register_account(
            username=real_username,
            display_name=display_name or real_username,
            email=email,
            session_file=session_file,
            proxy=proxy or "",
        )
        if not save_account_credentials(real_username, password):
            logger.warning("add_account: no se pudieron guardar credenciales cifradas para '%s'", real_username)
        logger.info("add_account: cuenta '%s' registrada correctamente", real_username)
        _set_login_status(
            real_username,
            "success",
            "Cuenta registrada y sesión guardada.",
            session_file=session_file,
        )
        print(f"[add_account] ✅ Cuenta '{real_username}' registrada en DB correctamente")

    except Exception as exc:
        print(f"[add_account] ❌ ERROR en background: {exc}")
        _set_login_status(temp_slug, "failed", str(exc))
        logger.exception("add_account background error: %s", exc)
    finally:
        try:
            from backend.scraper import _cleanup_pw
            _cleanup_pw()
        except Exception:
            pass


@router.delete("/accounts/{username}")
async def delete_account(username: str) -> Any:
    try:
        from backend.db import deactivate_account
        deactivate_account(username)
        return {"status": "deactivated", "username": username}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/accounts/{username}/stats")
async def account_stats(username: str) -> Any:
    stats = _account_stats(username)
    session_info = _session_status(username)
    return {**stats, **session_info}


# ── Search / trigger ──────────────────────────────────────────────────────────

@router.post("/search")
async def trigger_search(req: SearchRequest) -> Any:
    with _job_lock:
        _adopt_legacy_job_globals_if_needed()
        if _job_state.running:
            raise HTTPException(
                status_code=409,
                detail="Ya hay un scrape en curso. Espera a que termine.",
            )

        # Control de cadencia persistente (sobrevive reinicios)
        interval = _MIN_INDEX_INTERVAL if req.mode == "index" else _MIN_ENRICH_INTERVAL
        key = f"{req.account}:{req.mode}"
        from backend.db import get_last_trigger_epoch, set_last_trigger_epoch
        last = get_last_trigger_epoch(key)
        elapsed = time.time() - last
        if elapsed < interval:
            wait_min = int((interval - elapsed) / 60)
            raise HTTPException(
                status_code=429,
                detail=f"Demasiado pronto. Espera {wait_min} min antes del próximo {req.mode}.",
            )

        _job_state.running = True
        _job_state.mode = req.mode
        _job_state.account = req.account
        _job_state.error = None
        _job_state.started_at = _utc_now_iso()
        _job_state.finished_at = None
        _job_state.progress = {
            "phase": "queued",
            "label": "En cola",
            "detail": f"Preparando job {req.mode} para @{req.account}",
            "current": 0,
            "total": 0,
            "percent": 0.0,
            "new_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "queue_pending": 0,
            "queue_done": 0,
            "queue_error": 0,
            "eta_seconds": None,
            "strategy_errors": {"requests": 0, "voyager": 0, "overlay": 0},
        }
        _sync_legacy_job_globals()
        set_last_trigger_epoch(key, time.time())

    t = threading.Thread(target=_run_job, args=(req.mode, req.account, req.max_contacts), daemon=True)
    t.start()
    return {"status": "started", "mode": req.mode, "account": req.account}


def _run_job(mode: str, account: str, max_contacts: int) -> None:
    import sys
    backend_dir = str(Path(__file__).resolve().parent.parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    try:
        from backend.linkedin_main import run_index, run_enrich
        from backend.db import ensure_tables
        ensure_tables()

        def progress_cb(payload: dict[str, Any]) -> None:
            _update_job_progress(
                phase=payload.get("phase"),
                label=payload.get("label"),
                detail=payload.get("detail"),
                current=payload.get("current"),
                total=payload.get("total"),
                new_count=payload.get("new_count"),
                updated_count=payload.get("updated_count"),
                skipped_count=payload.get("skipped_count"),
                error_count=payload.get("error_count"),
                queue_pending=payload.get("queue_pending"),
                queue_done=payload.get("queue_done"),
                queue_error=payload.get("queue_error"),
                eta_seconds=payload.get("eta_seconds"),
            )
            if payload.get("strategy_errors") is not None:
                with _job_lock:
                    p = dict(_job_state.progress or {})
                    p["strategy_errors"] = payload.get("strategy_errors")
                    _job_state.progress = p
                    _sync_legacy_job_globals()

        if mode == "index":
            run_index(interactive=False, account=account, progress_callback=progress_cb)
        else:
            run_enrich(
                interactive=False,
                max_contacts_override=max_contacts,
                account=account,
                progress_callback=progress_cb,
            )
    except Exception as exc:
        logger.exception("_run_job error [%s/%s]: %s", mode, account, exc)
        with _job_lock:
            _job_state.error = str(exc)
            _job_state.progress["phase"] = "error"
            _job_state.progress["label"] = "Error en ejecución"
            _job_state.progress["detail"] = str(exc)
            _sync_legacy_job_globals()
    finally:
        # Reset Playwright singleton so the next job thread gets a fresh instance.
        # The sync_playwright instance is tied to the thread that created it; once
        # this daemon thread exits the instance is unusable ("cannot switch to a
        # different thread (which happens to have exited)").
        try:
            from backend.scraper import _cleanup_pw
            _cleanup_pw()
        except Exception:
            pass
        with _job_lock:
            _job_state.running = False
            _job_state.finished_at = _utc_now_iso()
            if _job_state.error:
                _job_state.progress["phase"] = "error"
                _job_state.progress["label"] = "Error en ejecución"
            else:
                _job_state.progress["phase"] = "done"
                _job_state.progress["label"] = "Completado"
                if int(_job_state.progress.get("total", 0)) > 0:
                    _job_state.progress["current"] = int(_job_state.progress.get("total", 0))
                    _job_state.progress["percent"] = 100.0
                elif mode == "index":
                    _job_state.progress["percent"] = 100.0
            _sync_legacy_job_globals()


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=JobStatusResponse)
async def job_status() -> Any:
    with _job_lock:
        _adopt_legacy_job_globals_if_needed()
        elapsed = None
        if _job_state.started_at:
            try:
                started_dt = datetime.fromisoformat(_job_state.started_at.replace("Z", "+00:00"))
                elapsed = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            except Exception:
                elapsed = None
        payload = {
            "running": _job_state.running,
            "mode": _job_state.mode,
            "account": _job_state.account,
            "error": _job_state.error,
            "started_at": _job_state.started_at,
            "finished_at": _job_state.finished_at,
            "elapsed_seconds": elapsed,
            **_job_state.progress,
        }
        # Evita mostrar 100% mientras el job todavía está marcado como running.
        if payload.get("running") and isinstance(payload.get("percent"), (int, float)):
            if float(payload["percent"]) >= 100.0:
                payload["percent"] = 99.0
                if payload.get("phase") == "done":
                    payload["phase"] = "finalizing"
                    payload["label"] = "Finalizando"
        return payload


# ── Jobs (historial de runs) ──────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(
    limit: int = Query(default=50, ge=1, le=500),
    account: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=0),
) -> Any:
    if not _db_exists():
        return []
    try:
        clauses = []
        params: list = []

        if days > 0:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            clauses.append("started_at >= ?")
            params.append(cutoff)

        if account:
            clauses.append("username = ?")
            params.append(account)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        conn = _db_conn()
        rows = conn.execute(
            f"""SELECT id, username, started_at, finished_at,
                       contacts_scraped, contacts_new, contacts_updated
                FROM runs {where}
                ORDER BY id DESC LIMIT ?""",
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Leads (contacts) ──────────────────────────────────────────────────────────

_DISPLAY_COLS = [
    "id", "username", "name", "first_name", "last_name",
    "position", "company", "location",
    "emails", "phones", "profile_link",
    "premium", "open_to_work", "followers", "connections",
    "first_scraped_at", "last_scraped_at",
]


@router.get("/leads")
async def list_leads(
    account: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    filter: Optional[str] = Query(default="all"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    sort: str = Query(default="last_scraped_at"),
    order: str = Query(default="desc"),
) -> Any:
    if not _db_exists():
        return {"total": 0, "page": page, "per_page": per_page, "pages": 1, "contacts": []}

    try:
        from backend.db import get_contacts_paginated, count_contacts_filtered

        filter_mode = filter if filter in ("all", "email", "phone", "email_phone") else "all"
        sort_col = sort if sort in (
            "name", "company", "position", "location", "emails", "last_scraped_at", "first_scraped_at"
        ) else "last_scraped_at"
        sort_order = "desc" if order.lower() != "asc" else "asc"

        total = count_contacts_filtered(account or "", search or "", filter_mode, None, None)
        contacts = get_contacts_paginated(
            account or "", page, per_page, search or "", filter_mode, sort_col, sort_order, None, None
        )

        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, -(-total // per_page)),
            "contacts": [
                {k: c.get(k) for k in _DISPLAY_COLS if k in c}
                for c in contacts
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/leads/export")
async def export_leads(
    account: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    filter: Optional[str] = Query(default="all"),
    format: str = Query(default="csv"),
) -> Any:
    if not _db_exists():
        raise HTTPException(status_code=404, detail="No hay datos aún.")

    filter_mode = filter if filter in ("all", "email", "phone", "email_phone") else "all"

    def _csv_gen():
        import csv

        _EXPORT_COLS = [
            ("name", "Nombre completo"),
            ("first_name", "Nombre"),
            ("last_name", "Apellidos"),
            ("position", "Cargo"),
            ("company", "Empresa"),
            ("location", "Ubicación"),
            ("emails", "Email"),
            ("phones", "Teléfono"),
            ("profile_link", "URL LinkedIn"),
            ("premium", "Premium"),
            ("open_to_work", "Disponible para trabajar"),
            ("followers", "Seguidores"),
            ("connections", "Conexiones"),
            ("first_scraped_at", "Primera vez scrapeado"),
            ("last_scraped_at", "Último scraping"),
        ]
        db_cols = [k for k, _ in _EXPORT_COLS]
        hdr_cols = [h for _, h in _EXPORT_COLS]

        hdr_buf = io.StringIO()
        csv.writer(hdr_buf).writerow(hdr_cols)
        yield ("\ufeff" + hdr_buf.getvalue()).encode("utf-8")

        from backend.db import get_contacts_paginated
        page = 1
        while True:
            batch = get_contacts_paginated(
                account or "", page=page, per_page=500,
                search=search or "", filter_mode=filter_mode,
                sort_col="last_scraped_at", sort_order="desc",
                run_from=None, run_to=None,
            )
            if not batch:
                break
            row_buf = io.StringIO()
            w = csv.writer(row_buf)
            for c in batch:
                w.writerow([c.get(col) or "" for col in db_cols])
            yield row_buf.getvalue().encode("utf-8")
            if len(batch) < 500:
                break
            page += 1

    suffix = f"_{account}" if account else ""
    filename = f"linkedin_leads{suffix}.csv"

    return StreamingResponse(
        _csv_gen(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
