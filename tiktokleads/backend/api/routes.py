import asyncio
import csv
import io
import json
import logging
import random
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.schemas import JobResponse, SearchRequest
from backend.config.settings import Settings
from backend.scraper.tiktok_client import TikTokLiveClient, TikTokMockClient, _norm_handle_key
from backend.scraper.tiktok_deduplicator import TikTokDeduplicator
from backend.scraper.tiktok_discovery import run_discovery
from backend.scraper.tiktok_profile import enrich_item
from backend.scraper import tiktok_serp
from backend.scraper.tiktok_ranking import email_likelihood_score, sort_discovery_items_by_email_likelihood
from backend.scraper.tiktok_rate_limiter import EnrichThrottle, RateLimiter
from backend.storage import database as db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tiktok")

_job_tasks: dict[str, asyncio.Task] = {}
_rate_limiter = RateLimiter()
_last_live_error: str | None = None
# Cooperativo: mismo proceso que sirve la API (BackgroundTasks). Se limpia en `finally` de `_run_job`.
_job_cancel_events: dict[str, asyncio.Event] = {}


async def _enrich_item_bounded(client, item: dict, job_id: str) -> tuple[dict, str]:
    limit = float(Settings.TIKTOKLEADS_ENRICH_TIMEOUT_SECONDS)
    if limit <= 0:
        return await enrich_item(client, item, job_id)
    return await asyncio.wait_for(enrich_item(client, item, job_id), timeout=limit)


def _classify_enrichment_error(inner: BaseException) -> tuple[str, str]:
    if isinstance(inner, TimeoutError):
        lim = Settings.TIKTOKLEADS_ENRICH_TIMEOUT_SECONDS
        return "timeout", f"timeout enrich ({lim}s)"
    msg = str(inner) or type(inner).__name__
    lower = msg.lower()
    kind = "retryable"
    if "captcha" in lower:
        kind = "captcha"
    elif "blocked" in lower or "denied" in lower:
        kind = "blocked"
    elif "parse" in lower:
        kind = "parse_error"
    return kind, msg


def _resolve_engine(requested: str | None) -> str:
    """Producción: siempre live salvo petición explícita `mock` (tests). `auto` se trata como live."""
    raw = (requested or Settings.TIKTOKLEADS_ENGINE or "live").strip().lower()
    if raw == "mock":
        return "mock"
    if raw in {"live", "auto", ""}:
        return "live"
    return "live"


def _build_client(engine: str):
    if engine == "live":
        return TikTokLiveClient()
    return TikTokMockClient()


@router.get("/health")
async def health():
    default_engine = _resolve_engine(None)
    live_available = TikTokLiveClient.is_available()
    playwright_ready = TikTokLiveClient.playwright_installed()
    status = "ok"
    if default_engine == "live" and not playwright_ready:
        status = "degraded"
    return {
        "status": status,
        "engine_default": default_engine,
        "live_available": live_available,
        "playwright_ready": playwright_ready,
        "proxy_count": len(Settings.TIKTOKLEADS_PROXY_LIST),
        "last_live_error": _last_live_error,
        "jobs_in_memory": len(_job_tasks),
    }


@router.get("/proxy-stats")
async def proxy_stats():
    return {"items": TikTokLiveClient.proxy_stats()}


@router.get("/debug/{job_id}")
async def debug_job(job_id: str, limit: int = 200):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    events = await db.get_job_events(job_id, limit=limit)
    return {"job": job, "events": events}


@router.get("/limits")
async def limits():
    return {
        "mode": "local-live-ready",
        "proxy_count": len(Settings.TIKTOKLEADS_PROXY_LIST),
        "recommended_delay_min": Settings.TIKTOKLEADS_DELAY_MIN,
        "recommended_delay_max": Settings.TIKTOKLEADS_DELAY_MAX,
        "default_engine": _resolve_engine(None),
    }


@router.get("/jobs")
async def list_jobs(limit: int = 24):
    return await db.get_all_jobs(limit=limit)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    row = await db.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    status = str(row.get("status") or "")
    if status != "running":
        return {"ok": False, "reason": "already_finished", "status": status}
    ev = _job_cancel_events.get(job_id)
    if ev is None:
        return {"ok": False, "reason": "not_running_locally"}
    ev.set()
    await db.add_job_event(job_id, "cancel", "info", "Cancelación solicitada por el usuario")
    return {"ok": True}


@router.post("/search", response_model=JobResponse)
async def start_search(body: SearchRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    engine_requested = _resolve_engine(body.engine)
    email_goal = max(1, min(200, int(body.email_goal)))
    total_ui = email_goal
    await db.upsert_job(
        job_id,
        body.mode,
        body.target,
        body.max_results,
        engine_requested=engine_requested,
        total=total_ui,
        email_goal=email_goal,
    )
    cancel_ev = asyncio.Event()
    _job_cancel_events[job_id] = cancel_ev
    background_tasks.add_task(_run_job, body, job_id, cancel_ev)
    return JobResponse(job_id=job_id, status="running")


async def _run_job(body: SearchRequest, job_id: str, cancel_ev: asyncio.Event):
    global _last_live_error
    requested_engine = _resolve_engine(body.engine)
    effective_engine = requested_engine
    fallback_used = False
    await db.add_job_event(job_id, "engine_selected", "info", f"requested={requested_engine}")

    client = _build_client(effective_engine)
    _enrich_concurrency = max(1, Settings.TIKTOKLEADS_ENRICH_CONCURRENCY)
    use_parallel_enrich = bool(body.enrich_profiles) and _enrich_concurrency > 1
    enrich_throttle = (
        EnrichThrottle.from_settings(_rate_limiter) if use_parallel_enrich else None
    )
    job_lock = asyncio.Lock()
    if effective_engine == "live" and hasattr(client, "set_runtime_hooks"):
        async def _event_hook(stage: str, level: str, message: str):
            await db.add_job_event(job_id, stage, level, message)

        if use_parallel_enrich:
            # Delay sin Lock: cada enrich concurrente espera de forma independiente
            # antes de disparar su GET HTTP (evita ráfagas simultáneas a TikTok).
            # No usar _rate_limiter.wait_turn aquí: tiene asyncio.Lock y serializa
            # todos los GETs, anulando el beneficio del enrich paralelo.
            async def _enrich_http_delay():
                await asyncio.sleep(
                    random.uniform(
                        Settings.TIKTOKLEADS_DELAY_MIN,
                        Settings.TIKTOKLEADS_DELAY_MAX,
                    )
                )

            client.set_runtime_hooks(
                wait_turn_cb=_rate_limiter.wait_turn,
                wait_turn_enrich_cb=_enrich_http_delay,
                event_cb=_event_hook,
            )
        else:
            client.set_runtime_hooks(wait_turn_cb=_rate_limiter.wait_turn, event_cb=_event_hook)
    await db.set_job_engine(job_id, effective_engine, fallback_used=False)
    progress = 0
    leads_found = 0
    leads_detected = 0
    profiles_seen = 0
    errors = 0
    metrics = {
        "requests_total": 0,
        "blocked_count": 0,
        "captcha_count": 0,
        "proxy_switches": 0,
        "source_success_rate": 0.0,
    }
    async def _refresh_metrics():
        nonlocal metrics
        if effective_engine == "live" and hasattr(client, "metrics"):
            metrics = client.metrics()

    async def _emit_progress(
        disc_cnt: int,
        enrich_cnt: int,
        prog: int,
        lf: int,
        ld: int,
        ps: int,
    ):
        await db.update_job_progress(
            job_id,
            prog,
            lf,
            disc_cnt,
            enrich_cnt,
            profiles_seen=ps,
            leads_detected=ld,
            error_count=errors,
            requests_total=metrics["requests_total"],
            blocked_count=metrics["blocked_count"],
            captcha_count=metrics["captcha_count"],
            proxy_switches=metrics["proxy_switches"],
            source_success_rate=metrics["source_success_rate"],
        )

    try:
        email_goal = max(1, min(200, int(body.email_goal)))
        if not body.enrich_profiles:
            await db.finish_job(
                job_id,
                "failed",
                last_error="Cada job tiene objetivo de correos; enrich_profiles=true es obligatorio.",
            )
            return

        dedup = TikTokDeduplicator()
        await dedup.load_from_db(Settings.TIKTOKLEADS_DEDUP_STALENESS_DAYS)
        dedup_frozen = frozenset(dedup.exclude_set)

        seen_norm: set[str] = set()
        goal = email_goal
        per_email_floor = goal * max(1, Settings.TIKTOKLEADS_EMAIL_GOAL_PROFILES_PER_EMAIL)
        max_profiles = min(500, max(int(body.max_results), goal, per_email_floor))
        chunk = max(
            goal,
            min(Settings.TIKTOKLEADS_EMAIL_GOAL_DISCOVERY_CHUNK, max_profiles),
        )
        niche, loc = tiktok_serp.parse_niche_location(body.target.strip())
        if loc:
            n, L = niche.strip(), loc.strip()
            alt_keyword_targets = [
                body.target.strip(),
                f"{n} {L}",
                f"{n} {L} contacto",
                f"{n} {L} email",
                f"{n} contacto email {L}",
                f"{body.target.strip()} @gmail.com",
                f"{n} clínica {L}",
                f"{n} cita {L}",
            ]
        else:
            t = body.target.strip()
            alt_keyword_targets = [
                t,
                f"{t} contacto email",
                f"{t} @gmail.com",
                f"{t} whatsapp",
            ]
        discovery_count = 0
        enrichment_count = 0
        emails_found = 0
        rounds = 0
        no_new_email_streak = 0
        email_goal_stop_note: str | None = None
        max_stale = max(1, Settings.TIKTOKLEADS_EMAIL_GOAL_NO_EMAIL_ROUNDS)
        cancelled = False

        while (
            emails_found < goal
            and len(seen_norm) < max_profiles
            and rounds < Settings.TIKTOKLEADS_EMAIL_GOAL_MAX_ROUNDS
        ):
            rounds += 1
            if cancel_ev.is_set():
                cancelled = True
                break
            emails_before_round = emails_found
            remaining = max_profiles - len(seen_norm)
            if remaining <= 0:
                break
            ask = min(chunk, remaining)
            ti = min(rounds - 1, len(alt_keyword_targets) - 1)
            target_try = alt_keyword_targets[ti]
            try:
                items = await run_discovery(
                    client,
                    body.mode,
                    target_try,
                    ask,
                    job_id,
                    exclude_handles=seen_norm | dedup_frozen,
                )
            except Exception as live_err:
                _last_live_error = str(live_err)
                await db.add_job_event(
                    job_id,
                    "discovery",
                    "warning",
                    f"ronda {rounds} discovery: {live_err}",
                )
                items = []

            if effective_engine == "live":
                await db.add_job_event(
                    job_id,
                    "live_discovery_ok",
                    "info",
                    json.dumps(
                        {"round": rounds, "target": str(target_try)[:120], "items": len(items)}
                    ),
                )

            if not items:
                if emails_found == emails_before_round:
                    no_new_email_streak += 1
                else:
                    no_new_email_streak = 0
                if no_new_email_streak >= max_stale:
                    email_goal_stop_note = (
                        f"{max_stale} rondas seguidas sin nuevos candidatos de discovery "
                        "o sin nuevos correos."
                    )
                    await db.add_job_event(job_id, "email_goal", "info", email_goal_stop_note)
                    break
                continue

            if Settings.TIKTOKLEADS_RANK_DISCOVERY_ITEMS:
                items = sort_discovery_items_by_email_likelihood(items)
                if effective_engine == "live" and items:
                    await db.add_job_event(
                        job_id,
                        "ranking",
                        "info",
                        json.dumps(
                            {
                                "top_handle": items[0].get("author_handle"),
                                "top_score": email_likelihood_score(items[0]),
                            }
                        ),
                    )

            profiles_seen_at_round_start = profiles_seen

            discovery_count += len(items)

            async def _budget_acquire():
                if enrich_throttle is not None:
                    await enrich_throttle.acquire_start()
                else:
                    await _rate_limiter.wait_turn()

            def _budget_release():
                if enrich_throttle is not None:
                    enrich_throttle.release_slot()

            async def _enrich_one_email_goal(item: dict):
                nonlocal emails_found, leads_detected, enrichment_count, errors, profiles_seen
                await _budget_acquire()
                try:
                    async with job_lock:
                        profiles_seen += 1
                    lead, source = await _enrich_item_bounded(client, item, job_id)
                    skip_handle = None
                    skip_aid = None
                    async with job_lock:
                        email_ok = (lead.get("email") or "").strip()
                        if email_ok:
                            emails_found += 1
                            leads_detected += 1
                        else:
                            skip_handle = lead.get("handle") or item.get("author_handle")
                            skip_aid = lead.get("author_id") or item.get("author_id")
                        enrichment_count += 1
                        progress_local = emails_found
                        leads_local = emails_found
                        ld_local = leads_detected
                        ps_local = profiles_seen
                        ec_local = enrichment_count
                    if skip_handle is not None:
                        await db.insert_tiktok_skipped(skip_handle, skip_aid, "no_email")
                    await _refresh_metrics()
                    await _emit_progress(
                        discovery_count,
                        ec_local,
                        progress_local,
                        leads_local,
                        ld_local,
                        ps_local,
                    )
                except Exception as inner:
                    async with job_lock:
                        errors += 1
                        ec_local = enrichment_count
                        ef_local = emails_found
                        ld_local = leads_detected
                        ps_local = profiles_seen
                    kind, msg = _classify_enrichment_error(inner)
                    await db.add_job_event(job_id, "enrichment", "error", f"{kind}:{msg}")
                    await _rate_limiter.on_retryable_error(error_kind=kind)
                    await _refresh_metrics()
                    await _emit_progress(
                        discovery_count,
                        ec_local,
                        ef_local,
                        ef_local,
                        ld_local,
                        ps_local,
                    )
                finally:
                    await _refresh_metrics()
                    _budget_release()

            if enrich_throttle is None:
                for item in items:
                    if cancel_ev.is_set():
                        cancelled = True
                        break
                    if emails_found >= goal or len(seen_norm) >= max_profiles:
                        break
                    nk = _norm_handle_key(item.get("author_handle"))
                    if dedup.should_skip(nk):
                        await db.add_job_event(
                            job_id,
                            "dedup",
                            "info",
                            f"omitido (ya en base o skipped reciente): @{nk}",
                        )
                        continue
                    if nk:
                        seen_norm.add(nk)
                    await _enrich_one_email_goal(item)
                    if cancel_ev.is_set():
                        cancelled = True
                        break
                    if emails_found >= goal:
                        break
            else:
                batch: list[dict] = []
                for item in items:
                    if cancel_ev.is_set():
                        cancelled = True
                        break
                    if emails_found >= goal or len(seen_norm) >= max_profiles:
                        break
                    nk = _norm_handle_key(item.get("author_handle"))
                    if dedup.should_skip(nk):
                        await db.add_job_event(
                            job_id,
                            "dedup",
                            "info",
                            f"omitido (ya en base o skipped reciente): @{nk}",
                        )
                        continue
                    if nk:
                        seen_norm.add(nk)
                    batch.append(item)
                    if len(batch) >= _enrich_concurrency:
                        await asyncio.gather(*(_enrich_one_email_goal(x) for x in batch))
                        batch.clear()
                        if cancel_ev.is_set():
                            cancelled = True
                            break
                        if emails_found >= goal:
                            break
                if batch and not cancelled:
                    await asyncio.gather(*(_enrich_one_email_goal(x) for x in batch))
                if cancel_ev.is_set():
                    cancelled = True

            if cancelled:
                break

            if emails_found >= goal:
                break

            if emails_found > emails_before_round:
                no_new_email_streak = 0
            elif emails_found == emails_before_round:
                # Solo cuenta si hubo al menos un enrich: si todo fue dedup, no penalizar
                # (el discovery repite handles ya vistos / en base).
                if profiles_seen > profiles_seen_at_round_start:
                    no_new_email_streak += 1
            if no_new_email_streak >= max_stale:
                email_goal_stop_note = (
                    f"{max_stale} rondas seguidas con perfiles analizados pero sin nuevo correo "
                    "(discovery siguió aportando candidatos nuevos para enrich)."
                )
                await db.add_job_event(job_id, "email_goal", "info", email_goal_stop_note)
                break

        if cancelled:
            cancel_msg = "Cancelado por el usuario."
            await db.add_job_event(
                job_id,
                "summary",
                "info",
                json.dumps(
                    {
                        "cancelled": True,
                        "engine": effective_engine,
                        "email_goal": goal,
                        "emails_found": emails_found,
                        "profiles_seen": profiles_seen,
                        "leads_detected": leads_detected,
                        "errors": errors,
                        "discovery_rounds": rounds,
                        **metrics,
                    }
                ),
            )
            if effective_engine == "live":
                await db.save_proxy_stats(TikTokLiveClient.proxy_stats())
            await db.finish_job(job_id, "cancelled", last_error=cancel_msg)
            return

        last_partial: str | None = None
        if emails_found < goal:
            last_partial = (
                f"Objetivo correos: {emails_found}/{goal}. "
                f"Perfiles analizados: {profiles_seen}. "
            )
            if email_goal_stop_note:
                last_partial += email_goal_stop_note
            else:
                last_partial += (
                    "No se encontraron más candidatos o se alcanzó el límite de rondas."
                )
            await db.add_job_event(job_id, "email_goal", "warning", last_partial)

        final_status = "completed" if emails_found >= goal and errors == 0 else "completed_partial"
        await db.add_job_event(
            job_id,
            "summary",
            "info",
            json.dumps(
                {
                    "engine": effective_engine,
                    "email_goal": goal,
                    "emails_found": emails_found,
                    "profiles_seen": profiles_seen,
                    "leads_detected": leads_detected,
                    "errors": errors,
                    "discovery_rounds": rounds,
                    **metrics,
                }
            ),
        )
        if effective_engine == "live":
            await db.save_proxy_stats(TikTokLiveClient.proxy_stats())
        await db.finish_job(job_id, final_status, last_error=last_partial)
    except Exception as exc:
        logger.exception("TikTok job failed: %s", exc)
        if effective_engine == "live":
            _last_live_error = str(exc)
        await db.finish_job(job_id, "failed", last_error=str(exc))
    finally:
        _job_cancel_events.pop(job_id, None)


@router.get("/leads")
async def get_leads(limit: int = 200, offset: int = 0, job_id: str | None = None):
    if job_id:
        return await db.get_leads_by_job(job_id)
    return await db.get_all_leads(limit=limit, offset=offset)


@router.get("/stats")
async def stats():
    return {"total_leads": await db.count_leads()}


@router.get("/export")
async def export_all():
    return await _export_csv(job_id=None)


@router.get("/export/{job_id}")
async def export_by_job(job_id: str):
    return await _export_csv(job_id=job_id)


async def _export_csv(job_id: str | None):
    leads = await (db.get_leads_by_job(job_id) if job_id else db.get_all_leads(limit=10000, offset=0))
    if not leads:
        raise HTTPException(status_code=404, detail="No leads found")
    output = io.StringIO()
    fields = ["author_id", "handle", "email", "phone", "website", "source_confidence", "first_seen_at", "last_seen_at"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for lead in leads:
        writer.writerow({k: lead.get(k) for k in fields})
    output.seek(0)
    filename = f"tiktokleads_{job_id[:8]}.csv" if job_id else "tiktokleads_all.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
