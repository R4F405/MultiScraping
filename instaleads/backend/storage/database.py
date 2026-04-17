import aiosqlite

from backend.config.settings import settings

_db_path = settings.db_path


async def init_db() -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS ig_jobs (
                job_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                target TEXT NOT NULL,
                niche TEXT,
                location TEXT,
                language TEXT,
                market TEXT,
                email_goal INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                emails_found INTEGER NOT NULL DEFAULT 0,
                status_detail TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS ig_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                username TEXT NOT NULL,
                full_name TEXT,
                biography TEXT,
                bio_url TEXT,
                follower_count INTEGER DEFAULT 0,
                is_private INTEGER DEFAULT 0,
                source TEXT NOT NULL,
                UNIQUE(job_id, username)
            );

            CREATE TABLE IF NOT EXISTS ig_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                email_source TEXT NOT NULL,
                confidence REAL NOT NULL,
                phone TEXT,
                business_category TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, username, email)
            );

            CREATE TABLE IF NOT EXISTS ig_accounts (
                username TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'active',
                last_login_at TEXT,
                cooldown_until TEXT,
                daily_requests INTEGER NOT NULL DEFAULT 0,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS ig_pipeline_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                level TEXT NOT NULL,
                code TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        await db.commit()


async def create_job(payload: dict) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO ig_jobs (
                job_id, mode, target, niche, location, language, market, email_goal,
                status, progress, total, emails_found, status_detail, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["job_id"],
                payload["mode"],
                payload["target"],
                payload.get("niche"),
                payload.get("location"),
                payload.get("language"),
                payload.get("market"),
                payload["email_goal"],
                payload["status"],
                payload["progress"],
                payload["total"],
                payload["emails_found"],
                payload.get("status_detail"),
                payload["started_at"],
                payload.get("finished_at"),
            ),
        )
        await db.commit()


async def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    keys = list(fields.keys())
    set_clause = ", ".join([f"{k} = ?" for k in keys])
    values = [fields[k] for k in keys] + [job_id]
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(f"UPDATE ig_jobs SET {set_clause} WHERE job_id = ?", values)
        await db.commit()


def _to_job(row: aiosqlite.Row) -> dict:
    return {
        "job_id": row["job_id"],
        "mode": row["mode"],
        "target": row["target"],
        "status": row["status"],
        "progress": row["progress"],
        "total": row["total"],
        "emails_found": row["emails_found"],
        "status_detail": row["status_detail"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM ig_jobs WHERE job_id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return _to_job(row) if row else None


async def list_jobs(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM ig_jobs ORDER BY started_at DESC LIMIT ?",
            (max(1, min(200, limit)),),
        ) as cur:
            rows = await cur.fetchall()
            return [_to_job(r) for r in rows]


async def add_candidate(job_id: str, candidate: dict) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO ig_candidates (
                job_id, username, full_name, biography, bio_url, follower_count, is_private, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                candidate["username"],
                candidate.get("full_name"),
                candidate.get("biography"),
                candidate.get("bio_url"),
                candidate.get("follower_count", 0),
                1 if candidate.get("is_private") else 0,
                candidate.get("source", "internal"),
            ),
        )
        await db.commit()


async def add_lead(job_id: str, lead: dict) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO ig_leads (
                job_id, username, email, email_source, confidence, phone, business_category, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                lead["username"],
                lead["email"],
                lead.get("email_source", "unknown"),
                lead.get("confidence", 0.5),
                lead.get("phone"),
                lead.get("business_category"),
                lead["created_at"],
            ),
        )
        await db.commit()


async def list_leads(job_id: str | None = None, limit: int = 200) -> list[dict]:
    query = """
        SELECT job_id, username, email, email_source, confidence, phone, business_category, created_at
        FROM ig_leads
    """
    values: tuple = ()
    if job_id:
        query += " WHERE job_id = ?"
        values = (job_id,)
    query += " ORDER BY created_at DESC LIMIT ?"
    values = values + (max(1, min(2000, limit)),)
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, values) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def upsert_account(username: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO ig_accounts (username, status)
            VALUES (?, 'active')
            ON CONFLICT(username) DO UPDATE SET status = 'active'
            """,
            (username,),
        )
        await db.commit()


async def list_accounts() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT username, status, last_login_at, cooldown_until, daily_requests, last_error FROM ig_accounts ORDER BY username"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def remove_account(username: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("DELETE FROM ig_accounts WHERE username = ?", (username,))
        await db.commit()


async def add_event(job_id: str, stage: str, level: str, code: str | None, detail: str, created_at: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            """
            INSERT INTO ig_pipeline_events (job_id, stage, level, code, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, stage, level, code, detail, created_at),
        )
        await db.commit()


async def get_today_stats() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM ig_leads WHERE DATE(created_at) = DATE('now')"
        ) as cur:
            leads_today = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM ig_jobs WHERE DATE(started_at) = DATE('now')") as cur:
            jobs_today = (await cur.fetchone())[0]
        return {"leads": leads_today, "jobs": jobs_today}


async def diagnostics_pipeline() -> dict:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM ig_jobs") as cur:
            jobs = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM ig_candidates") as cur:
            discovered = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM ig_leads") as cur:
            leads = (await cur.fetchone())[0]
        conversion = round((leads / discovered) * 100, 2) if discovered else 0.0
        return {
            "jobs_analyzed": jobs,
            "discovered": discovered,
            "profiles_hydrated": discovered,
            "profiles_enriched": leads,
            "emails_validos": leads,
            "conversion_pct": conversion,
        }
