"""Declarative registry of hot-editable rate-limit / anti-ban settings.

Each entry maps an env var name to the ``settings`` instance attribute it
backs. The panel's PUT handler writes overrides to the encrypted store and
calls :func:`apply_all`, which mutates the ``settings`` instance in place —
every call site in the codebase reads ``settings.xxx`` live, so this takes
effect immediately, no restart needed.

Resolution order per key: value in the encrypted store → original .env/default
captured at import time (before any mutation) → never a stale mutated value.
"""

from dataclasses import dataclass
from typing import Literal

from backend.config.settings import settings

TunableType = Literal["int", "float", "bool"]


@dataclass(frozen=True)
class Tunable:
    key: str          # env var name == store key
    attr: str          # attribute name on the settings instance
    type: TunableType
    label: str
    help: str = ""


TUNABLES: list[Tunable] = [
    Tunable("MAX_CONCURRENT_REQUESTS", "max_concurrent_requests", "int",
            "Concurrencia máxima",
            "Peticiones simultáneas a Google Maps."),
    Tunable("REQUEST_DELAY_MIN_SECONDS", "request_delay_min", "float",
            "Pausa mínima entre peticiones (s)"),
    Tunable("REQUEST_DELAY_MAX_SECONDS", "request_delay_max", "float",
            "Pausa máxima entre peticiones (s)"),
    Tunable("MAX_REQUESTS_PER_PROXY_BEFORE_COOLDOWN", "max_requests_per_proxy_before_cooldown", "int",
            "Peticiones por proxy antes de cooldown"),
    Tunable("PROXY_COOLDOWN_SECONDS", "proxy_cooldown_seconds", "int",
            "Cooldown de un proxy (s)"),
    Tunable("ERROR_RATE_THRESHOLD", "error_rate_threshold", "float",
            "Umbral de tasa de error",
            "Si un proxy supera esta proporción de errores, entra en cooldown largo. 0–1."),
    Tunable("HIGH_ERROR_COOLDOWN_SECONDS", "high_error_cooldown_seconds", "int",
            "Cooldown largo por tasa de error alta (s)"),
    Tunable("MAX_REQUESTS_PER_DAY", "max_requests_per_day", "int",
            "Máximo de peticiones al día"),
    Tunable("DEDUPE_DAYS", "dedupe_days", "int",
            "Días de deduplicado",
            "No repetir negocios ya vistos en los últimos N días."),
    Tunable("EMAIL_SCRAPER_FORCE_DIRECT", "email_scraper_force_direct", "bool",
            "Forzar búsqueda de email sin proxy",
            "Útil para diagnosticar problemas de proxy/red."),
]

# Snapshot of the true .env/hard-coded defaults, captured once at import time
# — BEFORE apply_all() ever mutates settings — so clearing a DB override
# always falls back to the real original value, never a previously-applied one.
_ENV_DEFAULTS: dict[str, object] = {t.key: getattr(settings, t.attr) for t in TUNABLES}


def _cast(t: Tunable, raw: str):
    if t.type == "int":
        return int(raw)
    if t.type == "float":
        return float(raw)
    if t.type == "bool":
        return raw.strip().lower() not in ("0", "false", "off", "no", "")
    return raw


def effective_value(t: Tunable):
    from backend.config.settings_store import store

    raw = store.get(t.key)
    if raw is not None:
        try:
            return _cast(t, raw)
        except (TypeError, ValueError):
            pass  # corrupt override — fall through to default
    return _ENV_DEFAULTS[t.key]


def apply_all() -> None:
    """Re-sync every settings.<attr> from store/.env. Call after any change."""
    for t in TUNABLES:
        setattr(settings, t.attr, effective_value(t))


def describe_all() -> dict:
    from backend.config.settings_store import store

    out = {}
    for t in TUNABLES:
        value = effective_value(t)
        source = "db" if store.has(t.key) else "env"
        out[t.key] = {
            "value": value,
            "type": t.type,
            "label": t.label,
            "help": t.help,
            "source": source,
            "default": _ENV_DEFAULTS[t.key],
        }
    return out


def update_many(values: dict[str, str]) -> list[str]:
    """Apply a partial {key: raw_string} update. Empty string clears the override."""
    from backend.config.settings_store import store

    by_key = {t.key: t for t in TUNABLES}
    changed = []
    for key, raw in values.items():
        t = by_key.get(key)
        if t is None:
            continue
        raw = "" if raw is None else str(raw).strip()
        if raw == "":
            store.delete(t.key)
        else:
            _cast(t, raw)  # validate before persisting
            store.set(t.key, raw)
        changed.append(key)
    if changed:
        apply_all()
    return changed


# Apply any pre-existing DB overrides immediately at import (process start).
apply_all()
