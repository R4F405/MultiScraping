import uuid
from datetime import date, datetime, timezone

import aiosqlite

from backend.config.settings import settings

_db_path = settings.db_path


async def init_db() -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS tt_scrape_jobs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id           TEXT NOT NULL UNIQUE,
                target           TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'pending',
                status_detail    TEXT,
                progress         INTEGER DEFAULT 0,
                total            INTEGER DEFAULT 0,
                emails_found     INTEGER DEFAULT 0,
                profiles_scanned INTEGER DEFAULT 0,
                emails_from_bio  INTEGER DEFAULT 0,
                emails_from_web  INTEGER DEFAULT 0,
                skipped_count    INTEGER DEFAULT 0,
                failure_reason   TEXT,
                last_error       TEXT,
                started_at       TEXT NOT NULL,
                finished_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS tt_leads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT NOT NULL,
                username        TEXT NOT NULL,
                nickname        TEXT,
                email           TEXT NOT NULL,
                email_source    TEXT,
                followers_count INTEGER,
                verified        INTEGER DEFAULT 0,
                bio_link        TEXT,
                profile_url     TEXT,
                bio_text        TEXT,
                scraped_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tt_skipped (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL UNIQUE,
                reason      TEXT,
                checked_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tt_daily_stats (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                date     TEXT NOT NULL UNIQUE,
                requests INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_tt_leads_username
                ON tt_leads (username);
            CREATE INDEX IF NOT EXISTS idx_tt_leads_job_id
                ON tt_leads (job_id);
            CREATE INDEX IF NOT EXISTS idx_tt_leads_scraped_at
                ON tt_leads (scraped_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tt_scrape_jobs_started
                ON tt_scrape_jobs (started_at DESC);
        """)

        # Migraciones additive: añadir columnas nuevas sin romper la BD existente
        for col, typedef in [
            ("status_detail", "TEXT"),
            ("profiles_scanned", "INTEGER DEFAULT 0"),
            ("emails_from_bio", "INTEGER DEFAULT 0"),
            ("emails_from_web", "INTEGER DEFAULT 0"),
            ("skipped_count", "INTEGER DEFAULT 0"),
            ("failure_reason", "TEXT"),
            ("last_error", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE tt_scrape_jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # columna ya existe

        for col, typedef in [
            ("nickname", "TEXT"),
            ("bio_text", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE tt_leads ADD COLUMN {col} {typedef}")
            except Exception:
                pass

        await db.commit()


async def create_job(target: str, total: int = 0) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT INTO tt_scrape_jobs (job_id, target, status, total, started_at)"
            " VALUES (?, ?, 'running', ?, ?)",
            (job_id, target, total, now),
        )
        await db.commit()
    return job_id


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tt_scrape_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_job_progress(job_id: str, progress: int, emails_found: int) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "UPDATE tt_scrape_jobs SET progress = ?, emails_found = ? WHERE job_id = ?",
            (progress, emails_found, job_id),
        )
        await db.commit()


async def update_job_fields(job_id: str, **fields: object) -> None:
    allowed = {
        "status",
        "status_detail",
        "progress",
        "total",
        "emails_found",
        "profiles_scanned",
        "emails_from_bio",
        "emails_from_web",
        "skipped_count",
        "failure_reason",
        "last_error",
        "finished_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    keys = list(updates.keys())
    setters = ", ".join(f"{k} = ?" for k in keys)
    values = [updates[k] for k in keys] + [job_id]
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            f"UPDATE tt_scrape_jobs SET {setters} WHERE job_id = ?",
            tuple(values),
        )
        await db.commit()


async def finish_job(job_id: str, status: str = "completed") -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "UPDATE tt_scrape_jobs SET status = ?, finished_at = ? WHERE job_id = ?",
            (status, now, job_id),
        )
        await db.commit()


async def get_all_jobs(limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tt_scrape_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_lead(
    job_id: str,
    username: str,
    nickname: str | None,
    email: str,
    email_source: str | None,
    followers_count: int | None,
    verified: bool,
    bio_link: str | None,
    bio_text: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    profile_url = f"https://www.tiktok.com/@{username}"
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO tt_leads
               (job_id, username, nickname, email, email_source,
                followers_count, verified, bio_link, profile_url, bio_text, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id, username, nickname, email, email_source,
                followers_count, int(verified), bio_link, profile_url, bio_text, now,
            ),
        )
        await db.commit()


async def save_skipped(username: str, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO tt_skipped (username, reason, checked_at) VALUES (?, ?, ?)",
            (username, reason, now),
        )
        await db.commit()


async def get_leads(job_id: str | None = None, limit: int = 500) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if job_id:
            query = "SELECT * FROM tt_leads WHERE job_id = ? ORDER BY scraped_at DESC LIMIT ?"
            params: tuple = (job_id, limit)
        else:
            query = """
                SELECT * FROM tt_leads
                WHERE id IN (
                    SELECT MAX(id) FROM tt_leads GROUP BY username
                )
                ORDER BY scraped_at DESC LIMIT ?
            """
            params = (limit,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_last_lead() -> dict | None:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tt_leads ORDER BY scraped_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_stats() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM tt_leads") as c:
            (leads_count,) = await c.fetchone()
        async with db.execute("SELECT COUNT(*) FROM tt_skipped") as c:
            (skipped_count,) = await c.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM tt_scrape_jobs WHERE status = 'running'"
        ) as c:
            (running_jobs,) = await c.fetchone()
    return {
        "total_leads": leads_count,
        "total_skipped": skipped_count,
        "running_jobs": running_jobs,
    }


async def get_today_stats() -> dict:
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tt_daily_stats WHERE date = ?", (today,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return {"date": today, "requests": 0}


async def increment_daily_stat() -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO tt_daily_stats (date, requests) VALUES (?, 1)
               ON CONFLICT(date) DO UPDATE SET requests = requests + 1""",
            (today,),
        )
        await db.commit()


async def get_all_seen_usernames() -> set[str]:
    seen: set[str] = set()
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT DISTINCT username FROM tt_leads") as c:
            for (u,) in await c.fetchall():
                seen.add(u)
        async with db.execute("SELECT username FROM tt_skipped") as c:
            for (u,) in await c.fetchall():
                seen.add(u)
    return seen
