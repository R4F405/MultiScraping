import os
from contextlib import asynccontextmanager
from datetime import datetime
import aiosqlite

from backend.config.settings import Settings


def _norm_handle_for_dedup(h: str | None) -> str:
    """Misma semántica que `tiktok_client._norm_handle_key` (evita import circular)."""
    if not h:
        return ""
    return str(h).strip().lstrip("@").lower()


def _db_path() -> str:
    path = Settings.DB_PATH
    return os.path.abspath(path) if path != ":memory:" else path


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
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS tiktok_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                mode TEXT,
                target TEXT,
                max_results INTEGER,
                status TEXT DEFAULT 'running',
                progress INTEGER DEFAULT 0,
                total INTEGER DEFAULT 0,
                leads_found INTEGER DEFAULT 0,
                discovery_count INTEGER DEFAULT 0,
                enrichment_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                profiles_seen INTEGER DEFAULT 0,
                leads_detected INTEGER DEFAULT 0,
                engine_requested TEXT,
                engine_used TEXT,
                fallback_used INTEGER DEFAULT 0,
                requests_total INTEGER DEFAULT 0,
                blocked_count INTEGER DEFAULT 0,
                captcha_count INTEGER DEFAULT 0,
                proxy_switches INTEGER DEFAULT 0,
                source_success_rate REAL DEFAULT 0.0,
                last_error TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME
            );

            CREATE TABLE IF NOT EXISTS tiktok_discovery_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_value TEXT,
                author_id TEXT NOT NULL,
                author_handle TEXT NOT NULL,
                video_id TEXT,
                discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_id, author_id, video_id)
            );

            CREATE TABLE IF NOT EXISTS tiktok_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id TEXT UNIQUE,
                handle TEXT,
                nickname TEXT,
                bio TEXT,
                followers INTEGER,
                following INTEGER,
                likes INTEGER,
                verified INTEGER DEFAULT 0,
                website TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tiktok_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT UNIQUE,
                author_id TEXT,
                description TEXT,
                hashtags TEXT,
                view_count INTEGER,
                like_count INTEGER,
                comment_count INTEGER,
                share_count INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tiktok_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                author_id TEXT,
                handle TEXT,
                email TEXT,
                phone TEXT,
                website TEXT,
                source_confidence REAL DEFAULT 0.0,
                first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(author_id)
            );

            CREATE TABLE IF NOT EXISTS tiktok_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                stage TEXT,
                level TEXT,
                message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tiktok_proxy_stats (
                proxy TEXT PRIMARY KEY,
                ok INTEGER DEFAULT 0,
                fail INTEGER DEFAULT 0,
                cooldown_left_s INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tiktok_skipped (
                handle TEXT NOT NULL UNIQUE,
                author_id TEXT,
                reason TEXT,
                checked_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for ddl in (
            "ALTER TABLE tiktok_jobs ADD COLUMN profiles_seen INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN leads_detected INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN engine_requested TEXT",
            "ALTER TABLE tiktok_jobs ADD COLUMN engine_used TEXT",
            "ALTER TABLE tiktok_jobs ADD COLUMN fallback_used INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN requests_total INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN blocked_count INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN captcha_count INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN proxy_switches INTEGER DEFAULT 0",
            "ALTER TABLE tiktok_jobs ADD COLUMN source_success_rate REAL DEFAULT 0.0",
            "ALTER TABLE tiktok_jobs ADD COLUMN email_goal INTEGER DEFAULT 0",
        ):
            try:
                await db.execute(ddl)
            except Exception:
                pass
        await db.commit()


async def upsert_job(
    job_id: str,
    mode: str,
    target: str,
    max_results: int,
    engine_requested: str | None = None,
    total: int | None = None,
    email_goal: int = 0,
):
    tot = int(total) if total is not None else int(max_results)
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO tiktok_jobs (job_id, mode, target, max_results, total, engine_requested, email_goal)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, mode, target, max_results, tot, engine_requested, int(email_goal or 0)),
        )
        await db.commit()


async def get_job(job_id: str) -> dict | None:
    async with get_db() as db:
        async with db.execute("SELECT * FROM tiktok_jobs WHERE job_id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_jobs(limit: int = 24) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM tiktok_jobs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def update_job_progress(
    job_id: str,
    progress: int,
    leads_found: int,
    discovery_count: int,
    enrichment_count: int,
    profiles_seen: int | None = None,
    leads_detected: int | None = None,
    error_count: int = 0,
    requests_total: int | None = None,
    blocked_count: int | None = None,
    captcha_count: int | None = None,
    proxy_switches: int | None = None,
    source_success_rate: float | None = None,
):
    async with get_db() as db:
        await db.execute(
            """
            UPDATE tiktok_jobs
            SET progress = ?, leads_found = ?, discovery_count = ?, enrichment_count = ?, error_count = ?,
                profiles_seen = COALESCE(?, profiles_seen),
                leads_detected = COALESCE(?, leads_detected),
                requests_total = COALESCE(?, requests_total),
                blocked_count = COALESCE(?, blocked_count),
                captcha_count = COALESCE(?, captcha_count),
                proxy_switches = COALESCE(?, proxy_switches),
                source_success_rate = COALESCE(?, source_success_rate)
            WHERE job_id = ?
            """,
            (
                progress,
                leads_found,
                discovery_count,
                enrichment_count,
                error_count,
                profiles_seen,
                leads_detected,
                requests_total,
                blocked_count,
                captcha_count,
                proxy_switches,
                source_success_rate,
                job_id,
            ),
        )
        await db.commit()


async def set_job_engine(job_id: str, engine_used: str, fallback_used: bool = False):
    async with get_db() as db:
        await db.execute(
            """
            UPDATE tiktok_jobs
            SET engine_used = ?, fallback_used = ?
            WHERE job_id = ?
            """,
            (engine_used, 1 if fallback_used else 0, job_id),
        )
        await db.commit()


async def finish_job(job_id: str, status: str, last_error: str | None = None):
    async with get_db() as db:
        await db.execute(
            """
            UPDATE tiktok_jobs
            SET status = ?, finished_at = ?, last_error = ?
            WHERE job_id = ?
            """,
            (status, datetime.now().isoformat(), last_error, job_id),
        )
        await db.commit()


async def add_job_event(job_id: str, stage: str, level: str, message: str):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO tiktok_job_events (job_id, stage, level, message)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, stage, level, message),
        )
        await db.commit()


async def insert_discovery_item(job_id: str, item: dict):
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO tiktok_discovery_items
            (job_id, source_type, source_value, author_id, author_handle, video_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                item.get("source_type", "keyword"),
                item.get("source_value"),
                item.get("author_id"),
                item.get("author_handle"),
                item.get("video_id"),
            ),
        )
        await db.commit()


async def upsert_profile(profile: dict):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO tiktok_profiles
            (author_id, handle, nickname, bio, followers, following, likes, verified, website, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(author_id) DO UPDATE SET
              handle=excluded.handle,
              nickname=excluded.nickname,
              bio=excluded.bio,
              followers=excluded.followers,
              following=excluded.following,
              likes=excluded.likes,
              verified=excluded.verified,
              website=excluded.website,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                profile.get("author_id"),
                profile.get("handle"),
                profile.get("nickname"),
                profile.get("bio"),
                profile.get("followers", 0),
                profile.get("following", 0),
                profile.get("likes", 0),
                1 if profile.get("verified") else 0,
                profile.get("website"),
            ),
        )
        await db.commit()


async def upsert_video(video: dict):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO tiktok_videos
            (video_id, author_id, description, hashtags, view_count, like_count, comment_count, share_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
              description=excluded.description,
              hashtags=excluded.hashtags,
              view_count=excluded.view_count,
              like_count=excluded.like_count,
              comment_count=excluded.comment_count,
              share_count=excluded.share_count
            """,
            (
                video.get("video_id"),
                video.get("author_id"),
                video.get("description"),
                ",".join(video.get("hashtags", [])),
                video.get("view_count", 0),
                video.get("like_count", 0),
                video.get("comment_count", 0),
                video.get("share_count", 0),
                video.get("created_at"),
            ),
        )
        await db.commit()


async def get_lead_handles_with_email_norms() -> set[str]:
    """Handles normalizados que ya tienen fila en tiktok_leads con email no vacío (hard-skip)."""
    async with get_db() as db:
        out: set[str] = set()
        async with db.execute(
            """
            SELECT handle FROM tiktok_leads
            WHERE email IS NOT NULL AND TRIM(email) != ''
            """
        ) as cur:
            async for row in cur:
                nk = _norm_handle_for_dedup(row["handle"])
                if nk:
                    out.add(nk)
        return out


async def get_recent_skipped_handle_norms(cutoff_iso: str) -> set[str]:
    """Handles en tiktok_skipped con checked_at >= cutoff (soft-skip temporal)."""
    async with get_db() as db:
        out: set[str] = set()
        async with db.execute(
            """
            SELECT handle FROM tiktok_skipped
            WHERE handle IS NOT NULL AND checked_at >= ?
            """,
            (cutoff_iso,),
        ) as cur:
            async for row in cur:
                nk = _norm_handle_for_dedup(row["handle"])
                if nk:
                    out.add(nk)
        return out


async def insert_tiktok_skipped(handle: str, author_id: str | None, reason: str):
    """Registra perfil ya enriquecido sin email (INSERT OR IGNORE, como ig_skipped)."""
    key = _norm_handle_for_dedup(handle)
    if not key:
        return
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO tiktok_skipped (handle, author_id, reason)
            VALUES (?, ?, ?)
            """,
            (key, author_id, reason),
        )
        await db.commit()


async def upsert_lead(job_id: str, lead: dict):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO tiktok_leads (job_id, author_id, handle, email, phone, website, source_confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
              job_id=excluded.job_id,
              handle=excluded.handle,
              email=COALESCE(excluded.email, tiktok_leads.email),
              phone=COALESCE(excluded.phone, tiktok_leads.phone),
              website=COALESCE(excluded.website, tiktok_leads.website),
              source_confidence=MAX(excluded.source_confidence, tiktok_leads.source_confidence),
              last_seen_at=CURRENT_TIMESTAMP
            """,
            (
                job_id,
                lead.get("author_id"),
                lead.get("handle"),
                lead.get("email"),
                lead.get("phone"),
                lead.get("website"),
                lead.get("source_confidence", 0.0),
            ),
        )
        await db.commit()


async def get_all_leads(limit: int = 200, offset: int = 0) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT
              l.*,
              COALESCE(p.followers, 0) AS followers
            FROM tiktok_leads l
            LEFT JOIN tiktok_profiles p ON p.author_id = l.author_id
            WHERE TRIM(COALESCE(l.email, '')) != ''
            ORDER BY l.last_seen_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_leads_by_job(job_id: str) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT
              l.*,
              COALESCE(p.followers, 0) AS followers
            FROM tiktok_leads l
            LEFT JOIN tiktok_profiles p ON p.author_id = l.author_id
            WHERE l.job_id = ? AND TRIM(COALESCE(l.email, '')) != ''
            ORDER BY l.last_seen_at DESC
            """,
            (job_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def count_leads() -> int:
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM tiktok_leads WHERE TRIM(COALESCE(email, '')) != ''"
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def get_job_events(job_id: str, limit: int = 200) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM tiktok_job_events
            WHERE job_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (job_id, limit),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def save_proxy_stats(items: list[dict]):
    async with get_db() as db:
        for item in items:
            await db.execute(
                """
                INSERT INTO tiktok_proxy_stats (proxy, ok, fail, cooldown_left_s, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(proxy) DO UPDATE SET
                  ok = excluded.ok,
                  fail = excluded.fail,
                  cooldown_left_s = excluded.cooldown_left_s,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item.get("proxy"),
                    int(item.get("ok", 0)),
                    int(item.get("fail", 0)),
                    int(item.get("cooldown_left_s", 0)),
                ),
            )
        await db.commit()


async def load_proxy_stats() -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT proxy, ok, fail, cooldown_left_s FROM tiktok_proxy_stats ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
