from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.storage import database as db
from backend.storage.database import init_db

_MIN_DISCOVERY = {
    "source_type": "keyword",
    "source_value": "test",
    "author_id": "live_knownh",
    "author_handle": "knownh",
    "video_id": "v1",
    "description": "",
    "hashtags": [],
    "view_count": 0,
    "like_count": 0,
    "comment_count": 0,
    "share_count": 0,
    "created_at": "2026-01-01T00:00:00Z",
}


@pytest.mark.asyncio
async def test_dedup_skips_enrich_when_lead_has_email(monkeypatch):
    await init_db()
    await db.upsert_lead(
        "prior-job",
        {
            "author_id": "live_knownh",
            "handle": "knownh",
            "email": "already@example.com",
            "phone": None,
            "website": None,
            "source_confidence": 0.5,
        },
    )

    async def force_discovery(*_a, **_kw):
        return [dict(_MIN_DISCOVERY)]

    monkeypatch.setattr("backend.api.routes.run_discovery", force_discovery)
    mock_enrich = AsyncMock(side_effect=AssertionError("enrich no debía ejecutarse"))
    monkeypatch.setattr("backend.api.routes._enrich_item_bounded", mock_enrich)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={"mode": "keyword", "target": "niche x", "max_results": 3, "engine": "mock"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(80):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") != "running":
                break
        data = j.json()

    assert mock_enrich.await_count == 0
    assert data.get("enrichment_count", 0) == 0


@pytest.mark.asyncio
async def test_dedup_skips_enrich_when_recent_tiktok_skipped(monkeypatch):
    await init_db()
    await db.insert_tiktok_skipped("softme", "live_softme", "no_email")

    item = {**_MIN_DISCOVERY, "author_id": "live_softme", "author_handle": "softme"}

    async def force_discovery(*_a, **_kw):
        return [item]

    monkeypatch.setattr("backend.api.routes.run_discovery", force_discovery)
    mock_enrich = AsyncMock(side_effect=AssertionError("enrich no debía ejecutarse"))
    monkeypatch.setattr("backend.api.routes._enrich_item_bounded", mock_enrich)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={"mode": "keyword", "target": "otro nicho", "max_results": 3, "engine": "mock"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(80):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") != "running":
                break

    assert mock_enrich.await_count == 0


@pytest.mark.asyncio
async def test_stale_skipped_not_excluded_from_dedup(monkeypatch):
    await init_db()
    await db.insert_tiktok_skipped("oldh", "live_oldh", "no_email")
    async with db.get_db() as conn:
        await conn.execute(
            "UPDATE tiktok_skipped SET checked_at = ? WHERE handle = ?",
            ("2020-01-01T00:00:00", "oldh"),
        )
        await conn.commit()

    item = {**_MIN_DISCOVERY, "author_id": "live_oldh", "author_handle": "oldh"}

    async def force_discovery(*_a, **_kw):
        return [item]

    monkeypatch.setattr("backend.api.routes.run_discovery", force_discovery)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/tiktok/search",
            json={"mode": "keyword", "target": "nicho z", "max_results": 3, "engine": "mock"},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        for _ in range(80):
            j = await client.get(f"/api/tiktok/jobs/{job_id}")
            if j.json().get("status") != "running":
                break
        data = j.json()

    assert data.get("enrichment_count", 0) >= 1
