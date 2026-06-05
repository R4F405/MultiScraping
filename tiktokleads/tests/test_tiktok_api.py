import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import backend.api.routes as tiktok_routes
from backend.api.schemas import SearchRequest
from backend.config.settings import Settings
from backend.scraper.tiktok_deduplicator import TikTokDeduplicator
from backend.main import app
from backend.storage import database as db
from backend.storage.database import init_db


@pytest.mark.asyncio
async def test_health_and_search_flow():
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/tiktok/health")
        assert health.status_code == 200
        proxy_stats = await client.get("/api/tiktok/proxy-stats")
        assert proxy_stats.status_code == 200

        r = await client.post(
            "/api/tiktok/search",
            json={"mode": "keyword", "target": "agencia marketing", "max_results": 5, "engine": "mock"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        # Poll rápido para cerrar el job
        for _ in range(40):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") in ("completed", "completed_partial", "failed"):
                break

        leads = await client.get(f"/api/tiktok/leads?job_id={job_id}")
        assert leads.status_code == 200
        assert isinstance(leads.json(), list)
        dbg = await client.get(f"/api/tiktok/debug/{job_id}")
        assert dbg.status_code == 200


@pytest.mark.asyncio
async def test_email_goal_mock_job_finishes():
    """Con engine mock, email_goal>0 debe terminar (completado o parcial) sin colgar."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={
                "mode": "keyword",
                "target": "agencia test",
                "max_results": 60,
                "email_goal": 2,
                "engine": "mock",
            },
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(80):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            st = j.json().get("status")
            if st in ("completed", "completed_partial", "failed"):
                break
        data = j.json()
        assert data.get("status") in ("completed", "completed_partial")
        assert int(data.get("email_goal") or 0) == 2


@pytest.mark.asyncio
async def test_email_goal_exits_when_discovery_stays_empty(monkeypatch):
    """Sin candidatos de discovery, debe cerrar por racha (no quedarse en running)."""

    async def no_items(*_args, **_kwargs):
        return []

    await init_db()
    monkeypatch.setattr("backend.api.routes.run_discovery", no_items)
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS", 2)
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS", 50)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={
                "mode": "keyword",
                "target": "vacío",
                "max_results": 40,
                "email_goal": 3,
                "engine": "mock",
            },
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(40):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") in ("completed", "completed_partial", "failed"):
                break
        data = j.json()
        assert data.get("status") == "completed_partial"
        assert "rondas seguidas" in (data.get("last_error") or "")


@pytest.mark.asyncio
async def test_email_goal_dedup_only_never_increments_no_email_streak(monkeypatch):
    """Si todos los candidatos son dedup, no debe cortar por racha 'sin correo'; acaba por max rondas."""
    await init_db()
    monkeypatch.setattr(TikTokDeduplicator, "should_skip", lambda self, nk: True)
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS", 4)
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS", 2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={
                "mode": "keyword",
                "target": "dedupstreak",
                "max_results": 80,
                "email_goal": 2,
                "engine": "mock",
            },
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(60):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") in ("completed", "completed_partial", "failed"):
                break
        data = j.json()
        assert data.get("status") == "completed_partial"
        err = (data.get("last_error") or "").lower()
        assert "límite de rondas" in err or "limite de rondas" in err
        assert "perfiles analizados pero sin nuevo correo" not in err


@pytest.mark.asyncio
async def test_enrich_timeout_finishes_job(monkeypatch):
    async def bounded_timeout(_client, _item, _job_id):
        raise TimeoutError()

    await init_db()
    monkeypatch.setattr("backend.api.routes._enrich_item_bounded", bounded_timeout)
    monkeypatch.setattr(tiktok_routes._rate_limiter, "wait_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tiktok_routes._rate_limiter,
        "on_retryable_error",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS", 2)
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_DISCOVERY_CHUNK", 3)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={
                "mode": "keyword",
                "target": "agencia marketing",
                "max_results": 1,
                "email_goal": 1,
                "engine": "mock",
            },
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        data = None
        j = None
        for _ in range(200):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.status_code == 404:
                await asyncio.sleep(0.05)
                continue
            assert j.status_code == 200, f"GET /jobs/{job_id}: {j.status_code} {j.text}"
            body = j.json()
            if body.get("status") in ("completed", "completed_partial", "failed"):
                data = body
                break
            await asyncio.sleep(0.05)
        assert data is not None, "el job no terminó o nunca apareció en la base"
        assert data.get("status") == "completed_partial"
        assert int(data.get("error_count") or 0) >= 1
        dbg = await client.get(f"/api/tiktok/debug/{job_id}?limit=50")
        assert dbg.status_code == 200
        ev = " ".join(e.get("message", "") for e in dbg.json().get("events", []))
        assert "timeout" in ev.lower()


@pytest.mark.asyncio
async def test_run_job_honours_cancel_event(monkeypatch):
    item_counter = {"n": 0}

    async def slow_run_discovery(client, mode, target, max_results, job_id, exclude_handles=None):
        await asyncio.sleep(0.12)
        i = item_counter["n"]
        item_counter["n"] += 1
        return [
            {
                "source_type": mode,
                "source_value": target,
                "author_id": f"author_{i}",
                "author_handle": f"cancelx.{i}",
                "video_id": f"video_{i}",
                "description": "dc",
                "hashtags": ["h"],
                "view_count": 100,
                "like_count": 10,
                "comment_count": 1,
                "share_count": 1,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]

    await init_db()
    monkeypatch.setattr(tiktok_routes, "run_discovery", slow_run_discovery)
    monkeypatch.setattr(tiktok_routes._rate_limiter, "wait_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(tiktok_routes._rate_limiter, "on_retryable_error", AsyncMock(return_value=None))
    monkeypatch.setattr(Settings, "TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS", 500)

    job_id = str(uuid.uuid4())
    await db.upsert_job(
        job_id,
        "keyword",
        "cancelw",
        200,
        engine_requested="mock",
        total=50,
        email_goal=50,
    )
    cancel_ev = asyncio.Event()
    tiktok_routes._job_cancel_events[job_id] = cancel_ev
    body = SearchRequest(mode="keyword", target="cancelw", max_results=200, email_goal=50, engine="mock")
    run_task = asyncio.create_task(tiktok_routes._run_job(body, job_id, cancel_ev))
    await asyncio.sleep(0.05)
    cancel_ev.set()
    await asyncio.wait_for(run_task, timeout=90)
    row = await db.get_job(job_id)
    assert row is not None
    assert row["status"] == "cancelled"
    assert "cancelado" in (row.get("last_error") or "").lower()


@pytest.mark.asyncio
async def test_cancel_http_on_running_row():
    await init_db()
    job_id = str(uuid.uuid4())
    await db.upsert_job(
        job_id, "keyword", "zz", 30, engine_requested="mock", total=3, email_goal=3
    )
    ev = asyncio.Event()
    tiktok_routes._job_cancel_events[job_id] = ev
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(f"/api/tiktok/jobs/{job_id}/cancel")
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert ev.is_set()


@pytest.mark.asyncio
async def test_cancel_endpoint_idempotent():
    """Job ya terminado: cancel devuelve ok=false sin 500."""
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={"mode": "keyword", "target": "xx", "max_results": 5, "engine": "mock"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(80):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") in ("completed", "completed_partial", "failed", "cancelled"):
                break
            await asyncio.sleep(0.05)
        c = await client.post(f"/api/tiktok/jobs/{job_id}/cancel")
        assert c.status_code == 200
        body = c.json()
        assert body.get("ok") is False
        assert body.get("reason") == "already_finished"
