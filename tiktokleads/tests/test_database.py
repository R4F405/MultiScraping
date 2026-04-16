import pytest

from backend.storage import database


@pytest.mark.asyncio
async def test_create_job_returns_uuid():
    job_id = await database.create_job(target="#fotografo", total=10)
    assert len(job_id) == 36  # UUID4
    assert "-" in job_id


@pytest.mark.asyncio
async def test_get_job_returns_none_for_unknown():
    result = await database.get_job("nonexistent-job-id")
    assert result is None


@pytest.mark.asyncio
async def test_get_job_returns_created_job():
    job_id = await database.create_job(target="diseñador", total=5)
    job = await database.get_job(job_id)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["target"] == "diseñador"
    assert job["status"] == "running"
    assert job["total"] == 5


@pytest.mark.asyncio
async def test_save_lead_and_get_leads():
    job_id = await database.create_job(target="#test", total=1)
    await database.save_lead(
        job_id=job_id,
        username="testuser",
        nickname="Test User",
        email="test@example.com",
        email_source="bio",
        followers_count=1000,
        verified=False,
        bio_link=None,
        bio_text="Hola soy test",
    )
    leads = await database.get_leads(job_id=job_id)
    assert len(leads) == 1
    assert leads[0]["username"] == "testuser"
    assert leads[0]["email"] == "test@example.com"
    assert leads[0]["email_source"] == "bio"


@pytest.mark.asyncio
async def test_save_skipped_is_idempotent():
    await database.save_skipped("skipuser1", "no_email")
    await database.save_skipped("skipuser1", "no_email")  # debe ignorar el duplicado
    # Si no lanza excepción, el INSERT OR IGNORE funciona correctamente


@pytest.mark.asyncio
async def test_finish_job_sets_finished_at():
    job_id = await database.create_job(target="#finish_test", total=3)
    await database.finish_job(job_id, status="completed")
    job = await database.get_job(job_id)
    assert job["status"] == "completed"
    assert job["finished_at"] is not None


@pytest.mark.asyncio
async def test_update_job_fields_allowlist():
    job_id = await database.create_job(target="#allowlist_test", total=1)
    # Campo permitido
    await database.update_job_fields(job_id, status_detail="procesando")
    job = await database.get_job(job_id)
    assert job["status_detail"] == "procesando"
    # Campo NO permitido — no debe lanzar excepción, simplemente se ignora
    await database.update_job_fields(job_id, nonexistent_field="value")


@pytest.mark.asyncio
async def test_get_stats_counts():
    stats = await database.get_stats()
    assert "total_leads" in stats
    assert "total_skipped" in stats
    assert "running_jobs" in stats
    assert isinstance(stats["total_leads"], int)


@pytest.mark.asyncio
async def test_increment_daily_stat():
    before = await database.get_today_stats()
    requests_before = before["requests"]
    await database.increment_daily_stat()
    after = await database.get_today_stats()
    assert after["requests"] == requests_before + 1


@pytest.mark.asyncio
async def test_update_job_progress():
    job_id = await database.create_job(target="#progress_test", total=10)
    await database.update_job_progress(job_id, progress=5, emails_found=3)
    job = await database.get_job(job_id)
    assert job["progress"] == 5
    assert job["emails_found"] == 3


@pytest.mark.asyncio
async def test_get_all_jobs_returns_list():
    jobs = await database.get_all_jobs(limit=10)
    assert isinstance(jobs, list)


@pytest.mark.asyncio
async def test_get_all_seen_usernames():
    await database.save_skipped("seenuser_unique_xyz", "test")
    seen = await database.get_all_seen_usernames()
    assert "seenuser_unique_xyz" in seen
