from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProxyStats:
    url: str
    total_requests: int = 0
    total_errors: int = 0
    requests_since_last_cooldown: int = 0
    cooldown_until: datetime | None = None

    @property
    def is_available(self) -> bool:
        if self.cooldown_until is None:
            return True
        return datetime.now() > self.cooldown_until

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_errors / self.total_requests

    @property
    def seconds_until_available(self) -> int:
        if self.is_available:
            return 0
        delta = self.cooldown_until - datetime.now()
        return max(0, int(delta.total_seconds()))
