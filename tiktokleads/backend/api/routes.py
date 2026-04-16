import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from backend.api.schemas import HealthResponse, JobResponse, LeadResponse, SearchRequest
from backend.storage import database, exporter
from backend.tiktok import tt_health
from backend.tiktok.tt_deduplicator import deduplicator
from backend.tiktok.tt_profile import extract_and_save
from backend.tiktok.tt_rate_limiter import RateLimitExceeded
from backend.tiktok.tt_search import find_creators

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Job state ─────────────────────────────────────────────────────────────────

_job_lock = asyncio.Lock()
_job_running = False


# ── Health & stats ────────────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> Any:
    return await tt_health.get_health()


@router.get("/stats")
async def stats() -> Any:
    return await database.get_stats()


@router.get("/limits")
async def limits() -> Any:
    today_stats = await database.get_today_stats()
    from backend.tiktok.tt_rate_limiter import limiter
    from backend.config.settings import settings
    requests_today = today_stats["requests"]
    requests_this_hour = limiter.count_this_hour()
    can_start = (
        requests_today < settings.max_daily
        and requests_this_hour < settings.max_req_hour
    )
    return {
        "requests_today": requests_today,
        "requests_this_hour": requests_this_hour,
        "max_daily": settings.max_daily,
        "max_per_hour": settings.max_req_hour,
        "can_start": can_start,
        "daily_reached": requests_today >= settings.max_daily,
        "hourly_reached": requests_this_hour >= settings.max_req_hour,
    }


@router.get("/debug/last")
async def debug_last() -> Any:
    lead = await database.get_last_lead()
    stats_data = await database.get_stats()
    return {"last_lead": lead, "stats": stats_data}


# ── Search / Jobs ─────────────────────────────────────────────────────────────

@router.post("/search")
async def start_search(request: SearchRequest, background_tasks: BackgroundTasks) -> Any:
    global _job_running
    async with _job_lock:
        if _job_running:
            raise HTTPException(status_code=409, detail="Ya hay un job en curso. Espera a que termine.")

    job_id = await database.create_job(target=request.target, total=request.email_goal)
    background_tasks.add_task(_run_job, job_id, request)
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs")
async def list_jobs(limit: int = Query(default=100, ge=1, le=500)) -> Any:
    jobs = await database.get_all_jobs(limit=limit)
    return [JobResponse(**j) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> Any:
    job = await database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return JobResponse(**job)


# ── Leads & Export ────────────────────────────────────────────────────────────

@router.get("/leads")
async def list_leads(
    job_id: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> Any:
    leads = await database.get_leads(job_id=job_id, limit=limit)
    return [LeadResponse(**lead) for lead in leads]


@router.get("/export/{job_id}")
async def export_leads(job_id: str) -> Any:
    job = await database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    leads = await database.get_leads(job_id=job_id, limit=10000)
    if not leads:
        raise HTTPException(status_code=404, detail="No hay leads para este job")

    csv_bytes = exporter.leads_to_csv(leads)
    target_slug = job["target"].replace("#", "").replace(" ", "_")[:30]
    filename = f"tiktok_leads_{target_slug}_{job_id[:8]}.csv"

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_job(job_id: str, request: SearchRequest) -> None:
    global _job_running
    async with _job_lock:
        _job_running = True

    logger.info("Job %s started — target='%s', email_goal=%d, min_followers=%d",
                job_id, request.target, request.email_goal, request.min_followers)
    try:
        await _run_hashtag_job(job_id, request)

        job = await database.get_job(job_id)
        emails_found = int((job or {}).get("emails_found") or 0)
        final_status = "completed" if emails_found >= request.email_goal else "completed_partial"
        await database.finish_job(job_id, status=final_status)
        logger.info("Job %s finished with status '%s' — %d emails", job_id, final_status, emails_found)

    except RateLimitExceeded as exc:
        logger.warning("Job %s rate limited: %s", job_id, exc)
        await database.update_job_fields(job_id, failure_reason=str(exc), last_error=str(exc))
        await database.finish_job(job_id, status="rate_limited")

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
        tt_health.record_error(str(exc))
        await database.update_job_fields(job_id, failure_reason=str(exc), last_error=str(exc))
        await database.finish_job(job_id, status="failed")

    finally:
        async with _job_lock:
            _job_running = False


async def _run_hashtag_job(job_id: str, request: SearchRequest) -> None:
    """Main scraping pipeline: discover creators → extract + save leads."""
    from backend.config.settings import settings

    # Phase 1: Discovery
    await database.update_job_fields(job_id, status_detail="Buscando creadores en TikTok...")
    max_to_fetch = max(request.email_goal * 5, 50)
    creators = await find_creators(request.target, max_results=max_to_fetch)

    if not creators:
        await database.update_job_fields(job_id, failure_reason="no_creators_found")
        logger.warning("Job %s: no creators found for '%s'", job_id, request.target)
        return

    await database.update_job_fields(
        job_id,
        total=request.email_goal,
        status_detail=f"Analizando {len(creators)} creadores encontrados...",
    )
    logger.info("Job %s: %d creators to process", job_id, len(creators))

    # Phase 2: Process
    emails_found = 0
    profiles_scanned = 0
    emails_from_bio = 0
    emails_from_web = 0
    skipped_count = 0
    stop_event = asyncio.Event()
    counter_lock = asyncio.Lock()

    queue: asyncio.Queue = asyncio.Queue()
    for creator in creators:
        queue.put_nowait(creator)

    async def worker() -> None:
        nonlocal emails_found, profiles_scanned, emails_from_bio, emails_from_web, skipped_count
        while not stop_event.is_set():
            try:
                creator = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                username = creator.get("unique_id", "")
                if not username:
                    queue.task_done()
                    continue

                if deduplicator.is_duplicate(username):
                    async with counter_lock:
                        skipped_count += 1
                    queue.task_done()
                    continue

                await database.update_job_fields(
                    job_id,
                    status_detail=f"Analizando @{username} ({emails_found}/{request.email_goal} emails)",
                )

                lead = await extract_and_save(
                    username=username,
                    job_id=job_id,
                    min_followers=request.min_followers,
                )

                async with counter_lock:
                    profiles_scanned += 1
                    if lead:
                        emails_found += 1
                        await deduplicator.mark_seen(username)
                        if lead.get("email_source") == "bio":
                            emails_from_bio += 1
                        elif lead.get("email_source") == "biolink":
                            emails_from_web += 1
                        if emails_found >= request.email_goal:
                            stop_event.set()
                    else:
                        skipped_count += 1

                    await database.update_job_progress(job_id, profiles_scanned, emails_found)
                    await database.update_job_fields(
                        job_id,
                        emails_from_bio=emails_from_bio,
                        emails_from_web=emails_from_web,
                        skipped_count=skipped_count,
                        profiles_scanned=profiles_scanned,
                    )
            except Exception as exc:
                logger.error("Worker error processing @%s: %s", creator.get("unique_id", "?"), exc)
                async with counter_lock:
                    skipped_count += 1
            finally:
                queue.task_done()

    n_workers = min(settings.max_concurrent_workers, max(1, len(creators)))
    await asyncio.gather(*(worker() for _ in range(n_workers)), return_exceptions=True)

    logger.info(
        "Job %s pipeline done — scanned=%d, emails=%d (bio=%d, web=%d), skipped=%d",
        job_id, profiles_scanned, emails_found, emails_from_bio, emails_from_web, skipped_count,
    )
