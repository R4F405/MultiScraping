"""Declarative registry of hot-editable rate-limit / anti-ban settings.

Each entry maps an env var name to the ``Settings`` class attribute it backs.
The panel's PUT handler writes overrides to the encrypted store and calls
:func:`apply_all`, which mutates the ``Settings`` class attributes in place —
every call site in the codebase reads ``Settings.XXX`` live, so this takes
effect immediately, no restart needed.

Resolution order per key: value in the encrypted store → original .env/default
captured at import time (before any mutation) → never a stale mutated value.
"""

from dataclasses import dataclass
from typing import Literal

from backend.config.settings import Settings

TunableType = Literal["int", "float", "bool"]


@dataclass(frozen=True)
class Tunable:
    key: str          # env var name == store key
    attr: str          # attribute name on Settings
    type: TunableType
    label: str
    help: str = ""


TUNABLES: list[Tunable] = [
    Tunable("IG_LIMIT_DAILY_FOLLOWERS", "IG_LIMIT_DAILY_FOLLOWERS", "int",
            "Máximo de seguidores al día",
            "Tope diario sumando todas las búsquedas de Seguidores (antibaneo). 0 = sin límite."),
    Tunable("IG_FOLLOWERS_MAX_PER_JOB", "IG_FOLLOWERS_MAX_PER_JOB", "int",
            "Máximo de seguidores por búsqueda",
            "Tope duro para una sola búsqueda, aunque el formulario pida más."),
    Tunable("IG_FOLLOWERS_PAGE_SIZE", "IG_FOLLOWERS_PAGE_SIZE", "int",
            "Seguidores por página",
            "Cuántos seguidores se piden a Instagram en cada página."),
    Tunable("IG_FOLLOWERS_DELAY_MIN", "IG_FOLLOWERS_DELAY_MIN", "float",
            "Pausa mínima entre páginas (s)"),
    Tunable("IG_FOLLOWERS_DELAY_MAX", "IG_FOLLOWERS_DELAY_MAX", "float",
            "Pausa máxima entre páginas (s)"),
    Tunable("IG_FOLLOWERS_REST_EVERY", "IG_FOLLOWERS_REST_EVERY", "int",
            "Descanso largo cada N seguidores",
            "Rompe el ritmo constante de peticiones. 0 = desactivado."),
    Tunable("IG_FOLLOWERS_REST_SECONDS", "IG_FOLLOWERS_REST_SECONDS", "float",
            "Duración del descanso largo (s)"),
    Tunable("IG_LIMIT_DAILY_UNAUTHENTICATED", "IG_LIMIT_DAILY_UNAUTHENTICATED", "int",
            "Máximo diario en modo Dorking (sin sesión)"),
    Tunable("IG_DELAY_UNAUTH_MIN", "IG_DELAY_UNAUTH_MIN", "float",
            "Pausa mínima sin sesión (s)"),
    Tunable("IG_DELAY_UNAUTH_MAX", "IG_DELAY_UNAUTH_MAX", "float",
            "Pausa máxima sin sesión (s)"),
    Tunable("IG_BACKOFF_INITIAL", "IG_BACKOFF_INITIAL", "int",
            "Backoff inicial (s)",
            "Espera tras el primer bloqueo/429 antes de reintentar."),
    Tunable("IG_BACKOFF_MULTIPLIER", "IG_BACKOFF_MULTIPLIER", "int",
            "Multiplicador de backoff",
            "El backoff se multiplica por esto en cada bloqueo consecutivo."),
    Tunable("IG_BACKOFF_MAX", "IG_BACKOFF_MAX", "int",
            "Backoff máximo (s)"),
    Tunable("IG_CONCURRENCY", "IG_CONCURRENCY", "int",
            "Concurrencia en modo Dorking",
            "Peticiones simultáneas al buscar por nicho/ubicación."),
    Tunable("IG_MAX_RETRIES", "IG_MAX_RETRIES", "int",
            "Reintentos máximos por petición"),
    Tunable("IG_PROXY_ERROR_COOLDOWN", "IG_PROXY_ERROR_COOLDOWN", "int",
            "Cooldown de un proxy tras error (s)"),
    Tunable("IG_SESSION_PINNED_PROXY", "IG_SESSION_PINNED_PROXY", "bool",
            "Fijar un proxy por sesión",
            "Recomendado: una cuenta logueada usa siempre el mismo IP en vez de rotar entre todos."),
]

# Snapshot of the true .env/hard-coded defaults, captured once at import time
# — BEFORE apply_all() ever mutates Settings — so clearing a DB override
# always falls back to the real original value, never a previously-applied one.
_ENV_DEFAULTS: dict[str, object] = {t.key: getattr(Settings, t.attr) for t in TUNABLES}


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
    """Re-sync every Settings.<attr> from store/.env. Call after any change."""
    for t in TUNABLES:
        setattr(Settings, t.attr, effective_value(t))


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
