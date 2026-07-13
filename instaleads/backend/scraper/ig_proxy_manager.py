import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class _ProxyStat:
    url: str
    requests: int = 0
    errors: int = 0
    cooldown_until: datetime | None = None

    @property
    def available(self) -> bool:
        return self.cooldown_until is None or datetime.now() > self.cooldown_until


class IgProxyManager:
    def __init__(self):
        self._stats: list[_ProxyStat] = []
        self._index: int = 0

    def init(self, proxy_urls: list[str]) -> None:
        self._stats = [_ProxyStat(url=u) for u in proxy_urls]
        logger.info("IgProxyManager: %d proxies cargados", len(self._stats))

    def get_next(self) -> str | None:
        """Round-robin sobre proxies disponibles. None si no hay proxies configurados."""
        if not self._stats:
            return None
        available = [s for s in self._stats if s.available]
        if not available:
            # Todos en cooldown: usar el que antes salga
            earliest = min(self._stats, key=lambda s: s.cooldown_until or datetime.min)
            logger.debug("IgProxyManager: todos en cooldown, usando %s", earliest.url[:35])
            return earliest.url
        stat = available[self._index % len(available)]
        self._index += 1
        stat.requests += 1
        return stat.url

    def get_pinned(self, key: str) -> str | None:
        """Stable proxy for an authenticated session.

        Maps ``key`` (e.g. the account's ds_user_id) deterministically to a
        single proxy, so one logged-in account keeps the same egress IP instead
        of hopping across all of them — the latter is a strong ban signal.

        Only falls back to another proxy when the pinned one is in error
        cooldown; otherwise it always returns the same IP for a given key.
        """
        if not self._stats:
            return None
        idx = int(hashlib.md5(key.encode()).hexdigest(), 16) % len(self._stats)
        stat = self._stats[idx]
        if not stat.available:
            available = [s for s in self._stats if s.available]
            if available:
                stat = available[0]
        stat.requests += 1
        return stat.url

    def report_error(self, proxy_url: str, cooldown_seconds: int = 300) -> None:
        """Marca un proxy como bloqueado durante cooldown_seconds."""
        for s in self._stats:
            if s.url == proxy_url:
                s.errors += 1
                s.cooldown_until = datetime.now() + timedelta(seconds=cooldown_seconds)
                logger.warning(
                    "IgProxyManager: %s en cooldown %ds (total errors=%d)",
                    proxy_url[:35], cooldown_seconds, s.errors,
                )
                return

    def report_success(self, proxy_url: str) -> None:
        """Reduce el contador de errores gradualmente tras un éxito."""
        for s in self._stats:
            if s.url == proxy_url:
                s.errors = max(0, s.errors - 1)
                return

    def status_summary(self) -> list[dict]:
        return [
            {
                "url": s.url[:40],
                "requests": s.requests,
                "errors": s.errors,
                "available": s.available,
                "cooldown_until": s.cooldown_until.isoformat() if s.cooldown_until else None,
            }
            for s in self._stats
        ]


ig_proxy_manager = IgProxyManager()
