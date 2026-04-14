"""
Tests del módulo email_enrichment.
Sin llamadas reales a APIs — se mockean todas las requests HTTP.
"""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import email_enrichment as ee


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status_code: int, body: dict) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = body
    return m


# ── enrich_email_if_missing — flag disabled ───────────────────────────────────

def test_enrich_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "0")
    result = ee.enrich_email_if_missing("Acme Corp")
    assert result is None


def test_enrich_company_vacia_returns_none(monkeypatch):
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "1")
    assert ee.enrich_email_if_missing("") is None
    assert ee.enrich_email_if_missing("   ") is None


# ── get_company_domain — Clearbit ─────────────────────────────────────────────

def test_get_company_domain_ok(monkeypatch):
    mock_resp = _mock_response(200, [{"name": "Acme", "domain": "acme.com"}])
    with patch("email_enrichment.requests.get", return_value=mock_resp):
        assert ee.get_company_domain("Acme") == "acme.com"


def test_get_company_domain_lista_vacia(monkeypatch):
    mock_resp = _mock_response(200, [])
    with patch("email_enrichment.requests.get", return_value=mock_resp):
        assert ee.get_company_domain("Unknown Corp") is None


def test_get_company_domain_error_http(monkeypatch):
    mock_resp = _mock_response(500, {})
    with patch("email_enrichment.requests.get", return_value=mock_resp):
        assert ee.get_company_domain("Acme") is None


def test_get_company_domain_excepcion(monkeypatch):
    with patch("email_enrichment.requests.get", side_effect=Exception("timeout")):
        assert ee.get_company_domain("Acme") is None


def test_get_company_domain_nombre_vacio():
    assert ee.get_company_domain("") is None
    assert ee.get_company_domain(None) is None


# ── Hunter.io ─────────────────────────────────────────────────────────────────

def test_hunter_sin_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    # No debe hacer ninguna llamada HTTP
    with patch("email_enrichment.requests.get") as mock_get:
        result = ee._hunter_find_email("acme.com")
    assert result is None
    mock_get.assert_not_called()


def test_hunter_cuota_agotada_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_API_KEY", "testkey")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 25, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        with patch("email_enrichment.requests.get") as mock_get:
            result = ee._hunter_find_email("acme.com")
        assert result is None
        mock_get.assert_not_called()
    finally:
        ee.USAGE_FILE = original


def test_hunter_ok_retorna_email_e_incrementa(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_API_KEY", "testkey")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 0, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        mock_resp = _mock_response(200, {"data": {"emails": [{"value": "ceo@acme.com"}]}})
        with patch("email_enrichment.requests.get", return_value=mock_resp):
            result = ee._hunter_find_email("acme.com")
        assert result == "ceo@acme.com"
        # Verificar que se incrementó el contador
        with open(usage_file) as f:
            data = json.load(f)
        assert data["hunter"] == 1
    finally:
        ee.USAGE_FILE = original


def test_hunter_lista_vacia_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("HUNTER_API_KEY", "testkey")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 0, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        mock_resp = _mock_response(200, {"data": {"emails": []}})
        with patch("email_enrichment.requests.get", return_value=mock_resp):
            result = ee._hunter_find_email("acme.com")
        assert result is None
    finally:
        ee.USAGE_FILE = original


# ── Snov.io ───────────────────────────────────────────────────────────────────

def test_snov_sin_credenciales_returns_none(monkeypatch):
    monkeypatch.delenv("SNOV_CLIENT_ID", raising=False)
    monkeypatch.delenv("SNOV_CLIENT_SECRET", raising=False)
    with patch("email_enrichment.requests.post") as mock_post:
        result = ee._snov_find_email("acme.com")
    assert result is None
    mock_post.assert_not_called()


def test_snov_ok_retorna_email(monkeypatch, tmp_path):
    monkeypatch.setenv("SNOV_CLIENT_ID", "cid")
    monkeypatch.setenv("SNOV_CLIENT_SECRET", "csecret")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 0, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        token_resp = _mock_response(200, {"access_token": "tok123"})
        domain_resp = _mock_response(200, {"emails": [{"email": "info@acme.com"}]})
        with patch("email_enrichment.requests.post", side_effect=[token_resp, domain_resp]):
            result = ee._snov_find_email("acme.com")
        assert result == "info@acme.com"
    finally:
        ee.USAGE_FILE = original


# ── Cuota mensual reset ───────────────────────────────────────────────────────

def test_monthly_reset(tmp_path):
    usage_file = str(tmp_path / "usage.json")
    # Simular mes anterior
    with open(usage_file, "w") as f:
        json.dump({"month": "2020-01", "hunter": 25, "snov": 50}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        usage = ee._load_usage()
        assert usage["hunter"] == 0
        assert usage["snov"] == 0
        from datetime import datetime
        assert usage["month"] == datetime.now().strftime("%Y-%m")
    finally:
        ee.USAGE_FILE = original


# ── enrich_email_if_missing — integración ─────────────────────────────────────

def test_enrich_hunter_primero_snov_no_se_llama(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "1")
    monkeypatch.setenv("HUNTER_API_KEY", "testkey")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 0, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        clearbit_resp = _mock_response(200, [{"domain": "acme.com"}])
        hunter_resp = _mock_response(200, {"data": {"emails": [{"value": "ceo@acme.com"}]}})
        with patch("email_enrichment.requests.get", side_effect=[clearbit_resp, hunter_resp]):
            with patch("email_enrichment._snov_find_email") as mock_snov:
                result = ee.enrich_email_if_missing("Acme Corp")
        assert result == "ceo@acme.com"
        mock_snov.assert_not_called()
    finally:
        ee.USAGE_FILE = original


def test_enrich_hunter_falla_snov_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "1")
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.setenv("SNOV_CLIENT_ID", "cid")
    monkeypatch.setenv("SNOV_CLIENT_SECRET", "csecret")
    usage_file = str(tmp_path / "usage.json")
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    with open(usage_file, "w") as f:
        json.dump({"month": month, "hunter": 0, "snov": 0}, f)
    original = ee.USAGE_FILE
    ee.USAGE_FILE = usage_file
    try:
        clearbit_resp = _mock_response(200, [{"domain": "acme.com"}])
        token_resp = _mock_response(200, {"access_token": "tok"})
        domain_resp = _mock_response(200, {"emails": [{"email": "info@acme.com"}]})
        with patch("email_enrichment.requests.get", return_value=clearbit_resp):
            with patch("email_enrichment.requests.post", side_effect=[token_resp, domain_resp]):
                result = ee.enrich_email_if_missing("Acme Corp")
        assert result == "info@acme.com"
    finally:
        ee.USAGE_FILE = original


def test_enrich_sin_dominio_returns_none(monkeypatch):
    monkeypatch.setenv("EMAIL_ENRICHMENT_ENABLED", "1")
    clearbit_resp = _mock_response(200, [])
    with patch("email_enrichment.requests.get", return_value=clearbit_resp):
        result = ee.enrich_email_if_missing("Empresa Desconocida XYZ")
    assert result is None
