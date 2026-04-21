import asyncio
import csv
import io
import uuid
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import (
    DorkingRequest,
    FollowersRequest,
    JobResponse,
    LeadOut,
    LimitsUpdate,
    LoginRequest,
    SearchRequest,
)
from backend.config.settings import Settings
from backend.scraper.ig_health import run_health_check
from backend.scraper.ig_session import clear_session, get_authenticated_client, session_info
from backend.storage import database as db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instagram")

# In-memory job registry: job_id → asyncio.Task
_jobs: dict[str, asyncio.Task] = {}


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return await run_health_check()


# ── Session ───────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest):
    try:
        get_authenticated_client(body.username, body.password)
        return {"status": "ok", "message": "Login successful"}
    except RuntimeError as e:
        msg = str(e)
        if "challenge" in msg.lower() or "2fa" in msg.lower():
            return {"status": "2fa_required", "message": msg}
        raise HTTPException(status_code=400, detail=msg)


@router.get("/session")
async def get_session():
    return session_info()


@router.delete("/session")
async def delete_session():
    clear_session()
    return {"status": "cleared"}


# ── Limits ────────────────────────────────────────────────────────────────────

@router.get("/limits")
async def get_limits():
    used_unauth = await db.get_daily_count("unauth")
    used_auth = await db.get_daily_count("auth")
    used_hourly = await db.get_hourly_count("auth")
    unauth_reached = used_unauth >= Settings.IG_LIMIT_DAILY_UNAUTHENTICATED
    auth_daily_reached = used_auth >= Settings.IG_LIMIT_DAILY_AUTHENTICATED
    auth_hourly_reached = used_hourly >= Settings.IG_LIMIT_HOURLY_AUTHENTICATED
    return {
        "daily_unauth": Settings.IG_LIMIT_DAILY_UNAUTHENTICATED,
        "daily_auth": Settings.IG_LIMIT_DAILY_AUTHENTICATED,
        "hourly_auth": Settings.IG_LIMIT_HOURLY_AUTHENTICATED,
        "used_today_unauth": used_unauth,
        "used_today_auth": used_auth,
        "used_this_hour_auth": used_hourly,
        "unauth_daily_reached": unauth_reached,
        "auth_daily_reached": auth_daily_reached,
        "auth_hourly_reached": auth_hourly_reached,
        "can_start_dorking": not unauth_reached,
        "can_start_followers": not auth_daily_reached and not auth_hourly_reached,
    }


@router.put("/limits")
async def update_limits(body: LimitsUpdate):
    if body.daily_unauth is not None:
        Settings.IG_LIMIT_DAILY_UNAUTHENTICATED = body.daily_unauth
    if body.daily_auth is not None:
        Settings.IG_LIMIT_DAILY_AUTHENTICATED = body.daily_auth
    if body.hourly_auth is not None:
        Settings.IG_LIMIT_HOURLY_AUTHENTICATED = body.hourly_auth
    return {"status": "updated"}


# ── Jobs ──────────────────────────────────────────────────────────────────────

def _normalize_job(job: dict) -> dict:
    """Normalize legacy job records so the frontend JS can interpret them correctly."""
    if not job.get("total") and job.get("max_results"):
        job["total"] = job["max_results"]
    # JS expects 'completed' but old records stored 'done'
    if job.get("status") == "done":
        job["status"] = "completed"
    return job


@router.get("/jobs")
async def list_jobs(limit: int = 24):
    jobs = await db.get_all_jobs(limit=limit)
    return [_normalize_job(j) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _normalize_job(job)


# ── Search: Unified endpoint ─────────────────────────────────────────────────

@router.post("/search", response_model=JobResponse)
async def start_search(body: SearchRequest, background_tasks: BackgroundTasks):
    if body.mode == "dorking":
        parts = body.target.split("|", 1)
        niche = parts[0].strip()
        location = parts[1].strip() if len(parts) > 1 else ""
        existing = await db.find_recent_job("dorking", body.target, within_seconds=15)
        if existing:
            return JobResponse(job_id=existing["job_id"], status=existing["status"])
        job_id = str(uuid.uuid4())
        await db.upsert_job(job_id, "dorking", body.target, body.email_goal)
        background_tasks.add_task(_run_dorking_job, niche, location, body.email_goal, job_id)
        return JobResponse(job_id=job_id, status="running")

    if body.mode == "followers":
        info = session_info()
        if not info["logged_in"]:
            raise HTTPException(status_code=401, detail="No active Instagram session. Login first.")
        target = body.target.lstrip("@")
        existing = await db.find_recent_job("followers", target, within_seconds=15)
        if existing:
            return JobResponse(job_id=existing["job_id"], status=existing["status"])
        job_id = str(uuid.uuid4())
        await db.upsert_job(job_id, "followers", target, body.email_goal)
        background_tasks.add_task(_run_followers_job, target, body.email_goal, job_id)
        return JobResponse(job_id=job_id, status="running")

    raise HTTPException(status_code=400, detail=f"Unknown mode: {body.mode}")


# ── Accounts pool (single-session stub) ──────────────────────────────────────

@router.get("/accounts")
async def list_accounts():
    info = session_info()
    if info["logged_in"]:
        return [{"username": info.get("username", ""), "status": "active", "manual_login_required": False}]
    return []


@router.post("/accounts")
async def add_account(body: LoginRequest):
    try:
        get_authenticated_client(body.username, body.password)
        return {"status": "ok", "username": body.username}
    except RuntimeError as e:
        msg = str(e)
        if "challenge" in msg.lower() or "2fa" in msg.lower():
            return {"status": "2fa_required", "message": msg}
        raise HTTPException(status_code=400, detail=msg)


@router.delete("/accounts/{username}")
async def remove_account(username: str):
    info = session_info()
    if info.get("username") == username:
        clear_session()
    return {"status": "removed"}


# ── Search: Dorking (Modo A) ──────────────────────────────────────────────────

async def _run_dorking_job(niche: str, location: str, max_results: int, job_id: str):
    from backend.scraper.ig_dorking import search_and_extract
    try:
        async for _ in search_and_extract(niche, location, max_results, job_id):
            pass
        job = await db.get_job(job_id)
        emails_found = job["emails_found"] if job else 0
        status = "completed" if emails_found >= max_results else "completed_partial"
        await db.finish_job(job_id, status)
    except Exception as e:
        logger.error("Dorking job %s failed: %s", job_id, e)
        await db.finish_job(job_id, "failed")


@router.post("/search/dorking", response_model=JobResponse)
async def start_dorking(body: DorkingRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    target = f"{body.niche}|{body.location}"
    await db.upsert_job(job_id, "dorking", target, body.max_results)
    background_tasks.add_task(
        _run_dorking_job, body.niche, body.location, body.max_results, job_id
    )
    return JobResponse(job_id=job_id, status="running")


# ── Search: Followers (Modo B) ────────────────────────────────────────────────

async def _run_followers_job(target_username: str, max_followers: int, job_id: str):
    from backend.scraper.ig_followers import get_followers_emails
    try:
        async for _ in get_followers_emails(target_username, max_followers, job_id):
            pass
        job = await db.get_job(job_id)
        emails_found = job["emails_found"] if job else 0
        status = "completed" if emails_found >= max_followers else "completed_partial"
        await db.finish_job(job_id, status)
    except Exception as e:
        logger.error("Followers job %s failed: %s", job_id, e)
        await db.finish_job(job_id, "failed")


@router.post("/search/followers", response_model=JobResponse)
async def start_followers(body: FollowersRequest, background_tasks: BackgroundTasks):
    info = session_info()
    if not info["logged_in"]:
        raise HTTPException(status_code=401, detail="No active Instagram session. Login first.")
    job_id = str(uuid.uuid4())
    await db.upsert_job(job_id, "followers", body.target_username, body.max_followers)
    background_tasks.add_task(
        _run_followers_job, body.target_username, body.max_followers, job_id
    )
    return JobResponse(job_id=job_id, status="running")


# ── Leads ─────────────────────────────────────────────────────────────────────

@router.get("/leads")
async def get_leads(limit: int = 200, offset: int = 0, job_id: str | None = None):
    if job_id:
        return await db.get_leads_by_job(job_id)
    return await db.get_all_leads(limit=limit, offset=offset)


@router.get("/leads/job/{job_id}")
async def get_leads_by_job(job_id: str):
    return await db.get_leads_by_job(job_id)


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/export/{job_id}")
async def export_csv(job_id: str):
    leads = await db.get_leads_by_job(job_id)
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found for this job")

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["username", "full_name", "email", "email_source", "phone",
                    "website", "follower_count", "is_business", "source_type", "source_value", "scraped_at"],
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow({k: lead.get(k) for k in writer.fieldnames})

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=instaleads_{job_id[:8]}.csv"},
    )


@router.get("/export")
async def export_all_csv():
    leads = await db.get_all_leads(limit=10000)
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found")

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["username", "full_name", "email", "email_source", "phone",
                    "website", "follower_count", "is_business", "source_type", "source_value", "scraped_at"],
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow({k: lead.get(k) for k in writer.fieldnames})

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=instaleads_all.csv"},
    )
