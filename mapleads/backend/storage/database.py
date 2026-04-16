import logging
import os
from datetime import datetime, timezone

import aiosqlite

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_CREATE_LEADS = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT,
    -- NOTE:
    -- `place_id` NO debe ser único globalmente.
    -- Si lo es, cuando el mismo `place_id` aparece en jobs distintos,
    -- SQLite actualiza la fila existente y "mueve" el lead (job_id),
    -- dejando jobs anteriores con menos leads de los que deberían.
    place_id      TEXT,
    business_name TEXT,
    address       TEXT,
    phone         TEXT,
    website       TEXT,
    email         TEXT,
    email_status  TEXT DEFAULT 'pending',
    category      TEXT,
    rating        REAL,
    maps_url      TEXT,
    scraped_at    DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_SCRAPE_JOBS = """
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT UNIQUE,
    query        TEXT,
    location     TEXT,
    status       TEXT DEFAULT 'running',
    progress     INTEGER DEFAULT 0,
    total        INTEGER DEFAULT 0,
    emails_found INTEGER DEFAULT 0,
    mode         TEXT DEFAULT 'single',
    current_location_index INTEGER DEFAULT 0,
    total_locations INTEGER DEFAULT 0,
    current_location_label TEXT,
    current_location_emails_found INTEGER DEFAULT 0,
    emails_target_per_location INTEGER DEFAULT 0,
    started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at  DATETIME
)
"""

_CREATE_JOB_LOCATIONS = """
CREATE TABLE IF NOT EXISTS job_locations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL,
    location_index INTEGER NOT NULL,
    location_label TEXT NOT NULL,
    status         TEXT DEFAULT 'pending',
    emails_found   INTEGER DEFAULT 0,
    leads_found    INTEGER DEFAULT 0,
    started_at     DATETIME,
    finished_at    DATETIME,
    UNIQUE(job_id, location_index)
)
"""

_CREATE_LEADS_PLACE_ID_SCRAPED_AT_IDX = """
CREATE INDEX IF NOT EXISTS idx_leads_place_id_scraped_at
ON leads(place_id, scraped_at)
"""


def _db_path() -> str:
    path = settings.db_path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


async def init_db() -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(_db_path()) as db:
        # Create desired schemas
        await db.execute(_CREATE_LEADS)
        await db.execute(_CREATE_SCRAPE_JOBS)
        await db.execute(_CREATE_JOB_LOCATIONS)
        await db.execute(_CREATE_LEADS_PLACE_ID_SCRAPED_AT_IDX)

        # Additive migrations for scrape_jobs new columns
        scrape_job_columns = {
            "mode": "TEXT DEFAULT 'single'",
            "current_location_index": "INTEGER DEFAULT 0",
            "total_locations": "INTEGER DEFAULT 0",
            "current_location_label": "TEXT",
            "current_location_emails_found": "INTEGER DEFAULT 0",
            "emails_target_per_location": "INTEGER DEFAULT 0",
        }
        async with db.execute("PRAGMA table_info('scrape_jobs')") as c:
            existing_cols = {row[1] for row in await c.fetchall()}
        for col, ddl in scrape_job_columns.items():
            if col not in existing_cols:
                await db.execute(f"ALTER TABLE scrape_jobs ADD COLUMN {col} {ddl}")

        # Lightweight migration:
        # If we previously shipped any UNIQUE constraint on `leads` (e.g. by
        # `place_id`), migrate to the current schema which allows duplicates
        # so pagination matches `scrape_jobs.total`.
        try:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='leads'"
            ) as c:
                leads_exists = await c.fetchone()

            if leads_exists:
                async with db.execute("PRAGMA index_list('leads')") as c:
                    indexes = await c.fetchall()

                # index_list: (seq, name, unique, origin, partial)
                has_any_unique = False
                for idx in indexes:
                    # skip non-unique indexes
                    if len(idx) >= 3 and idx[2] == 1:
                        has_any_unique = True
                        break

                if has_any_unique:
                    await db.execute("ALTER TABLE leads RENAME TO leads_legacy")
                    await db.execute(_CREATE_LEADS)

                    await db.execute(
                        """
                        INSERT INTO leads
                            (id, job_id, place_id, business_name, address, phone, website,
                             email, email_status, category, rating, maps_url, scraped_at)
                        SELECT
                            id, job_id, place_id, business_name, address, phone, website,
                            email, email_status, category, rating, maps_url, scraped_at
                        FROM leads_legacy
                        """
                    )
                    await db.execute("DROP TABLE leads_legacy")

        except Exception as exc:
            logger.warning("Leads schema migration skipped: %s", exc)
        await db.commit()
    logger.info("Database initialized at %s", _db_path())


async def create_job(
    job_id: str,
    query: str,
    location: str,
    total: int,
    *,
    mode: str = "single",
    total_locations: int = 0,
    emails_target_per_location: int = 0,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO scrape_jobs
                (job_id, query, location, total, mode, total_locations, emails_target_per_location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, query, location, total, mode, total_locations, emails_target_per_location),
        )
        await db.commit()


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scrape_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_job_total(job_id: str, total: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE scrape_jobs SET total = ? WHERE job_id = ?",
            (total, job_id),
        )
        await db.commit()


async def update_job_progress(job_id: str, progress: int, emails_found: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE scrape_jobs SET progress = ?, emails_found = ? WHERE job_id = ?",
            (progress, emails_found, job_id),
        )
        await db.commit()


async def update_job_location_progress(
    job_id: str,
    *,
    current_location_index: int,
    total_locations: int,
    current_location_label: str | None,
    current_location_emails_found: int,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE scrape_jobs
            SET current_location_index = ?,
                total_locations = ?,
                current_location_label = ?,
                current_location_emails_found = ?
            WHERE job_id = ?
            """,
            (
                current_location_index,
                total_locations,
                current_location_label,
                current_location_emails_found,
                job_id,
            ),
        )
        await db.commit()


async def create_job_locations(job_id: str, locations: list[str]) -> None:
    if not locations:
        return
    async with aiosqlite.connect(_db_path()) as db:
        await db.executemany(
            """
            INSERT OR IGNORE INTO job_locations (job_id, location_index, location_label, status)
            VALUES (?, ?, ?, 'pending')
            """,
            [(job_id, idx + 1, location) for idx, location in enumerate(locations)],
        )
        await db.commit()


async def start_job_location(job_id: str, location_index: int) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE job_locations
            SET status = 'running', started_at = COALESCE(started_at, ?), finished_at = NULL
            WHERE job_id = ? AND location_index = ?
            """,
            (datetime.now(timezone.utc).isoformat(), job_id, location_index),
        )
        await db.commit()


async def update_job_location_metrics(
    job_id: str,
    location_index: int,
    *,
    emails_found: int,
    leads_found: int,
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE job_locations
            SET emails_found = ?, leads_found = ?
            WHERE job_id = ? AND location_index = ?
            """,
            (emails_found, leads_found, job_id, location_index),
        )
        await db.commit()


async def finish_job_location(job_id: str, location_index: int, status: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE job_locations
            SET status = ?, finished_at = ?
            WHERE job_id = ? AND location_index = ?
            """,
            (status, datetime.now(timezone.utc).isoformat(), job_id, location_index),
        )
        await db.commit()


async def get_job_locations(job_id: str) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM job_locations WHERE job_id = ? ORDER BY location_index ASC",
            (job_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def finish_job(job_id: str, status: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE scrape_jobs SET status = ?, finished_at = ? WHERE job_id = ?",
            (status, datetime.now(timezone.utc).isoformat(), job_id),
        )
        await db.commit()


async def save_lead(lead: dict, job_id: str) -> None:
    """
    Insert a lead.
    Uniqueness is enforced per-job: (job_id, place_id).
    Updates email/email_status if the record already exists without them.
    """
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO leads
                (job_id, place_id, business_name, address, phone, website,
                 email, email_status, category, rating, maps_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                lead.get("place_id"),
                lead.get("business_name"),
                lead.get("address"),
                lead.get("phone"),
                lead.get("website"),
                lead.get("email"),
                lead.get("email_status", "pending"),
                lead.get("category"),
                lead.get("rating"),
                lead.get("maps_url"),
            ),
        )
        await db.commit()


async def get_all_jobs(limit: int = 100) -> list[dict]:
    """Return all scraping jobs ordered by most recent first."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scrape_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_leads_stats() -> dict:
    """Return lead counts grouped by source (for databases page)."""
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute("SELECT COUNT(*) FROM leads") as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
    return {"google_maps": total, "instagram": 0}


async def get_leads(job_id: str | None = None, has_email: bool | None = None) -> list[dict]:
    conditions = []
    params: list = []

    if job_id:
        conditions.append("job_id = ?")
        params.append(job_id)
    if has_email is True:
        conditions.append("email IS NOT NULL AND email != ''")
    elif has_email is False:
        conditions.append("(email IS NULL OR email = '')")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # UI requirement:
    # - When `job_id` is null ("Todos"), we want to avoid duplicates across jobs.
    #   Keep only 1 row per `place_id` (the most recent one).
    # - When `job_id` is provided ("Scrapeos"), keep all rows for that job so
    #   pagination matches `scrape_jobs.total`.
    if job_id is None:
        query = f"""
            WITH filtered AS (
                SELECT * FROM leads {where}
            ),
            ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY COALESCE(place_id, 'NULL_' || id)
                        ORDER BY scraped_at DESC, id DESC
                    ) AS rn
                FROM filtered
            )
            SELECT * FROM ranked
            WHERE rn = 1
            ORDER BY scraped_at DESC, id DESC
        """
    else:
        query = f"SELECT * FROM leads {where} ORDER BY scraped_at DESC, id DESC"

    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def get_recent_place_ids(place_ids: list[str], *, days: int) -> set[str]:
    """
    Return place_ids seen in the last N days.

    Uses chunked IN queries to stay below SQLite variable limits.
    """
    unique_ids = [pid for pid in {str(p).strip() for p in place_ids if p}]
    if not unique_ids or days <= 0:
        return set()

    recent: set[str] = set()
    chunk_size = 800
    threshold = f"-{int(days)} days"

    async with aiosqlite.connect(_db_path()) as db:
        for i in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[i:i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            sql = f"""
                SELECT DISTINCT place_id
                FROM leads
                WHERE place_id IN ({placeholders})
                  AND scraped_at >= datetime('now', ?)
            """
            params = [*chunk, threshold]
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                recent.update(str(row[0]) for row in rows if row and row[0])

    return recent


async def delete_lead(lead_id: int) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        await db.commit()
        return cursor.rowcount > 0
