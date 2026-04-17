import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import proxy_router, router
from backend.config.settings import settings
from backend.storage.database import init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directory exists
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)

    await init_db()
    logger.info("Database initialized: %s", settings.db_path)
    logger.info("Instagram leads pipeline initialized (hybrid discovery + enrichment).")

    yield

    logger.info("InstaLeads backend shutting down")


app = FastAPI(
    title="InstaLeads API",
    description="Instagram backend para captacion automatica de leads",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/instagram", tags=["instagram"])
app.include_router(proxy_router, prefix="/api/proxy", tags=["proxy"])


@app.get("/", tags=["root"])
async def root():
    return {"service": "InstaLeads", "version": "1.0.0", "docs": "/docs"}
