import asyncio
import logging
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from backend.api.schemas import EmailProbeRequest, JobLocationResponse, JobResponse, LeadResponse, SearchRequest
from backend.config.settings import settings
from backend.proxy.proxy_manager import proxy_manager, set_current_job
from backend.scraper.category_catalog import clear_category_catalog_cache, search_categories
from backend.scraper.email_finder import (
    find_email_in_website_diagnostics,
    is_social_url,
    pick_best_email,
    pick_best_email_confidence,
)
from backend.scraper.maps_categories import load_categories, load_categories_meta
from backend.scraper.email_verifier import verify_email_mx
from backend.scraper.maps_client import MapsFetchError, search_maps
from backend.storage import database as db
from backend.storage.exporter import export_to_csv

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
_MAX_MULTI_LOCALITIES = 5000
_NETWORK_CHECK_URLS = (
    "https://roymo.es/",
    "https://www.marketingdigitaldirecto.com/diseno-web/",
)

_CATEGORIES_SYNC_STATE_LOCK = threading.Lock()
_CATEGORIES_SYNC_STATE: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_stdout_tail": None,
    "last_stderr_tail": None,
}

_CATEGORIES_SYNC_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_maps_categories.py"
_CATEGORIES_SYNC_BG_TIMEOUT_SEC = 180  # evita bloqueos eternos

_categories_sync_endpoint_lock = asyncio.Lock()


def _sync_categories_script_background() -> None:
    """
    Ejecuta el script en background (threadpool) para no bloquear el servidor.
    """
    stdout_tail = ""
    stderr_tail = ""
    ok = False

    try:
        cmd = [sys.executable, str(_CATEGORIES_SYNC_SCRIPT_PATH), "--write"]
        logger.info("Categories sync: starting %s", cmd)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_CATEGORIES_SYNC_BG_TIMEOUT_SEC,
        )
        stdout_tail = (proc.stdout or "")[-4000:]
        stderr_tail = (proc.stderr or "")[-4000:]
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired as exc:
        stdout_tail = (getattr(exc, "stdout", "") or "")[-4000:]
        stderr_tail = (getattr(exc, "stderr", "") or "")[-4000:]
        ok = False
        stderr_tail = stderr_tail or f"TimeoutExpired after {_CATEGORIES_SYNC_BG_TIMEOUT_SEC}s"
    except Exception as exc:
        stdout_tail = ""
        stderr_tail = str(exc)[-4000:]
        ok = False

    # Actualiza estado del sync
    with _CATEGORIES_SYNC_STATE_LOCK:
        if ok:
            _CATEGORIES_SYNC_STATE["last_error"] = None
            _CATEGORIES_SYNC_STATE["last_stdout_tail"] = stdout_tail
            _CATEGORIES_SYNC_STATE["last_stderr_tail"] = stderr_tail
        else:
            _CATEGORIES_SYNC_STATE["last_error"] = (stderr_tail or "sync failed")[:2000]
            _CATEGORIES_SYNC_STATE["last_stdout_tail"] = stdout_tail
            _CATEGORIES_SYNC_STATE["last_stderr_tail"] = stderr_tail

        _CATEGORIES_SYNC_STATE["running"] = False
        from datetime import datetime, timezone

        _CATEGORIES_SYNC_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()

    # Limpia caches para que los endpoints reflejen el nuevo JSON.
    try:
        load_categories.cache_clear()
        load_categories_meta.cache_clear()
        clear_category_catalog_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _normalize_email_reason(
    *,
    email: str | None,
    raw_reason: str | None,
    form_vendor: str | None,
) -> str | None:
    if email:
        return "found"
    if not raw_reason:
        return None
    if form_vendor and raw_reason in {"unreachable_or_blocked", "no_visible_email", "ssl_or_insecure_site"}:
        return "form_backend_hidden_recipient"
    return raw_reason


async def _enrich_business_email(business: dict) -> tuple[str | None, str, str | None, str | None]:
    existing = (business.get("email") or "").strip() if isinstance(business.get("email"), str) else business.get("email")
    if existing:
        return str(existing), business.get("email_status", "pending") or "pending", "found", business.get("email_confidence")

    email = None
    email_status = "pending"
    email_reason = None
    email_confidence = None

    website = business.get("website")
    if website and not is_social_url(str(website)):
        try:
            diag = await find_email_in_website_diagnostics(str(website))
            emails = diag.get("emails", [])
            if emails:
                chosen = pick_best_email(emails, str(website))
                if chosen:
                    email = chosen
                    email_status = await verify_email_mx(email)
                    if email_status == "invalid":
                        email_status = "pending"
                    email_confidence = pick_best_email_confidence(emails, str(website))
            email_reason = _normalize_email_reason(
                email=email,
                raw_reason=diag.get("reason"),
                form_vendor=diag.get("form_vendor"),
            )
        except Exception as exc:
            logger.debug("Email search failed for %s: %s", business.get("website"), exc)
            email_reason = "enrichment_error"
    return email, email_status, email_reason, email_confidence


async def _search_unique_businesses(
    *,
    query: str,
    location: str,
    target: int,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 10.0,
    dedupe_days: int,
) -> list[dict]:
    """
    Paginate Maps results and keep unique businesses, skipping recently seen place_ids.
    """
    if target <= 0:
        return []

    uniques: list[dict] = []
    seen_in_job: set[str] = set()
    start = 0
    pages_scanned = 0
    dropped_recent = 0
    dropped_in_job = 0
    max_pages = max(3, min(200, target * 6))

    while len(uniques) < target and pages_scanned < max_pages:
        batch = await search_maps(
            query=query,
            location=location,
            start=start,
            lat=lat,
            lng=lng,
            radius_km=radius_km,
        )
        pages_scanned += 1

        if not batch:
            break

        candidate_place_ids: list[str] = []
        for business in batch:
            pid_raw = business.get("place_id")
            pid = str(pid_raw).strip() if pid_raw else ""
            if pid and pid not in seen_in_job:
                candidate_place_ids.append(pid)

        recent_ids = await db.get_recent_place_ids(candidate_place_ids, days=dedupe_days) if candidate_place_ids else set()

        for business in batch:
            pid_raw = business.get("place_id")
            pid = str(pid_raw).strip() if pid_raw else ""

            if pid:
                if pid in seen_in_job:
                    dropped_in_job += 1
                    continue
                seen_in_job.add(pid)

                if pid in recent_ids:
                    dropped_recent += 1
                    continue

            uniques.append(business)
            if len(uniques) >= target:
                break

        start += 20
        if len(batch) < 20:
            break

    logger.info(
        "Unique search query='%s' location='%s': uniques=%d target=%d pages=%d dropped_recent=%d dropped_in_job=%d",
        query,
        location,
        len(uniques),
        target,
        pages_scanned,
        dropped_recent,
        dropped_in_job,
    )
    return uniques


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
        logger.info("Job %s: calling _search_unique_businesses...", job_id)
        businesses = await _search_unique_businesses(
            query=request.query,
            location=request.location,
            target=request.max_results,
            lat=request.lat,
            lng=request.lng,
            radius_km=request.radius_km,
            dedupe_days=settings.dedupe_days,
        )
        logger.info("Job %s: got %d businesses from Maps", job_id, len(businesses))

        total = len(businesses)
        await db.update_job_total(job_id, total)
        await db.update_job_progress(job_id, 0, 0)

        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        progress_lock = asyncio.Lock()

        async def process_business(index: int, business: dict) -> None:
            nonlocal emails_found
            async with semaphore:
                email, email_status, email_reason, email_confidence = await _enrich_business_email(business)
                if email:
                    async with progress_lock:
                        emails_found += 1
                        current_emails = emails_found
                else:
                    current_emails = emails_found

                business["email"] = email
                business["email_status"] = email_status
                business["email_reason"] = email_reason
                business["email_confidence"] = email_confidence
                await db.save_lead(business, job_id)
                await db.update_job_progress(job_id, index + 1, current_emails)

        tasks = [process_business(i, b) for i, b in enumerate(businesses)]
        await asyncio.gather(*tasks)

        await db.finish_job(job_id, "done")
        logger.info("Job %s done: %d businesses, %d emails", job_id, total, emails_found)

    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc)
        await db.finish_job(job_id, "failed")
    finally:
        set_current_job(None)


def _normalize_locations(raw_locations: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in raw_locations:
        raw_value = (raw or "").strip()
        if len(raw_value) > 220:
            raw_value = raw_value[:220]
        parts = [p.strip() for p in raw_value.split(",") if p.strip()]
        normalized = ", ".join(parts)
        if not normalized or len(normalized) < 2:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return unique


async def _run_multi_locality_job(job_id: str, request: SearchRequest, locations: list[str]) -> None:
    set_current_job(job_id)
    total_locations = len(locations)
    total_progress = 0
    total_scanned = 0
    total_emails_found = 0
    failed_locations = 0

    try:
        await db.create_job_locations(job_id, locations)

        for idx, location in enumerate(locations, start=1):
            await db.update_job_location_progress(
                job_id,
                current_location_index=idx,
                total_locations=total_locations,
                current_location_label=location,
                current_location_emails_found=0,
            )
            await db.start_job_location(job_id, idx)

            locality_emails_found = 0
            locality_leads_found = 0
            locality_status = "done"
            query = f"{request.category_query} {location}".strip()

            logger.info(
                "Job %s: processing location %d/%d — '%s'",
                job_id, idx, total_locations, location,
            )

            try:
                businesses = await _search_unique_businesses(
                    query=query,
                    location=location,
                    target=request.target_per_location,
                    dedupe_days=settings.dedupe_days,
                )
                total_scanned += len(businesses)
                await db.update_job_total(job_id, total_scanned)

                if not businesses:
                    locality_status = "empty"
                    continue

                for business in businesses:
                    email, email_status, email_reason, email_confidence = await _enrich_business_email(business)
                    business["email"] = email
                    business["email_status"] = email_status
                    business["email_reason"] = email_reason
                    business["email_confidence"] = email_confidence
                    await db.save_lead(business, job_id)
                    locality_leads_found += 1
                    total_progress += 1
                    if email:
                        locality_emails_found += 1
                        total_emails_found += 1

                    await db.update_job_progress(job_id, total_progress, total_emails_found)
                    await db.update_job_location_metrics(
                        job_id,
                        idx,
                        emails_found=locality_emails_found,
                        leads_found=locality_leads_found,
                    )
                    await db.update_job_location_progress(
                        job_id,
                        current_location_index=idx,
                        total_locations=total_locations,
                        current_location_label=location,
                        # En Multiarea el objetivo es "empresas/leads" (no correos).
                        # Reutilizamos el campo existente `current_location_emails_found`
                        # para que la UI muestre el progreso de empresas por localidad.
                        current_location_emails_found=locality_leads_found,
                    )

                    if locality_leads_found >= request.target_per_location:
                        break
            except MapsFetchError as exc:
                locality_status = "failed"
                failed_locations += 1
                logger.error(
                    "Job %s: location '%s' failed: %s",
                    job_id, location, exc,
                )
            except Exception as exc:
                locality_status = "failed"
                failed_locations += 1
                logger.error(
                    "Job %s: location '%s' failed (unexpected): %s",
                    job_id, location, exc,
                )
            finally:
                await db.finish_job_location(job_id, idx, locality_status)

        await db.update_job_location_progress(
            job_id,
            current_location_index=total_locations,
            total_locations=total_locations,
            current_location_label=locations[-1] if locations else None,
            current_location_emails_found=0,
        )
        if total_locations > 0 and failed_locations >= total_locations:
            await db.finish_job(job_id, "failed")
        else:
            await db.finish_job(job_id, "done")
    except Exception as exc:
        logger.error("Job %s failed (multi locality): %s", job_id, exc)
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


@router.get("/network/check")
async def get_network_check():
    """
    Diagnóstico rápido de salida de red para el scraper de emails.
    """
    from backend.scraper import email_finder as ef

    env_proxy_vars = {
        k: os.getenv(k)
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        if os.getenv(k)
    }

    checks: list[dict] = []
    for url in _NETWORK_CHECK_URLS:
        row: dict = {"url": url, "proxy": {"ok": False, "status": None}, "direct": {"ok": False, "status": None}}

        proxy = None
        if proxy_manager._stats:
            proxy = await proxy_manager.wait_for_available(timeout_seconds=1)
        try:
            html, used_proxy, _reason = await ef._fetch_page(url, proxy)
            row["proxy"]["ok"] = bool(html and used_proxy)
            row["proxy"]["status"] = "ok" if row["proxy"]["ok"] else "failed_or_empty"
        except Exception as exc:
            row["proxy"]["status"] = f"error: {exc}"

        try:
            html, used_proxy, _reason = await ef._fetch_page(url, None)
            row["direct"]["ok"] = bool(html and not used_proxy)
            row["direct"]["status"] = "ok" if row["direct"]["ok"] else "failed_or_empty"
        except Exception as exc:
            row["direct"]["status"] = f"error: {exc}"

        checks.append(row)

    return {
        "force_direct_enabled": settings.email_scraper_force_direct,
        "configured_proxy_count": len(proxy_manager._stats),
        "env_proxy_vars": env_proxy_vars,
        "checks": checks,
    }


@router.post("/email/probe")
async def post_email_probe(body: EmailProbeRequest):
    """
    Probe a single URL with the same website-email logic used in jobs.
    Useful for manual diagnostics against real runtime connectivity.
    """
    website = (body.url or "").strip()
    if not website:
        raise HTTPException(status_code=422, detail="url is required")

    if is_social_url(website):
        return {
            "url": website,
            "skipped": True,
            "reason": "social_or_non_business",
            "contact_method": "none",
            "form_vendor": None,
            "emails_found": [],
            "best_email": None,
            "best_email_confidence": None,
            "email_status": "pending",
        }

    diag = await find_email_in_website_diagnostics(website)
    emails = diag.get("emails", [])
    best = pick_best_email(emails, website) if emails else None
    best_confidence = pick_best_email_confidence(emails, website) if best else None
    email_status = "pending"
    if best:
        try:
            email_status = await verify_email_mx(best)
            if email_status == "invalid":
                email_status = "pending"
        except Exception:
            email_status = "pending"

    form_vendor = diag.get("form_vendor")
    contact_method = "email" if best else ("form" if form_vendor else "none")
    reason = diag.get("reason", "unknown")
    if not best and form_vendor and reason in {"unreachable_or_blocked", "no_visible_email", "ssl_or_insecure_site"}:
        reason = "form_backend_hidden_recipient"

    return {
        "url": website,
        "skipped": False,
        "emails_found": sorted(emails),
        "best_email": best,
        "best_email_confidence": best_confidence,
        "email_status": email_status,
        "reason": reason,
        "contact_method": contact_method,
        "form_vendor": form_vendor,
        "visited_urls": diag.get("visited_urls", []),
    }


@router.get("/maps/categories")
async def get_maps_categories(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
):
    return search_categories(query=q, limit=limit)


@router.get("/maps/categories/meta")
async def get_maps_categories_meta():
    # Devuelve metadata generada por `mapleads/scripts/update_maps_categories.py`.
    # Si no existe el fichero meta, retorna {}.
    return load_categories_meta()


@router.post("/maps/categories/sync")
async def post_maps_categories_sync(background_tasks: BackgroundTasks):
    async with _categories_sync_endpoint_lock:
        with _CATEGORIES_SYNC_STATE_LOCK:
            if _CATEGORIES_SYNC_STATE.get("running"):
                raise HTTPException(status_code=409, detail="Sync already running")

            from datetime import datetime, timezone

            _CATEGORIES_SYNC_STATE.update(
                {
                    "running": True,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "finished_at": None,
                    "last_error": None,
                    "last_stdout_tail": None,
                    "last_stderr_tail": None,
                }
            )

        background_tasks.add_task(_sync_categories_script_background)

    return {"status": "started"}


@router.get("/maps/categories/sync/status")
async def get_maps_categories_sync_status():
    with _CATEGORIES_SYNC_STATE_LOCK:
        return dict(_CATEGORIES_SYNC_STATE)


@router.get("/maps/categories/sync/report")
async def get_maps_categories_sync_report():
    meta = load_categories_meta()
    return {
        "catalog_version": meta.get("catalog_version"),
        "updated_at": meta.get("updated_at"),
        "source_urls": meta.get("source_urls", []),
        "catalog_types_count": meta.get("catalog_types_count", 0),
        "hybrid_summary": meta.get("hybrid_summary", {}),
        "fetch_errors": meta.get("fetch_errors", []),
    }


@router.post("/search")
async def start_search(body: SearchRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]

    if body.mode == "multi_locality":
        normalized_locations = _normalize_locations(body.locations)
        if not normalized_locations:
            raise HTTPException(
                status_code=422,
                detail="No valid locations provided. Use one locality per line or a valid imported file column.",
            )
        if len(normalized_locations) > _MAX_MULTI_LOCALITIES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Too many locations after normalization ({len(normalized_locations)}). "
                    f"Maximum allowed is {_MAX_MULTI_LOCALITIES}."
                ),
            )

        await db.create_job(
            job_id,
            body.category_query,
            f"{len(normalized_locations)} localidades",
            total=0,
            mode="multi_locality",
            total_locations=len(normalized_locations),
            emails_target_per_location=body.target_per_location,
        )
        background_tasks.add_task(_run_multi_locality_job, job_id, body, normalized_locations)
        logger.info(
            "Started multi-locality job %s: category='%s' locations=%d target/location=%d",
            job_id, body.category_query, len(normalized_locations), body.target_per_location,
        )
    else:
        await db.create_job(job_id, body.query, body.location, total=0, mode="single")
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
        mode=job.get("mode", "single"),
        current_location_index=job.get("current_location_index", 0),
        total_locations=job.get("total_locations", 0),
        current_location_label=job.get("current_location_label"),
        current_location_emails_found=job.get("current_location_emails_found", 0),
        emails_target_per_location=job.get("emails_target_per_location", 0),
    )


@router.get("/jobs/{job_id}/locations", response_model=list[JobLocationResponse])
async def get_job_locations(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return await db.get_job_locations(job_id)


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
