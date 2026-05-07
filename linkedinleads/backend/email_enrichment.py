# email_enrichment.py
# Enriquecimiento de emails cuando LinkedIn overlay no muestra el email.
#
# Flujo:
#   1. Clearbit Autocomplete (gratis, sin API key) → infiere dominio de empresa
#   2. Hunter.io (25 búsquedas/mes free)           → busca emails del dominio
#   3. Snov.io (50 créditos/mes free)              → fallback si Hunter falla
#
# Activo solo si EMAIL_ENRICHMENT_ENABLED=1 en .env
# Cuota mensual persistida en .email_enrichment_usage.json

import json
import logging
import os
from datetime import datetime
from typing import Optional

import requests

from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger(__name__)

USAGE_FILE = ".email_enrichment_usage.json"
MONTHLY_LIMITS = {"hunter": 25, "snov": 50}


# ── Control de cuota mensual ──────────────────────────────────────────────────

def _load_usage() -> dict:
    today = datetime.now().strftime("%Y-%m")
    try:
        if os.path.isfile(USAGE_FILE):
            with open(USAGE_FILE) as f:
                data = json.load(f)
            if data.get("month") == today:
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"month": today, "hunter": 0, "snov": 0}


def _save_usage(usage: dict) -> None:
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f)
    except OSError as e:
        _log.debug("No se pudo guardar usage: %s", e)


def _can_use(source: str) -> bool:
    return _load_usage().get(source, 0) < MONTHLY_LIMITS.get(source, 0)


def _increment_usage(source: str) -> None:
    usage = _load_usage()
    usage[source] = usage.get(source, 0) + 1
    _save_usage(usage)


def get_remaining_quota() -> dict:
    """Devuelve cuántas búsquedas quedan este mes por servicio."""
    usage = _load_usage()
    return {
        source: max(0, MONTHLY_LIMITS[source] - usage.get(source, 0))
        for source in MONTHLY_LIMITS
    }


# ── Clearbit Autocomplete — infiere dominio desde nombre de empresa ───────────

def get_company_domain(company_name: str) -> Optional[str]:
    """
    Usa Clearbit Autocomplete para convertir nombre de empresa → dominio.
    Completamente gratuito, sin API key ni cuenta.
    """
    if not company_name or not company_name.strip():
        return None
    try:
        resp = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": company_name.strip()},
            timeout=8,
        )
        if resp.status_code == 200:
            results = resp.json()
            if results and isinstance(results, list):
                domain = results[0].get("domain")
                if domain:
                    _log.debug("Clearbit: '%s' → %s", company_name, domain)
                    return domain
    except Exception as e:
        _log.debug("Clearbit error para '%s': %s", company_name, e)
    return None


# ── Hunter.io — busca email de una persona específica ────────────────────────

_HUNTER_MIN_SCORE = 50  # descarta resultados de baja confianza

def _hunter_find_email(
    domain: str,
    first_name: str = "",
    last_name: str = "",
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Hunter.io email-finder (búsqueda por persona, no por dominio).
    Requiere first_name + last_name para buscar el email específico del contacto.
    Sin nombres cae a domain-search, que devuelve emails aleatorios del dominio
    y provoca falsos positivos — por eso se desactiva sin nombres.
    """
    api_key = os.getenv("HUNTER_API_KEY", "").strip()
    if not api_key:
        return None
    if not _can_use("hunter"):
        _log.debug("Hunter: cuota mensual agotada")
        return None
    if not first_name or not last_name:
        _log.debug("Hunter: sin nombre/apellido para %s, se omite para evitar falsos positivos", domain)
        return None
    client = session or requests
    try:
        resp = client.get(
            "https://api.hunter.io/v2/email-finder",
            params={
                "domain": domain,
                "first_name": first_name.strip(),
                "last_name": last_name.strip(),
                "api_key": api_key,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            email = data.get("email")
            score = data.get("score", 0)
            if email and score >= _HUNTER_MIN_SCORE:
                _increment_usage("hunter")
                _log.debug("Hunter: %s %s → %s (score %d)", first_name, last_name, email, score)
                return email
            elif email:
                _log.debug("Hunter: descartado %s (score %d < %d)", email, score, _HUNTER_MIN_SCORE)
        elif resp.status_code == 401:
            _log.warning("Hunter: API key inválida")
        elif resp.status_code == 429:
            _log.warning("Hunter: rate limit alcanzado")
    except Exception as e:
        _log.debug("Hunter error para '%s': %s", domain, e)
    return None


# ── Snov.io — busca emails por dominio (fallback) ─────────────────────────────

def _snov_get_token() -> Optional[str]:
    """Obtiene token OAuth2 de Snov.io."""
    client_id = os.getenv("SNOV_CLIENT_ID", "").strip()
    client_secret = os.getenv("SNOV_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    try:
        resp = requests.post(
            "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception as e:
        _log.debug("Snov token error: %s", e)
    return None


def _snov_find_email(domain: str) -> Optional[str]:
    """Snov.io domain emails. Requiere SNOV_CLIENT_ID + SNOV_CLIENT_SECRET. Límite: 50/mes free."""
    if not _can_use("snov"):
        _log.debug("Snov: cuota mensual agotada")
        return None
    token = _snov_get_token()
    if not token:
        return None
    try:
        resp = requests.post(
            "https://api.snov.io/v1/get-domain-emails",
            data={"access_token": token, "domain": domain, "type": "personal", "limit": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            emails = resp.json().get("emails", [])
            if emails:
                email = emails[0].get("email")
                if email:
                    _increment_usage("snov")
                    _log.debug("Snov: encontrado %s para %s", email, domain)
                    return email
    except Exception as e:
        _log.debug("Snov error para '%s': %s", domain, e)
    return None


# ── Función principal ─────────────────────────────────────────────────────────

def enrich_email_if_missing(
    company: str,
    first_name: str = "",
    last_name: str = "",
) -> Optional[str]:
    """
    Intenta encontrar el email de un contacto a partir de su empresa.

    Flujo: Clearbit → dominio → Hunter → Snov (fallback)
    Retorna el email encontrado o None.
    Solo actúa si EMAIL_ENRICHMENT_ENABLED=1 en el entorno.
    """
    enabled = os.getenv("EMAIL_ENRICHMENT_ENABLED", "0").strip() in ("1", "true", "yes")
    if not enabled:
        return None
    if not company or not company.strip():
        return None

    domain = get_company_domain(company)
    if not domain:
        _log.debug("enrich_email: no se encontró dominio para '%s'", company)
        return None

    email = _hunter_find_email(domain, first_name=first_name, last_name=last_name)
    if not email:
        # Snov also does domain-level search — only use it when names are available
        # so we don't store emails that belong to other people at the same company.
        if first_name and last_name:
            email = _snov_find_email(domain)
    if email:
        _log.info("enrich_email: '%s %s' @ %s (%s) → %s", first_name, last_name, company, domain, email)
    return email
