import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import routes as routes_mod
from backend.api.schemas import SearchRequest
from backend.main import app
from backend.storage import database as db


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_cancel_http_sets_event_when_running(client):
    job_id = "cancel-http-1"
    await db.create_job(job_id, "dentistas", "Madrid", total=0, mode="single")
    ev = asyncio.Event()
    routes_mod._job_cancel_events[job_id] = ev

    try:
        res = await client.post(f"/api/jobs/{job_id}/cancel")
        assert res.status_code == 200
        data = res.json()
        assert data.get("ok") is True
        assert ev.is_set()
    finally:
        routes_mod._job_cancel_events.pop(job_id, None)


@pytest.mark.asyncio
async def test_cancel_http_already_finished(client):
    job_id = "cancel-http-done"
    await db.create_job(job_id, "q", "loc", total=0, mode="single")
    await db.finish_job(job_id, "done")

    res = await client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is False
    assert data.get("reason") == "already_finished"


@pytest.mark.asyncio
async def test_cancel_http_not_running_locally(client):
    job_id = "cancel-http-nolocal"
    await db.create_job(job_id, "q", "loc", total=0, mode="single")
    # Job is "running" in DB but no in-process event (e.g. after server restart).
    res = await client.post(f"/api/jobs/{job_id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is False
    assert data.get("reason") == "not_running_locally"


@pytest.mark.asyncio
async def test_cancel_http_not_found(client):
    res = await client.post("/api/jobs/doesnotexist/cancel")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_run_scrape_job_cancelled_before_enrich(monkeypatch):
    job_id = "scrape-cancel-pre"
    await db.create_job(job_id, "x", "y", total=0, mode="single")

    async def _fake_search(**_kwargs):
        return [{"place_id": "p1", "business_name": "B1", "website": "https://b1.test"}]

    monkeypatch.setattr(routes_mod, "_search_unique_businesses", _fake_search)

    cancel_ev = asyncio.Event()
    cancel_ev.set()
    req = SearchRequest(query="x", location="y", max_results=5)

    await routes_mod._run_scrape_job(job_id, req, cancel_ev)

    job = await db.get_job(job_id)
    assert job is not None
    assert job["status"] == "cancelled"
    leads = await db.get_leads(job_id=job_id)
    assert len(leads) == 0
    assert job_id not in routes_mod._job_cancel_events


@pytest.mark.asyncio
async def test_run_multi_locality_cancelled_before_first_location(monkeypatch):
    job_id = "multi-cancel-pre"
    locations = ["A", "B"]
    await db.create_job(
        job_id,
        "cat",
        f"{len(locations)} localidades",
        total=0,
        mode="multi_locality",
        total_locations=len(locations),
        emails_target_per_location=2,
    )

    cancel_ev = asyncio.Event()
    cancel_ev.set()
    req = SearchRequest(
        mode="multi_locality",
        category_query="cat",
        locations=locations,
        emails_target_per_location=2,
    )

    await routes_mod._run_multi_locality_job(job_id, req, locations, cancel_ev)

    job = await db.get_job(job_id)
    assert job["status"] == "cancelled"
    rows = await db.get_job_locations(job_id)
    assert all(r["status"] == "pending" for r in rows)
    assert job_id not in routes_mod._job_cancel_events
