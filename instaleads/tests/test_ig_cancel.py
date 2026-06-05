import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

from backend.main import app


@pytest.mark.asyncio
async def test_search_and_extract_exits_immediately_when_stop_preset():
    from backend.scraper import ig_dorking

    stop = asyncio.Event()
    stop.set()
    collected = []

    with (
        patch(
            "backend.scraper.ig_dorking._scrape_serp_all_pages",
            new=AsyncMock(),
        ) as mock_serp,
        patch("backend.scraper.ig_dorking.Deduplicator") as MockDedup,
    ):
        mock_dedup = MagicMock()
        mock_dedup.load_from_db = AsyncMock()
        mock_dedup.should_skip = MagicMock(return_value=False)
        mock_dedup.mark_seen = MagicMock()
        MockDedup.return_value = mock_dedup

        async for p in ig_dorking.search_and_extract(
            "foto", "Valencia", 10, "job-stop-1", stop_event=stop
        ):
            collected.append(p)

    assert collected == []
    mock_serp.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_endpoint_sets_event_for_running_job():
    job_id = "job-cancel-api-1"
    from backend.api import routes as routes_mod

    ev = asyncio.Event()
    async with routes_mod._cancel_registry_lock:
        routes_mod._job_cancel_events[job_id] = ev

    try:
        with patch(
            "backend.api.routes.db.get_job",
            new=AsyncMock(
                return_value={"job_id": job_id, "status": "running"},
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                res = await ac.post(f"/api/instagram/jobs/{job_id}/cancel")
        assert res.status_code == 200
        assert res.json()["detail"] == "cancel_requested"
        assert ev.is_set()
    finally:
        async with routes_mod._cancel_registry_lock:
            routes_mod._job_cancel_events.pop(job_id, None)


@pytest.mark.asyncio
async def test_cancel_endpoint_idempotent_when_already_cancelled():
    job_id = "job-cancel-api-2"
    with patch(
        "backend.api.routes.db.get_job",
        new=AsyncMock(
            return_value={"job_id": job_id, "status": "cancelled"},
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/instagram/jobs/{job_id}/cancel")
    assert res.status_code == 200
    assert res.json()["detail"] == "already_cancelled"


@pytest.mark.asyncio
async def test_cancel_endpoint_409_when_not_running():
    job_id = "job-cancel-api-3"
    with patch(
        "backend.api.routes.db.get_job",
        new=AsyncMock(
            return_value={"job_id": job_id, "status": "completed"},
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/instagram/jobs/{job_id}/cancel")
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_cancel_endpoint_503_without_local_handle():
    job_id = "job-cancel-api-4"
    with patch(
        "backend.api.routes.db.get_job",
        new=AsyncMock(
            return_value={"job_id": job_id, "status": "running"},
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post(f"/api/instagram/jobs/{job_id}/cancel")
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_cancel_endpoint_404():
    with patch("backend.api.routes.db.get_job", new=AsyncMock(return_value=None)):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res = await ac.post("/api/instagram/jobs/does-not-exist/cancel")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_run_dorking_job_finishes_cancelled_when_stop_set():
    from backend.api.routes import _run_dorking_job

    job_id = "job-run-cancel-1"
    stop = asyncio.Event()
    stop.set()

    async def fake_extract(*args, **kwargs):
        if False:  # pragma: no cover
            yield {}

    finish = AsyncMock()
    get_job = AsyncMock(return_value={"emails_found": 0})

    with (
        patch("backend.scraper.ig_dorking.search_and_extract", fake_extract),
        patch("backend.api.routes.db.finish_job", finish),
        patch("backend.api.routes.db.get_job", get_job),
    ):
        await _run_dorking_job("n", "l", 10, job_id, stop)

    finish.assert_called_once_with(job_id, "cancelled")
