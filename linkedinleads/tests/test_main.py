"""
Tests del flujo de main: cooldown, intervalo mínimo, franja horaria,
presupuesto diario, extract_username, get_username_non_interactive,
run_scrape, run_index, run_enrich.
Sin llamadas a LinkedIn; se usan archivos temporales y mocks.
"""
import os
import sys
import time
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import linkedin_main as main_module


# ── extract_username ──────────────────────────────────────────────────────────

def test_extract_username_ok():
    assert main_module.extract_username("https://www.linkedin.com/in/juan-perez") == "juan-perez"
    assert main_module.extract_username("https://linkedin.com/in/maria-lopez-123") == "maria-lopez-123"


def test_extract_username_con_trailing_slash():
    assert main_module.extract_username("https://www.linkedin.com/in/juan-perez/") == "juan-perez"


def test_extract_username_con_query():
    assert main_module.extract_username("https://www.linkedin.com/in/juan-perez?trk=foo") == "juan-perez"


def test_extract_username_invalida():
    with pytest.raises(ValueError) as exc_info:
        main_module.extract_username("https://google.com/foo")
    assert "URL inválida" in str(exc_info.value)


# ── _check_cooldown ───────────────────────────────────────────────────────────

def test_check_cooldown_sin_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown.txt")
        assert main_module._check_cooldown() is False


def test_check_cooldown_archivo_futuro_activa_cooldown():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "cooldown.txt")
        with open(f, "w") as fp:
            fp.write(str(time.time() + 3600))
        main_module.COOLDOWN_FILE = f
        assert main_module._check_cooldown() is True


def test_check_cooldown_archivo_pasado_permite_y_borra():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "cooldown.txt")
        with open(f, "w") as fp:
            fp.write(str(time.time() - 10))
        main_module.COOLDOWN_FILE = f
        assert main_module._check_cooldown() is False
        assert not os.path.isfile(f)


def test_check_cooldown_archivo_corrupto_permite():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "cooldown.txt")
        with open(f, "w") as fp:
            fp.write("not a number")
        main_module.COOLDOWN_FILE = f
        assert main_module._check_cooldown() is False


def test_write_cooldown_crea_archivo_con_timestamp_futuro():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "cooldown.txt")
        main_module.COOLDOWN_FILE = f
        main_module.COOLDOWN_COUNT_FILE = os.path.join(tmp, "count.txt")
        main_module._write_cooldown()
        assert os.path.isfile(f)
        with open(f) as fp:
            assert float(fp.read().strip()) > time.time()


def test_write_cooldown_primer_bloqueo_es_4h():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown.txt")
        main_module.COOLDOWN_COUNT_FILE = os.path.join(tmp, "count.txt")
        hours = main_module._write_cooldown()
        assert hours == 4
        with open(main_module.COOLDOWN_FILE) as fp:
            until = float(fp.read().strip())
        assert abs(until - (time.time() + 4 * 3600)) < 5


def test_write_cooldown_segundo_bloqueo_es_8h():
    with tempfile.TemporaryDirectory() as tmp:
        count_f = os.path.join(tmp, "count.txt")
        with open(count_f, "w") as fp:
            fp.write("2")  # segundo bloqueo
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown.txt")
        main_module.COOLDOWN_COUNT_FILE = count_f
        hours = main_module._write_cooldown()
        assert hours == 8


def test_write_cooldown_techo_48h():
    with tempfile.TemporaryDirectory() as tmp:
        count_f = os.path.join(tmp, "count.txt")
        with open(count_f, "w") as fp:
            fp.write("10")  # muchos bloqueos → techo 48h
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown.txt")
        main_module.COOLDOWN_COUNT_FILE = count_f
        hours = main_module._write_cooldown()
        assert hours == 48


def test_write_cooldown_retorna_horas_efectivas():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown.txt")
        main_module.COOLDOWN_COUNT_FILE = os.path.join(tmp, "count.txt")
        result = main_module._write_cooldown()
        assert isinstance(result, int)


def test_reset_cooldown_counter_borra_archivo():
    with tempfile.TemporaryDirectory() as tmp:
        count_f = os.path.join(tmp, "count.txt")
        with open(count_f, "w") as fp:
            fp.write("3")
        main_module.COOLDOWN_COUNT_FILE = count_f
        main_module._reset_cooldown_counter()
        assert not os.path.isfile(count_f)


def test_reset_cooldown_counter_sin_archivo_no_explota():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_COUNT_FILE = os.path.join(tmp, "count.txt")
        main_module._reset_cooldown_counter()  # no debe lanzar excepción


# ── _check_min_interval ───────────────────────────────────────────────────────

def test_check_min_interval_desactivado():
    orig = main_module.MIN_HOURS_BETWEEN_RUNS
    main_module.MIN_HOURS_BETWEEN_RUNS = 0
    try:
        assert main_module._check_min_interval() is False
    finally:
        main_module.MIN_HOURS_BETWEEN_RUNS = orig


def test_check_min_interval_sin_archivo_permite_y_escribe():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "last_run.txt")
        main_module.LAST_RUN_FILE = f
        main_module.MIN_HOURS_BETWEEN_RUNS = 24
        assert main_module._check_min_interval() is False
        assert os.path.isfile(f)


def test_check_min_interval_archivo_reciente_bloquea():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "last_run.txt")
        main_module.LAST_RUN_FILE = f
        main_module.MIN_HOURS_BETWEEN_RUNS = 24
        with open(f, "w") as fp:
            fp.write(str(time.time()))
        assert main_module._check_min_interval() is True


def test_check_min_interval_archivo_antiguo_permite_y_actualiza():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "last_run.txt")
        main_module.LAST_RUN_FILE = f
        main_module.MIN_HOURS_BETWEEN_RUNS = 1
        with open(f, "w") as fp:
            fp.write(str(time.time() - 7200))
        assert main_module._check_min_interval() is False
        with open(f) as fp:
            assert float(fp.read().strip()) >= time.time() - 2


def test_check_min_interval_archivo_corrupto_permite():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "last_run.txt")
        main_module.LAST_RUN_FILE = f
        main_module.MIN_HOURS_BETWEEN_RUNS = 24
        with open(f, "w") as fp:
            fp.write("invalid")
        assert main_module._check_min_interval() is False


# ── _check_time_window ────────────────────────────────────────────────────────

def test_check_time_window_dentro_franja():
    orig_start = main_module.SCRAPE_WINDOW_START
    orig_end = main_module.SCRAPE_WINDOW_END
    main_module.SCRAPE_WINDOW_START = 0
    main_module.SCRAPE_WINDOW_END = 23
    try:
        assert main_module._check_time_window() is False  # sin restricción
    finally:
        main_module.SCRAPE_WINDOW_START = orig_start
        main_module.SCRAPE_WINDOW_END = orig_end


def test_check_time_window_fuera_de_franja():
    """Simula que son las 3 de la madrugada con franja 8-21."""
    orig_start = main_module.SCRAPE_WINDOW_START
    orig_end = main_module.SCRAPE_WINDOW_END
    main_module.SCRAPE_WINDOW_START = 8
    main_module.SCRAPE_WINDOW_END = 21
    try:
        from datetime import datetime
        fake_now = datetime(2026, 3, 12, 3, 0, 0)  # 03:00
        with patch("linkedin_main.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert main_module._check_time_window() is True
    finally:
        main_module.SCRAPE_WINDOW_START = orig_start
        main_module.SCRAPE_WINDOW_END = orig_end


def test_check_time_window_dentro_de_franja_diurna():
    """Simula que son las 14:00 con franja 8-21."""
    orig_start = main_module.SCRAPE_WINDOW_START
    orig_end = main_module.SCRAPE_WINDOW_END
    main_module.SCRAPE_WINDOW_START = 8
    main_module.SCRAPE_WINDOW_END = 21
    try:
        from datetime import datetime
        fake_now = datetime(2026, 3, 12, 14, 0, 0)  # 14:00
        with patch("linkedin_main.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            assert main_module._check_time_window() is False
    finally:
        main_module.SCRAPE_WINDOW_START = orig_start
        main_module.SCRAPE_WINDOW_END = orig_end


# ── _check_daily_budget ───────────────────────────────────────────────────────

def test_check_daily_budget_bajo_limite():
    orig = main_module.MAX_CONTACTS_PER_DAY
    main_module.MAX_CONTACTS_PER_DAY = 80
    try:
        with patch("linkedin_main.get_daily_count", return_value=30):
            assert main_module._check_daily_budget("u") is False
    finally:
        main_module.MAX_CONTACTS_PER_DAY = orig


def test_check_daily_budget_limite_alcanzado():
    orig = main_module.MAX_CONTACTS_PER_DAY
    main_module.MAX_CONTACTS_PER_DAY = 80
    try:
        with patch("linkedin_main.get_daily_count", return_value=80):
            assert main_module._check_daily_budget("u") is True
    finally:
        main_module.MAX_CONTACTS_PER_DAY = orig


def test_check_daily_budget_superado():
    orig = main_module.MAX_CONTACTS_PER_DAY
    main_module.MAX_CONTACTS_PER_DAY = 80
    try:
        with patch("linkedin_main.get_daily_count", return_value=95):
            assert main_module._check_daily_budget("u") is True
    finally:
        main_module.MAX_CONTACTS_PER_DAY = orig


# ── get_username_non_interactive ──────────────────────────────────────────────

def test_get_username_non_interactive_desde_api():
    """Detección automática desde la sesión activa — caso principal."""
    fake = MagicMock()
    with patch("linkedin_main.get_current_username", return_value="juan-perez"):
        assert main_module.get_username_non_interactive(fake) == "juan-perez"


def test_get_username_non_interactive_desde_env():
    """Fallback a LINKEDIN_PROFILE_URL cuando la sesión no devuelve username."""
    fake = MagicMock()
    with patch("linkedin_main.get_current_username", return_value=None):
        with patch.dict(os.environ, {"LINKEDIN_PROFILE_URL": "https://linkedin.com/in/maria-lopez"}):
            assert main_module.get_username_non_interactive(fake) == "maria-lopez"


def test_get_username_non_interactive_fallback_account_slug():
    """
    Fallback a account_slug cuando la auto-detección y LINKEDIN_PROFILE_URL fallan.
    Este es el caso real en servidor: --account=miquel-roca siempre resuelve.
    """
    fake = MagicMock()
    with patch("linkedin_main.get_current_username", return_value=None):
        env = {k: v for k, v in os.environ.items() if k != "LINKEDIN_PROFILE_URL"}
        with patch.dict(os.environ, env, clear=True):
            result = main_module.get_username_non_interactive(fake, account_slug="miquel-roca")
            assert result == "miquel-roca"


def test_get_username_non_interactive_account_slug_no_usado_si_hay_sesion():
    """
    El account_slug NO se usa si la sesión ya detectó el username (prioridad 1 > 2).
    """
    fake = MagicMock()
    with patch("linkedin_main.get_current_username", return_value="username-real"):
        result = main_module.get_username_non_interactive(fake, account_slug="slug-diferente")
        assert result == "username-real"


def test_get_username_non_interactive_sin_usuario_ni_env_lanza():
    """
    Sin sesión, sin account_slug y sin LINKEDIN_PROFILE_URL → ValueError.
    Solo ocurre en configuraciones muy incompletas.
    """
    fake = MagicMock()
    with patch("linkedin_main.get_current_username", return_value=None):
        env = {k: v for k, v in os.environ.items() if k != "LINKEDIN_PROFILE_URL"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError):
                main_module.get_username_non_interactive(fake)


# ── run_scrape (modo legacy) ──────────────────────────────────────────────────

def test_run_scrape_no_interactivo_registra_run_y_escribe_csv():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0

        fake_account = MagicMock()
        fake_account.on_block = False
        perfil = {"profile_id": "test-user", "name": "Test"}
        conexiones = pd.DataFrame([{"profile_id": "c1", "name": "Conexión 1"}])

        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="test-user"):
                with patch("linkedin_main.scrape_profile_and_connections", return_value=(perfil, conexiones)):
                    with patch("linkedin_main.insert_run") as mock_insert:
                        with patch("linkedin_main.update_account_last_run"):
                            orig_cwd = os.getcwd()
                            try:
                                os.chdir(tmp)
                                main_module.run_scrape(interactive=False)
                            finally:
                                os.chdir(orig_cwd)

        mock_insert.assert_called_once()
        kw = mock_insert.call_args[1]
        assert kw["username"] == "test-user"
        assert kw["contacts_scraped"] == 1


def test_run_scrape_dry_run_no_llama_init_client():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0
        with patch("linkedin_main.init_client") as mock_init:
            with patch("linkedin_main.scrape_profile_and_connections"):
                main_module.run_scrape(interactive=False, dry_run=True)
        mock_init.assert_not_called()


def test_run_scrape_cooldown_activo_lanza_en_no_interactivo():
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "cooldown")
        with open(f, "w") as fp:
            fp.write(str(time.time() + 9999))
        main_module.COOLDOWN_FILE = f
        with pytest.raises(RuntimeError, match="ooldown"):
            main_module.run_scrape(interactive=False)


# ── run_index ─────────────────────────────────────────────────────────────────

def test_run_index_encola_slugs_y_muestra_stats():
    """run_index llama a collect_all_slugs, queue_slugs y muestra el resumen."""
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0
        main_module.SCRAPE_WINDOW_START = 0
        main_module.SCRAPE_WINDOW_END = 23

        fake_account = MagicMock()
        fake_account.username = "yo"

        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main.collect_all_slugs", return_value=["a", "b", "c"]):
                    with patch("linkedin_main.queue_slugs", return_value=3) as mock_queue:
                        with patch("linkedin_main.get_queue_stats", return_value={
                            "pending": 3, "done": 0, "error": 0, "total": 3
                        }):
                            with patch("linkedin_main._check_time_window", return_value=False):
                                with patch("linkedin_main._check_daily_budget", return_value=False):
                                    main_module.run_index(interactive=False)

        mock_queue.assert_called_once_with("yo", ["a", "b", "c"])


def test_run_index_sin_slugs_no_encola():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0

        fake_account = MagicMock()
        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main.collect_all_slugs", return_value=[]):
                    with patch("linkedin_main.queue_slugs") as mock_queue:
                        with patch("linkedin_main._check_time_window", return_value=False):
                            with patch("linkedin_main._check_daily_budget", return_value=False):
                                main_module.run_index(interactive=False)

        mock_queue.assert_not_called()


# ── run_enrich ────────────────────────────────────────────────────────────────

def test_run_enrich_procesa_slugs_pendientes_y_guarda_en_db():
    """run_enrich visita perfiles, guarda en contacts y marca done en queue."""
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0
        main_module.MAX_CONTACTS_PER_RUN = 5
        main_module.MAX_CONTACTS_PER_DAY = 80

        fake_account = MagicMock()
        fake_account.on_block = False
        fake_driver = MagicMock()

        fake_data = {"profile_id": "alice", "name": "Alice", "position": "Dev",
                     "company": "Acme", "location": "Madrid", "emails": "a@a.com",
                     "phones": None, "profile_link": "https://linkedin.com/in/alice/",
                     "profile_photo": None, "premium": None, "creator": None,
                     "open_to_work": None, "followers": None, "connections": None,
                     "first_name": None, "last_name": None, "is_connection": True}

        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main._check_time_window", return_value=False):
                    with patch("linkedin_main.get_daily_count", return_value=0):
                        with patch("linkedin_main.get_pending_slugs", return_value=["alice"]):
                            with patch("linkedin_main._create_driver_with_cookies", return_value=fake_driver):
                                with patch("linkedin_main._enrich_connection_from_profile", return_value=fake_data):
                                    with patch("linkedin_main.upsert_contact", return_value="inserted") as mock_upsert:
                                        with patch("linkedin_main.mark_queue_done") as mock_done:
                                            with patch("linkedin_main.insert_run"):
                                                with patch("linkedin_main.get_queue_stats", return_value={
                                                    "pending": 0, "done": 1, "error": 0, "total": 1
                                                }):
                                                    with patch("linkedin_main.time.sleep"):
                                                        main_module.run_enrich(interactive=False)

        mock_upsert.assert_called_once_with("yo", fake_data)
        mock_done.assert_called_once_with("yo", "alice")


def test_run_enrich_sin_pendientes_no_procesa():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0

        fake_account = MagicMock()
        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main._check_time_window", return_value=False):
                    with patch("linkedin_main.get_daily_count", return_value=0):
                        with patch("linkedin_main.get_pending_slugs", return_value=[]):
                            with patch("linkedin_main.get_queue_stats", return_value={
                                "pending": 0, "done": 10, "error": 0, "total": 10
                            }):
                                with patch("linkedin_main._create_driver_with_cookies") as mock_driver:
                                    main_module.run_enrich(interactive=False)

        mock_driver.assert_not_called()


def test_run_enrich_presupuesto_agotado_no_procesa():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0
        main_module.MAX_CONTACTS_PER_DAY = 80

        fake_account = MagicMock()
        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main._check_time_window", return_value=False):
                    with patch("linkedin_main.get_daily_count", return_value=80):  # = límite
                        with patch("linkedin_main._check_daily_budget", return_value=True):
                            with patch("linkedin_main._run_safety_checks") as mock_checks:
                                # _run_safety_checks lanzará si presupuesto agotado
                                mock_checks.side_effect = RuntimeError("Presupuesto diario")
                                with pytest.raises(RuntimeError, match="Presupuesto"):
                                    main_module.run_enrich(interactive=False)


def test_run_enrich_skip_inteligente_contacto_fresco(tmp_path):
    """Contactos recién scrapeados (< CONTACT_REFRESH_DAYS) se saltan sin visitar."""
    import linkedin_main as m
    orig_cf = m.COOLDOWN_FILE
    orig_lr = m.LAST_RUN_FILE
    orig_min = m.MIN_HOURS_BETWEEN_RUNS
    orig_rd = m.CONTACT_REFRESH_DAYS
    orig_rpd = m.MAX_CONTACTS_PER_RUN
    orig_day = m.MAX_CONTACTS_PER_DAY
    m.COOLDOWN_FILE = str(tmp_path / "cooldown")
    m.LAST_RUN_FILE = str(tmp_path / "last_run")
    m.MIN_HOURS_BETWEEN_RUNS = 0
    m.CONTACT_REFRESH_DAYS = 30
    m.MAX_CONTACTS_PER_RUN = 5
    m.MAX_CONTACTS_PER_DAY = 80

    fake_account = MagicMock()
    fake_account.on_block = False
    fake_driver = MagicMock()

    try:
        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main._check_time_window", return_value=False):
                    with patch("linkedin_main.get_daily_count", return_value=0):
                        with patch("linkedin_main.get_pending_slugs", return_value=["alice", "bob"]):
                            with patch("linkedin_main._create_driver_with_cookies", return_value=fake_driver):
                                # alice existe y fue scrapeada hace 5 días (< 30) → skip
                                # bob no existe → debe visitarse
                                with patch("linkedin_main.contact_exists", side_effect=[True, False]):
                                    with patch("linkedin_main.days_since_last_scrape", return_value=5.0):
                                        with patch("linkedin_main.contact_has_core_fields", return_value=True):
                                            with patch("linkedin_main._enrich_connection_from_profile",
                                                       return_value={"profile_id": "bob"}) as mock_enrich:
                                                with patch("linkedin_main.upsert_contact", return_value="inserted"):
                                                    with patch("linkedin_main.mark_queue_done") as mock_done:
                                                        with patch("linkedin_main.mark_queue_error"):
                                                            with patch("linkedin_main.insert_run"):
                                                                with patch("linkedin_main.get_queue_stats",
                                                                           return_value={"pending": 0, "done": 2, "error": 0, "total": 2}):
                                                                    with patch("linkedin_main.update_account_last_run"):
                                                                        with patch("linkedin_main.time.sleep"):
                                                                            m.run_enrich(interactive=False)

        # alice fue saltada (skip) pero también marcada done
        # bob fue visitada
        assert mock_enrich.call_count == 1  # solo bob
        done_calls = [c.args[1] for c in mock_done.call_args_list]
        assert "alice" in done_calls  # marcada done aunque saltada
        assert "bob" in done_calls
    finally:
        m.COOLDOWN_FILE = orig_cf
        m.LAST_RUN_FILE = orig_lr
        m.MIN_HOURS_BETWEEN_RUNS = orig_min
        m.CONTACT_REFRESH_DAYS = orig_rd
        m.MAX_CONTACTS_PER_RUN = orig_rpd
        m.MAX_CONTACTS_PER_DAY = orig_day


def test_run_enrich_fuera_de_franja_lanza_en_no_interactivo():
    with tempfile.TemporaryDirectory() as tmp:
        main_module.COOLDOWN_FILE = os.path.join(tmp, "cooldown")
        main_module.LAST_RUN_FILE = os.path.join(tmp, "last_run")
        main_module.MIN_HOURS_BETWEEN_RUNS = 0
        main_module.SCRAPE_WINDOW_START = 8
        main_module.SCRAPE_WINDOW_END = 21

        fake_account = MagicMock()
        with patch("linkedin_main.init_client", return_value=fake_account):
            with patch("linkedin_main.get_username_non_interactive", return_value="yo"):
                with patch("linkedin_main.get_daily_count", return_value=0):
                    with patch("linkedin_main._check_time_window", return_value=True):
                        with pytest.raises(RuntimeError, match="[Ff]ranja"):
                            main_module.run_enrich(interactive=False)
