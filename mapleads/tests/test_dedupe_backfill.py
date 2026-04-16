import pytest
import aiosqlite

from backend.storage import database as db


@pytest.mark.asyncio
async def test_get_recent_place_ids_filters_by_window():
    job_a = "dedupe-job-a-window"
    job_b = "dedupe-job-b-window"
    await db.create_job(job_a, "gym", "Valencia", total=0)
    await db.create_job(job_b, "gym", "Valencia", total=0)

    await db.save_lead(
        {
            "place_id": "recent-place",
            "business_name": "Recent Gym",
            "address": "addr",
            "phone": "111",
            "website": "https://recent.example",
            "email": None,
            "email_status": "pending",
            "category": "Gym",
            "rating": 4.2,
            "maps_url": "http://maps/recent",
        },
        job_a,
    )

    # Insert an old record with explicit scraped_at older than 30 days.
    async with aiosqlite.connect(db._db_path()) as conn:  # noqa: SLF001 - test helper usage
        await conn.execute(
            """
            INSERT INTO leads
                (job_id, place_id, business_name, address, phone, website,
                 email, email_status, category, rating, maps_url, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '-45 days'))
            """,
            (
                job_b,
                "old-place",
                "Old Gym",
                "addr",
                "222",
                "https://old.example",
                None,
                "pending",
                "Gym",
                3.9,
                "http://maps/old",
            ),
        )
        await conn.commit()

    found = await db.get_recent_place_ids(["recent-place", "old-place", "missing"], days=30)
    assert "recent-place" in found
    assert "old-place" not in found
    assert "missing" not in found

    # Cleanup to keep the shared session DB stable for following tests.
    async with aiosqlite.connect(db._db_path()) as conn:  # noqa: SLF001 - test helper usage
        await conn.execute("DELETE FROM leads WHERE job_id IN (?, ?)", (job_a, job_b))
        await conn.execute("DELETE FROM scrape_jobs WHERE job_id IN (?, ?)", (job_a, job_b))
        await conn.commit()


@pytest.mark.asyncio
async def test_search_unique_businesses_backfills_after_recent_duplicates(monkeypatch):
    from backend.api import routes

    async def _fake_recent_ids(place_ids: list[str], *, days: int) -> set[str]:
        assert days == 30
        return {pid for pid in place_ids if pid.startswith("dup-")}

    async def _fake_search_maps(*, query: str, location: str, start: int, lat=None, lng=None, radius_km=10.0):
        assert query == "gym"
        assert location == "Valencia"
        if start == 0:
            return [{"place_id": f"dup-{i}", "business_name": f"Dup {i}"} for i in range(20)]
        if start == 20:
            return [{"place_id": f"new-{i}", "business_name": f"New {i}"} for i in range(20)]
        return []

    monkeypatch.setattr(routes.db, "get_recent_place_ids", _fake_recent_ids)
    monkeypatch.setattr(routes, "search_maps", _fake_search_maps)

    uniques = await routes._search_unique_businesses(
        query="gym",
        location="Valencia",
        target=5,
        dedupe_days=30,
    )

    assert len(uniques) == 5
    assert all(item["place_id"].startswith("new-") for item in uniques)

