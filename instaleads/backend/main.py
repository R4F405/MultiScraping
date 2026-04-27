import logging
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import router
from backend.storage.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="InstaLeads", version="1.0.0")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(router)


@app.on_event("startup")
async def startup():
    await init_db()
    from backend.storage import database as db
    orphaned = await db.get_all_jobs(limit=100)
    count = 0
    for job in orphaned:
        if job.get("status") == "running":
            await db.finish_job(job["job_id"], "failed")
            count += 1
    logger = logging.getLogger(__name__)
    if count:
        logger.warning("Marked %d orphaned running jobs as failed on startup", count)
    logger.info("InstaLeads started")


@app.get("/")
async def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "instagram.html"))
