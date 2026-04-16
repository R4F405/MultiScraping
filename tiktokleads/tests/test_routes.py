"""
Integration tests for the FastAPI routes.
Mocks _run_job and get_health to avoid starting Playwright.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    with patch(
        "backend.tiktok.tt_health.get_health",
        new=AsyncMock(return_value={
            "status": "ok",
            "requests_today": 0,
            "requests_this_hour": 0,
            "consecutive_errors": 0,
            "last_error": None,
            "proxy_configured": False,
            "headless_mode": True,
            "limits": {"max_daily": 200, "max_per_hour": 40},
        }),
    ):
        r = client.get("/api/tiktok/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_root_returns_service_info(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "TikTokLeads"


# ── Stats & Limits ────────────────────────────────────────────────────────────

def test_stats_returns_dict(client):
    r = client.get("/api/tiktok/stats")
    assert r.status_code == 200
    data = r.json()
    assert "total_leads" in data
    assert "total_skipped" in data


def test_limits_returns_dict(client):
    r = client.get("/api/tiktok/limits")
    assert r.status_code == 200
    data = r.json()
    assert "requests_today" in data
    assert "can_start" in data


# ── Jobs ─────────────────────────────────────────────────────────────────────

def test_list_jobs_empty(client):
    r = client.get("/api/tiktok/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_start_search_creates_job(client):
    with patch("backend.api.routes._run_job", new=AsyncMock()):
        r = client.post(
            "/api/tiktok/search",
            json={"target": "#fotografo", "email_goal": 5, "min_followers": 0},
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "running"


def test_get_job_not_found(client):
    r = client.get("/api/tiktok/jobs/nonexistent-job-id")
    assert r.status_code == 404


def test_concurrent_job_rejected(client):
    """Second POST while job is running should return 409."""
    import backend.api.routes as routes_module
    original = routes_module._job_running
    try:
        routes_module._job_running = True
        r = client.post(
            "/api/tiktok/search",
            json={"target": "#test", "email_goal": 1, "min_followers": 0},
        )
        assert r.status_code == 409
    finally:
        routes_module._job_running = original


# ── Leads & Export ────────────────────────────────────────────────────────────

def test_list_leads_empty(client):
    r = client.get("/api/tiktok/leads")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_export_not_found(client):
    r = client.get("/api/tiktok/export/nonexistent-job-id")
    assert r.status_code == 404


def test_debug_last(client):
    r = client.get("/api/tiktok/debug/last")
    assert r.status_code == 200
    data = r.json()
    assert "stats" in data
