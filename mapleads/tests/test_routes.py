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
async def test_search_multi_locality_returns_job_id(client):
    res = await client.post(
        "/api/search",
        json={
            "mode": "multi_locality",
            "category_query": "dentistas",
            "locations": ["Valencia, Valencia, España", "Madrid, Madrid, España"],
            "emails_target_per_location": 2,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert "job_id" in data
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_search_multi_locality_requires_locations(client):
    res = await client.post(
        "/api/search",
        json={
            "mode": "multi_locality",
            "category_query": "dentistas",
            "locations": [],
            "emails_target_per_location": 2,
        },
    )
    assert res.status_code == 422


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
async def test_maps_categories_endpoint(client):
    res = await client.get("/api/maps/categories?q=dent&limit=5")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) <= 5
    if data:
        assert "type" in data[0]
        assert "label_es" in data[0]
        assert "label_en" in data[0]
        assert "source" in data[0]
        assert "mapped_place_types" in data[0]


@pytest.mark.asyncio
async def test_maps_categories_meta_endpoint(client):
    res = await client.get("/api/maps/categories/meta")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert "catalog_version" in data
    assert "source_urls" in data
    assert "catalog_types_count" in data


@pytest.mark.asyncio
async def test_maps_categories_sync_status_endpoint(client):
    res = await client.get("/api/maps/categories/sync/status")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert "running" in data


@pytest.mark.asyncio
async def test_maps_categories_sync_report_endpoint(client):
    res = await client.get("/api/maps/categories/sync/report")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, dict)
    assert "catalog_types_count" in data
    assert "hybrid_summary" in data


@pytest.mark.asyncio
async def test_job_locations_endpoint(client):
    from backend.storage import database as db

    await db.create_job(
        "job-locations",
        "dentistas",
        "2 localidades",
        total=0,
        mode="multi_locality",
        total_locations=2,
        emails_target_per_location=3,
    )
    await db.create_job_locations(
        "job-locations",
        ["Valencia, Valencia, España", "Madrid, Madrid, España"],
    )

    res = await client.get("/api/jobs/job-locations/locations")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 2
    assert rows[0]["location_index"] == 1
    assert "location_label" in rows[0]


@pytest.mark.asyncio
async def test_get_job_exposes_multi_locality_progress_fields(client):
    from backend.storage import database as db

    await db.create_job(
        "job-progress",
        "dentistas",
        "2 localidades",
        total=0,
        mode="multi_locality",
        total_locations=2,
        emails_target_per_location=4,
    )
    await db.update_job_location_progress(
        "job-progress",
        current_location_index=1,
        total_locations=2,
        current_location_label="Valencia, Valencia, España",
        current_location_emails_found=2,
    )

    res = await client.get("/api/jobs/job-progress")
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "multi_locality"
    assert data["current_location_index"] == 1
    assert data["total_locations"] == 2
    assert data["current_location_label"] == "Valencia, Valencia, España"
    assert data["current_location_emails_found"] == 2
    assert data["emails_target_per_location"] == 4


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


@pytest.mark.asyncio
async def test_enrich_business_email_skips_social_website(monkeypatch):
    from backend.api import routes

    called = {"n": 0}

    async def _fake_find_email(_url: str):
        called["n"] += 1
        return ["ok@empresa.com"]

    monkeypatch.setattr(routes, "find_email_in_website", _fake_find_email)

    email, status = await routes._enrich_business_email({"website": "https://instagram.com/miempresa"})
    assert email is None
    assert status == "pending"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_enrich_business_email_calls_finder_on_real_website(monkeypatch):
    from backend.api import routes

    called = {"n": 0}

    async def _fake_find_email(_url: str):
        called["n"] += 1
        return ["ok@empresa.com"]

    async def _fake_verify(_email: str):
        return "valid"

    monkeypatch.setattr(routes, "find_email_in_website", _fake_find_email)
    monkeypatch.setattr(routes, "verify_email_mx", _fake_verify)

    email, status = await routes._enrich_business_email({"website": "https://miempresa.com"})
    assert email == "ok@empresa.com"
    assert status == "valid"
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_enrich_business_email_pending_when_mx_invalid(monkeypatch):
    from backend.api import routes

    async def _fake_find(_url: str):
        return ["x@rare-domain-xyz123.com"]

    async def _bad_mx(_email: str):
        return "invalid"

    monkeypatch.setattr(routes, "find_email_in_website", _fake_find)
    monkeypatch.setattr(routes, "verify_email_mx", _bad_mx)

    email, status = await routes._enrich_business_email({"website": "https://miempresa.com"})
    assert email == "x@rare-domain-xyz123.com"
    assert status == "pending"
