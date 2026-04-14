"""
Tests del módulo db: runs, contact_queue y contacts.
Todos los tests usan una DB temporal en memoria / directorio temporal
para no contaminar la DB real del proyecto.
"""
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

import db as db_module


# ── Fixture: DB temporal aislada ───────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Retorna la ruta a una DB SQLite temporal vacía y parchea DB_PATH."""
    db_path = str(tmp_path / "test_contacts.db")
    with patch.object(db_module, "DB_PATH", db_path):
        yield db_path


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── ensure_tables / ensure_runs_table ─────────────────────────────────────────

def test_ensure_tables_crea_las_tres_tablas(tmp_db):
    db_module.ensure_tables()
    conn = _conn(tmp_db)
    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "runs" in tablas
    assert "contact_queue" in tablas
    assert "contacts" in tablas


def test_ensure_runs_table_alias_compatible(tmp_db):
    """El alias ensure_runs_table() también crea todas las tablas."""
    db_module.ensure_runs_table()
    conn = _conn(tmp_db)
    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "runs" in tablas
    assert "contacts" in tablas


def test_ensure_tables_idempotente(tmp_db):
    """Llamar varias veces no lanza error ni duplica tablas."""
    db_module.ensure_tables()
    db_module.ensure_tables()
    conn = _conn(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()[0]
    conn.close()
    assert n == 1


# ── insert_run ────────────────────────────────────────────────────────────────

def test_insert_run_guarda_fila(tmp_db):
    db_module.insert_run(
        username="miquel",
        started_at="2026-03-12T10:00:00Z",
        finished_at="2026-03-12T10:05:00Z",
        contacts_scraped=20,
        contacts_new=15,
        contacts_updated=5,
    )
    conn = _conn(tmp_db)
    row = dict(conn.execute("SELECT * FROM runs").fetchone())
    conn.close()
    assert row["username"] == "miquel"
    assert row["contacts_scraped"] == 20
    assert row["contacts_new"] == 15
    assert row["contacts_updated"] == 5


def test_insert_run_multiples_filas(tmp_db):
    db_module.insert_run("u1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
    db_module.insert_run("u2", "2026-01-02T00:00:00Z", "2026-01-02T00:01:00Z")
    conn = _conn(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    conn.close()
    assert n == 2


# ── queue_slugs ───────────────────────────────────────────────────────────────

def test_queue_slugs_inserta_nuevos(tmp_db):
    n = db_module.queue_slugs("miquel", ["alice", "bob", "carlos"])
    assert n == 3
    conn = _conn(tmp_db)
    rows = conn.execute("SELECT slug, status FROM contact_queue WHERE username='miquel'").fetchall()
    conn.close()
    slugs = {r["slug"] for r in rows}
    assert slugs == {"alice", "bob", "carlos"}
    assert all(r["status"] == "pending" for r in rows)


def test_queue_slugs_ignora_duplicados(tmp_db):
    db_module.queue_slugs("miquel", ["alice", "bob"])
    n2 = db_module.queue_slugs("miquel", ["bob", "carlos"])  # bob ya existe
    assert n2 == 1  # solo carlos es nuevo
    conn = _conn(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM contact_queue WHERE username='miquel'").fetchone()[0]
    conn.close()
    assert n == 3  # alice, bob, carlos


def test_queue_slugs_diferencia_por_username(tmp_db):
    """El mismo slug puede estar en la cola para distintos usernames."""
    db_module.queue_slugs("user1", ["alice"])
    db_module.queue_slugs("user2", ["alice"])
    conn = _conn(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM contact_queue WHERE slug='alice'").fetchone()[0]
    conn.close()
    assert n == 2


# ── get_pending_slugs ─────────────────────────────────────────────────────────

def test_get_pending_slugs_devuelve_solo_pending(tmp_db):
    db_module.queue_slugs("u", ["a", "b", "c"])
    db_module.mark_queue_done("u", "b")
    result = db_module.get_pending_slugs("u", limit=10)
    assert set(result) == {"a", "c"}
    assert "b" not in result


def test_get_pending_slugs_respeta_limit(tmp_db):
    db_module.queue_slugs("u", [f"slug-{i}" for i in range(20)])
    result = db_module.get_pending_slugs("u", limit=5)
    assert len(result) == 5


def test_get_pending_slugs_orden_fifo(tmp_db):
    """Los slugs más antiguos (queued_at menor) salen primero."""
    import time
    db_module.queue_slugs("u", ["primero"])
    time.sleep(0.01)
    db_module.queue_slugs("u", ["segundo"])
    result = db_module.get_pending_slugs("u", limit=2)
    assert result[0] == "primero"
    assert result[1] == "segundo"


def test_get_pending_slugs_vacia(tmp_db):
    assert db_module.get_pending_slugs("nadie", limit=10) == []


# ── mark_queue_done / mark_queue_error ────────────────────────────────────────

def test_mark_queue_done_cambia_status(tmp_db):
    db_module.queue_slugs("u", ["slug1"])
    db_module.mark_queue_done("u", "slug1")
    conn = _conn(tmp_db)
    row = conn.execute("SELECT status, processed_at FROM contact_queue WHERE slug='slug1'").fetchone()
    conn.close()
    assert row["status"] == "done"
    assert row["processed_at"] is not None


def test_mark_queue_error_cambia_status(tmp_db):
    db_module.queue_slugs("u", ["slug2"])
    db_module.mark_queue_error("u", "slug2", "Timeout al cargar perfil")
    conn = _conn(tmp_db)
    row = conn.execute("SELECT status, error_msg FROM contact_queue WHERE slug='slug2'").fetchone()
    conn.close()
    assert row["status"] == "error"
    assert "Timeout" in row["error_msg"]


def test_mark_queue_error_trunca_mensaje_largo(tmp_db):
    db_module.queue_slugs("u", ["slug3"])
    db_module.mark_queue_error("u", "slug3", "X" * 1000)
    conn = _conn(tmp_db)
    row = conn.execute("SELECT error_msg FROM contact_queue WHERE slug='slug3'").fetchone()
    conn.close()
    assert len(row["error_msg"]) <= 500


# ── requeue_pending ───────────────────────────────────────────────────────────

def test_requeue_pending_reactiva_done(tmp_db):
    db_module.queue_slugs("u", ["s1"])
    db_module.mark_queue_done("u", "s1")
    n = db_module.requeue_pending("u", ["s1"])
    assert n == 1
    conn = _conn(tmp_db)
    row = conn.execute("SELECT status FROM contact_queue WHERE slug='s1'").fetchone()
    conn.close()
    assert row["status"] == "pending"


# ── get_queue_stats ───────────────────────────────────────────────────────────

def test_get_queue_stats_cola_vacia(tmp_db):
    stats = db_module.get_queue_stats("nadie")
    assert stats["pending"] == 0
    assert stats["done"] == 0
    assert stats["error"] == 0
    assert stats["total"] == 0


def test_get_queue_stats_con_datos(tmp_db):
    db_module.queue_slugs("u", ["a", "b", "c", "d", "e"])
    db_module.mark_queue_done("u", "a")
    db_module.mark_queue_done("u", "b")
    db_module.mark_queue_error("u", "c", "fallo")
    stats = db_module.get_queue_stats("u")
    assert stats["pending"] == 2
    assert stats["done"] == 2
    assert stats["error"] == 1
    assert stats["total"] == 5


# ── upsert_contact ────────────────────────────────────────────────────────────

CONTACT_EJEMPLO = {
    "profile_id": "alice-dev",
    "name": "Alice Dev",
    "first_name": "Alice",
    "last_name": "Dev",
    "position": "Engineer",
    "company": "Acme",
    "location": "Madrid",
    "emails": "alice@acme.com",
    "phones": "+34 600 000 001",
    "profile_link": "https://www.linkedin.com/in/alice-dev/",
    "profile_photo": "https://media.licdn.com/photo.jpg",
    "premium": True,
    "creator": False,
    "open_to_work": None,
    "followers": "1.2K",
    "connections": "500+",
}


def test_upsert_contact_inserta_nuevo(tmp_db):
    result = db_module.upsert_contact("miquel", CONTACT_EJEMPLO)
    assert result == "inserted"
    conn = _conn(tmp_db)
    row = dict(conn.execute("SELECT * FROM contacts WHERE profile_id='alice-dev'").fetchone())
    conn.close()
    assert row["name"] == "Alice Dev"
    assert row["emails"] == "alice@acme.com"
    assert row["premium"] == 1
    assert row["creator"] == 0
    assert row["open_to_work"] is None
    assert row["first_scraped_at"] is not None
    assert row["last_scraped_at"] is not None


def test_upsert_contact_actualiza_existente(tmp_db):
    db_module.upsert_contact("miquel", CONTACT_EJEMPLO)
    actualizado = {**CONTACT_EJEMPLO, "position": "Senior Engineer", "company": "Megacorp"}
    result = db_module.upsert_contact("miquel", actualizado)
    assert result == "updated"
    conn = _conn(tmp_db)
    row = dict(conn.execute("SELECT * FROM contacts WHERE profile_id='alice-dev'").fetchone())
    conn.close()
    assert row["position"] == "Senior Engineer"
    assert row["company"] == "Megacorp"


def test_upsert_contact_first_scraped_at_no_cambia(tmp_db):
    """first_scraped_at no debe cambiar al actualizar."""
    db_module.upsert_contact("miquel", CONTACT_EJEMPLO)
    conn = _conn(tmp_db)
    primera = conn.execute("SELECT first_scraped_at FROM contacts WHERE profile_id='alice-dev'").fetchone()[0]
    conn.close()

    import time; time.sleep(0.01)
    db_module.upsert_contact("miquel", {**CONTACT_EJEMPLO, "name": "Alice Nuevo"})
    conn = _conn(tmp_db)
    segunda = conn.execute("SELECT first_scraped_at FROM contacts WHERE profile_id='alice-dev'").fetchone()[0]
    conn.close()
    assert primera == segunda


def test_upsert_contact_diferencia_por_username(tmp_db):
    """Mismo profile_id para dos usernames distintos → dos filas."""
    db_module.upsert_contact("user1", CONTACT_EJEMPLO)
    db_module.upsert_contact("user2", CONTACT_EJEMPLO)
    conn = _conn(tmp_db)
    n = conn.execute("SELECT COUNT(*) FROM contacts WHERE profile_id='alice-dev'").fetchone()[0]
    conn.close()
    assert n == 2


# ── contact_exists ────────────────────────────────────────────────────────────

def test_contact_exists_true(tmp_db):
    db_module.upsert_contact("u", CONTACT_EJEMPLO)
    assert db_module.contact_exists("u", "alice-dev") is True


def test_contact_exists_false(tmp_db):
    assert db_module.contact_exists("u", "no-existe") is False


# ── get_daily_count ───────────────────────────────────────────────────────────

def test_get_daily_count_cero_sin_datos(tmp_db):
    assert db_module.get_daily_count("nadie") == 0


def test_get_daily_count_cuenta_solo_done_de_hoy(tmp_db):
    db_module.queue_slugs("u", ["a", "b", "c", "d"])
    db_module.mark_queue_done("u", "a")
    db_module.mark_queue_done("u", "b")
    db_module.mark_queue_error("u", "c")
    # "d" sigue pending
    count = db_module.get_daily_count("u")
    assert count == 2  # solo a y b están done hoy


def test_get_daily_count_no_cuenta_otro_username(tmp_db):
    db_module.queue_slugs("user1", ["x"])
    db_module.mark_queue_done("user1", "x")
    assert db_module.get_daily_count("user2") == 0


# ── days_since_last_scrape ────────────────────────────────────────────────────

def test_days_since_last_scrape_none_si_no_existe(tmp_db):
    assert db_module.days_since_last_scrape("u", "no-existe") is None


def test_days_since_last_scrape_cero_recien_scrapeado(tmp_db):
    db_module.upsert_contact("u", CONTACT_EJEMPLO)
    days = db_module.days_since_last_scrape("u", "alice-dev")
    assert days is not None
    assert 0 <= days < 0.01   # recién creado: menos de 1 segundo


def test_days_since_last_scrape_diferencia_por_username(tmp_db):
    db_module.upsert_contact("user1", CONTACT_EJEMPLO)
    # Para user2 no existe, debe devolver None
    assert db_module.days_since_last_scrape("user2", "alice-dev") is None
    # Para user1 debe devolver algo cercano a 0
    assert db_module.days_since_last_scrape("user1", "alice-dev") is not None


# ── filtros "todas las cuentas" ───────────────────────────────────────────────

def test_count_contacts_filtered_todas_las_cuentas(tmp_db):
    db_module.upsert_contact("user1", {**CONTACT_EJEMPLO, "profile_id": "p1", "name": "Uno"})
    db_module.upsert_contact("user2", {**CONTACT_EJEMPLO, "profile_id": "p2", "name": "Dos"})

    total_all = db_module.count_contacts_filtered("")
    total_user1 = db_module.count_contacts_filtered("user1")

    assert total_all == 2
    assert total_user1 == 1


def test_get_contacts_paginated_todas_las_cuentas(tmp_db):
    db_module.upsert_contact("user1", {**CONTACT_EJEMPLO, "profile_id": "p1", "name": "Uno"})
    db_module.upsert_contact("user2", {**CONTACT_EJEMPLO, "profile_id": "p2", "name": "Dos"})

    rows_all = db_module.get_contacts_paginated("", page=1, per_page=50)
    rows_user1 = db_module.get_contacts_paginated("user1", page=1, per_page=50)

    assert len(rows_all) == 2
    assert len(rows_user1) == 1
    usernames_all = {r["username"] for r in rows_all}
    assert usernames_all == {"user1", "user2"}


# ── trigger_limits (cadencia persistente) ─────────────────────────────────────

def test_trigger_limits_guardar_y_leer(tmp_db):
    key = "miquel1818:index"
    assert db_module.get_last_trigger_epoch(key) == 0.0

    db_module.set_last_trigger_epoch(key, 12345.6)
    assert db_module.get_last_trigger_epoch(key) == 12345.6

    db_module.set_last_trigger_epoch(key, 777.1)
    assert db_module.get_last_trigger_epoch(key) == 777.1


# ── accounts ──────────────────────────────────────────────────────────────────

def test_register_account_inserta(tmp_db):
    result = db_module.register_account("alice", "sessions/alice.pkl", "Alice Dev")
    assert result == "inserted"
    accounts = db_module.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["username"] == "alice"
    assert accounts[0]["status"] == "active"


def test_register_account_update_si_ya_existe(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    result = db_module.register_account("alice", "sessions/alice_nueva.pkl", "Alice Nueva")
    assert result == "updated"
    accounts = db_module.list_accounts()
    assert len(accounts) == 1
    assert accounts[0]["session_file"] == "sessions/alice_nueva.pkl"


def test_list_accounts_solo_activas(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    db_module.register_account("bob", "sessions/bob.pkl")
    db_module.deactivate_account("bob")
    accounts = db_module.list_accounts(include_inactive=False)
    assert len(accounts) == 1
    assert accounts[0]["username"] == "alice"


def test_list_accounts_include_inactive(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    db_module.register_account("bob", "sessions/bob.pkl")
    db_module.deactivate_account("bob")
    accounts = db_module.list_accounts(include_inactive=True)
    assert len(accounts) == 2


def test_deactivate_account_existente(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    found = db_module.deactivate_account("alice")
    assert found is True
    accounts = db_module.list_accounts(include_inactive=False)
    assert len(accounts) == 0


def test_deactivate_account_no_existente(tmp_db):
    found = db_module.deactivate_account("nadie")
    assert found is False


def test_register_account_guarda_proxy(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl", proxy="user:pass@proxy.host:8080")
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(tmp_db)
    conn.row_factory = _sqlite3.Row
    row = conn.execute("SELECT proxy FROM accounts WHERE username='alice'").fetchone()
    conn.close()
    assert row["proxy"] == "user:pass@proxy.host:8080"


def test_get_account_proxy_devuelve_proxy(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl", proxy="host:3128")
    assert db_module.get_account_proxy("alice") == "host:3128"


def test_get_account_proxy_none_si_no_configurado(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    assert db_module.get_account_proxy("alice") is None


def test_get_account_proxy_none_si_no_existe(tmp_db):
    assert db_module.get_account_proxy("nadie") is None


def test_update_account_last_run(tmp_db):
    db_module.register_account("alice", "sessions/alice.pkl")
    db_module.update_account_last_run("alice")
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(tmp_db)
    conn.row_factory = _sqlite3.Row
    row = conn.execute("SELECT last_run_at FROM accounts WHERE username='alice'").fetchone()
    conn.close()
    assert row["last_run_at"] is not None
