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

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
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

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
        yield {"instagram_id": "1", "username": "withemail", "full_name": "A", "is_private": False}
        yield {"instagram_id": "2", "username": "noemail", "full_name": "B", "is_private": False}

    profile_calls = []

    async def fake_get_profile(username, user_id=None, **kwargs):
        profile_calls.append((username, user_id, kwargs))
        if username == "withemail":
            return {
                "instagram_id": "1", "username": "withemail", "full_name": "A",
                "email": "a@biz.com", "email_source": "public_email",
                "phone": "+34612345678", "phone_source": "public_phone",
                "website": "https://biz.com", "follower_count": 1234,
                "category": "Restaurante", "city": "Valencia",
                "is_business": True, "private": False, "bio": "hi",
            }
        return {"instagram_id": "2", "username": "noemail", "email": None, "private": False}

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    monkeypatch.setattr("backend.scraper.ig_profile.get_profile", fake_get_profile)

    stop = asyncio.Event()
    await routes_mod._run_followers_job("acc2", 2, True, job_id, stop)

    leads = {lead["username"]: lead for lead in await db.get_leads_by_job(job_id)}
    assert leads["withemail"]["email"] == "a@biz.com"
    assert leads["withemail"]["phone"] == "+34612345678"
    assert leads["withemail"]["phone_source"] == "public_phone"
    assert leads["withemail"]["category"] == "Restaurante"
    assert leads["withemail"]["website"] == "https://biz.com"
    assert leads["withemail"]["email_status"] == "found"
    assert leads["noemail"]["email"] is None
    assert leads["noemail"]["email_status"] == "not_found"

    # Enrichment goes through the mobile API, by user id, and a dead session
    # must stop Fase 2 instead of silently degrading.
    assert profile_calls[0][1] == "1"
    assert profile_calls[0][2] == {"mobile": True, "strict_auth": True}

    job = await db.get_job(job_id)
    assert job["emails_found"] == 1
    assert job["phones_found"] == 1
    assert job["enrich_total"] == 2
    assert job["status"] == "completed"


@pytest.mark.asyncio
async def test_run_followers_job_auth_error(monkeypatch, file_db):
    from backend.api import routes as routes_mod
    from backend.scraper.ig_client import IgAuthError

    job_id = "flw-job-auth"
    await db.upsert_job(job_id, "followers", "acc3", 5)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
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


@pytest.mark.asyncio
async def test_fase2_picks_up_pending_leads_from_earlier_jobs(monkeypatch, file_db):
    """Followers collected by an earlier (cancelled/throttled) job on the same
    account but never enriched are finished by the next job and exported
    with it."""
    from backend.api import routes as routes_mod

    await db.upsert_job("old-job", "followers", "acc4", 5)
    await db.upsert_ig_lead(
        {"instagram_id": "41", "username": "leftover"}, job_id="old-job",
        source_type="followers", source_value="acc4",
    )

    job_id = "new-job"
    await db.upsert_job(job_id, "followers", "acc4", 5)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
        yield {"instagram_id": "42", "username": "fresh", "is_private": False}

    async def fake_get_profile(username, user_id=None, **kwargs):
        return {"instagram_id": user_id, "username": username, "email": f"{username}@biz.es",
                "email_source": "public_email", "private": False}

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    monkeypatch.setattr("backend.scraper.ig_profile.get_profile", fake_get_profile)

    await routes_mod._run_followers_job("acc4", 5, True, job_id, asyncio.Event())

    leads = {lead["username"]: lead for lead in await db.get_leads_by_job(job_id)}
    assert set(leads) == {"leftover", "fresh"}
    assert leads["leftover"]["email"] == "leftover@biz.es"
    job = await db.get_job(job_id)
    assert job["emails_found"] == 2


@pytest.mark.asyncio
async def test_fase2_stops_at_daily_profile_cap(monkeypatch, file_db):
    from backend.api import routes as routes_mod

    job_id = "flw-cap"
    await db.upsert_job(job_id, "followers", "acc5", 3)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
        for i in range(3):
            yield {"instagram_id": str(500 + i), "username": f"capped{i}", "is_private": False}

    async def must_not_run(*args, **kwargs):
        raise AssertionError("profile fetched past the daily cap")

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    monkeypatch.setattr("backend.scraper.ig_profile.get_profile", must_not_run)
    monkeypatch.setattr(Settings, "IG_LIMIT_DAILY_PROFILES", 10)
    for _ in range(10):
        await db.increment_daily_count("enrich")

    await routes_mod._run_followers_job("acc5", 3, True, job_id, asyncio.Event())

    job = await db.get_job(job_id)
    assert job["status"] == "completed_partial"
    assert "Límite diario" in job["status_detail"]
    # Nothing was wrongly marked as checked: they stay pending for tomorrow.
    pending = await db.get_pending_email_leads(job_id)
    assert len(pending) == 3


@pytest.mark.asyncio
async def test_fase2_stops_when_session_dies(monkeypatch, file_db):
    from backend.api import routes as routes_mod
    from backend.scraper.ig_client import IgChallengeError

    job_id = "flw-dead"
    await db.upsert_job(job_id, "followers", "acc6", 2)
    calls = []

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, **kwargs):
        for i in range(2):
            yield {"instagram_id": str(600 + i), "username": f"d{i}", "is_private": False}

    async def challenged(username, user_id=None, **kwargs):
        calls.append(username)
        raise IgChallengeError("challenge_required")

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    monkeypatch.setattr("backend.scraper.ig_profile.get_profile", challenged)

    await routes_mod._run_followers_job("acc6", 2, True, job_id, asyncio.Event())

    assert calls == ["d0"]  # stopped at the first failure, no hammering
    job = await db.get_job(job_id)
    assert job["status"] == "auth_required"
    assert "sesión" in job["status_detail"]


@pytest.mark.asyncio
async def test_short_followers_list_is_explained_in_job(monkeypatch, file_db):
    """Instagram capped the list: the job must say so (completed_partial +
    which endpoints were limited), not report a silent 'completed'."""
    from backend.api import routes as routes_mod

    job_id = "flw-capped"
    await db.upsert_job(job_id, "followers", "bigacc", 16000)

    async def fake_scrape(target, amount, stop_event=None, reset_cursor=False, report=None, **kwargs):
        report.update({
            "stop": "sources_exhausted", "unique": 50, "follower_count": 16000,
            "requested": 16000, "job_cap": 50000,
            "sources": [{"source": "v1", "new": 50, "end": "limited"},
                        {"source": "search", "new": 0, "end": "failed"}],
        })
        for i in range(50):
            yield {"instagram_id": str(700 + i), "username": f"c{i}", "is_private": False}

    monkeypatch.setattr("backend.scraper.ig_followers.scrape_followers", fake_scrape)
    await routes_mod._run_followers_job("bigacc", 16000, False, job_id, asyncio.Event())

    job = await db.get_job(job_id)
    assert job["status"] == "completed_partial"
    assert job["progress"] == 50
    assert "Instagram solo devolvió 50 de 16.000" in job["status_detail"]
    assert "búsqueda por letras: +0 (error)" in job["status_detail"]
