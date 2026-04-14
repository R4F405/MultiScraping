"""
Tests del módulo proxy_pool.
Sin llamadas reales a ningún proxy — se valida la lógica de round-robin y thread-safety.
"""
import threading
import os
import sys

import pytest

# Asegurar que el directorio backend está en el path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _make_pool(proxy_list: str):
    """Crea un ProxyPool con la variable de entorno seteada."""
    import importlib
    import proxy_pool as pp_module
    # Recargar el módulo con la variable de entorno mockeada
    from proxy_pool import ProxyPool
    original = os.environ.get("PROXY_LIST", "")
    os.environ["PROXY_LIST"] = proxy_list
    pool = ProxyPool()
    os.environ["PROXY_LIST"] = original
    return pool


def test_proxy_pool_sin_env_devuelve_none():
    pool = _make_pool("")
    assert pool.get_next() is None


def test_proxy_pool_available_false_sin_proxies():
    pool = _make_pool("")
    assert pool.available is False


def test_proxy_pool_available_true_con_proxies():
    pool = _make_pool("http://user:pass@1.2.3.4:8080")
    assert pool.available is True


def test_proxy_pool_un_proxy_siempre_devuelve_el_mismo():
    pool = _make_pool("http://user:pass@1.2.3.4:8080")
    assert pool.get_next() == "http://user:pass@1.2.3.4:8080"
    assert pool.get_next() == "http://user:pass@1.2.3.4:8080"


def test_proxy_pool_round_robin():
    pool = _make_pool("http://a:b@1.1.1.1:80,http://a:b@2.2.2.2:80,http://a:b@3.3.3.3:80")
    assert pool.get_next() == "http://a:b@1.1.1.1:80"
    assert pool.get_next() == "http://a:b@2.2.2.2:80"
    assert pool.get_next() == "http://a:b@3.3.3.3:80"
    # Vuelve al inicio
    assert pool.get_next() == "http://a:b@1.1.1.1:80"


def test_proxy_pool_ignora_entradas_vacias():
    pool = _make_pool("http://a:b@1.1.1.1:80, , http://a:b@2.2.2.2:80,")
    assert len(pool) == 2


def test_proxy_pool_thread_safe():
    pool = _make_pool("http://a:b@1.1.1.1:80,http://a:b@2.2.2.2:80,http://a:b@3.3.3.3:80")
    results = []
    valid = {"http://a:b@1.1.1.1:80", "http://a:b@2.2.2.2:80", "http://a:b@3.3.3.3:80"}

    def worker():
        for _ in range(5):
            results.append(pool.get_next())

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 50
    assert all(r in valid for r in results)
