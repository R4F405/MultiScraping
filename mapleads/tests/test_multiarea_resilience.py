import pytest

from backend.api.routes import _normalize_locations, _run_multi_locality_job
from backend.api.schemas import SearchRequest
from backend.scraper.maps_client import MapsFetchError
from backend.storage import database as db


@pytest.mark.asyncio
async def test_fetch_cid_list_raises_maps_fetch_error_on_operational_exception(monkeypatch):
    from backend.scraper import maps_client

    async def _no_proxy():
        return None

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(maps_client.proxy_manager, "wait_for_available", _no_proxy)
    # Asegura que `_fetch_cid_list` no toma el early-return por "no proxy"
    # (eso depende de si proxy_manager._stats está vacío o no).
    monkeypatch.setattr(maps_client.proxy_manager, "_stats", {})
    monkeypatch.setattr(maps_client.curl_requests, "get", _raise)

    with pytest.raises(MapsFetchError) as exc:
        await maps_client._fetch_cid_list(query="dentistas", location="Madrid", start=0)

    assert exc.value.kind in ("connection", "unknown")


@pytest.mark.asyncio
async def test_multiarea_marks_locations_failed_and_job_failed_when_all_fail(monkeypatch):
    job_id = "job-multi-all-fail"
    locations = ["Alzira, Valencia, España", "Albacete, Albacete, España", "San Sebastián, Gipuzkoa, España"]

    await db.create_job(
        job_id,
        "gimnasio",
        f"{len(locations)} localidades",
        total=0,
        mode="multi_locality",
        total_locations=len(locations),
        emails_target_per_location=5,
    )

    async def _always_fail(*_args, **_kwargs):
        raise MapsFetchError("proxy down", kind="connection", retryable=True)

    monkeypatch.setattr("backend.api.routes._search_unique_businesses", _always_fail)

    req = SearchRequest(
        mode="multi_locality",
        category_query="gimnasio",
        locations=locations,
        emails_target_per_location=5,
    )

    await _run_multi_locality_job(job_id, req, locations)

    job = await db.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"

    rows = await db.get_job_locations(job_id)
    assert len(rows) == 3
    assert all(r["status"] == "failed" for r in rows)


@pytest.mark.asyncio
async def test_multiarea_finishes_by_companies_even_without_valid_emails(monkeypatch):
    job_id = "job-multi-companies-target"
    locations = ["Valencia, Valencia, España"]
    await db.create_job(
        job_id,
        "fontaneros",
        "1 localidades",
        total=0,
        mode="multi_locality",
        total_locations=1,
        emails_target_per_location=2,
    )

    async def _fake_search_unique_businesses(*_args, **_kwargs):
        return [
            {"place_id": "a", "business_name": "A", "website": "https://a.test"},
            {"place_id": "b", "business_name": "B", "website": "https://b.test"},
        ]

    async def _fake_enrich(_business):
        return None, "pending", "no_visible_email", None

    monkeypatch.setattr("backend.api.routes._search_unique_businesses", _fake_search_unique_businesses)
    monkeypatch.setattr("backend.api.routes._enrich_business_email", _fake_enrich)

    req = SearchRequest(
        mode="multi_locality",
        category_query="fontaneros",
        locations=locations,
        companies_target_per_location=2,
    )
    await _run_multi_locality_job(job_id, req, locations)

    job = await db.get_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["progress"] == 2
    assert job["emails_found"] == 0

def test_normalize_locations_does_not_insert_extra_commas():
    raw = ["San Sebastián, Gipuzkoa, España", " San Sebastián ,  Gipuzkoa , España "]
    out = _normalize_locations(raw)
    assert out == ["San Sebastián, Gipuzkoa, España"]

