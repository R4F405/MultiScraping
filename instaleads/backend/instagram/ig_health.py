import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

from backend.config.settings import settings
from backend.instagram.ig_proxy_manager import proxy_manager
from backend.storage import database

_consecutive_errors: int = 0
_last_error: str | None = None
_stage_counters: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "error": 0})
_error_codes: dict[str, int] = defaultdict(int)
_discovery_counters: dict[str, dict[str, int]] = defaultdict(
    lambda: {"requests": 0, "failures": 0, "empty": 0}
)
_latency_ms: dict[str, list[float]] = defaultdict(list)


def record_error(message: str, code: str = "UNCLASSIFIED") -> None:
    global _consecutive_errors, _last_error
    _consecutive_errors += 1
    _last_error = message
    _error_codes[code] += 1


def record_success() -> None:
    global _consecutive_errors
    _consecutive_errors = max(0, _consecutive_errors - 1)


def record_discovery_event(provider: str, success: bool, empty: bool = False) -> None:
    bucket = _discovery_counters[provider]
    bucket["requests"] += 1
    if not success:
        bucket["failures"] += 1
    if empty:
        bucket["empty"] += 1


@contextmanager
def track_stage(stage: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
        _stage_counters[stage]["ok"] += 1
    except Exception:
        _stage_counters[stage]["error"] += 1
        raise
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        samples = _latency_ms[stage]
        samples.append(elapsed)
        if len(samples) > 300:
            del samples[0]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = int((len(sorted_values) - 1) * p)
    return round(sorted_values[idx], 2)


def snapshot_metrics() -> dict:
    return {
        "stages": dict(_stage_counters),
        "error_codes": dict(_error_codes),
        "discovery": dict(_discovery_counters),
        "latency": {
            stage: {
                "p50_ms": _percentile(samples, 0.5),
                "p95_ms": _percentile(samples, 0.95),
                "samples": len(samples),
            }
            for stage, samples in _latency_ms.items()
        },
    }


async def get_health() -> dict:
    today = await database.get_today_stats()
    status = "ok"
    if _consecutive_errors >= 6:
        status = "blocked"
    limits = {
        "max_daily_unauth": settings.max_daily_unauth,
        "max_daily_auth": settings.max_daily_auth,
        "max_hourly_auth": settings.max_hourly_auth,
    }
    return {
        "status": status,
        "session_active": True,
        "unauth_today": 0,
        "auth_today": 0,
        "auth_this_hour": 0,
        "consecutive_errors": _consecutive_errors,
        "last_error": _last_error,
        "proxy_configured": proxy_manager.has_proxy(),
        "discovery_strategies": dict(_discovery_counters),
        "limits": limits,
        "metrics": snapshot_metrics(),
        "leads_today": today["leads"],
    }
