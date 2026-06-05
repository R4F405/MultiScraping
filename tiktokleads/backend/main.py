import logging

from fastapi import FastAPI

from backend.api.routes import router
from backend.scraper.tiktok_client import TikTokLiveClient
from backend.storage.database import finish_job, get_all_jobs, init_db, load_proxy_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="TikTokLeads", version="1.0.0")
app.include_router(router)


@app.on_event("startup")
async def startup():
    await init_db()
    TikTokLiveClient.load_proxy_stats(await load_proxy_stats())
    jobs = await get_all_jobs(limit=100)
    for job in jobs:
        if job.get("status") == "running":
            await finish_job(job["job_id"], "failed", last_error="Orphaned on startup")

