"""
Tests de integración para los endpoints FastAPI de LinkedIn.
Se usa TestClient de Starlette; todos los accesos a DB y al job-state se mockean.
"""
import time
import pickle
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── App fixture ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient con lifespan deshabilitado para evitar efectos secundarios."""
    from backend.main import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_job_state():
    """Resetea el estado global del job en routes.py a idle."""
    import backend.api.routes as r
    r._job_running = False
    r._job_mode = None
    r._job_account = None
    r._job_error = None
    r._job_started_at = None
    r._job_finished_at = None
    r._job_progress = {}


@pytest.fixture(autouse=True)
def reset_job(tmp_path):
    """Resetea job state y parchea DB_PATH antes de cada test."""
    _reset_job_state()
    db_path = str(tmp_path / "test.db")
    with patch("backend.api.routes.DB_PATH", db_path), \
         patch("backend.db.DB_PATH", db_path):
        import backend.db as db_mod
        db_mod._tables_initialized_for = None
        yield
    _reset_job_state()


# ── GET /api/linkedin/health ──────────────────────────────────────────────────

class TestHealth:
    def test_health_ok_sin_db(self, client, tmp_path):
        with patch("backend.api.routes._db_exists", return_value=False):
            r = client.get("/api/linkedin/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["db_exists"] is False
        assert "accounts_count" in data
        assert "max_contacts_cap" in data

    def test_health_ok_con_db(self, client):
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.api.routes._db_conn") as mock_conn:
            mock_conn.return_value.execute.return_value.fetchone.return_value = {"n": 3}
            mock_conn.return_value.close.return_value = None
            r = client.get("/api/linkedin/health")
        assert r.status_code == 200
        assert r.json()["accounts_count"] == 3


# ── GET /api/linkedin/stats ───────────────────────────────────────────────────

class TestStats:
    def test_stats_sin_db(self, client):
        with patch("backend.api.routes._db_exists", return_value=False):
            r = client.get("/api/linkedin/stats")
        assert r.status_code == 200
        assert r.json() == {"total_contacts": 0, "with_email": 0, "with_phone": 0}

    def test_stats_con_db(self, client):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.side_effect = [
            {"n": 10}, {"n": 4}, {"n": 2}
        ]
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.api.routes._db_conn", return_value=mock_conn):
            r = client.get("/api/linkedin/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_contacts"] == 10
        assert data["with_email"] == 4
        assert data["with_phone"] == 2


# ── GET /api/linkedin/accounts ────────────────────────────────────────────────

class TestListAccounts:
    def test_lista_vacia(self, client):
        with patch("backend.db.get_all_accounts_with_stats", return_value=[]):
            r = client.get("/api/linkedin/accounts")
        assert r.status_code == 200
        assert r.json() == []

    def test_lista_con_cuenta(self, client):
        fake_account = {
            "username": "testuser", "display_name": "Test", "email": "t@e.com",
            "status": "active", "proxy": None, "added_at": "2025-01-01T00:00:00Z",
            "last_run_at": None, "session_file": "/tmp/s.pkl",
            "queue_pending": 5, "queue_done": 10, "queue_error": 1, "queue_total": 16,
            "contacts_total": 8,
        }
        with patch("backend.db.get_all_accounts_with_stats", return_value=[fake_account]), \
             patch("backend.api.routes._session_status", return_value={"session_ok": True, "session_age_days": 2.0, "session_exists": True}), \
             patch("backend.api.routes._cooldown_remaining", return_value={"index_cooldown_remaining": 0, "enrich_cooldown_remaining": 0}):
            r = client.get("/api/linkedin/accounts")
        assert r.status_code == 200
        accounts = r.json()
        assert len(accounts) == 1
        assert accounts[0]["username"] == "testuser"
        assert accounts[0]["queue_pending"] == 5
        assert "enrich_cooldown_remaining" in accounts[0]


# ── POST /api/linkedin/accounts ──────────────────────────────────────────────

class TestAddAccount:
    def test_campos_requeridos(self, client):
        r = client.post("/api/linkedin/accounts", json={"email": "", "password": "x"})
        assert r.status_code == 400

    def test_password_requerido(self, client):
        r = client.post("/api/linkedin/accounts", json={"email": "a@b.com", "password": ""})
        assert r.status_code == 400

    def test_proxy_invalido(self, client):
        r = client.post("/api/linkedin/accounts", json={
            "email": "a@b.com", "password": "secret", "proxy": "badproxy"
        })
        assert r.status_code == 400

    def test_ok_inicia_login(self, client):
        with patch("backend.api.routes._do_add_account"):
            r = client.post("/api/linkedin/accounts", json={
                "email": "user@example.com", "password": "pass123"
            })
        assert r.status_code == 200
        assert r.json()["status"] == "login_started"
        assert "account" in r.json()

    def test_login_status_endpoint(self, client):
        with patch("backend.api.routes._do_add_account"):
            r = client.post("/api/linkedin/accounts", json={
                "email": "trace@example.com", "password": "pass123"
            })
        account = r.json()["account"]
        status = client.get(f"/api/linkedin/accounts/login-status?account={account}")
        assert status.status_code == 200
        assert status.json()["status"] in ("started", "running", "success", "failed")

    def test_do_add_account_guarda_credencial_cifrada(self, tmp_path):
        import backend.api.routes as routes
        session_file = tmp_path / "temp-user.pkl"
        with open(session_file, "wb") as f:
            pickle.dump({"username": "real-user"}, f)

        with patch("backend.api.routes.SESSIONS_DIR", tmp_path), \
             patch("backend.scraper.login_with_credentials", return_value={"status": "ok"}), \
             patch("backend.db.ensure_tables"), \
             patch("backend.db.register_account"), \
             patch("backend.db.save_account_credentials", return_value=True) as mock_save:
            routes._do_add_account(
                username="temp-user",
                email="user@example.com",
                password="supersecret",
                display_name="User",
                proxy=None,
            )
        mock_save.assert_called_once_with("temp-user", "supersecret")


# ── DELETE /api/linkedin/accounts/{username} ─────────────────────────────────

class TestDeleteAccount:
    def test_delete_existente(self, client):
        with patch("backend.db.deactivate_account", return_value=True):
            r = client.delete("/api/linkedin/accounts/testuser")
        assert r.status_code == 200
        assert r.json()["status"] == "deactivated"

    def test_delete_no_existente(self, client):
        with patch("backend.db.deactivate_account", return_value=False):
            r = client.delete("/api/linkedin/accounts/noexiste")
        assert r.status_code == 200  # devuelve 200 igualmente


# ── POST /api/linkedin/search ────────────────────────────────────────────────

class TestSearch:
    def test_lanza_job_index(self, client):
        with patch("backend.db.get_last_trigger_epoch", return_value=0.0), \
             patch("backend.db.set_last_trigger_epoch"), \
             patch("backend.api.routes._run_job"):
            r = client.post("/api/linkedin/search", json={
                "mode": "index", "account": "miquel1818", "max_contacts": 10
            })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "started"
        assert data["mode"] == "index"

    def test_lanza_job_enrich(self, client):
        with patch("backend.db.get_last_trigger_epoch", return_value=0.0), \
             patch("backend.db.set_last_trigger_epoch"), \
             patch("backend.api.routes._run_job"):
            r = client.post("/api/linkedin/search", json={
                "mode": "enrich", "account": "miquel1818", "max_contacts": 5
            })
        assert r.status_code == 200
        assert r.json()["mode"] == "enrich"

    def test_409_si_job_en_curso(self, client):
        import backend.api.routes as routes
        routes._job_running = True
        r = client.post("/api/linkedin/search", json={
            "mode": "index", "account": "miquel1818", "max_contacts": 10
        })
        assert r.status_code == 409

    def test_429_cooldown_activo(self, client):
        last = time.time() - 30  # 30s ago, cooldown de 1200s activo
        with patch("backend.db.get_last_trigger_epoch", return_value=last):
            r = client.post("/api/linkedin/search", json={
                "mode": "enrich", "account": "miquel1818", "max_contacts": 10
            })
        assert r.status_code == 429
        assert "Espera" in r.json()["detail"]

    def test_modo_invalido_rechazado(self, client):
        r = client.post("/api/linkedin/search", json={
            "mode": "hack", "account": "miquel1818", "max_contacts": 10
        })
        assert r.status_code == 422

    def test_account_vacio_rechazado(self, client):
        r = client.post("/api/linkedin/search", json={
            "mode": "index", "account": "   ", "max_contacts": 10
        })
        assert r.status_code == 422


# ── GET /api/linkedin/status ─────────────────────────────────────────────────

class TestStatus:
    def test_status_idle(self, client):
        r = client.get("/api/linkedin/status")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["mode"] is None

    def test_status_running(self, client):
        import backend.api.routes as routes
        routes._job_running = True
        routes._job_mode = "enrich"
        routes._job_account = "miquel1818"
        routes._job_started_at = "2025-01-01T10:00:00Z"
        routes._job_progress = {"percent": 50.0, "current": 5, "total": 10}
        r = client.get("/api/linkedin/status")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is True
        assert data["mode"] == "enrich"
        assert data["percent"] < 100.0  # no puede mostrar 100% mientras corre

    def test_status_completado(self, client):
        import backend.api.routes as routes
        routes._job_running = False
        routes._job_mode = "index"
        routes._job_account = "miquel1818"
        routes._job_finished_at = "2025-01-01T10:05:00Z"
        routes._job_progress = {"phase": "done", "label": "Completado", "percent": 100.0}
        r = client.get("/api/linkedin/status")
        assert r.status_code == 200
        assert r.json()["running"] is False


# ── GET /api/linkedin/jobs ────────────────────────────────────────────────────

class TestJobs:
    def test_jobs_sin_db(self, client):
        with patch("backend.api.routes._db_exists", return_value=False):
            r = client.get("/api/linkedin/jobs")
        assert r.status_code == 200
        assert r.json() == []

    def test_jobs_con_filtro(self, client):
        import sqlite3
        from unittest.mock import MagicMock
        fake_row = {
            "id": 1, "username": "miquel1818",
            "started_at": "2025-01-01T10:00:00Z",
            "finished_at": "2025-01-01T10:05:00Z",
            "contacts_scraped": 5, "contacts_new": 3, "contacts_updated": 2,
        }
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [fake_row]
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.api.routes._db_conn", return_value=mock_conn):
            r = client.get("/api/linkedin/jobs?limit=10&account=miquel1818&days=7")
        assert r.status_code == 200


# ── GET /api/linkedin/leads ───────────────────────────────────────────────────

class TestLeads:
    def test_leads_sin_db(self, client):
        with patch("backend.api.routes._db_exists", return_value=False):
            r = client.get("/api/linkedin/leads")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["contacts"] == []

    def test_leads_paginacion(self, client):
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.db.count_contacts_filtered", return_value=3), \
             patch("backend.db.get_contacts_paginated", return_value=[
                 {"id": 1, "username": "u", "name": "Ana", "emails": "ana@test.com",
                  "phones": None, "profile_link": None, "position": None,
                  "company": None, "location": None, "last_scraped_at": "2025-01-01Z",
                  "first_scraped_at": "2025-01-01Z"},
             ]):
            r = client.get("/api/linkedin/leads?page=1&per_page=50")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["contacts"]) == 1
        assert data["contacts"][0]["name"] == "Ana"

    def test_leads_filtro_email(self, client):
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.db.count_contacts_filtered", return_value=0), \
             patch("backend.db.get_contacts_paginated", return_value=[]):
            r = client.get("/api/linkedin/leads?filter=email")
        assert r.status_code == 200

    def test_leads_busqueda(self, client):
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.db.count_contacts_filtered", return_value=0), \
             patch("backend.db.get_contacts_paginated", return_value=[]):
            r = client.get("/api/linkedin/leads?search=google&account=miquel1818")
        assert r.status_code == 200


# ── GET /api/linkedin/leads/export ───────────────────────────────────────────

class TestExport:
    def test_export_sin_db(self, client):
        with patch("backend.api.routes._db_exists", return_value=False):
            r = client.get("/api/linkedin/leads/export")
        assert r.status_code == 404

    def test_export_csv_streaming(self, client):
        fake_contacts = [
            {"name": "Ana García", "first_name": "Ana", "last_name": "García",
             "position": "Dev", "company": "Acme", "location": "Madrid",
             "emails": "ana@acme.com", "phones": None, "profile_link": "https://linkedin.com/in/ana",
             "premium": 0, "open_to_work": 0, "followers": "500", "connections": "200",
             "first_scraped_at": "2025-01-01Z", "last_scraped_at": "2025-01-15Z"},
        ]
        with patch("backend.api.routes._db_exists", return_value=True), \
             patch("backend.db.get_contacts_paginated", side_effect=[fake_contacts, []]):
            r = client.get("/api/linkedin/leads/export")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")
        content = r.content.decode("utf-8-sig")
        assert "Ana García" in content
        assert "Nombre completo" in content
