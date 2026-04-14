import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_endpoint(client):
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_search_returns_job_id(client):
    res = await client.post(
        "/api/search",
        json={"query": "test", "location": "Madrid", "max_results": 1},
    )
    assert res.status_code == 200
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_get_job_not_found(client):
    res = await client.get("/api/jobs/nonexistent-job-id")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_get_leads_empty(client):
    res = await client.get("/api/leads?job_id=nonexistent")
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_export_not_found(client):
    res = await client.get("/api/export/nonexistent")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_lead_not_found(client):
    res = await client.delete("/api/leads/99999")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_leads_all_dedupes_by_place_id_keeps_most_recent(client):
    # Arrange: create 2 jobs with the same place_id but different business_name,
    # inserted in sequence so the second one is the "most recent".
    from backend.storage import database as db

    await db.create_job("job-a", "query", "Valencia", total=2)
    await db.create_job("job-b", "query", "Valencia", total=2)

    await db.save_lead(
        {
            "place_id": "place-1",
            "business_name": "First name",
            "address": "addr",
            "phone": "111",
            "website": "http://example.com",
            "email": None,
            "email_status": "pending",
            "category": "Cat",
            "rating": 4.0,
            "maps_url": "http://maps",
        },
        "job-a",
    )

    await db.save_lead(
        {
            "place_id": "place-1",
            "business_name": "Second name",
            "address": "addr",
            "phone": "222",
            "website": "http://example2.com",
            "email": None,
            "email_status": "pending",
            "category": "Cat",
            "rating": 4.5,
            "maps_url": "http://maps2",
        },
        "job-b",
    )

    # Act
    res = await client.get("/api/leads")
    assert res.status_code == 200
    leads = res.json()

    # Assert
    assert isinstance(leads, list)
    # Deduped: only one lead per place_id
    assert len(leads) == 1
    assert leads[0]["place_id"] == "place-1"
    # Most recent by insertion order (scraped_at/id)
    assert leads[0]["business_name"] == "Second name"
