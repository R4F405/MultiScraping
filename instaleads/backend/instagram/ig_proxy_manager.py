import random
import time
from dataclasses import dataclass, field

from backend.config.settings import settings


@dataclass
class ProxyStats:
    success: int = 0
    failures: int = 0
    score: float = 100.0
    state: str = "closed"
    cooldown_until: float = 0.0
    last_error: str | None = None
    updated_at: float = field(default_factory=time.time)


class ProxyManager:
    def __init__(self) -> None:
        self._proxies = settings.proxy_list[:]
        if settings.ig_proxy_url and settings.ig_proxy_url not in self._proxies:
            self._proxies.append(settings.ig_proxy_url)
        self._stats = {p: ProxyStats() for p in self._proxies}

    def choose(self) -> str:
        if not self._proxies:
            return ""
        now = time.time()
        healthy = [p for p in self._proxies if self._stats[p].cooldown_until <= now]
        return random.choice(healthy or self._proxies)

    def report_success(self, proxy: str) -> None:
        if not proxy or proxy not in self._stats:
            return
        stat = self._stats[proxy]
        stat.success += 1
        stat.score = min(100.0, stat.score + 4.0)
        stat.state = "closed"
        stat.cooldown_until = 0.0
        stat.last_error = None
        stat.updated_at = time.time()

    def report_failure(self, proxy: str, reason: str) -> None:
        if not proxy or proxy not in self._stats:
            return
        stat = self._stats[proxy]
        stat.failures += 1
        stat.score = max(1.0, stat.score - 10.0)
        stat.last_error = reason
        stat.updated_at = time.time()
        if stat.score <= settings.proxy_open_threshold:
            stat.state = "open"
            stat.cooldown_until = time.time() + settings.proxy_cooldown_seconds
        elif stat.score <= settings.proxy_half_open_threshold:
            stat.state = "half_open"

    def snapshot(self) -> dict:
        return {
            proxy: {
                "score": round(stat.score, 2),
                "state": stat.state,
                "success": stat.success,
                "failures": stat.failures,
                "cooldown_until": stat.cooldown_until,
                "last_error": stat.last_error,
            }
            for proxy, stat in self._stats.items()
        }

    def has_proxy(self) -> bool:
        return bool(self._proxies)


proxy_manager = ProxyManager()
