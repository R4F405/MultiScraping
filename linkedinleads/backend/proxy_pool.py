# proxy_pool.py
# Pool de proxies estáticos en round-robin para requests HTTP sin Selenium.
# Carga la lista desde PROXY_LIST en .env (formato CSV: http://user:pass@ip:port,...).
# El proxy de Selenium/Chrome NO usa este pool — viene de la tabla accounts.

import os
import threading
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


class ProxyPool:
    """Round-robin pool de proxies estáticos. Thread-safe."""

    def __init__(self) -> None:
        self._proxies: List[str] = []
        self._index: int = 0
        self._lock = threading.Lock()
        raw = os.getenv("PROXY_LIST", "").strip()
        if raw:
            self._proxies = [p.strip() for p in raw.split(",") if p.strip()]

    def get_next(self) -> Optional[str]:
        """Devuelve el siguiente proxy en round-robin, o None si no hay pool."""
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy

    @property
    def available(self) -> bool:
        return bool(self._proxies)

    def __len__(self) -> int:
        return len(self._proxies)


# Singleton a nivel de módulo
proxy_pool = ProxyPool()
