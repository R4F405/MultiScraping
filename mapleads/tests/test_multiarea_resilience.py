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


def test_normalize_locations_does_not_insert_extra_commas():
    raw = ["San Sebastián, Gipuzkoa, España", " San Sebastián ,  Gipuzkoa , España "]
    out = _normalize_locations(raw)
    assert out == ["San Sebastián, Gipuzkoa, España"]

