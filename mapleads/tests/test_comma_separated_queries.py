import asyncio
import pytest
from backend.api import routes as routes_mod
from backend.api.schemas import SearchRequest
from backend.storage import database as db


@pytest.mark.asyncio
async def test_run_scrape_job_comma_separated(monkeypatch):
    job_id = "scrape-comma-test"
    await db.create_job(job_id, "abogado, asesoria", "Madrid", total=0, mode="single")

    searched_queries = []

    async def _fake_search(*, query, **_kwargs):
        searched_queries.append(query)
        if query == "abogado":
            return [{"place_id": "p1", "business_name": "Abogado 1", "website": "https://ab1.test"}]
        elif query == "asesoria":
            return [
                {"place_id": "p1", "business_name": "Abogado 1", "website": "https://ab1.test"},  # Duplicate
                {"place_id": "p2", "business_name": "Asesoria 1", "website": "https://as1.test"},
            ]
        return []

    async def _fake_enrich(business):
        return None, "no_website", "no_website", "low"

    monkeypatch.setattr(routes_mod, "_search_unique_businesses", _fake_search)
    monkeypatch.setattr(routes_mod, "_enrich_business_email", _fake_enrich)

    cancel_ev = asyncio.Event()
    req = SearchRequest(query="abogado, asesoria", location="Madrid", max_results=5)

    await routes_mod._run_scrape_job(job_id, req, cancel_ev)

    assert searched_queries == ["abogado", "asesoria"]

    job = await db.get_job(job_id)
    assert job["status"] == "done"
    assert job["total"] == 2  # Deduplicated: p1 and p2 (p1 is shared, counted once)

    leads = await db.get_leads(job_id=job_id)
    assert len(leads) == 2
    place_ids = {lead["place_id"] for lead in leads}
    assert place_ids == {"p1", "p2"}


@pytest.mark.asyncio
async def test_run_multi_locality_comma_separated(monkeypatch):
    job_id = "multi-comma-test"
    locations = ["Gandia", "Valencia"]
    await db.create_job(
        job_id,
        "abogado, asesoria",
        f"{len(locations)} localidades",
        total=0,
        mode="multi_locality",
        total_locations=len(locations),
        emails_target_per_location=5,
    )

    searched_queries = []

    async def _fake_search(*, query, **_kwargs):
        searched_queries.append(query)
        if "abogado" in query:
            return [{"place_id": f"p_ab_{query}", "business_name": "Ab", "website": "https://ab.test"}]
        elif "asesoria" in query:
            return [{"place_id": f"p_as_{query}", "business_name": "As", "website": "https://as.test"}]
        return []

    async def _fake_enrich(business):
        return None, "no_website", "no_website", "low"

    monkeypatch.setattr(routes_mod, "_search_unique_businesses", _fake_search)
    monkeypatch.setattr(routes_mod, "_enrich_business_email", _fake_enrich)

    cancel_ev = asyncio.Event()
    req = SearchRequest(
        mode="multi_locality",
        category_query="abogado, asesoria",
        locations=locations,
        emails_target_per_location=5,
    )

    await routes_mod._run_multi_locality_job(job_id, req, locations, cancel_ev)

    # Searches per location
    assert searched_queries == [
        "abogado Gandia",
        "asesoria Gandia",
        "abogado Valencia",
        "asesoria Valencia",
    ]

    job = await db.get_job(job_id)
    assert job["status"] == "done"
    assert job["progress"] == 4  # 2 leads per location (Gandia, Valencia)

    leads = await db.get_leads(job_id=job_id)
    assert len(leads) == 4


@pytest.mark.asyncio
async def test_search_unique_businesses_resilience_returns_partial_results(monkeypatch):
    from backend.scraper.maps_client import MapsFetchError

    call_count = 0

    async def _fake_search_maps(*, start, **_kwargs):
        nonlocal call_count
        call_count += 1
        if start == 0:
            # First page succeeds: returns 20 businesses to satisfy the len(batch) < 20 check.
            return [{"place_id": f"p_resil_{i}", "business_name": f"B{i}"} for i in range(20)]
        else:
            # Second page fails
            raise MapsFetchError("Rate limited on second page", kind="rate_limited", retryable=True)

    async def _fake_get_recent(*_args, **_kwargs):
        return set()

    monkeypatch.setattr(routes_mod, "search_maps", _fake_search_maps)
    monkeypatch.setattr(routes_mod.db, "get_recent_place_ids", _fake_get_recent)

    cancel_ev = asyncio.Event()
    results = await routes_mod._search_unique_businesses(
        query="dentistas",
        location="Madrid",
        target=40,
        dedupe_days=30,
        cancel_ev=cancel_ev,
    )

    # Should have called search_maps: 1 (success) + 3 retries (fails) = 4 calls total
    assert call_count == 4
    # Should return the 20 results from the first page instead of raising MapsFetchError
    assert len(results) == 20
    assert [r["place_id"] for r in results] == [f"p_resil_{i}" for i in range(20)]

