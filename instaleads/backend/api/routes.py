import asyncio
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import (
    AccountAddRequest,
    AccountResponse,
    DiagnoseResponse,
    HealthResponse,
    JobResponse,
    LeadResponse,
    ProfilePreview,
    ProxyStatusResponse,
    SearchRequest,
    SessionLoginRequest,
)
from backend.instagram import ig_health, ig_session
from backend.instagram.ig_account_pool import account_pool
from backend.instagram.ig_deduplicator import deduplicator
from backend.instagram.ig_rate_limiter import RateLimitExceeded
from backend.config.settings import settings as cfg
from backend.storage import database, exporter

logger = logging.getLogger(__name__)

router = APIRouter()
proxy_router = APIRouter()


# ── Health & diagnostics ──────────────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health() -> Any:
    return await ig_health.get_health()


@router.api_route("/diagnose", methods=["GET", "POST"], response_model=DiagnoseResponse)
async def diagnose() -> Any:
    return await ig_health.get_diagnose()


@router.get("/debug/last")
async def debug_last() -> Any:
    lead = await database.get_last_lead()
    stats = await database.get_stats()
    return {"last_lead": lead, "stats": stats}


# ── Session management ────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: SessionLoginRequest) -> Any:
    """Login with Instagram credentials and add as primary pool account."""
    success, error_type, message = await account_pool.add_account(
        body.username, body.password, is_primary=True
    )
    if not success:
        # Map error_type to a status the frontend understands
        status = error_type if error_type in ("challenge", "phone", "2fa") else "error"
        return {"status": status, "message": message}
    return {"status": "ok", "message": "Sesión iniciada correctamente."}


@router.get("/session")
async def get_session() -> Any:
    """Return session status based on pool primary account."""
    info = account_pool.get_primary_info()
    if info is None:
        return {"logged_in": False, "username": None, "session_age_hours": None}
    return info


@router.delete("/session")
async def delete_session() -> Any:
    """Remove the primary account from the pool."""
    await account_pool.remove_primary()
    return {"status": "cleared"}


@router.post("/session/login")
async def session_login(body: SessionLoginRequest) -> Any:
    success = await ig_session.login(body.username, body.password)
    if not success:
        raise HTTPException(
            status_code=401,
            detail=ig_session.get_last_login_error() or "Instagram login failed",
        )
    return {"status": "ok", "message": "Logged in successfully"}


@router.post("/session/logout")
async def session_logout() -> Any:
    await ig_session.logout()
    return {"status": "ok"}


@router.get("/session/status")
async def session_status() -> Any:
    return {"active": ig_session.is_logged_in()}


# ── Rate limits ───────────────────────────────────────────────────────────────

@router.get("/limits")
async def get_limits() -> Any:
    """Return current rate limit settings and today's usage."""
    from backend.storage.database import get_today_stats
    from backend.instagram.ig_rate_limiter import auth_limiter

    stats = await get_today_stats()
    hourly_used = auth_limiter.count_this_hour()
    unauth_used = stats.get("unauth_requests", 0)
    auth_used = stats.get("auth_requests", 0)
    unauth_daily_reached = unauth_used >= cfg.max_unauth_daily
    auth_daily_reached = auth_used >= cfg.max_auth_daily
    auth_hourly_reached = hourly_used >= cfg.max_auth_hourly
    return {
        "daily_unauth": cfg.max_unauth_daily,
        "daily_auth": cfg.max_auth_daily,
        "hourly_auth": cfg.max_auth_hourly,
        "used_today_unauth": unauth_used,
        "used_today_auth": auth_used,
        "used_this_hour_auth": hourly_used,
        "unauth_daily_reached": unauth_daily_reached,
        "auth_daily_reached": auth_daily_reached,
        "auth_hourly_reached": auth_hourly_reached,
        "can_start_dorking": not unauth_daily_reached,
        "can_start_followers": not (auth_daily_reached or auth_hourly_reached),
    }




# ── Account pool management ───────────────────────────────────────────────────

@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts() -> Any:
    """List all pool accounts with their current status."""
    return account_pool.get_pool_status()


@router.post("/accounts", response_model=AccountResponse)
async def add_account(body: AccountAddRequest) -> Any:
    """Add a new Instagram account to the pool."""
    from fastapi.responses import JSONResponse

    success, error_type, message = await account_pool.add_account(
        body.username, body.password, proxy_url=body.proxy_url
    )
    if not success:
        return JSONResponse(
            status_code=400,
            content={"error_type": error_type, "message": message},
        )
    status_list = account_pool.get_pool_status()
    for entry in status_list:
        if entry["username"] == body.username:
            return entry
    raise HTTPException(status_code=500, detail="Account added but not found in pool.")


@router.post("/accounts/relogin/{username}")
async def relogin_account(username: str) -> Any:
    """Re-authenticate a pool account using saved credentials."""
    success, error_type, message = await account_pool.attempt_relogin(username)
    if success:
        return {"status": "ok", "message": "Sesión reconectada correctamente."}
    status = error_type if error_type in ("challenge", "phone", "2fa") else "error"
    return {"status": status, "message": message}


@router.delete("/accounts/{username}")
async def remove_account(username: str) -> Any:
    """Remove an account from the pool."""
    removed = await account_pool.remove_account(username)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Account '{username}' not found in pool.")
    return {"status": "removed", "username": username}


# ── Profile preview ───────────────────────────────────────────────────────────

@router.get("/profile/{username}", response_model=ProfilePreview)
async def get_profile(username: str) -> Any:
    from backend.instagram.ig_client import get_profile_best as fetch_profile

    data = await fetch_profile(username)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Profile '{username}' not found or private")
    return data


# ── Jobs ──────────────────────────────────────────────────────────────────────

@router.post("/search")
async def start_search(body: SearchRequest, background_tasks: BackgroundTasks) -> Any:
    limits = await get_limits()
    if body.mode == "followers" and account_pool.is_empty():
        raise HTTPException(status_code=400, detail="Necesitas sesión activa para Modo B.")
    if body.mode == "dorking" and not limits["can_start_dorking"]:
        raise HTTPException(
            status_code=429,
            detail=f"Límite diario sin login alcanzado ({limits['used_today_unauth']}/{limits['daily_unauth']}).",
        )
    if body.mode == "followers" and not limits["can_start_followers"]:
        if limits["auth_hourly_reached"]:
            detail = f"Límite por hora con login alcanzado ({limits['used_this_hour_auth']}/{limits['hourly_auth']})."
        else:
            detail = f"Límite diario con login alcanzado ({limits['used_today_auth']}/{limits['daily_auth']})."
        raise HTTPException(status_code=429, detail=detail)

    job_id = await database.create_job(mode=body.mode, target=body.target, total=body.email_goal)
    background_tasks.add_task(_run_job, job_id, body)
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(limit: int = 100) -> Any:
    return await database.get_all_jobs(limit=limit)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> Any:
    job = await database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats_endpoint() -> Any:
    return await database.get_stats()


# ── Leads ─────────────────────────────────────────────────────────────────────

@router.get("/leads", response_model=list[LeadResponse])
async def list_leads(job_id: str | None = None, limit: int = 500) -> Any:
    return await database.get_leads(job_id=job_id, limit=limit)


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/export/{job_id}")
async def export_csv(job_id: str) -> StreamingResponse:
    job = await database.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    leads = await database.get_leads(job_id=job_id, limit=10000)
    csv_bytes = exporter.leads_to_csv(leads)
    filename = f"instaleads_{job_id[:8]}.csv"
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Proxy status (compatibility with Laravel proxy controller) ────────────────

@proxy_router.get("/status", response_model=ProxyStatusResponse)
async def proxy_status() -> Any:
    health = await ig_health.get_health()
    available = health["status"] == "ok"
    return {
        "available": available,
        "message": "ok" if available else health["status"],
    }


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_job(job_id: str, request: SearchRequest) -> None:
    try:
        if request.mode == "dorking":
            await _run_dorking_job(job_id, request)
            await database.finish_job(job_id, status="completed")
        elif request.mode == "followers":
            await _run_followers_job(job_id, request)
    except RateLimitExceeded as exc:
        logger.warning("Job %s paused: rate limit — %s", job_id, exc)
        await database.update_job_fields(job_id, failure_reason=str(exc), last_error=str(exc))
        await database.finish_job(job_id, status="rate_limited")
    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
        ig_health.record_error(str(exc))
        await database.update_job_fields(job_id, failure_reason=str(exc), last_error=str(exc))
        await database.finish_job(job_id, status="failed")


async def _run_dorking_job(job_id: str, request: SearchRequest) -> None:
    from backend.instagram.ig_dorking import find_usernames
    from backend.instagram.ig_profile import extract_and_save

    discovery_quota = max(request.email_goal, min(500, request.email_goal * 6))
    usernames = await find_usernames(request.target, max_results=discovery_quota)
    await database.update_job_progress(job_id, progress=0, emails_found=0)

    emails_found = 0
    processed = 0
    counter_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()

    for idx, username in enumerate(usernames):
        queue.put_nowait((idx, username))

    async def worker() -> None:
        nonlocal emails_found, processed
        while not stop_event.is_set():
            try:
                _, username = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                if stop_event.is_set():
                    return
                if deduplicator.is_duplicate(username):
                    async with counter_lock:
                        processed += 1
                        await database.update_job_progress(
                            job_id, progress=processed, emails_found=emails_found
                        )
                    continue

                lead = await extract_and_save(username, job_id=job_id, source_type="dorking")

                async with counter_lock:
                    processed += 1
                    if lead:
                        emails_found += 1
                        if request.email_goal and emails_found >= request.email_goal:
                            stop_event.set()
                    await database.update_job_progress(
                        job_id, progress=processed, emails_found=emails_found
                    )
            finally:
                queue.task_done()

    worker_count = min(3, len(usernames)) if usernames else 0
    if worker_count > 0:
        await asyncio.gather(*(worker() for _ in range(worker_count)), return_exceptions=True)


async def _run_followers_job(job_id: str, request: SearchRequest) -> None:
    from backend.instagram.ig_followers import extract_followers_leads

    scan_limit = max(request.email_goal, min(cfg.max_auth_daily, request.email_goal * 4))
    max_resumes = max(0, cfg.followers_max_resumes_per_day)

    # Use pool if it has accounts, otherwise single-session mode
    pool = account_pool if not account_pool.is_empty() else None

    while True:
        job = await database.get_job(job_id)
        if not job:
            return
        progress = int(job.get("profiles_scanned") or job.get("progress") or 0)
        emails_found = int(job.get("emails_found") or 0)
        resume_count = int(job.get("resume_count") or 0)
        from_ig = int(job.get("emails_from_ig") or 0)
        from_web = int(job.get("emails_from_web") or 0)
        enrichment_attempts = int(job.get("enrichment_attempts") or 0)
        enrichment_successes = int(job.get("enrichment_successes") or 0)
        skipped_private = int(job.get("skipped_private") or 0)

        await database.update_job_fields(
            job_id,
            status="running",
            status_detail=None,
            next_retry_at=None,
            failure_reason=None,
        )
        try:
            outcome = await extract_followers_leads(
                target_username=request.target,
                job_id=job_id,
                max_results=scan_limit,
                email_goal=request.email_goal,
                initial_emails_found=emails_found,
                initial_processed=progress,
                initial_from_ig=from_ig,
                initial_from_web=from_web,
                initial_enrichment_attempts=enrichment_attempts,
                initial_enrichment_successes=enrichment_successes,
                initial_skipped_private=skipped_private,
                account_pool=pool,
            )
            if outcome.get("stopped_reason") == "failed":
                await database.update_job_fields(job_id, status="failed")
                await database.finish_job(job_id, status="failed")
                return
            await database.finish_job(job_id, status="completed")
            return
        except RateLimitExceeded as exc:
            retry_after = int(getattr(exc, "retry_after_seconds", 0) or 0)
            if retry_after <= 0:
                retry_after = 60
            is_daily = retry_after > 3600
            if is_daily or not cfg.followers_auto_resume_enabled or resume_count >= max_resumes:
                detail = "Límite diario alcanzado." if is_daily else "Se alcanzó el máximo de reintentos automáticos."
                await database.update_job_fields(
                    job_id,
                    status="rate_limited",
                    status_detail=detail,
                    failure_reason=str(exc),
                    last_error=str(exc),
                )
                await database.finish_job(job_id, status="rate_limited")
                return
            next_retry = asyncio.get_running_loop().time() + retry_after
            # Store wall-clock ETA for UI
            from datetime import datetime, timezone
            eta_iso = datetime.fromtimestamp(
                datetime.now(tz=timezone.utc).timestamp() + retry_after,
                tz=timezone.utc,
            ).isoformat()
            await database.update_job_fields(
                job_id,
                status="waiting_rate_window",
                status_detail="Pausado por límite horario, reanudación automática programada.",
                next_retry_at=eta_iso,
                resume_count=resume_count + 1,
                failure_reason=str(exc),
                last_error=str(exc),
            )
            # Sleep in-job and retry automatically
            now = asyncio.get_running_loop().time()
            await asyncio.sleep(max(1, int(next_retry - now)))
