import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.config.settings import settings
from backend.storage.database import init_db
from backend.tiktok.tt_browser import close_browser
from backend.tiktok.tt_deduplicator import deduplicator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    data_dir = os.path.dirname(settings.db_path)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
    await init_db()
    await deduplicator.preload()
    logger.info("TikTokLeads API ready on port %d (headless=%s)", settings.port, settings.headless)
    yield
    # Shutdown
    await close_browser()
    logger.info("TikTokLeads API shut down")


app = FastAPI(
    title="TikTokLeads API",
    description="Scraper de TikTok para extracción de leads por hashtag o keyword",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:8081",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8081",
    ],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/tiktok", tags=["tiktok"])


@app.get("/")
async def root():
    return {
        "service": "TikTokLeads",
        "version": "1.0.0",
        "docs": "/docs",
        "port": settings.port,
    }
