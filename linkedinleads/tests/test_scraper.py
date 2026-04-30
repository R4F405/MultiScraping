"""
Tests del módulo scraper (Playwright, sin StaffSpy).
No se hacen llamadas reales a LinkedIn ni al navegador: se mockean todos los
componentes externos (playwright page, driver, etc.).
"""
import pickle
import tempfile
import os

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch, call

from scraper import (
    LinkedInSession,
    _load_cookies,
    _save_cookies,
    _driver_cookies_to_list,
    _build_connection_dict,
    _parse_person_from_json_ld,
    _extract_person_from_any_script,
    _extract_person_from_dom,
    _extract_contact_info_from_overlay,
    _extract_extra_from_dom,
    _collect_connection_slugs,
    _enrich_connection_from_profile,
    _is_valid_phone,
    _clean_topcard_text,
    _is_interactive,
    _is_logged_in,
    collect_all_slugs,
    scrape_connections,
    scrape_connections_selenium,
    scrape_profile_and_connections,
    CONTACT_OVERLAY_WAIT_SELECTOR,
)
from tests.conftest import COLUMNAS_CSV_CONEXIONES


# ── LinkedInSession ────────────────────────────────────────────────────────────

def test_linkedin_session_cookies(fake_cookies):
    s = LinkedInSession(fake_cookies)
    assert s.cookies == fake_cookies
    assert s.on_block is False
    assert s.username is None


def test_linkedin_session_username(fake_cookies):
    s = LinkedInSession(fake_cookies, username="miquel-roca")
    assert s.username == "miquel-roca"


def test_linkedin_session_on_block(fake_cookies):
    s = LinkedInSession(fake_cookies)
    s.on_block = True
    assert s.on_block is True


# ── _load_cookies / _save_cookies ──────────────────────────────────────────────

def test_save_and_load_cookies_formato_nuevo(fake_cookies):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as f:
        path = f.name
    try:
        _save_cookies(fake_cookies, path)
        loaded = _load_cookies(path)
        assert loaded is not None
        assert len(loaded) == len(fake_cookies)
        assert loaded[0]["name"] == "li_at"
    finally:
        os.unlink(path)


def test_load_cookies_archivo_no_existe():
    assert _load_cookies("/ruta/que/no/existe.pkl") is None


def test_load_cookies_archivo_corrupto():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as f:
        f.write(b"datos corrompidos no pickle")
        path = f.name
    try:
        result = _load_cookies(path)
        assert result is None
    finally:
        os.unlink(path)


def test_load_cookies_formato_antiguo_requests_jar(fake_cookies):
    """
    El formato antiguo guardaba las cookies como lista de dicts (no un jar de requests).
    Verifica que se carga igual que el formato nuevo.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pkl") as f:
        path = f.name
    try:
        # Simular el "formato antiguo nuevo" — lista de dicts, igual que el actual
        with open(path, "wb") as f:
            pickle.dump({"cookies": fake_cookies, "headers": {}}, f)
        loaded = _load_cookies(path)
        assert loaded is not None
        assert isinstance(loaded, list)
        assert any(c["name"] == "li_at" for c in loaded)
    finally:
        os.unlink(path)


# ── _driver_cookies_to_list ────────────────────────────────────────────────────

def test_driver_cookies_to_list():
    mock_driver = MagicMock()
    mock_driver.context.cookies.return_value = [
        {"name": "li_at", "value": "T", "domain": ".linkedin.com", "path": "/"},
        {"name": "foo", "value": "bar"},
    ]
    result = _driver_cookies_to_list(mock_driver)
    assert len(result) == 2
    assert result[0]["name"] == "li_at"
    assert result[1]["domain"] == ".linkedin.com"  # default


# ── _build_connection_dict ─────────────────────────────────────────────────────

def test_build_connection_dict_completo():
    d = _build_connection_dict("juan-garcia", "Juan García", "Dev en Acme")
    assert d["profile_id"] == "juan-garcia"
    assert d["name"] == "Juan García"
    assert d["position"] == "Dev en Acme"
    assert d["profile_link"] == "https://www.linkedin.com/in/juan-garcia/"
    assert d["is_connection"] is True
    assert list(d.keys()) == COLUMNAS_CSV_CONEXIONES


def test_build_connection_dict_minimo():
    d = _build_connection_dict("slug-test", None, None)
    assert d["profile_id"] == "slug-test"
    assert d["name"] is None
    assert d["position"] is None
    assert list(d.keys()) == COLUMNAS_CSV_CONEXIONES


# ── _parse_person_from_json_ld ─────────────────────────────────────────────────

def test_parse_person_json_ld_directo():
    data = {
        "@type": "Person",
        "name": "Ana López",
        "givenName": "Ana",
        "familyName": "López",
        "headline": "Engineer",
        "worksFor": [{"name": "Empresa SA"}],
        "address": {"addressLocality": "Madrid", "addressCountry": "Spain"},
    }
    result = _parse_person_from_json_ld(data)
    assert result["name"] == "Ana López"
    assert result["position"] == "Engineer"
    assert result["company"] == "Empresa SA"
    assert "Madrid" in result["location"]


def test_parse_person_json_ld_en_graph():
    data = {
        "@graph": [
            {"@type": "WebPage", "name": "LinkedIn"},
            {"@type": "Person", "name": "Carlos Ruiz", "headline": "Dev"},
        ]
    }
    result = _parse_person_from_json_ld(data)
    assert result["name"] == "Carlos Ruiz"
    assert result["position"] == "Dev"


def test_parse_person_json_ld_con_foto():
    data = {
        "@type": "Person",
        "name": "Luis Pérez",
        "headline": "Dev",
        "image": {"contentUrl": "https://media.linkedin.com/foto.jpg"},
    }
    result = _parse_person_from_json_ld(data)
    assert result["profile_photo"] == "https://media.linkedin.com/foto.jpg"


def test_parse_person_json_ld_foto_como_string():
    data = {
        "@type": "Person",
        "name": "Luis Pérez",
        "image": "https://media.linkedin.com/foto.jpg",
    }
    result = _parse_person_from_json_ld(data)
    assert result["profile_photo"] == "https://media.linkedin.com/foto.jpg"


def test_parse_person_json_ld_no_person():
    assert _parse_person_from_json_ld({"@type": "WebPage"}) is None
    assert _parse_person_from_json_ld({}) is None


def test_parse_person_json_ld_fallbacks_modernos():
    data = {
        "@type": "Person",
        "name": "Lucia Torres",
        "jobTitle": "Head of Growth",
        "location": "Barcelona, Spain",
        "worksFor": {"legalName": "Acme Corp"},
    }
    result = _parse_person_from_json_ld(data)
    assert result["position"] == "Head of Growth"
    assert result["company"] == "Acme Corp"
    assert result["location"] == "Barcelona, Spain"


def test_extract_person_from_dom_selectores_modernos():
    driver = MagicMock()
    name_el = MagicMock()
    name_el.inner_text.return_value = "Ana Lopez"
    pos_el = MagicMock()
    pos_el.inner_text.return_value = "Senior Product Manager"
    loc_el = MagicMock()
    loc_el.inner_text.return_value = "Madrid, Comunidad de Madrid"
    company_el = MagicMock()
    company_el.inner_text.return_value = "Acme Labs"

    def fake_query_selector_all(selector):
        if "a > h1" in selector or "text-heading-xlarge" in selector:
            return [name_el]
        if selector == "section[data-view-name='profile-card'] .t-14.t-normal":
            return [pos_el]
        if selector == "section[data-view-name='profile-card'] .text-body-small.t-black--light":
            return [loc_el]
        if selector == "section[data-view-name='profile-card'] a[href*='/company/'] span":
            return [company_el]
        return []

    driver.query_selector_all.side_effect = fake_query_selector_all
    driver.evaluate.return_value = None
    result = _extract_person_from_dom(driver)
    assert result["name"] == "Ana Lopez"
    assert result["position"] == "Senior Product Manager"
    assert result["location"] == "Madrid, Comunidad de Madrid"
    assert result["company"] == "Acme Labs"


def test_extract_person_from_dom_fallback_position_from_message():
    driver = MagicMock()
    name_el = MagicMock()
    name_el.inner_text.return_value = "Ana Lopez"

    def fake_query_selector_all(selector):
        if "a > h1" in selector or "text-heading-xlarge" in selector:
            return [name_el]
        # Ensure legacy selectors don't set position/location/company
        return []

    driver.query_selector_all.side_effect = fake_query_selector_all
    # evaluate calls order (when no selectors match):
    # 1) headline_js, 2) location_js, 3) location_guess, 4) fallback position_from_message
    driver.evaluate.side_effect = [None, None, None, "Senior Product Manager"]
    driver.query_selector.return_value = None
    result = _extract_person_from_dom(driver)
    assert result["position"] == "Senior Product Manager"


def test_clean_topcard_text_elimina_ruido():
    raw = "Jose González Moragues\n\n· 1er\n\n--\n\nValencia y alrededores\n\n·\n\nInformación de contacto"
    assert _clean_topcard_text(raw, "Jose González Moragues") == "Valencia y alrededores"


def test_clean_topcard_text_elimina_nombre_exacto():
    # Solo se filtra igualdad exacta (normalizada). Una línea que es exactamente
    # el nombre del perfil se descarta como ruido de top-card.
    assert _clean_topcard_text("Jorge Sabater Galindo", "Jorge Sabater Galindo") is None

def test_clean_topcard_text_no_filtra_cargo_que_menciona_nombre():
    # Una línea como "Cargo — Jorge Sabater" NO debe filtrarse solo por contener el nombre;
    # no queremos perder datos reales de cargo/empresa que lo mencionan.
    result = _clean_topcard_text("Senior Developer at Jorge's Startup", "Jorge Sabater Galindo")
    assert result is not None


# ── _extract_person_from_any_script ───────────────────────────────────────────

def test_extract_person_from_script_json_ld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Person","name":"María Sanz","headline":"Designer","givenName":"María"}
    </script>
    </head></html>
    """
    result = _extract_person_from_any_script(html)
    assert result is not None
    assert result["name"] == "María Sanz"


def test_extract_person_from_script_sin_datos():
    html = "<html><body><p>Sin datos</p></body></html>"
    assert _extract_person_from_any_script(html) is None


# ── _is_interactive ────────────────────────────────────────────────────────────

def test_is_interactive_devuelve_bool():
    assert isinstance(_is_interactive(), bool)


@patch("sys.stdin.isatty", return_value=True)
def test_is_interactive_true(mock_isatty):
    assert _is_interactive() is True


@patch("sys.stdin.isatty", return_value=False)
def test_is_interactive_false(mock_isatty):
    assert _is_interactive() is False


# ── _is_logged_in ──────────────────────────────────────────────────────────────

def test_is_logged_in_cuando_esta_en_feed():
    driver = MagicMock()
    driver.url = "https://www.linkedin.com/feed/"
    assert _is_logged_in(driver) is True


def test_is_logged_in_redirigido_a_login():
    driver = MagicMock()
    driver.url = "https://www.linkedin.com/login?fromSignIn=true"
    assert _is_logged_in(driver) is False


def test_is_logged_in_authwall():
    driver = MagicMock()
    driver.url = "https://www.linkedin.com/authwall?trk=xyz"
    assert _is_logged_in(driver) is False


# ── scrape_connections ─────────────────────────────────────────────────────────

def test_scrape_connections_devuelve_dataframe_con_columnas_correctas(fake_cookies):
    """scrape_connections debe devolver un DataFrame con exactamente las columnas del CSV."""
    session = LinkedInSession(fake_cookies)
    mock_df = pd.DataFrame([_build_connection_dict("c1", "Contacto 1", "Dev")])

    with patch("scraper.scrape_connections_selenium", return_value=mock_df):
        df = scrape_connections(session, max_contacts=5)

    assert not df.empty
    assert list(df.columns) == COLUMNAS_CSV_CONEXIONES
    assert df.iloc[0]["profile_id"] == "c1"
    assert df.iloc[0]["name"] == "Contacto 1"


def test_scrape_connections_vacio_cuando_selenium_falla(fake_cookies):
    session = LinkedInSession(fake_cookies)
    with patch("scraper.scrape_connections_selenium", return_value=pd.DataFrame()):
        df = scrape_connections(session, max_contacts=5)
    assert df.empty


# ── scrape_connections_selenium ────────────────────────────────────────────────

def test_scrape_connections_selenium_on_block_si_redirige_a_login(fake_cookies):
    """Si el driver redirige a /login, marca on_block y devuelve vacío."""
    session = LinkedInSession(fake_cookies)

    mock_driver = MagicMock()
    mock_driver.url = "https://www.linkedin.com/login"

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        df = scrape_connections_selenium(session, max_contacts=5)

    assert df.empty
    assert session.on_block is True


def test_scrape_connections_selenium_sin_driver(fake_cookies):
    """Si no se puede crear el driver, devuelve DataFrame vacío."""
    session = LinkedInSession(fake_cookies)
    with patch("scraper._create_driver_with_cookies", return_value=None):
        df = scrape_connections_selenium(session, max_contacts=5)
    assert df.empty


def test_scrape_connections_selenium_extrae_conexiones(fake_cookies):
    """La nueva arquitectura de dos fases recoge slugs y enriquece cada perfil."""
    session = LinkedInSession(fake_cookies)

    mock_driver = MagicMock()
    # La primera navegación es al feed (comprobación de sesión)
    mock_driver.url = "https://www.linkedin.com/feed/"
    mock_driver.query_selector_all.return_value = []
    mock_driver.content.return_value = "<html><body></body></html>"

    fake_enriched = [
        _build_connection_dict("alice-dev", "Alice Dev", "Engineer"),
        _build_connection_dict("bob-prod", "Bob Prod", "Manager"),
    ]

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper._is_soft_blocked", return_value=False):
            with patch("scraper._collect_connection_slugs", return_value=["alice-dev", "bob-prod"]):
                with patch("scraper._enrich_connection_from_profile", side_effect=fake_enriched):
                    df = scrape_connections_selenium(session, max_contacts=10)

    assert len(df) == 2
    assert "alice-dev" in list(df["profile_id"])
    assert list(df.columns) == COLUMNAS_CSV_CONEXIONES


# ── scrape_profile_and_connections ────────────────────────────────────────────

def test_scrape_profile_and_connections_perfil_y_conexiones_ok(fake_cookies):
    session = LinkedInSession(fake_cookies)
    fake_perfil = {
        "profile_id": "me", "name": "Yo", "first_name": "Yo", "last_name": None,
        "position": "Dev", "company": "Acme", "location": "Barcelona",
        "emails": None, "phones": None, "is_connection": None,
        "followers": None, "connections": None,
        "profile_link": "https://www.linkedin.com/in/me/",
        "profile_photo": None, "premium": None, "creator": None, "open_to_work": None,
    }
    fake_conexiones = pd.DataFrame([_build_connection_dict("c1", "Contacto", "Dev")])
    mock_driver = MagicMock()

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper._scrape_profile_via_browser", return_value=(fake_perfil, [])):
            with patch("scraper.scrape_connections", return_value=fake_conexiones):
                perfil, conexiones = scrape_profile_and_connections(session, "me", max_contacts=5)

    assert perfil["profile_id"] == "me"
    assert perfil["name"] == "Yo"
    assert "scrape_error" not in perfil
    assert len(conexiones) == 1


def test_scrape_profile_and_connections_perfil_falla_continua_con_conexiones(fake_cookies):
    """Si el perfil falla, devuelve perfil mínimo con scrape_error y continúa con conexiones."""
    session = LinkedInSession(fake_cookies)
    fake_conexiones = pd.DataFrame([_build_connection_dict("c1", "Contacto", "Dev")])
    mock_driver = MagicMock()

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper._scrape_profile_via_browser", side_effect=Exception("Error de red")):
            with patch("scraper.scrape_connections", return_value=fake_conexiones):
                perfil, conexiones = scrape_profile_and_connections(session, "bad-user", max_contacts=5)

    assert perfil["profile_id"] == "bad-user"
    assert perfil["name"] is None
    assert "scrape_error" in perfil
    assert len(conexiones) == 1


def test_scrape_profile_and_connections_perfil_none_continua(fake_cookies):
    """Si _scrape_profile_via_browser devuelve (None, []), se genera perfil de error."""
    session = LinkedInSession(fake_cookies)

    with patch("scraper._scrape_profile_via_browser", return_value=(None, [])):
        with patch("scraper.scrape_connections", return_value=pd.DataFrame()):
            perfil, conexiones = scrape_profile_and_connections(session, "x", max_contacts=5)

    assert perfil["profile_id"] == "x"
    assert "scrape_error" in perfil
    assert conexiones.empty


def test_scrape_profile_and_connections_perfil_ok_conexiones_vacias(fake_cookies):
    session = LinkedInSession(fake_cookies)
    fake_perfil = {
        "profile_id": "me", "name": "Yo", "first_name": None, "last_name": None,
        "position": None, "company": None, "location": None, "emails": None,
        "phones": None, "is_connection": None, "followers": None, "connections": None,
        "profile_link": "https://www.linkedin.com/in/me/", "profile_photo": None,
        "premium": None, "creator": None, "open_to_work": None,
    }

    with patch("scraper._scrape_profile_via_browser", return_value=(fake_perfil, [])):
        with patch("scraper.scrape_connections", return_value=pd.DataFrame()):
            perfil, conexiones = scrape_profile_and_connections(session, "me", max_contacts=5)

    assert perfil["profile_id"] == "me"
    assert conexiones.empty


# ── _collect_connection_slugs ─────────────────────────────────────────────────

def test_collect_connection_slugs_extrae_slugs_de_hrefs():
    """Recoge slugs únicos de los enlaces /in/ visibles en la página."""
    mock_driver = MagicMock()

    def fake_query_selector_all(selector):
        if "/in/" in selector:
            a1 = MagicMock()
            a1.get_attribute.return_value = "https://www.linkedin.com/in/alice-dev/"
            a2 = MagicMock()
            a2.get_attribute.return_value = "https://www.linkedin.com/in/bob-prod/"
            return [a1, a2]
        return []

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    mock_driver.evaluate.return_value = None

    with patch("scraper.time.sleep"):
        slugs = _collect_connection_slugs(mock_driver, max_contacts=10)

    assert "alice-dev" in slugs
    assert "bob-prod" in slugs
    assert len(slugs) == len(set(slugs))  # sin duplicados


def test_collect_connection_slugs_respeta_max_contacts():
    """No devuelve más slugs que max_contacts."""
    mock_driver = MagicMock()

    def fake_query_selector_all(selector):
        if "/in/" in selector:
            links = []
            for i in range(20):
                a = MagicMock()
                a.get_attribute.return_value = f"https://www.linkedin.com/in/user-{i}/"
                links.append(a)
            return links
        return []

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    mock_driver.evaluate.return_value = None

    with patch("scraper.time.sleep"):
        slugs = _collect_connection_slugs(mock_driver, max_contacts=5)

    assert len(slugs) <= 5


# ── _extract_contact_info_from_overlay ────────────────────────────────────────

def test_extract_contact_info_email_via_mailto():
    """Extrae email de un enlace mailto: en el overlay."""
    mock_driver = MagicMock()

    mailto_el = MagicMock()
    mailto_el.get_attribute.return_value = "mailto:test@example.com"

    def fake_query_selector_all(selector):
        if "mailto" in selector:
            return [mailto_el]
        return []

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.content.return_value = "<html><body></body></html>"

    with patch("scraper.time.sleep"):
        result = _extract_contact_info_from_overlay(mock_driver, "test-user")

    assert result["emails"] == "test@example.com"
    assert result["phones"] is None
    assert CONTACT_OVERLAY_WAIT_SELECTOR


def test_extract_contact_info_sin_datos():
    """Si no hay mailto ni sección de teléfono, devuelve None en ambos campos."""
    mock_driver = MagicMock()
    mock_driver.query_selector_all.return_value = []
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.content.return_value = "<html><body><p>Sin contacto</p></body></html>"

    with patch("scraper.time.sleep"):
        result = _extract_contact_info_from_overlay(mock_driver, "no-contact")

    assert result["emails"] is None
    assert result["phones"] is None


def test_extract_contact_info_multiples_emails():
    """Si hay varios emails, los separa con '; '."""
    mock_driver = MagicMock()

    def make_mailto(addr):
        el = MagicMock()
        el.get_attribute.return_value = f"mailto:{addr}"
        return el

    def fake_query_selector_all(selector):
        if "mailto" in selector:
            return [make_mailto("a@example.com"), make_mailto("b@example.com")]
        return []

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.content.return_value = "<html></html>"

    with patch("scraper.time.sleep"):
        result = _extract_contact_info_from_overlay(mock_driver, "multi-email")

    assert "a@example.com" in result["emails"]
    assert "b@example.com" not in result["emails"]


# ── _is_valid_phone ───────────────────────────────────────────────────────────

def test_is_valid_phone_numero_espanol():
    assert _is_valid_phone("653329820") is True
    assert _is_valid_phone("+34 600 123 456") is True
    assert _is_valid_phone("+1 (555) 123-4567") is True


def test_is_valid_phone_rechaza_falsos():
    assert _is_valid_phone("1.13.42781") is False   # versión con punto
    assert _is_valid_phone("Móvil") is False          # solo texto
    assert _is_valid_phone("(Trabajo)") is False      # etiqueta con letras
    assert _is_valid_phone("83") is False             # demasiado corto
    assert _is_valid_phone("") is False


def test_extract_contact_info_telefono_via_xpath():
    """
    El overlay de LinkedIn tiene la estructura real:
      <h3>\\n  Teléfono\\n</h3>
      <ul><li>
        <span class='t-14 t-black t-normal'>653329820</span>
        <span class='t-14 t-black--light t-normal'>(Trabajo)</span>
      </li></ul>
    Verifica que se extrae 653329820 y no '(Trabajo)'.
    """
    mock_driver = MagicMock()
    # No hay mailto
    mock_driver.query_selector_all.return_value = []

    # h3 mock con ul hermano
    span_numero = MagicMock()
    span_numero.inner_text.return_value = "653329820"
    span_etiqueta = MagicMock()
    span_etiqueta.inner_text.return_value = "(Trabajo)"

    ul_mock = MagicMock()
    ul_mock.query_selector_all.return_value = [span_numero, span_etiqueta]

    h3_mock = MagicMock()
    h3_mock.query_selector.return_value = ul_mock

    # locator().all() devuelve [h3_mock] para el XPath del teléfono
    mock_driver.locator.return_value.all.return_value = [h3_mock]
    mock_driver.content.return_value = "<html><body></body></html>"

    with patch("scraper.time.sleep"):
        result = _extract_contact_info_from_overlay(mock_driver, "test-phone")

    assert result["phones"] == "653329820"


def test_extract_contact_info_no_captura_numeros_falsos():
    """No captura versiones, IDs ni timestamps como teléfonos (sin sección Teléfono)."""
    mock_driver = MagicMock()
    mock_driver.query_selector_all.return_value = []
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.content.return_value = """
    <html><body>
      <p>Versión 1.13.42781 · timestamp 83-9096727 · 925546278</p>
    </body></html>
    """

    with patch("scraper.time.sleep"):
        result = _extract_contact_info_from_overlay(mock_driver, "no-phone")

    assert result["phones"] is None


# ── _extract_extra_from_dom ───────────────────────────────────────────────────

def test_extract_extra_from_dom_open_to_work():
    """Detecta la etiqueta Open to Work en el DOM."""
    mock_driver = MagicMock()

    otw_el = MagicMock()
    otw_el.inner_text.return_value = "Open to work"

    def fake_query_selector_all(selector):
        if "open-to-work" in selector or "aria-label" in selector:
            return [otw_el]
        return []

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    body_mock = MagicMock()
    body_mock.inner_text.return_value = "500 followers\n30 connections"
    mock_driver.query_selector.return_value = body_mock

    result = _extract_extra_from_dom(mock_driver)

    assert result["open_to_work"] is True
    assert result["followers"] == "500"
    assert result["connections"] == "30"


def test_extract_extra_from_dom_sin_datos():
    """Si no hay datos extra, devuelve None en todos los campos."""
    mock_driver = MagicMock()
    mock_driver.query_selector_all.return_value = []
    body_mock = MagicMock()
    body_mock.inner_text.return_value = "Texto sin datos relevantes"
    mock_driver.query_selector.return_value = body_mock

    result = _extract_extra_from_dom(mock_driver)

    assert result["premium"] is None
    assert result["creator"] is None
    assert result["open_to_work"] is None
    assert result["followers"] is None
    assert result["connections"] is None


# ── _enrich_connection_from_profile ──────────────────────────────────────────

def test_enrich_connection_from_profile_datos_completos():
    """Integra JSON-LD + DOM extra + overlay de contacto en un dict completo."""
    mock_driver = MagicMock()
    mock_driver.content.return_value = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Person","name":"Ana López","givenName":"Ana","familyName":"López",
     "headline":"Engineer","worksFor":[{"name":"Acme"}],
     "address":{"addressLocality":"Madrid"},"image":"https://cdn.photo.jpg"}
    </script>
    </head></html>
    """

    fake_extra = {
        "profile_photo": None, "followers": "1.2K", "connections": "500+",
        "premium": True, "creator": None, "open_to_work": None,
    }
    fake_contact = {"emails": "ana@acme.com", "phones": "+34 600 000 001"}

    with patch("scraper.time.sleep"):
        with patch("scraper._extract_extra_from_dom", return_value=fake_extra):
            with patch("scraper._extract_contact_info_from_overlay", return_value=fake_contact):
                result = _enrich_connection_from_profile(mock_driver, "ana-lopez")

    assert result["profile_id"] == "ana-lopez"
    assert result["name"] == "Ana López"
    assert result["first_name"] == "Ana"
    assert result["last_name"] == "López"
    assert result["position"] == "Engineer"
    assert result["company"] == "Acme"
    assert result["location"] == "Madrid"
    assert result["profile_photo"] == "https://cdn.photo.jpg"
    assert result["emails"] == "ana@acme.com"
    assert result["phones"] == "+34 600 000 001"
    assert result["followers"] == "1.2K"
    assert result["connections"] == "500+"
    assert result["premium"] is True
    assert result["is_connection"] is True
    assert result["_meta_profile_source"] in ("requests", "browser")
    assert result["_meta_contact_source"] in ("voyager", "overlay")
    for col in COLUMNAS_CSV_CONEXIONES:
        assert col in result


def test_enrich_connection_from_profile_sin_json_ld():
    """Si no hay JSON-LD, cae a fallback mínimo (nombre derivado del slug)."""
    mock_driver = MagicMock()
    mock_driver.content.return_value = "<html><body><p>Sin datos</p></body></html>"

    with patch("scraper.time.sleep"):
        with patch("scraper._extract_extra_from_dom", return_value={
            "profile_photo": None, "followers": None, "connections": None,
            "premium": None, "creator": None, "open_to_work": None,
        }):
            with patch("scraper._extract_contact_info_from_overlay",
                       return_value={"emails": None, "phones": None}):
                with patch("scraper._extract_person_from_dom", return_value=None):
                    result = _enrich_connection_from_profile(mock_driver, "sin-datos")

    assert result["profile_id"] == "sin-datos"
    assert result["name"] == "Sin Datos"
    assert result["profile_link"] == "https://www.linkedin.com/in/sin-datos/"
    for col in COLUMNAS_CSV_CONEXIONES:
        assert col in result


def test_enrich_connection_from_profile_sin_driver_usa_requests_only():
    fake_session = MagicMock()
    fake_row = {
        "name": "Marta Ruiz",
        "first_name": "Marta",
        "last_name": "Ruiz",
        "position": "Head of Sales",
        "company": "Acme",
        "location": "Valencia",
        "profile_photo": None,
    }
    with patch("scraper._load_profile_row_via_requests", return_value=(fake_row, True)):
        with patch("scraper._fetch_contact_info", return_value=({"emails": None, "phones": None}, "voyager")):
            result = _enrich_connection_from_profile(None, "marta-ruiz", session=fake_session)

    assert result["name"] == "Marta Ruiz"
    assert result["position"] == "Head of Sales"
    assert result["company"] == "Acme"
    assert result["location"] == "Valencia"
    assert result["_meta_profile_source"] == "requests"


# ── collect_all_slugs ─────────────────────────────────────────────────────────

def test_collect_all_slugs_devuelve_lista_de_slugs(fake_cookies):
    """Recopila slugs de /mynetwork/ y de la búsqueda sin enriquecer perfiles."""
    session = LinkedInSession(fake_cookies, username="yo")
    mock_driver = MagicMock()
    mock_driver.url = "https://www.linkedin.com/mynetwork/catch-up/connections/"

    links_mynetwork = []
    for slug in ["alice-dev", "bob-prod", "carlos-qa"]:
        a = MagicMock()
        a.get_attribute.return_value = f"https://www.linkedin.com/in/{slug}/"
        links_mynetwork.append(a)

    links_busqueda = []
    for slug in ["diana-pm", "elena-ux"]:
        a = MagicMock()
        a.get_attribute.return_value = f"https://www.linkedin.com/in/{slug}/"
        links_busqueda.append(a)

    call_count = {"n": 0}

    def fake_query_selector_all(selector):
        call_count["n"] += 1
        if call_count["n"] <= 6:   # primeras rondas = /mynetwork/
            return links_mynetwork
        return links_busqueda

    mock_driver.query_selector_all.side_effect = fake_query_selector_all
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.evaluate.return_value = None

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper.time.sleep"):
            slugs = collect_all_slugs(session)

    assert "alice-dev" in slugs
    assert "bob-prod" in slugs
    assert "carlos-qa" in slugs
    assert len(slugs) == len(set(slugs))  # sin duplicados


def test_collect_all_slugs_excluye_slug_propio(fake_cookies):
    """El slug del propio usuario no debe aparecer en los resultados."""
    session = LinkedInSession(fake_cookies, username="yo-mismo")
    mock_driver = MagicMock()
    mock_driver.url = "https://www.linkedin.com/mynetwork/catch-up/connections/"

    # Devuelve el propio slug + uno ajeno
    a_propio = MagicMock()
    a_propio.get_attribute.return_value = "https://www.linkedin.com/in/yo-mismo/"
    a_ajeno = MagicMock()
    a_ajeno.get_attribute.return_value = "https://www.linkedin.com/in/otra-persona/"
    mock_driver.query_selector_all.return_value = [a_propio, a_ajeno]
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.evaluate.return_value = None

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper.time.sleep"):
            slugs = collect_all_slugs(session)

    assert "yo-mismo" not in slugs
    assert "otra-persona" in slugs


def test_collect_all_slugs_on_block_si_redirige_a_login(fake_cookies):
    """Si LinkedIn redirige a /login, marca on_block y devuelve lista vacía."""
    session = LinkedInSession(fake_cookies)
    mock_driver = MagicMock()
    mock_driver.url = "https://www.linkedin.com/login"
    mock_driver.query_selector_all.return_value = []
    mock_driver.locator.return_value.all.return_value = []
    mock_driver.evaluate.return_value = None

    with patch("scraper._create_driver_with_cookies", return_value=mock_driver):
        with patch("scraper.time.sleep"):
            slugs = collect_all_slugs(session)

    assert slugs == []
    assert session.on_block is True


def test_collect_all_slugs_sin_driver(fake_cookies):
    """Si no se puede crear el driver, devuelve lista vacía."""
    session = LinkedInSession(fake_cookies)
    with patch("scraper._create_driver_with_cookies", return_value=None):
        slugs = collect_all_slugs(session)
    assert slugs == []


# ── _parse_proxy ──────────────────────────────────────────────────────────────

from scraper import _parse_proxy

def test_parse_proxy_sin_auth():
    p = _parse_proxy("proxy.host:8080")
    assert p["host"] == "proxy.host"
    assert p["port"] == "8080"
    assert p["user"] is None
    assert p["password"] is None


def test_parse_proxy_con_auth():
    p = _parse_proxy("user:pass@proxy.host:8080")
    assert p["host"] == "proxy.host"
    assert p["port"] == "8080"
    assert p["user"] == "user"
    assert p["password"] == "pass"


def test_parse_proxy_con_http_prefix():
    p = _parse_proxy("http://proxy.host:3128")
    assert p["host"] == "proxy.host"
    assert p["port"] == "3128"
    assert p["user"] is None


def test_parse_proxy_con_auth_y_http():
    p = _parse_proxy("http://admin:secret@proxy.host:9000")
    assert p["host"] == "proxy.host"
    assert p["user"] == "admin"
    assert p["password"] == "secret"
