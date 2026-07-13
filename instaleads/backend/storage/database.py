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
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id           TEXT UNIQUE,
                mode             TEXT,
                target           TEXT,
                max_results      INTEGER,
                status           TEXT DEFAULT 'running',
                progress         INTEGER DEFAULT 0,
                total            INTEGER DEFAULT 0,
                emails_found     INTEGER DEFAULT 0,
                profiles_checked INTEGER DEFAULT 0,
                started_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at      DATETIME
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
                message    TEXT,
                checked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Resume point per target account for the authenticated followers
            -- list (Modo B). Instagram's GraphQL cursor lets a new job pick up
            -- where the previous one on the same account left off, instead of
            -- re-walking the same first page every time.
            CREATE TABLE IF NOT EXISTS ig_followers_cursor (
                username     TEXT PRIMARY KEY,
                last_cursor  TEXT,
                collected    INTEGER DEFAULT 0,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Safe migration for existing databases
        for stmt in (
            "ALTER TABLE ig_scrape_jobs ADD COLUMN profiles_checked INTEGER DEFAULT 0",
            "ALTER TABLE ig_leads ADD COLUMN is_private INTEGER DEFAULT 0",
        ):
            try:
                await db.execute(stmt)
                await db.commit()
            except Exception:
                pass  # Column already exists
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


async def get_leads_usernames() -> set[str]:
    """Usernames that already have an email — always hard-skip."""
    async with get_db() as db:
        usernames: set[str] = set()
        async with db.execute("SELECT username FROM ig_leads WHERE username IS NOT NULL") as cur:
            async for row in cur:
                usernames.add(row["username"])
        return usernames


async def get_recent_skipped_usernames(cutoff_iso: str) -> set[str]:
    """Usernames skipped (no email / private) within the cutoff window."""
    async with get_db() as db:
        usernames: set[str] = set()
        async with db.execute(
            "SELECT username FROM ig_skipped WHERE username IS NOT NULL AND checked_at >= ?",
            (cutoff_iso,),
        ) as cur:
            async for row in cur:
                usernames.add(row["username"])
        return usernames


async def upsert_ig_lead(profile: dict, job_id: str, source_type: str, source_value: str):
    """Insert or update a lead.

    ``email_status`` is derived automatically when not given explicitly:
    'skipped' for private accounts, 'found'/'not_found' once enrichment has
    actually run (``email_checked=True``), otherwise left as 'pending' so a
    later enrichment pass (Fase 2) knows which leads still need a look.
    """
    email_checked = bool(profile.get("email_checked"))
    is_private = bool(profile.get("is_private"))
    if is_private:
        email_status = "skipped"
    elif email_checked:
        email_status = "found" if profile.get("email") else "not_found"
    else:
        email_status = "pending"

    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_leads
                (job_id, instagram_id, username, full_name, email, email_source, email_status,
                 phone, website, bio, follower_count, is_business, is_private, source_type, source_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instagram_id) DO UPDATE SET
                full_name      = COALESCE(NULLIF(excluded.full_name, ''), ig_leads.full_name),
                email          = CASE WHEN excluded.email IS NOT NULL AND excluded.email != '' THEN excluded.email ELSE ig_leads.email END,
                email_source   = CASE WHEN excluded.email IS NOT NULL AND excluded.email != '' THEN excluded.email_source ELSE ig_leads.email_source END,
                email_status   = CASE WHEN excluded.email_status != 'pending' THEN excluded.email_status ELSE ig_leads.email_status END,
                phone          = COALESCE(NULLIF(excluded.phone, ''), ig_leads.phone),
                website        = COALESCE(NULLIF(excluded.website, ''), ig_leads.website),
                bio            = COALESCE(NULLIF(excluded.bio, ''), ig_leads.bio),
                follower_count = CASE WHEN excluded.follower_count > 0 THEN excluded.follower_count ELSE ig_leads.follower_count END,
                is_business    = excluded.is_business OR ig_leads.is_business,
                is_private     = excluded.is_private,
                scraped_at     = CURRENT_TIMESTAMP
        """, (
            job_id,
            profile.get("instagram_id"),
            profile.get("username"),
            profile.get("full_name"),
            profile.get("email"),
            profile.get("email_source"),
            email_status,
            profile.get("phone"),
            profile.get("website"),
            profile.get("bio"),
            profile.get("follower_count", 0),
            1 if profile.get("is_business") else 0,
            1 if is_private else 0,
            source_type,
            source_value,
        ))
        await db.commit()


async def get_pending_email_leads(job_id: str) -> list[dict]:
    """Leads from this job still needing an email enrichment pass (Fase 2):
    not private, not already checked."""
    async with get_db() as db:
        async with db.execute(
            "SELECT username, instagram_id FROM ig_leads "
            "WHERE job_id = ? AND is_private = 0 AND email_status = 'pending'",
            (job_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_followers_cursor(username: str) -> str | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT last_cursor FROM ig_followers_cursor WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return row["last_cursor"] if row else None


async def save_followers_cursor(username: str, cursor: str, collected_delta: int = 0) -> None:
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_followers_cursor (username, last_cursor, collected, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(username) DO UPDATE SET
                last_cursor = excluded.last_cursor,
                collected   = ig_followers_cursor.collected + ?,
                updated_at  = CURRENT_TIMESTAMP
        """, (username, cursor, collected_delta, collected_delta))
        await db.commit()


async def reset_followers_cursor(username: str) -> None:
    async with get_db() as db:
        await db.execute("DELETE FROM ig_followers_cursor WHERE username = ?", (username,))
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


async def update_job_progress(job_id: str, progress: int, emails_found: int, profiles_checked: int = 0):
    async with get_db() as db:
        await db.execute("""
            UPDATE ig_scrape_jobs SET progress = ?, emails_found = ?, profiles_checked = ? WHERE job_id = ?
        """, (progress, emails_found, profiles_checked, job_id))
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


async def get_leads_count() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM ig_leads") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


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


async def insert_health_log(status: str, unauth_ok: bool, message: str):
    async with get_db() as db:
        await db.execute("""
            INSERT INTO ig_health_log (status, unauth_ok, message)
            VALUES (?, ?, ?)
        """, (
            status,
            1 if unauth_ok else 0,
            message,
        ))
        await db.commit()
