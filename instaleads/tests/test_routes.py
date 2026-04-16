"""
Integration tests for InstaLeads API routes.
Mocks ig_client.get_profile to avoid real HTTP calls.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from backend.api.schemas import SearchRequest
from backend.api import routes
from backend.config.settings import settings
from backend.main import app
from backend.instagram.ig_rate_limiter import RateLimitExceeded


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ── Health ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_health_returns_ok(client):
    resp = await client.get("/api/instagram/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] in ("ok", "rate_limited", "blocked", "no_session")


@pytest.mark.anyio
async def test_diagnose_returns_structure(client):
    resp = await client.get("/api/instagram/diagnose")
    assert resp.status_code == 200
    data = resp.json()
    assert "blocked" in data
    assert "rate_limited" in data
    assert "session_active" in data


@pytest.mark.anyio
async def test_proxy_status(client):
    resp = await client.get("/api/proxy/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


@pytest.mark.anyio
async def test_limits_uses_public_hourly_accessor(client):
    with patch("backend.instagram.ig_rate_limiter.auth_limiter.count_this_hour", return_value=7):
        resp = await client.get("/api/instagram/limits")
    assert resp.status_code == 200
    data = resp.json()
    assert data["used_this_hour_auth"] == 7


# ── Profile preview ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_profile_lookup_found(client):
    mock_profile = {
        "username": "testuser",
        "full_name": "Test User",
        "biography": "contact@test.com | CEO",
        "bio_url": "https://test.com",
        "is_business_account": True,
        "follower_count": 1200,
        "profile_pic_url": "https://cdn.test/avatar.jpg",
        "email": "contact@test.com",
        "email_source": "bio",
        "is_private": False,
    }
    with patch(
        "backend.instagram.ig_client.get_profile_best",
        new_callable=AsyncMock,
        return_value=mock_profile,
    ):
        resp = await client.get("/api/instagram/profile/testuser")
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "testuser"
    assert data["email"] == "contact@test.com"
    assert data["profile_pic_url"] == "https://cdn.test/avatar.jpg"


@pytest.mark.anyio
async def test_profile_lookup_not_found(client):
    with patch(
        "backend.instagram.ig_client.get_profile_best",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.get("/api/instagram/profile/nonexistent_user_xyz")
    assert resp.status_code == 404


# ── Jobs ───────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_search_creates_job(client):
    with patch(
        "backend.instagram.ig_dorking.find_usernames",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = await client.post(
            "/api/instagram/search",
            json={"mode": "dorking", "target": "dentistas barcelona", "email_goal": 10},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "running"


@pytest.mark.anyio
async def test_run_job_dorking_finishes_completed_partial_when_goal_not_reached():
    request = SearchRequest(mode="dorking", target="fotografo|vigo", email_goal=5)
    with patch("backend.api.routes._run_dorking_job", new_callable=AsyncMock), \
         patch(
             "backend.api.routes.database.get_job",
             new_callable=AsyncMock,
             return_value={"emails_found": 2, "total": 5},
         ), \
         patch("backend.api.routes.database.finish_job", new_callable=AsyncMock) as mock_finish:
        await routes._run_job("job-partial", request)
    mock_finish.assert_awaited_with("job-partial", status="completed_partial")


@pytest.mark.anyio
async def test_run_job_dorking_finishes_completed_when_goal_reached():
    request = SearchRequest(mode="dorking", target="fotografo|vigo", email_goal=5)
    with patch("backend.api.routes._run_dorking_job", new_callable=AsyncMock), \
         patch(
             "backend.api.routes.database.get_job",
             new_callable=AsyncMock,
             return_value={"emails_found": 5, "total": 5},
         ), \
         patch("backend.api.routes.database.finish_job", new_callable=AsyncMock) as mock_finish:
        await routes._run_job("job-complete", request)
    mock_finish.assert_awaited_with("job-complete", status="completed")


@pytest.mark.anyio
async def test_search_rejected_when_hourly_limit_reached(client):
    with patch("backend.storage.database.get_today_stats", new_callable=AsyncMock, return_value={"auth_requests": 1, "unauth_requests": 1}), \
         patch("backend.instagram.ig_rate_limiter.auth_limiter.count_this_hour", return_value=settings.max_auth_hourly), \
         patch("backend.instagram.ig_session.is_logged_in", return_value=True):
        resp = await client.post(
            "/api/instagram/search",
            json={"mode": "followers", "target": "nike", "email_goal": 10},
        )
    assert resp.status_code == 429


@pytest.mark.anyio
async def test_search_followers_requires_session_when_pool_empty(client):
    with patch("backend.api.routes.account_pool.is_empty", return_value=True), \
         patch("backend.instagram.ig_session.is_logged_in", return_value=False):
        resp = await client.post(
            "/api/instagram/search",
            json={"mode": "followers", "target": "nike", "email_goal": 10},
        )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_session_endpoint_falls_back_to_single_session(client):
    with patch("backend.api.routes.account_pool.get_primary_info", return_value=None), \
         patch(
             "backend.api.routes.ig_session.get_session_info",
             return_value={"logged_in": True, "username": "solo_account", "session_age_hours": 1.2},
         ):
        resp = await client.get("/api/instagram/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is True
    assert data["username"] == "solo_account"


@pytest.mark.anyio
async def test_jobs_list(client):
    resp = await client.get("/api/instagram/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_job_not_found(client):
    resp = await client.get("/api/instagram/jobs/nonexistent-job-id")
    assert resp.status_code == 404


# ── Leads ──────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_leads_list(client):
    resp = await client.get("/api/instagram/leads")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.anyio
async def test_leads_filter_by_job(client):
    resp = await client.get("/api/instagram/leads?job_id=nonexistent")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Export ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_export_not_found(client):
    resp = await client.get("/api/instagram/export/nonexistent-job")
    assert resp.status_code == 404


# ── Debug ──────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_debug_last(client):
    resp = await client.get("/api/instagram/debug/last")
    assert resp.status_code == 200
    data = resp.json()
    assert "stats" in data
    assert "last_lead" in data


@pytest.mark.anyio
async def test_dorking_job_stops_early_with_email_goal():
    usernames = [f"user{i}" for i in range(20)]
    processed: list[str] = []

    async def fake_extract(username: str, job_id: str, source_type: str):
        processed.append(username)
        return {"username": username, "email": f"{username}@test.com"}

    async def fake_update(*args, **kwargs):
        return None

    request = SearchRequest(mode="dorking", target="fotografo|valencia", email_goal=1)

    with patch("backend.instagram.ig_dorking.find_usernames", new_callable=AsyncMock, return_value=usernames), \
         patch("backend.instagram.ig_profile.extract_and_save", new_callable=AsyncMock, side_effect=fake_extract), \
         patch("backend.api.routes.database.update_job_progress", new_callable=AsyncMock, side_effect=fake_update), \
         patch("backend.api.routes.deduplicator.is_duplicate", return_value=False):
        await routes._run_dorking_job("job-test", request)

    assert 1 <= len(processed) <= 3
    assert len(processed) < len(usernames)


@pytest.mark.anyio
async def test_dorking_job_marks_discovery_degraded_when_no_results():
    request = SearchRequest(mode="dorking", target="fotografo|valencia", email_goal=3)

    with patch("backend.instagram.ig_dorking.find_usernames", new_callable=AsyncMock, return_value=[]), \
         patch(
             "backend.instagram.ig_dorking.get_last_discovery_report",
             return_value={"google_count": 0, "duckduckgo_count": 0, "hashtag_api_count": 0, "location_api_count": 0, "hashtag_fallback_count": 0, "last_error": "no_results"},
         ), \
         patch("backend.api.routes.database.update_job_progress", new_callable=AsyncMock), \
         patch("backend.api.routes.database.update_job_fields", new_callable=AsyncMock) as mock_update:
        await routes._run_dorking_job("job-test", request)

    captured = [kwargs for _, kwargs in mock_update.call_args_list]
    assert any(item.get("discovery_google") == 0 for item in captured)
    assert any(item.get("failure_reason") == "discovery_degraded" for item in captured)


@pytest.mark.anyio
async def test_followers_job_auto_resumes_on_hourly_limit():
    request = SearchRequest(mode="followers", target="nike", email_goal=2)
    job = {
        "job_id": "job-1",
        "profiles_scanned": 0,
        "progress": 0,
        "emails_found": 0,
        "resume_count": 0,
        "emails_from_ig": 0,
        "emails_from_web": 0,
        "enrichment_attempts": 0,
        "enrichment_successes": 0,
    }

    with patch.object(routes.cfg, "followers_auto_resume_enabled", True), \
         patch.object(routes.cfg, "followers_max_resumes_per_day", 3), \
         patch("backend.api.routes.database.get_job", new_callable=AsyncMock, side_effect=[job, job]), \
         patch("backend.api.routes.database.update_job_fields", new_callable=AsyncMock) as mock_update, \
         patch("backend.api.routes.database.finish_job", new_callable=AsyncMock) as mock_finish, \
         patch("backend.api.routes.asyncio.sleep", new_callable=AsyncMock), \
         patch(
             "backend.instagram.ig_followers.extract_followers_leads",
             new_callable=AsyncMock,
             side_effect=[
                 RateLimitExceeded("Hourly authenticated limit reached (20/h)", retry_after_seconds=1),
                 {"stopped_reason": "goal_reached", "emails_found": 2, "processed": 6},
             ],
         ):
        await routes._run_followers_job("job-1", request)

    statuses = [kwargs.get("status") for _, kwargs in mock_update.call_args_list]
    assert "waiting_rate_window" in statuses
    mock_finish.assert_awaited_with("job-1", status="completed")


@pytest.mark.anyio
async def test_followers_job_daily_limit_finishes_rate_limited():
    request = SearchRequest(mode="followers", target="nike", email_goal=2)
    job = {
        "job_id": "job-1",
        "profiles_scanned": 0,
        "progress": 0,
        "emails_found": 0,
        "resume_count": 0,
        "emails_from_ig": 0,
        "emails_from_web": 0,
        "enrichment_attempts": 0,
        "enrichment_successes": 0,
    }

    with patch.object(routes.cfg, "followers_auto_resume_enabled", True), \
         patch.object(routes.cfg, "followers_max_resumes_per_day", 3), \
         patch("backend.api.routes.database.get_job", new_callable=AsyncMock, return_value=job), \
         patch("backend.api.routes.database.update_job_fields", new_callable=AsyncMock) as mock_update, \
         patch("backend.api.routes.database.finish_job", new_callable=AsyncMock) as mock_finish, \
         patch(
             "backend.instagram.ig_followers.extract_followers_leads",
             new_callable=AsyncMock,
             side_effect=RateLimitExceeded("Daily authenticated limit reached", retry_after_seconds=40000),
         ):
        await routes._run_followers_job("job-1", request)

    statuses = [kwargs.get("status") for _, kwargs in mock_update.call_args_list]
    assert "rate_limited" in statuses
    mock_finish.assert_awaited_with("job-1", status="rate_limited")
