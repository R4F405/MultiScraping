import aiosqlite
import os
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime

from backend.config.settings import Settings

logger = logging.getLogger(__name__)


def _db_path() -> str:
    path = Settings.DB_PATH
    if path != ":memory:":
        return os.path.abspath(path)
    return path


@asynccontextmanager
async def get_db():
    path = _db_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init_db():
    async with get_db() as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS ig_leads (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id         TEXT,
                instagram_id   TEXT UNIQUE,
                username       TEXT,
                full_name      TEXT,
                email          TEXT,
                email_source   TEXT,
                email_status   TEXT DEFAULT 'pending',
                phone          TEXT,
                website        TEXT,
                bio            TEXT,
                follower_count INTEGER,
                is_business    INTEGER DEFAULT 0,
                source_type    TEXT,
                source_value   TEXT,
                scraped_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ig_skipped (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT UNIQUE,
                instagram_id TEXT,
                reason       TEXT,
                checked_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ig_scrape_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id       TEXT UNIQUE,
                mode         TEXT,
                target       TEXT,
                max_results  INTEGER,
                status       TEXT DEFAULT 'running',
                progress     INTEGER DEFAULT 0,
                total        INTEGER DEFAULT 0,
                emails_found INTEGER DEFAULT 0,
                started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at  DATETIME
            );

            CREATE TABLE IF NOT EXISTS ig_daily_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT,
                mode          TEXT,
                request_count INTEGER DEFAULT 0,
                UNIQUE(date, mode)
            );

            CREATE TABLE IF NOT EXISTS ig_health_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                status     TEXT,
                unauth_ok  INTEGER,
                auth_ok    INTEGER,
                message    TEXT,
                checked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    logger.info("Database initialized at %s", _db_path())


async def get_all_scraped_usernames() -> set[str]:
    async with get_db() as db:
        usernames: set[str] = set()
        async with db.execute("SELECT username FROM ig_leads WHERE username IS NOT NULL") as cur:
            async for row in cur:
                usernames.add(row["username"])
        async with db.execute("SELECT username FROM ig_skipped WHERE username IS NOT NULL") as cur:
            async for row in cur:
                usernames.add(row["username"])
        return usernames


async def upsert_ig_lead(profile: dict, job_id: str, source_type: str, source_value: str):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_leads
                (job_id, instagram_id, username, full_name, email, email_source,
                 phone, website, bio, follower_count, is_business, source_type, source_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instagram_id) DO UPDATE SET
                job_id       = excluded.job_id,
                email        = excluded.email,
                email_source = excluded.email_source,
                scraped_at   = CURRENT_TIMESTAMP
        """, (
            job_id,
            profile.get("instagram_id"),
            profile.get("username"),
            profile.get("full_name"),
            profile.get("email"),
            profile.get("email_source"),
            profile.get("phone"),
            profile.get("website"),
            profile.get("bio"),
            profile.get("follower_count", 0),
            1 if profile.get("is_business") else 0,
            source_type,
            source_value,
        ))
        await db.commit()


async def insert_ig_skipped(username: str, instagram_id: str | None, reason: str):
    async with get_db() as db:
        await db.execute("""
            INSERT OR IGNORE INTO ig_skipped (username, instagram_id, reason)
            VALUES (?, ?, ?)
        """, (username, instagram_id, reason))
        await db.commit()


async def upsert_job(job_id: str, mode: str, target: str, max_results: int):
    async with get_db() as db:
        await db.execute("""
            INSERT OR IGNORE INTO ig_scrape_jobs (job_id, mode, target, max_results, total)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, mode, target, max_results, max_results))
        await db.commit()


async def update_job_progress(job_id: str, progress: int, emails_found: int):
    async with get_db() as db:
        await db.execute("""
            UPDATE ig_scrape_jobs SET progress = ?, emails_found = ? WHERE job_id = ?
        """, (progress, emails_found, job_id))
        await db.commit()


async def finish_job(job_id: str, status: str = "done"):
    async with get_db() as db:
        await db.execute("""
            UPDATE ig_scrape_jobs SET status = ?, finished_at = ? WHERE job_id = ?
        """, (status, datetime.now().isoformat(), job_id))
        await db.commit()


async def get_job(job_id: str) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM ig_scrape_jobs WHERE job_id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_daily_count(mode: str) -> int:
    today = date.today().isoformat()
    async with get_db() as db:
        async with db.execute(
            "SELECT request_count FROM ig_daily_stats WHERE date = ? AND mode = ?",
            (today, mode),
        ) as cur:
            row = await cur.fetchone()
            return row["request_count"] if row else 0


async def get_hourly_count(mode: str) -> int:
    now = datetime.now()
    hour_start = now.replace(minute=0, second=0, microsecond=0).isoformat()
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM ig_leads WHERE source_type = ? AND scraped_at >= ?",
            (mode, hour_start),
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0


async def find_recent_job(mode: str, target: str, within_seconds: int = 15) -> dict | None:
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(seconds=within_seconds)).isoformat()
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM ig_scrape_jobs WHERE mode = ? AND target = ? AND started_at >= ? ORDER BY started_at DESC LIMIT 1",
            (mode, target, cutoff),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_jobs(limit: int = 24) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM ig_scrape_jobs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def increment_daily_count(mode: str):
    today = date.today().isoformat()
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_daily_stats (date, mode, request_count)
            VALUES (?, ?, 1)
            ON CONFLICT(date, mode) DO UPDATE SET request_count = request_count + 1
        """, (today, mode))
        await db.commit()


async def get_all_leads(limit: int = 500, offset: int = 0) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM ig_leads ORDER BY scraped_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_leads_by_job(job_id: str) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM ig_leads WHERE job_id = ? ORDER BY scraped_at DESC",
            (job_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def insert_health_log(status: str, unauth_ok: bool, auth_ok: bool | None, message: str):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_health_log (status, unauth_ok, auth_ok, message)
            VALUES (?, ?, ?, ?)
        """, (
            status,
            1 if unauth_ok else 0,
            (1 if auth_ok else 0) if auth_ok is not None else None,
            message,
        ))
        await db.commit()
