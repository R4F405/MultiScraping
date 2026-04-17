import uuid
from asyncio import Task, create_task
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from backend.api.schemas import (
    AccountAddRequest,
    DiagnoseResponse,
    HealthResponse,
    JobResponse,
    ProfilePreview,
    ProxyStatusResponse,
    SearchRequest,
    SessionLoginRequest,
)
from backend.config.settings import settings
from backend.instagram.ig_health import get_health, snapshot_metrics
from backend.instagram.ig_proxy_manager import proxy_manager
from backend.instagram.ig_service import run_job
from backend.storage import database

router = APIRouter()
proxy_router = APIRouter()

_job_tasks: dict[str, Task[Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_payload(job_id: str, request: SearchRequest) -> dict[str, Any]:
    now = _now_iso()
    target = request.target or " ".join(filter(None, [request.niche, request.location])).strip()
    return {
        "job_id": job_id,
        "mode": request.mode,
        "target": target,
        "niche": request.niche,
        "location": request.location,
        "language": request.language,
        "market": request.market,
        "email_goal": request.email_goal,
        "status": "queued",
        "progress": 0,
        "total": request.email_goal,
        "emails_found": 0,
        "status_detail": "Job encolado",
        "started_at": now,
        "finished_at": None,
    }


@router.get("/health", response_model=HealthResponse)
async def health() -> Any:
    return await get_health()


@router.api_route("/diagnose", methods=["GET", "POST"], response_model=DiagnoseResponse)
async def diagnose() -> Any:
    health = await get_health()
    return {
        "blocked": health["status"] == "blocked",
        "rate_limited": health["status"] == "rate_limited",
        "last_error": health.get("last_error"),
        "consecutive_errors": health.get("consecutive_errors", 0),
        "session_active": True,
    }


@router.get("/debug/last")
async def debug_last() -> Any:
    leads = await database.list_leads(limit=1)
    return {
        "stats": snapshot_metrics(),
        "last_lead": leads[0] if leads else None,
        "message": "debug_ok",
    }


@router.post("/login")
async def login(body: SessionLoginRequest) -> Any:
    await database.upsert_account(body.username)
    return {"status": "ok", "username": body.username}


@router.get("/session")
async def get_session() -> Any:
    accounts = await database.list_accounts()
    return {"logged_in": bool(accounts), "username": accounts[0]["username"] if accounts else None, "session_age_hours": 0}


@router.delete("/session")
async def delete_session() -> Any:
    return {"status": "ok"}


@router.post("/session/login")
async def session_login(body: SessionLoginRequest) -> Any:
    return await login(body)


@router.post("/session/logout")
async def session_logout() -> Any:
    return {"status": "ok"}


@router.get("/session/status")
async def session_status() -> Any:
    accounts = await database.list_accounts()
    return {"active": bool(accounts)}


@router.get("/limits")
async def limits() -> Any:
    return {
        "can_start_dorking": True,
        "can_start_followers": True,
        "unauth_daily_reached": False,
        "auth_daily_reached": False,
        "auth_hourly_reached": False,
        "used_today_unauth": 0,
        "used_today_auth": 0,
        "used_this_hour_auth": 0,
        "hourly_auth": settings.max_hourly_auth,
        "message": "ok",
    }


@router.get("/accounts")
async def list_accounts() -> Any:
    return await database.list_accounts()


@router.post("/accounts")
async def add_account(body: AccountAddRequest) -> Any:
    await database.upsert_account(body.username)
    return {"status": "ok", "username": body.username}


@router.post("/accounts/{username}/relogin")
async def relogin_account(username: str) -> Any:
    await database.upsert_account(username)
    return {"status": "ok", "username": username}


@router.delete("/accounts/{username}")
async def remove_account(_username: str) -> Any:
    await database.remove_account(_username)
    return {"status": "ok"}


@router.get("/profile/{username}", response_model=ProfilePreview)
async def profile(username: str) -> Any:
    leads = await database.list_leads(limit=500)
    for lead in leads:
        if lead["username"] == username:
            return {
                "username": username,
                "full_name": username,
                "biography": "Perfil detectado en campaign pipeline",
                "bio_url": None,
                "is_business_account": True,
                "follower_count": None,
                "profile_pic_url": None,
                "email": lead["email"],
                "email_source": lead["email_source"],
                "is_private": False,
                "phone": lead["phone"],
                "business_category": lead["business_category"],
            }
    raise HTTPException(status_code=404, detail=f"Perfil no encontrado (@{username})")


@router.post("/search", response_model=JobResponse)
async def start_search(body: SearchRequest) -> Any:
    job_id = str(uuid.uuid4())
    payload = _job_payload(job_id, body)
    await database.create_job(payload)
    task = create_task(run_job(job_id))
    _job_tasks[job_id] = task
    return payload


@router.get("/jobs")
async def list_jobs(limit: int = 20) -> Any:
    return await database.list_jobs(limit=limit)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> Any:
    payload = await database.get_job(job_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Job not found")
    return payload


@router.get("/stats")
async def get_stats() -> Any:
    stats = await database.get_today_stats()
    return {"today": stats["leads"], "total": stats["leads"], "message": "ok"}


@router.get("/diagnostics/accounts")
async def diagnostics_accounts(hours: int = 24) -> Any:
    return {"hours": hours, "accounts": await database.list_accounts(), "message": "ok"}


@router.get("/diagnostics/pipeline")
async def diagnostics_pipeline() -> Any:
    payload = await database.diagnostics_pipeline()
    payload["fallback_usage"] = snapshot_metrics().get("discovery", {})
    payload["message"] = "ok"
    return payload


@router.get("/leads")
async def list_leads(job_id: str | None = None, limit: int = 200) -> Any:
    return await database.list_leads(job_id=job_id, limit=limit)


@router.get("/export/{job_id}")
async def export(job_id: str) -> Any:
    leads = await database.list_leads(job_id=job_id, limit=2000)
    if not leads:
        raise HTTPException(status_code=404, detail=f"No hay leads para {job_id}")
    headers = ["username", "email", "email_source", "confidence", "phone", "business_category", "created_at"]
    rows = [",".join(headers)]
    for lead in leads:
        row = ",".join([str(lead.get(key, "") or "") for key in headers])
        rows.append(row)
    return Response(content="\n".join(rows), media_type="text/csv")


@proxy_router.get("/status", response_model=ProxyStatusResponse)
async def proxy_status() -> Any:
    return {"available": proxy_manager.has_proxy(), "message": "ok"}
