"""
Tests de los modelos Pydantic en api/schemas.py:
campos requeridos, tipos, defaults, validators y edge cases.
"""
import pytest
from pydantic import ValidationError

from api.schemas import (
    AccountAddRequest,
    AccountResponse,
    HealthResponse,
    JobResponse,
    JobStatusResponse,
    LeadResponse,
    SearchRequest,
    _MAX_CONTACTS_CAP,
)


# ── SearchRequest ─────────────────────────────────────────────────────────────

class TestSearchRequest:
    def test_modo_index_valido(self):
        sr = SearchRequest(mode="index", account="testuser")
        assert sr.mode == "index"
        assert sr.account == "testuser"
        assert sr.max_contacts == 20

    def test_modo_enrich_valido(self):
        sr = SearchRequest(mode="enrich", account="testuser", max_contacts=10)
        assert sr.mode == "enrich"
        assert sr.max_contacts == 10

    def test_modo_invalido_rechazado(self):
        with pytest.raises(ValidationError):
            SearchRequest(mode="hack", account="testuser")

    def test_max_contacts_capped(self):
        sr = SearchRequest(mode="index", account="u", max_contacts=9999)
        assert sr.max_contacts == _MAX_CONTACTS_CAP

    def test_max_contacts_minimo_1(self):
        sr = SearchRequest(mode="index", account="u", max_contacts=0)
        assert sr.max_contacts == 1

    def test_max_contacts_negativo_minimo_1(self):
        sr = SearchRequest(mode="index", account="u", max_contacts=-5)
        assert sr.max_contacts == 1

    def test_account_requerido(self):
        with pytest.raises(ValidationError):
            SearchRequest(mode="index")

    def test_account_no_puede_ser_vacio(self):
        with pytest.raises(ValidationError):
            SearchRequest(mode="index", account="   ")


# ── JobResponse ───────────────────────────────────────────────────────────────

class TestJobResponse:
    def test_campos_requeridos(self):
        with pytest.raises(ValidationError):
            JobResponse(username="u", started_at="2025-01-01Z", finished_at=None)

    def test_defaults(self):
        jr = JobResponse(id=1, username="u", started_at="2025-01-01Z", finished_at="2025-01-01Z")
        assert jr.contacts_scraped == 0
        assert jr.contacts_new == 0
        assert jr.contacts_updated == 0
        assert jr.status == "done"

    def test_todos_los_campos(self):
        jr = JobResponse(
            id=42, username="miquel1818",
            started_at="2025-01-01T10:00:00Z",
            finished_at="2025-01-01T10:05:00Z",
            contacts_scraped=20, contacts_new=15, contacts_updated=5,
        )
        assert jr.id == 42
        assert jr.contacts_scraped == 20


# ── LeadResponse ──────────────────────────────────────────────────────────────

class TestLeadResponse:
    def test_campos_minimos(self):
        lr = LeadResponse(id=1, username="u")
        assert lr.name is None
        assert lr.emails is None
        assert lr.phones is None

    def test_todos_opcionales_rellenables(self):
        lr = LeadResponse(
            id=1, username="u",
            name="Ana García", position="Dev", company="Acme",
            location="Madrid", emails="ana@acme.com", phones="+34600000000",
            profile_link="https://linkedin.com/in/ana",
            premium=0, open_to_work=1, followers="500", connections="200",
        )
        assert lr.name == "Ana García"
        assert lr.emails == "ana@acme.com"
        assert lr.open_to_work == 1

    def test_id_requerido(self):
        with pytest.raises(ValidationError):
            LeadResponse(username="u")

    def test_username_requerido(self):
        with pytest.raises(ValidationError):
            LeadResponse(id=1)


# ── AccountResponse ───────────────────────────────────────────────────────────

class TestAccountResponse:
    def test_campos_requeridos(self):
        with pytest.raises(ValidationError):
            AccountResponse(username="u")  # falta 'status'

    def test_defaults(self):
        ar = AccountResponse(username="u", status="active")
        assert ar.queue_pending == 0
        assert ar.queue_total == 0
        assert ar.contacts_total == 0
        assert ar.session_exists is False
        assert ar.session_ok is None

    def test_campos_completos(self):
        ar = AccountResponse(
            username="miquel1818", status="active",
            display_name="Miquel", email="m@test.com",
            session_ok=True, session_age_days=5.2,
            session_exists=True,
            queue_pending=10, queue_done=20, queue_error=1, queue_total=31,
            contacts_total=15, daily_count=3,
        )
        assert ar.session_ok is True
        assert ar.queue_total == 31


# ── AccountAddRequest ─────────────────────────────────────────────────────────

class TestAccountAddRequest:
    def test_email_requerido(self):
        with pytest.raises(ValidationError):
            AccountAddRequest(password="x")

    def test_password_requerido(self):
        with pytest.raises(ValidationError):
            AccountAddRequest(email="a@b.com")

    def test_ok_minimo(self):
        req = AccountAddRequest(email="a@b.com", password="secret")
        assert req.email == "a@b.com"
        assert req.username == ""
        assert req.display_name == ""
        assert req.proxy == ""

    def test_proxy_opcional(self):
        req = AccountAddRequest(email="a@b.com", password="x", proxy="host:3128")
        assert req.proxy == "host:3128"


# ── JobStatusResponse ─────────────────────────────────────────────────────────

class TestJobStatusResponse:
    def test_running_requerido(self):
        with pytest.raises(ValidationError):
            JobStatusResponse()

    def test_idle_defaults(self):
        jsr = JobStatusResponse(running=False)
        assert jsr.mode is None
        assert jsr.error is None
        assert jsr.percent is None
        assert jsr.new_count is None

    def test_running_con_progreso(self):
        jsr = JobStatusResponse(
            running=True, mode="enrich", account="u",
            percent=45.5, current=9, total=20,
            new_count=7, updated_count=2, skipped_count=0, error_count=0,
            eta_seconds=120,
        )
        assert jsr.running is True
        assert jsr.percent == 45.5
        assert jsr.eta_seconds == 120


# ── HealthResponse ────────────────────────────────────────────────────────────

class TestHealthResponse:
    def test_campos_requeridos(self):
        with pytest.raises(ValidationError):
            HealthResponse(status="ok")  # faltan db_exists y accounts_count

    def test_defaults(self):
        hr = HealthResponse(status="ok", db_exists=True, accounts_count=2)
        assert hr.max_contacts_cap == _MAX_CONTACTS_CAP
        assert hr.max_contacts_default == 20

    def test_valores_personalizados(self):
        hr = HealthResponse(
            status="ok", db_exists=True, accounts_count=5,
            max_contacts_cap=50, max_contacts_default=10,
        )
        assert hr.accounts_count == 5
        assert hr.max_contacts_cap == 50
