import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from backend.api.schemas import JobResponse, LeadResponse, SearchRequest
from backend.config.settings import settings
from backend.proxy.proxy_manager import proxy_manager, set_current_job
from backend.scraper.email_finder import find_email_in_website
from backend.scraper.email_verifier import verify_email_mx
from backend.scraper.maps_client import search_maps_paginated
from backend.storage import database as db
from backend.storage.exporter import export_to_csv

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

async def _run_scrape_job(job_id: str, request: SearchRequest) -> None:
    """
    Full scraping pipeline:
    1. Fetch business listings from Google Maps (paginated)
    2. For each business with a website, find email (via proxy_manager)
    3. Verify email MX records
    4. Save to DB with live progress updates
    """
    # Register job ID so proxy_manager can track waiting state visible to the UI
    set_current_job(job_id)
    logger.info("Job %s: starting scrape — query='%s', location='%s', max_results=%d", job_id, request.query, request.location, request.max_results)
    emails_found = 0

    try:
        logger.info("Job %s: calling search_maps_paginated...", job_id)
        businesses = await search_maps_paginated(
            request.query,
            request.location,
            max_results=request.max_results,
            lat=request.lat,
            lng=request.lng,
            radius_km=request.radius_km,
        )
        logger.info("Job %s: got %d businesses from Maps", job_id, len(businesses))

        total = len(businesses)
        await db.update_job_total(job_id, total)
        await db.update_job_progress(job_id, 0, 0)

        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

        async def process_business(index: int, business: dict) -> None:
            nonlocal emails_found
            async with semaphore:
                email = None
                email_status = "pending"

                if business.get("website"):
                    try:
                        emails = await find_email_in_website(business["website"])
                        if emails:
                            email = emails[0]
                            email_status = await verify_email_mx(email)
                            emails_found += 1
                    except Exception as exc:
                        logger.debug("Email search failed for %s: %s", business.get("website"), exc)

                business["email"] = email
                business["email_status"] = email_status
                await db.save_lead(business, job_id)
                await db.update_job_progress(job_id, index + 1, emails_found)

        tasks = [process_business(i, b) for i, b in enumerate(businesses)]
        await asyncio.gather(*tasks)

        await db.finish_job(job_id, "done")
        logger.info("Job %s done: %d businesses, %d emails", job_id, total, emails_found)

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc)
        await db.finish_job(job_id, "failed")
    finally:
        set_current_job(None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/proxy/status")
async def get_proxy_status():
    return proxy_manager.get_status()


@router.post("/search")
async def start_search(body: SearchRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    await db.create_job(job_id, body.query, body.location, total=0)
    background_tasks.add_task(_run_scrape_job, job_id, body)
    logger.info("Started job %s: '%s' in '%s'", job_id, body.query, body.location)
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    wait_secs = proxy_manager.get_job_wait_seconds(job_id)
    return JobResponse(
        job_id=job["job_id"],
        status=job["status"],
        progress=job["progress"],
        total=job["total"],
        emails_found=job["emails_found"],
        waiting_for_proxy=wait_secs > 0 and job["status"] == "running",
        proxy_wait_seconds=wait_secs,
    )


@router.get("/proxy/capacity")
async def get_proxy_capacity():
    return proxy_manager.estimate_capacity()


@router.get("/jobs")
async def list_jobs(limit: int = Query(default=100, ge=1, le=500)):
    jobs = await db.get_all_jobs(limit=limit)
    return jobs


@router.get("/stats")
async def get_stats():
    return await db.get_leads_stats()


@router.get("/leads", response_model=list[LeadResponse])
async def list_leads(
    job_id: str | None = Query(default=None),
    has_email: bool | None = Query(default=None),
):
    leads = await db.get_leads(job_id=job_id, has_email=has_email)
    return leads


@router.get("/export/{job_id}")
async def export_leads(job_id: str):
    leads = await db.get_leads(job_id=job_id)
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found for this job")

    csv_content = export_to_csv(leads)
    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=leads_{job_id}.csv"},
    )


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: int):
    deleted = await db.delete_lead(lead_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"status": "deleted"}
