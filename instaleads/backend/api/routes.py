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
    JobResponse,
    LimitsUpdate,
    SearchRequest,
)
from backend.config.settings import Settings
from backend.scraper.ig_health import run_health_check
from backend.storage import database as db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instagram")

# In-memory job registry: job_id → asyncio.Task
_jobs: dict[str, asyncio.Task] = {}

# Prevent concurrent dorking jobs from flooding proxies and crashing
_dorking_lock = asyncio.Lock()


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return await run_health_check()


# ── Limits ────────────────────────────────────────────────────────────────────

@router.get("/limits")
async def get_limits():
    used_unauth = await db.get_daily_count("unauth")
    unauth_reached = used_unauth >= Settings.IG_LIMIT_DAILY_UNAUTHENTICATED
    return {
        "daily_unauth": Settings.IG_LIMIT_DAILY_UNAUTHENTICATED,
        "used_today_unauth": used_unauth,
        "unauth_daily_reached": unauth_reached,
        "can_start_dorking": not unauth_reached,
    }


@router.put("/limits")
async def update_limits(body: LimitsUpdate):
    if body.daily_unauth is not None:
        Settings.IG_LIMIT_DAILY_UNAUTHENTICATED = body.daily_unauth
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

    raise HTTPException(status_code=400, detail=f"Unknown mode: {body.mode}")


# ── Search: Dorking (Modo A) ──────────────────────────────────────────────────

async def _run_dorking_job(niche: str, location: str, max_results: int, job_id: str):
    from backend.scraper.ig_dorking import search_and_extract
    async with _dorking_lock:
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


# ── Leads ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats():
    total = await db.get_leads_count()
    return {"total_leads": total}


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
