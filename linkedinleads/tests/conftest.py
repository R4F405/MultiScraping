"""
Fixtures compartidas para los tests del scraper (sin StaffSpy).
"""
import pytest

# Columnas exactas del CSV de conexiones (output/conexiones_*_*.csv).
COLUMNAS_CSV_CONEXIONES = [
    "profile_id",
    "name",
    "first_name",
    "last_name",
    "position",
    "company",
    "location",
    "emails",
    "phones",
    "is_connection",
    "followers",
    "connections",
    "profile_link",
    "profile_photo",
    "premium",
    "creator",
    "open_to_work",
]


@pytest.fixture
def fake_cookies():
    """Lista mínima de cookies de sesión para tests."""
    return [
        {"name": "li_at", "value": "TOKEN123", "domain": ".linkedin.com", "path": "/"},
        {"name": "JSESSIONID", "value": "ajax:SESSION", "domain": ".linkedin.com", "path": "/"},
    ]
