import asyncio
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

MAPLEADS_URL = os.getenv("MAPLEADS_API_URL", "http://localhost:8001").rstrip("/")
INSTALEADS_URL = os.getenv("INSTALEADS_API_URL", "http://localhost:8002").rstrip("/")
LINKEDINLEADS_URL = os.getenv("LINKEDINLEADS_API_URL", "http://localhost:8003").rstrip("/")
MAPLEADS_API_KEY = os.getenv("MAPLEADS_API_KEY", "").strip()

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ── Jinja2 custom filters ────────────────────────────────────────────────────

def format_number(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value) if value is not None else "0"


def format_date(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        months = ["ene", "feb", "mar", "abr", "may", "jun",
                  "jul", "ago", "sep", "oct", "nov", "dic"]
        return f"{dt.day} {months[dt.month - 1]} {dt.year}"
    except Exception:
        return str(value)


def format_duration(started_at, finished_at):
    if not started_at or not finished_at:
        return "—"
    try:
        start = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        secs = int(abs((end - start).total_seconds()))
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return "—"


templates.env.filters["format_number"] = format_number
templates.env.filters["format_date"] = format_date
templates.env.filters["format_duration"] = format_duration
templates.env.globals["format_duration"] = format_duration


# ── HTTP helpers ─────────────────────────────────────────────────────────────

async def fetch_json(url: str, params: dict | None = None, timeout: float = 10.0) -> dict | list:
    headers = {}
    if MAPLEADS_API_KEY and url.startswith(MAPLEADS_URL):
        headers["X-API-Key"] = MAPLEADS_API_KEY
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()


async def safe_fetch(url: str, params: dict | None = None, timeout: float = 10.0):
    """Returns (data, state) where state is 'ok' | 'timeout' | 'upstream_error'."""
    try:
        data = await fetch_json(url, params=params, timeout=timeout)
        return data, "ok"
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception:
        return None, "upstream_error"


# ── Page routes ──────────────────────────────────────────────────────────────

@app.get("/")
async def home(request: Request):
    jobs, jobs_state = await safe_fetch(f"{MAPLEADS_URL}/api/jobs", {"limit": 3})
    jobs_message = None
    if jobs_state == "timeout":
        jobs_message = "No se pudo cargar la actividad reciente por tiempo de espera."
    elif jobs_state == "upstream_error":
        jobs_message = "No se pudo cargar la actividad reciente por un error del backend."
    elif not jobs:
        jobs_state = "empty"
        jobs = []

    proxy_status, _ = await safe_fetch(f"{MAPLEADS_URL}/api/proxy/status", timeout=5.0)
    proxy_status = proxy_status or {}

    return templates.TemplateResponse("home.html", {
        "request": request,
        "jobs": jobs or [],
        "jobs_state": jobs_state,
        "jobs_message": jobs_message,
        "proxy_status": proxy_status,
    })


@app.get("/search")
async def search(request: Request):
    proxy_status, proxy_state = await safe_fetch(f"{MAPLEADS_URL}/api/proxy/status", timeout=5.0)
    proxy_message = None
    if proxy_state == "timeout":
        proxy_message = "El backend no responde (timeout). Puedes reintentar en unos segundos."
    elif proxy_state == "upstream_error":
        proxy_message = "No se pudo obtener el estado del backend. Puedes reintentar."
    proxy_status = proxy_status or {}

    return templates.TemplateResponse("search.html", {
        "request": request,
        "proxy_status": proxy_status,
        "proxy_state": proxy_state,
        "proxy_message": proxy_message,
    })


@app.get("/leads")
async def leads(request: Request, job_id: str | None = Query(default=None)):
    job, job_state = {}, "empty"
    job_message = None

    if job_id:
        job, job_state = await safe_fetch(f"{MAPLEADS_URL}/api/jobs/{job_id}", timeout=10.0)
        job = job or {}
        if job_state == "timeout":
            job_message = "No se pudo cargar el detalle del job por tiempo de espera."
        elif job_state == "upstream_error":
            job_message = "No se pudo cargar el detalle del job por un error del backend."

    leads_limit = 500
    if job_id and job and isinstance(job.get("total"), (int, float)):
        leads_limit = max(1, min(int(job["total"]), 1000))

    leads_params = {"limit": leads_limit}
    if job_id:
        leads_params["job_id"] = job_id

    leads_data, leads_state = await safe_fetch(f"{MAPLEADS_URL}/api/leads", params=leads_params, timeout=15.0)
    leads_data = leads_data or []
    leads_message = None
    if leads_state == "timeout":
        leads_message = "No se pudieron cargar los leads por tiempo de espera."
    elif leads_state == "upstream_error":
        leads_message = "No se pudieron cargar los leads por un error del backend."

    proxy_status, _ = await safe_fetch(f"{MAPLEADS_URL}/api/proxy/status", timeout=5.0)
    proxy_status = proxy_status or {}

    jobs, jobs_state, jobs_message = [], "empty", None
    can_load_jobs = job_state not in ("upstream_error", "timeout") and leads_state not in ("upstream_error", "timeout")
    if can_load_jobs:
        jobs, jobs_state = await safe_fetch(f"{MAPLEADS_URL}/api/jobs", {"limit": 200}, timeout=10.0)
        jobs = jobs or []
        if jobs_state == "timeout":
            jobs_message = "No se pudo cargar la lista de scrapeos por tiempo de espera."
        elif jobs_state == "upstream_error":
            jobs_message = "No se pudo cargar la lista de scrapeos por un error del backend."

    # Resolve missing query/location from jobs list
    if job_id and job and (not job.get("query") or not job.get("location")):
        for j in (jobs or []):
            jid = str(j.get("job_id") or j.get("id") or "")
            if jid == str(job_id):
                if not job.get("query"):
                    job["query"] = j.get("query")
                if not job.get("location"):
                    job["location"] = j.get("location")
                break

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "job": job,
        "job_id": job_id,
        "job_state": job_state,
        "job_message": job_message,
        "leads": leads_data,
        "leads_state": leads_state,
        "leads_message": leads_message,
        "proxy_status": proxy_status,
        "jobs": jobs,
        "jobs_state": jobs_state,
        "jobs_message": jobs_message,
    })


@app.get("/history")
async def history(request: Request):
    jobs, jobs_state = await safe_fetch(f"{MAPLEADS_URL}/api/jobs", {"limit": 200}, timeout=10.0)
    jobs = jobs or []
    jobs_message = None
    if jobs_state == "timeout":
        jobs_message = "No se pudo cargar el historial por tiempo de espera."
    elif jobs_state == "upstream_error":
        jobs_message = "No se pudo cargar el historial por un error del backend."
    elif not jobs:
        jobs_state = "empty"

    proxy_status, _ = await safe_fetch(f"{MAPLEADS_URL}/api/proxy/status", timeout=5.0)
    proxy_status = proxy_status or {}

    return templates.TemplateResponse("history.html", {
        "request": request,
        "jobs": jobs,
        "jobs_state": jobs_state,
        "jobs_message": jobs_message,
        "proxy_status": proxy_status,
    })


@app.get("/databases")
async def databases(request: Request):
    ml_stats_task = safe_fetch(f"{MAPLEADS_URL}/api/stats", timeout=10.0)
    proxy_task = safe_fetch(f"{MAPLEADS_URL}/api/proxy/status", timeout=5.0)
    li_stats_task = safe_fetch(f"{LINKEDINLEADS_URL}/api/linkedin/stats", timeout=5.0)

    (stats, _), (proxy_status, _), (li_data, _) = await asyncio.gather(
        ml_stats_task, proxy_task, li_stats_task
    )

    stats = stats or {}
    proxy_status = proxy_status or {}
    instagram_stats = 0

    linkedin_stats = {}
    if li_data and isinstance(li_data, dict):
        linkedin_stats = li_data

    return templates.TemplateResponse("databases.html", {
        "request": request,
        "stats": stats,
        "proxy_status": proxy_status,
        "instagram_stats": instagram_stats,
        "linkedin_stats": linkedin_stats,
    })


@app.get("/instagram")
async def instagram(request: Request, from_page: str | None = Query(default=None, alias="from")):
    _ = from_page
    return templates.TemplateResponse("instagram.html", {"request": request})


@app.get("/instagram/leads")
async def instagram_leads(request: Request, job_id: str | None = Query(default=None)):
    _ = job_id
    return templates.TemplateResponse("instagram_leads.html", {"request": request})


# ── Proxy routes: Google Maps → localhost:8001 ────────────────────────────────

PROXY_TIMEOUT = 25.0


async def _proxy_to(target_url: str, request: Request) -> Response:
    method = request.method.upper()
    query = str(request.url.query)
    url = f"{target_url}?{query}" if query else target_url

    allowed_headers = {"accept", "content-type"}
    headers = {k: v for k, v in request.headers.items() if k.lower() in allowed_headers}
    if MAPLEADS_API_KEY and target_url.startswith(f"{MAPLEADS_URL}/"):
        headers["X-API-Key"] = MAPLEADS_API_KEY

    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                r = await client.get(url, headers=headers, timeout=PROXY_TIMEOUT)
            elif method == "POST":
                body = await request.body()
                r = await client.post(url, content=body, headers=headers, timeout=PROXY_TIMEOUT)
            elif method == "DELETE":
                r = await client.delete(url, headers=headers, timeout=PROXY_TIMEOUT)
            else:
                return JSONResponse({"message": "Method not allowed"}, status_code=405)
        except Exception:
            return JSONResponse({"message": "Upstream API unavailable"}, status_code=502)

    content_type = r.headers.get("content-type", "application/json")

    # Stream binary responses (CSV exports)
    if "text/csv" in content_type or "octet-stream" in content_type:
        return StreamingResponse(
            iter([r.content]),
            status_code=r.status_code,
            headers={"Content-Type": content_type,
                     "Content-Disposition": r.headers.get("Content-Disposition", "")},
        )

    return Response(content=r.content, status_code=r.status_code,
                    media_type=content_type)


@app.get("/api/proxy/status")
async def proxy_status(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/proxy/status", request)


@app.get("/api/proxy/capacity")
async def proxy_capacity(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/proxy/capacity", request)


@app.post("/api/search")
async def api_search(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/search", request)


@app.get("/api/maps/categories")
async def api_maps_categories(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/maps/categories", request)


@app.post("/api/maps/categories/sync")
async def api_maps_categories_sync(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/maps/categories/sync", request)


@app.get("/api/maps/categories/sync/status")
async def api_maps_categories_sync_status(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/maps/categories/sync/status", request)


@app.get("/api/maps/categories/sync/report")
async def api_maps_categories_sync_report(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/maps/categories/sync/report", request)


@app.get("/api/leads")
async def api_leads(request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/leads", request)


@app.delete("/api/leads/{lead_id}")
async def api_delete_lead(lead_id: str, request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/leads/{lead_id}", request)


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str, request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/jobs/{job_id}", request)


@app.get("/api/jobs/{job_id}/locations")
async def api_job_locations(job_id: str, request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/jobs/{job_id}/locations", request)


@app.get("/api/export/{job_id}")
async def api_export(job_id: str, request: Request):
    return await _proxy_to(f"{MAPLEADS_URL}/api/export/{job_id}", request)


# ── Proxy routes: Instagram → localhost:8002 ──────────────────────────────────

@app.get("/api/instagram/health")
async def ig_health(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/health", request)


@app.get("/api/instagram/debug/last")
async def ig_debug_last(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/debug/last", request)


@app.post("/api/instagram/diagnose")
async def ig_diagnose(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/diagnose", request)


@app.get("/api/instagram/profile/{username}")
async def ig_profile(username: str, request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/profile/{username}", request)


@app.post("/api/instagram/search")
async def ig_search(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/search", request)


@app.get("/api/instagram/jobs")
async def ig_jobs(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/jobs", request)


@app.get("/api/instagram/jobs/{job_id}")
async def ig_job(job_id: str, request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/jobs/{job_id}", request)


@app.get("/api/instagram/leads")
async def ig_leads(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/leads", request)


@app.get("/api/instagram/export/{job_id}")
async def ig_export(job_id: str, request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/export/{job_id}", request)


@app.post("/api/instagram/login")
async def ig_login(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/login", request)


@app.get("/api/instagram/session")
async def ig_session_get(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/session", request)


@app.delete("/api/instagram/session")
async def ig_session_delete(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/session", request)


@app.get("/api/instagram/limits")
async def ig_limits(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/limits", request)


@app.get("/api/instagram/accounts")
async def ig_accounts_list(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/accounts", request)


@app.post("/api/instagram/accounts")
async def ig_accounts_add(request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/accounts", request)


@app.delete("/api/instagram/accounts/{username}")
async def ig_accounts_remove(username: str, request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/accounts/{username}", request)


@app.post("/api/instagram/accounts/relogin/{username}")
async def ig_accounts_relogin(username: str, request: Request):
    return await _proxy_to(f"{INSTALEADS_URL}/api/instagram/accounts/relogin/{username}", request)



@app.get("/tiktok")
async def tiktok_page(request: Request):
    return templates.TemplateResponse("tiktok.html", {"request": request})


@app.get("/tiktok/leads")
async def tiktok_leads(request: Request, job_id: str | None = Query(default=None)):
    _ = job_id
    return templates.TemplateResponse("tiktok_leads.html", {"request": request})


@app.get("/linkedin")
async def linkedin_page(request: Request):
    health, health_state = await safe_fetch(f"{LINKEDINLEADS_URL}/api/linkedin/health", timeout=5.0)
    health = health or {"status": "unknown", "db_exists": False, "accounts_count": 0}
    state = "ok"
    message = None
    if health_state == "timeout":
        state = "timeout"
        message = "El backend de LinkedIn no responde. Asegúrate de que LinkedInLeads está corriendo en el puerto 8003."
    elif health_state == "upstream_error":
        state = "upstream_error"
        message = "No se pudo cargar el estado de LinkedIn."
    return templates.TemplateResponse("linkedin.html", {
        "request": request,
        "health": health,
        "state": state,
        "message": message,
    })


# ── Proxy routes: LinkedIn → localhost:8003 ───────────────────────────────────

@app.get("/api/linkedin/health")
async def li_health(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/health", request)


@app.get("/api/linkedin/stats")
async def li_stats(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/stats", request)


@app.post("/api/linkedin/search")
async def li_search(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/search", request)


@app.get("/api/linkedin/status")
async def li_status(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/status", request)


@app.get("/api/linkedin/jobs")
async def li_jobs(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/jobs", request)


@app.get("/api/linkedin/leads")
async def li_leads(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/leads", request)


@app.get("/api/linkedin/leads/export")
async def li_leads_export(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/leads/export", request)


@app.get("/api/linkedin/accounts")
async def li_accounts_list(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/accounts", request)


@app.post("/api/linkedin/accounts")
async def li_accounts_add(request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/accounts", request)


@app.delete("/api/linkedin/accounts/{username}")
async def li_accounts_delete(username: str, request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/accounts/{username}", request)


@app.get("/api/linkedin/accounts/{username}/stats")
async def li_account_stats(username: str, request: Request):
    return await _proxy_to(f"{LINKEDINLEADS_URL}/api/linkedin/accounts/{username}/stats", request)


@app.get("/api/instagram/avatar")
async def ig_avatar(url: str):
    """
    Proxy avatar images from Instagram CDN to avoid hotlink/referrer blocks.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return JSONResponse({"message": "Invalid avatar URL"}, status_code=400)
        host = (parsed.hostname or "").lower()
        if not host.endswith("fbcdn.net"):
            return JSONResponse({"message": "Avatar host not allowed"}, status_code=400)
    except Exception:
        return JSONResponse({"message": "Invalid avatar URL"}, status_code=400)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.instagram.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, headers=headers, timeout=15.0)
        except Exception:
            return JSONResponse({"message": "Could not fetch avatar"}, status_code=502)

    if r.status_code != 200:
        return JSONResponse({"message": "Avatar unavailable"}, status_code=502)

    media_type = r.headers.get("content-type", "image/jpeg")
    return Response(content=r.content, media_type=media_type)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8081"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
