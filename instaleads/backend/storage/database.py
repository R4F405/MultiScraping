import uuid
from datetime import date, datetime, timezone

import aiosqlite

from backend.config.settings import settings

_db_path = settings.db_path


async def init_db() -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS ig_leads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      TEXT NOT NULL,
                username    TEXT NOT NULL,
                full_name   TEXT,
                email       TEXT NOT NULL,
                email_source TEXT,
                followers_count INTEGER,
                is_business INTEGER DEFAULT 0,
                bio_url     TEXT,
                profile_url TEXT,
                source_type TEXT,
                phone       TEXT,
                business_category TEXT,
                scraped_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ig_skipped (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL UNIQUE,
                reason      TEXT,
                checked_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ig_scrape_jobs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id       TEXT NOT NULL UNIQUE,
                mode         TEXT NOT NULL,
                target       TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                status_detail TEXT,
                progress     INTEGER DEFAULT 0,
                total        INTEGER DEFAULT 0,
                emails_found INTEGER DEFAULT 0,
                next_retry_at TEXT,
                resume_count INTEGER DEFAULT 0,
                profiles_scanned INTEGER DEFAULT 0,
                enrichment_attempts INTEGER DEFAULT 0,
                enrichment_successes INTEGER DEFAULT 0,
                emails_from_ig INTEGER DEFAULT 0,
                emails_from_web INTEGER DEFAULT 0,
                skipped_private INTEGER DEFAULT 0,
                failure_reason TEXT,
                last_error TEXT,
                started_at   TEXT NOT NULL,
                finished_at  TEXT
            );

            CREATE TABLE IF NOT EXISTS ig_daily_stats (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT NOT NULL UNIQUE,
                unauth_requests  INTEGER DEFAULT 0,
                auth_requests    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ig_accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT UNIQUE NOT NULL,
                proxy_url   TEXT,
                status      TEXT NOT NULL DEFAULT 'active',
                added_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ig_account_daily_stats (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                date                TEXT NOT NULL,
                account_username    TEXT NOT NULL,
                auth_requests       INTEGER DEFAULT 0,
                UNIQUE(date, account_username)
            );

            CREATE INDEX IF NOT EXISTS idx_ig_leads_username
                ON ig_leads (username);
            CREATE INDEX IF NOT EXISTS idx_ig_leads_job_id
                ON ig_leads (job_id);
            CREATE INDEX IF NOT EXISTS idx_ig_leads_scraped_at
                ON ig_leads (scraped_at DESC);
            CREATE INDEX IF NOT EXISTS idx_ig_scrape_jobs_started
                ON ig_scrape_jobs (started_at DESC);
        """)

        # Add migration for new columns if they don't exist
        for col, typedef in [("phone", "TEXT"), ("business_category", "TEXT")]:
            try:
                await db.execute(f"ALTER TABLE ig_leads ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

        for col, typedef in [
            ("status_detail", "TEXT"),
            ("next_retry_at", "TEXT"),
            ("resume_count", "INTEGER DEFAULT 0"),
            ("profiles_scanned", "INTEGER DEFAULT 0"),
            ("enrichment_attempts", "INTEGER DEFAULT 0"),
            ("enrichment_successes", "INTEGER DEFAULT 0"),
            ("emails_from_ig", "INTEGER DEFAULT 0"),
            ("emails_from_web", "INTEGER DEFAULT 0"),
            ("skipped_private", "INTEGER DEFAULT 0"),
            ("failure_reason", "TEXT"),
            ("last_error", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE ig_scrape_jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

        await db.commit()


async def create_job(mode: str, target: str, total: int = 0) -> str:
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT INTO ig_scrape_jobs (job_id, mode, target, status, total, started_at)"
            " VALUES (?, ?, ?, 'running', ?, ?)",
            (job_id, mode, target, total, now),
        )
        await db.commit()
    return job_id


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ig_scrape_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_job_progress(job_id: str, progress: int, emails_found: int) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "UPDATE ig_scrape_jobs SET progress = ?, emails_found = ? WHERE job_id = ?",
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
        "next_retry_at",
        "resume_count",
        "profiles_scanned",
        "enrichment_attempts",
        "enrichment_successes",
        "emails_from_ig",
        "emails_from_web",
        "skipped_private",
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
            f"UPDATE ig_scrape_jobs SET {setters} WHERE job_id = ?",
            tuple(values),
        )
        await db.commit()


async def finish_job(job_id: str, status: str = "completed") -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "UPDATE ig_scrape_jobs SET status = ?, finished_at = ? WHERE job_id = ?",
            (status, now, job_id),
        )
        await db.commit()


async def get_all_jobs(limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ig_scrape_jobs ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_lead(
    job_id: str,
    username: str,
    full_name: str | None,
    email: str,
    email_source: str | None,
    followers_count: int | None,
    is_business: bool,
    bio_url: str | None,
    source_type: str,
    phone: str | None = None,
    business_category: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    profile_url = f"https://www.instagram.com/{username}/"
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO ig_leads
               (job_id, username, full_name, email, email_source,
                followers_count, is_business, bio_url, profile_url, source_type, phone, business_category, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id, username, full_name, email, email_source,
                followers_count, int(is_business), bio_url, profile_url, source_type, phone, business_category, now,
            ),
        )
        await db.commit()


async def save_skipped(username: str, reason: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ig_skipped (username, reason, checked_at) VALUES (?, ?, ?)",
            (username, reason, now),
        )
        await db.commit()


async def get_leads(job_id: str | None = None, limit: int = 500) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        if job_id:
            query = "SELECT * FROM ig_leads WHERE job_id = ? ORDER BY scraped_at DESC LIMIT ?"
            params = (job_id, limit)
        else:
            # Deduplicate by username — keep most recent when viewing all leads
            query = """
                SELECT * FROM ig_leads
                WHERE id IN (
                    SELECT MAX(id) FROM ig_leads GROUP BY username
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
            "SELECT * FROM ig_leads ORDER BY scraped_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_stats() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM ig_leads") as c:
            (leads_count,) = await c.fetchone()
        async with db.execute("SELECT COUNT(*) FROM ig_skipped") as c:
            (skipped_count,) = await c.fetchone()
        async with db.execute(
            "SELECT COUNT(*) FROM ig_scrape_jobs WHERE status = 'running'"
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
            "SELECT * FROM ig_daily_stats WHERE date = ?", (today,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return {"date": today, "unauth_requests": 0, "auth_requests": 0}


async def increment_daily_stat(mode: str) -> None:
    today = date.today().isoformat()
    col = "unauth_requests" if mode == "unauth" else "auth_requests"
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            f"""INSERT INTO ig_daily_stats (date, {col}) VALUES (?, 1)
                ON CONFLICT(date) DO UPDATE SET {col} = {col} + 1""",
            (today,),
        )
        await db.commit()


async def save_account(username: str, proxy_url: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ig_accounts (username, proxy_url, status, added_at) VALUES (?, ?, 'active', ?)",
            (username, proxy_url, now),
        )
        await db.commit()


async def get_all_accounts() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ig_accounts ORDER BY added_at ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def delete_account(username: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("DELETE FROM ig_accounts WHERE username = ?", (username,))
        await db.commit()


async def update_account_status(username: str, status: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "UPDATE ig_accounts SET status = ? WHERE username = ?",
            (status, username),
        )
        await db.commit()


async def increment_account_daily_stat(account_username: str) -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """INSERT INTO ig_account_daily_stats (date, account_username, auth_requests) VALUES (?, ?, 1)
               ON CONFLICT(date, account_username) DO UPDATE SET auth_requests = auth_requests + 1""",
            (today, account_username),
        )
        await db.commit()


async def get_account_today_stats(account_username: str) -> dict:
    today = date.today().isoformat()
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ig_account_daily_stats WHERE date = ? AND account_username = ?",
            (today, account_username),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return {"date": today, "account_username": account_username, "auth_requests": 0}


async def get_all_seen_usernames() -> set[str]:
    seen: set[str] = set()
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT DISTINCT username FROM ig_leads") as c:
            for (u,) in await c.fetchall():
                seen.add(u)
        async with db.execute("SELECT username FROM ig_skipped") as c:
            for (u,) in await c.fetchall():
                seen.add(u)
    return seen
