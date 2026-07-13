import asyncio
import csv
import io
import os
import uuid
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import (
    DorkingRequest,
    FollowersRequest,
    JobResponse,
    LimitsUpdate,
    SearchRequest,
    SettingsUpdate,
)
from backend.config.settings import Settings
from backend.scraper.ig_client import IgAuthError
from backend.scraper.ig_health import run_health_check
from backend.storage import database as db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/instagram")

# In-memory: job_id → asyncio.Task (dorking worker)
_jobs: dict[str, asyncio.Task] = {}

# Cooperative cancel: job_id → Event (POST .../cancel). Solo el worker que ejecuta el job
# tiene el Event; en multi-worker la cancelación puede devolver 503 si el job corre en otro proceso.
_job_cancel_events: dict[str, asyncio.Event] = {}
_cancel_registry_lock = asyncio.Lock()

# Prevent concurrent dorking jobs from flooding proxies and crashing
_dorking_lock = asyncio.Lock()


def _dorking_task_done(task: asyncio.Task, job_id: str) -> None:
    _jobs.pop(job_id, None)
    try:
        exc = task.exception()
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            logger.error("Dorking task %s raised: %s", job_id[:8], exc)
    except asyncio.CancelledError:
        pass
    except Exception as err:  # pragma: no cover
        logger.error("Dorking task done callback: %s", err)


def _schedule_dorking_job(niche: str, location: str, max_results: int, job_id: str) -> None:
    """Start dorking in the current event loop; register cancel Event before returning."""
    stop_event = asyncio.Event()
    _job_cancel_events[job_id] = stop_event
    task = asyncio.create_task(
        _run_dorking_job(niche, location, max_results, job_id, stop_event)
    )
    _jobs[job_id] = task
    task.add_done_callback(lambda t, jid=job_id: _dorking_task_done(t, jid))


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


# ── Settings (encrypted store, editable from the panel) ───────────────────────

def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


@router.get("/settings")
async def get_settings():
    """Current effective config, with the session id masked. Proxies are
    returned in full so the panel can show/edit them (the panel is behind auth,
    same trust level as the old .env)."""
    from backend.config.settings_store import store
    from backend.scraper.ig_client import effective_proxy_list

    sessionid = (store.get("IG_SESSIONID") or os.getenv("IG_SESSIONID", "")).strip()
    guest_sessionid = (store.get("IG_GUEST_SESSIONID") or os.getenv("IG_GUEST_SESSIONID", "")).strip()
    proxies = effective_proxy_list()

    if store.has("IG_SESSIONID"):
        sess_source = "db"
    elif os.getenv("IG_SESSIONID", "").strip():
        sess_source = "env"
    else:
        sess_source = "none"

    if store.has("IG_GUEST_SESSIONID"):
        guest_source = "db"
    elif os.getenv("IG_GUEST_SESSIONID", "").strip():
        guest_source = "env"
    else:
        guest_source = "none"

    if store.has("IG_PROXY_LIST"):
        proxy_source = "db"
    elif Settings.IG_PROXY_LIST:
        proxy_source = "env"
    else:
        proxy_source = "none"

    from backend.config.tunables import describe_all

    return {
        "ig_sessionid_set": bool(sessionid),
        "ig_sessionid_masked": _mask_secret(sessionid),
        "ig_sessionid_source": sess_source,
        "ig_guest_sessionid_set": bool(guest_sessionid),
        "ig_guest_sessionid_masked": _mask_secret(guest_sessionid),
        "ig_guest_sessionid_source": guest_source,
        "proxy_list": "\n".join(proxies),
        "proxy_count": len(proxies),
        "proxy_source": proxy_source,
        "limits": describe_all(),
    }


@router.put("/settings")
async def update_settings(body: SettingsUpdate):
    from backend.config.settings_store import store
    from backend.config.tunables import update_many
    from backend.scraper.ig_client import reload_proxies
    from backend.scraper.ig_session import reload_guest_session, reload_session

    changed = []

    if body.ig_sessionid is not None:
        value = body.ig_sessionid.strip()
        if value:
            store.set("IG_SESSIONID", value)
        else:
            store.delete("IG_SESSIONID")
        reload_session()
        changed.append("ig_sessionid")

    if body.ig_guest_sessionid is not None:
        value = body.ig_guest_sessionid.strip()
        if value:
            store.set("IG_GUEST_SESSIONID", value)
        else:
            store.delete("IG_GUEST_SESSIONID")
        reload_guest_session()
        changed.append("ig_guest_sessionid")

    if body.proxy_list is not None:
        # Accept newline- or comma-separated; store normalised as comma list.
        parts = [p.strip() for p in body.proxy_list.replace("\n", ",").split(",") if p.strip()]
        if parts:
            store.set("IG_PROXY_LIST", ",".join(parts))
        else:
            store.delete("IG_PROXY_LIST")
        count = reload_proxies()
        changed.append("proxy_list")

    if body.limits:
        try:
            changed += update_many(body.limits)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Valor inválido en límites: {exc}")

    return {"status": "ok", "changed": changed}


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


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Request cooperative cancellation of a running dorking job in this worker."""
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    status = job.get("status") or ""
    if status == "cancelled":
        return {"status": "ok", "detail": "already_cancelled"}
    if status != "running":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not running (status={status})",
        )
    async with _cancel_registry_lock:
        ev = _job_cancel_events.get(job_id)
    if not ev:
        # Running in DB but no local handle (other worker / race).
        logger.warning("Cancel requested for %s but no local cancel handle", job_id[:8])
        raise HTTPException(
            status_code=503,
            detail="Cannot cancel: job is not running in this process",
        )
    ev.set()
    return {"status": "ok", "detail": "cancel_requested"}


# ── Search: Unified endpoint ─────────────────────────────────────────────────

@router.post("/search", response_model=JobResponse)
async def start_search(body: SearchRequest):
    if body.mode == "dorking":
        parts = body.target.split("|", 1)
        niche = parts[0].strip()
        location = parts[1].strip() if len(parts) > 1 else ""
        existing = await db.find_recent_job("dorking", body.target, within_seconds=15)
        if existing:
            return JobResponse(job_id=existing["job_id"], status=existing["status"])
        job_id = str(uuid.uuid4())
        await db.upsert_job(job_id, "dorking", body.target, body.email_goal)
        async with _cancel_registry_lock:
            _schedule_dorking_job(niche, location, body.email_goal, job_id)
        return JobResponse(job_id=job_id, status="running")

    if body.mode == "followers":
        target = body.target.strip().lstrip("@")
        if not target:
            raise HTTPException(status_code=422, detail="target account is required for followers mode")
        existing = await db.find_recent_job("followers", target, within_seconds=15)
        if existing:
            return JobResponse(job_id=existing["job_id"], status=existing["status"])
        job_id = str(uuid.uuid4())
        await db.upsert_job(job_id, "followers", target, body.max_results)
        async with _cancel_registry_lock:
            _schedule_followers_job(target, body.max_results, body.enrich_emails, job_id, body.reset_cursor)
        return JobResponse(job_id=job_id, status="running")

    raise HTTPException(status_code=400, detail=f"Unknown mode: {body.mode}")


# ── Search: Followers (Modo B) ────────────────────────────────────────────────

def _schedule_followers_job(
    target: str, max_results: int, enrich: bool, job_id: str, reset_cursor: bool = False
) -> None:
    """Start a followers scrape in the current event loop; register cancel Event."""
    stop_event = asyncio.Event()
    _job_cancel_events[job_id] = stop_event
    task = asyncio.create_task(
        _run_followers_job(target, max_results, enrich, job_id, stop_event, reset_cursor)
    )
    _jobs[job_id] = task
    task.add_done_callback(lambda t, jid=job_id: _dorking_task_done(t, jid))


async def _run_followers_job(
    target: str,
    max_results: int,
    enrich: bool,
    job_id: str,
    stop_event: asyncio.Event,
    reset_cursor: bool = False,
) -> None:
    """Fase 1: pull the full list of usernames first (fast, authenticated,
    single account). Fase 2 (optional): only once Fase 1 is done, go back
    and fetch emails for the collected usernames — this can ride on a
    separate guest session (see ig_session.get_enrichment_session) so the
    high-volume per-profile checking never touches the main account.
    """
    from backend.scraper.ig_deduplicator import Deduplicator
    from backend.scraper.ig_followers import FollowersError, scrape_followers
    from backend.scraper.ig_profile import get_profile

    collected = 0
    emails_found = 0
    try:
        dedup = Deduplicator()
        await dedup.load_from_db()

        # ── Fase 1: usernames ────────────────────────────────────────────
        try:
            async for follower in scrape_followers(
                target, amount=max_results, stop_event=stop_event, reset_cursor=reset_cursor
            ):
                if stop_event.is_set():
                    break

                username = (follower.get("username") or "").strip()
                if not username or dedup.should_skip(username):
                    continue
                dedup.mark_seen(username)

                lead = {
                    "instagram_id": follower.get("instagram_id"),
                    "username": username,
                    "full_name": follower.get("full_name"),
                    "is_private": bool(follower.get("is_private")),
                }
                if not lead.get("instagram_id"):
                    # Followers endpoint always returns a pk; guard just in case.
                    continue

                await db.upsert_ig_lead(
                    lead, job_id=job_id, source_type="followers", source_value=target
                )
                collected += 1
                await db.update_job_progress(job_id, collected, emails_found, 0)

            logger.info(
                "Followers job %s: fase 1 done — @%s → %d usernames",
                job_id[:8], target, collected,
            )
        except IgAuthError as exc:
            logger.error("Followers job %s: auth error: %s", job_id[:8], exc)
            await db.finish_job(job_id, "auth_required")
            return
        except FollowersError as exc:
            logger.error("Followers job %s: %s", job_id[:8], exc)
            await db.finish_job(job_id, "completed_partial" if collected else "failed")
            return

        # ── Fase 2: emails (only if requested and not cancelled) ────────
        if enrich and not stop_event.is_set():
            pending = await db.get_pending_email_leads(job_id)
            logger.info(
                "Followers job %s: fase 2 start — %d perfiles a comprobar", job_id[:8], len(pending)
            )
            checked = 0
            for row in pending:
                if stop_event.is_set():
                    break

                # Daily "unauth" quota is shared with Dorking mode and enforced
                # inside ig_get's rate limiter — but a DailyLimitReached raised
                # there gets swallowed by ig_client's retry loop and surfaces as
                # a generic fetch failure, not this exception. Check proactively
                # so we stop cleanly instead of burning 3 retries per lead.
                daily_used = await db.get_daily_count("unauth")
                if Settings.IG_LIMIT_DAILY_UNAUTHENTICATED and daily_used >= Settings.IG_LIMIT_DAILY_UNAUTHENTICATED:
                    logger.warning(
                        "Followers job %s: fase 2 stopped — daily unauth limit reached (%d/%d). "
                        "%d perfiles quedan pendientes para la próxima vez.",
                        job_id[:8], daily_used, Settings.IG_LIMIT_DAILY_UNAUTHENTICATED, len(pending) - checked,
                    )
                    break

                username = row["username"]
                try:
                    profile = await get_profile(username)
                except Exception as exc:
                    logger.warning("Followers enrich @%s failed: %s", username, exc)
                    profile = None

                checked += 1

                if profile is None:
                    # Fetch genuinely failed (network/proxy/rate-limit) — leave
                    # email_status as 'pending' so a later Fase 2 run retries it,
                    # instead of wrongly recording it as "no email found".
                    await db.update_job_progress(job_id, collected, emails_found, checked)
                    continue

                enrich_lead = {
                    "instagram_id": row["instagram_id"],
                    "username": username,
                    "email_checked": True,
                    "is_private": bool(profile.get("private")),
                }
                if not profile.get("private"):
                    enrich_lead.update({
                        "full_name": profile.get("full_name"),
                        "email": profile.get("email"),
                        "email_source": profile.get("email_source"),
                        "phone": profile.get("phone"),
                        "website": profile.get("website"),
                        "bio": profile.get("bio"),
                        "follower_count": profile.get("follower_count", 0),
                        "is_business": profile.get("is_business", False),
                    })
                    if profile.get("email"):
                        emails_found += 1

                await db.upsert_ig_lead(
                    enrich_lead, job_id=job_id, source_type="followers", source_value=target
                )
                await db.update_job_progress(job_id, collected, emails_found, checked)

            logger.info(
                "Followers job %s: fase 2 done — %d/%d perfiles comprobados, %d emails",
                job_id[:8], checked, len(pending), emails_found,
            )

        if stop_event.is_set():
            await db.finish_job(job_id, "cancelled")
        else:
            await db.finish_job(job_id, "completed")
        logger.info(
            "Followers job %s: @%s → %d followers, %d emails",
            job_id[:8], target, collected, emails_found,
        )
    except Exception as exc:
        logger.error("Followers job %s failed: %s", job_id, exc)
        await db.finish_job(job_id, "failed")
    finally:
        async with _cancel_registry_lock:
            _job_cancel_events.pop(job_id, None)


@router.post("/search/followers", response_model=JobResponse)
async def start_followers(body: FollowersRequest):
    target = body.target.strip().lstrip("@")
    if not target:
        raise HTTPException(status_code=422, detail="target account is required")
    job_id = str(uuid.uuid4())
    await db.upsert_job(job_id, "followers", target, body.max_results)
    async with _cancel_registry_lock:
        _schedule_followers_job(target, body.max_results, body.enrich_emails, job_id, body.reset_cursor)
    return JobResponse(job_id=job_id, status="running")


# ── Search: Dorking (Modo A) ──────────────────────────────────────────────────

async def _run_dorking_job(
    niche: str,
    location: str,
    max_results: int,
    job_id: str,
    stop_event: asyncio.Event,
):
    from backend.scraper.ig_dorking import search_and_extract

    try:
        async with _dorking_lock:
            try:
                async for _ in search_and_extract(
                    niche,
                    location,
                    max_results,
                    job_id,
                    stop_event=stop_event,
                ):
                    pass
                job = await db.get_job(job_id)
                emails_found = job["emails_found"] if job else 0
                if stop_event.is_set():
                    await db.finish_job(job_id, "cancelled")
                else:
                    st = (
                        "completed"
                        if emails_found >= max_results
                        else "completed_partial"
                    )
                    await db.finish_job(job_id, st)
            except Exception as e:
                logger.error("Dorking job %s failed: %s", job_id, e)
                await db.finish_job(job_id, "failed")
    finally:
        async with _cancel_registry_lock:
            _job_cancel_events.pop(job_id, None)


@router.post("/search/dorking", response_model=JobResponse)
async def start_dorking(body: DorkingRequest):
    job_id = str(uuid.uuid4())
    target = f"{body.niche}|{body.location}"
    await db.upsert_job(job_id, "dorking", target, body.max_results)
    async with _cancel_registry_lock:
        _schedule_dorking_job(body.niche, body.location, body.max_results, job_id)
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
