import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.mark.anyio
async def test_health_available(client):
    resp = await client.get("/api/instagram/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in {"ok", "blocked", "rate_limited"}
    assert "limits" in data
    assert "metrics" in data


@pytest.mark.anyio
async def test_diagnose_shape(client):
    resp = await client.get("/api/instagram/diagnose")
    assert resp.status_code == 200
    data = resp.json()
    assert "blocked" in data
    assert "session_active" in data


@pytest.mark.anyio
async def test_search_creates_real_job(client):
    resp = await client.post(
        "/api/instagram/search",
        json={"mode": "dorking", "target": "dentistas barcelona", "email_goal": 10},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert "job_id" in data


@pytest.mark.anyio
async def test_jobs_list_contains_job(client):
    create = await client.post(
        "/api/instagram/search",
        json={"mode": "dorking", "target": "moda madrid", "email_goal": 5},
    )
    job_id = create.json()["job_id"]
    resp = await client.get("/api/instagram/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert any(j["job_id"] == job_id for j in jobs)


@pytest.mark.anyio
async def test_pipeline_generates_leads(client):
    create = await client.post(
        "/api/instagram/search",
        json={"mode": "followers", "niche": "restaurantes", "location": "valencia", "email_goal": 3},
    )
    assert create.status_code == 200
    job_id = create.json()["job_id"]

    for _ in range(80):
        job = await client.get(f"/api/instagram/jobs/{job_id}")
        assert job.status_code == 200
        payload = job.json()
        if payload["status"] in {"completed", "completed_partial", "failed"}:
            break
        await asyncio.sleep(0.05)
    assert payload["status"] in {"completed", "completed_partial"}

    leads = await client.get(f"/api/instagram/leads?job_id={job_id}&limit=20")
    assert leads.status_code == 200
    data = leads.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.anyio
async def test_login_and_accounts(client):
    resp = await client.post(
        "/api/instagram/login",
        json={"username": "demo", "password": "demo"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    accounts = await client.get("/api/instagram/accounts")
    assert accounts.status_code == 200
    assert any(a["username"] == "demo" for a in accounts.json())


@pytest.mark.anyio
async def test_proxy_status_shape(client):
    resp = await client.get("/api/proxy/status")
    assert resp.status_code == 200
    payload = resp.json()
    assert "available" in payload


@pytest.mark.anyio
async def test_export_csv(client):
    create = await client.post(
        "/api/instagram/search",
        json={"mode": "dorking", "target": "fisioterapeutas madrid", "email_goal": 2},
    )
    job_id = create.json()["job_id"]
    for _ in range(80):
        job = await client.get(f"/api/instagram/jobs/{job_id}")
        if job.json()["status"] in {"completed", "completed_partial", "failed"}:
            break
        await asyncio.sleep(0.05)
    exp = await client.get(f"/api/instagram/export/{job_id}")
    assert exp.status_code == 200
    assert "text/csv" in exp.headers.get("content-type", "")
    assert "username,email,email_source" in exp.text
