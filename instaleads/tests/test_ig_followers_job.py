"""Integration tests for the followers job runner and API endpoint."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config.settings import Settings
from backend.main import app
from backend.storage import database as db


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    """Point the DB at a temp file so rows persist across connections.

    The session conftest sets DB_PATH=":memory:", where each aiosqlite
    connection gets its own empty database — unusable for integration tests
    that write in one call and read in another.
    """
    db_file = tmp_path / "ig_test.db"
    monkeypatch.setattr(Settings, "DB_PATH", str(db_file))
    await db.init_db()
    yield


@pytest.mark.asyncio
async def test_run_followers_job_saves_leads(monkeypatch, file_db):
    from backend.api import routes as routes_mod

    job_id = "flw-job-1"
    await db.upsert_job(job_id, "followers", "targetacc", 5)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False):
        assert target == "targetacc"
        for i in range(5):
            yield {
                "instagram_id": str(9000 + i),
                "username": f"follower{i}",
                "full_name": f"Follower {i}",
                "is_private": False,
                "is_verified": i == 0,
            }

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)

    stop = asyncio.Event()
    await routes_mod._run_followers_job("targetacc", 5, False, job_id, stop)

    leads = await db.get_leads_by_job(job_id)
    assert len(leads) == 5
    usernames = {lead["username"] for lead in leads}
    assert usernames == {f"follower{i}" for i in range(5)}

    job = await db.get_job(job_id)
    assert job["status"] == "completed"
    assert job["progress"] == 5


@pytest.mark.asyncio
async def test_run_followers_job_enriches_email(monkeypatch, file_db):
    from backend.api import routes as routes_mod

    job_id = "flw-job-enrich"
    await db.upsert_job(job_id, "followers", "acc2", 2)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False):
        yield {"instagram_id": "1", "username": "withemail", "full_name": "A", "is_private": False}
        yield {"instagram_id": "2", "username": "noemail", "full_name": "B", "is_private": False}

    async def fake_get_profile(username):
        if username == "withemail":
            return {
                "instagram_id": "1", "username": "withemail", "full_name": "A",
                "email": "a@biz.com", "email_source": "business_field",
                "website": "https://biz.com", "follower_count": 1234,
                "is_business": True, "private": False, "bio": "hi",
            }
        return {"instagram_id": "2", "username": "noemail", "email": None, "private": False}

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    monkeypatch.setattr("backend.scraper.ig_profile.get_profile", fake_get_profile)

    stop = asyncio.Event()
    await routes_mod._run_followers_job("acc2", 2, True, job_id, stop)

    leads = {lead["username"]: lead for lead in await db.get_leads_by_job(job_id)}
    assert leads["withemail"]["email"] == "a@biz.com"
    assert leads["withemail"]["website"] == "https://biz.com"
    assert leads["noemail"]["email"] is None

    job = await db.get_job(job_id)
    assert job["emails_found"] == 1


@pytest.mark.asyncio
async def test_run_followers_job_auth_error(monkeypatch, file_db):
    from backend.api import routes as routes_mod
    from backend.scraper.ig_client import IgAuthError

    job_id = "flw-job-auth"
    await db.upsert_job(job_id, "followers", "acc3", 5)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False):
        raise IgAuthError("no session")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)

    stop = asyncio.Event()
    await routes_mod._run_followers_job("acc3", 5, False, job_id, stop)

    job = await db.get_job(job_id)
    assert job["status"] == "auth_required"


@pytest.mark.asyncio
async def test_followers_endpoint_schedules_job(monkeypatch, file_db):
    scheduled = {}

    def fake_schedule(target, max_results, enrich, job_id, reset_cursor=False):
        scheduled["target"] = target
        scheduled["max_results"] = max_results
        scheduled["enrich"] = enrich

    monkeypatch.setattr("backend.api.routes._schedule_followers_job", fake_schedule)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/instagram/search/followers",
            json={"target": "@someaccount", "max_results": 300, "enrich_emails": True},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert scheduled["target"] == "someaccount"  # '@' stripped
    assert scheduled["max_results"] == 300
    assert scheduled["enrich"] is True


@pytest.mark.asyncio
async def test_search_endpoint_followers_mode(monkeypatch, file_db):
    scheduled = {}

    def fake_schedule(target, max_results, enrich, job_id, reset_cursor=False):
        scheduled["target"] = target

    monkeypatch.setattr("backend.api.routes._schedule_followers_job", fake_schedule)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/instagram/search",
            json={"mode": "followers", "target": "@acct", "max_results": 100},
        )

    assert res.status_code == 200
    assert scheduled["target"] == "acct"


@pytest.mark.asyncio
async def test_search_endpoint_followers_requires_target():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/instagram/search",
            json={"mode": "followers", "target": "  "},
        )
    assert res.status_code == 422
