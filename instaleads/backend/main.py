import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import proxy_router, router
from backend.config.settings import settings
from backend.instagram import ig_client
from backend.instagram.ig_account_pool import account_pool
from backend.instagram.ig_deduplicator import deduplicator
from backend.instagram.ig_session import load_session
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

    await deduplicator.preload()
    logger.info("Deduplicator loaded %d seen usernames", deduplicator.seen_count)

    # Check discovery strategies
    google_api = os.getenv("GOOGLE_API_KEY", "").strip()
    google_cse = os.getenv("GOOGLE_CSE_ID", "").strip()
    if google_api and google_cse:
        logger.info("✅ Google CSE configured — will use for best dorking results")
    else:
        logger.warning("⚠️ Google CSE NOT configured — dorking will use fallback strategies (slower, lower quality)")
        logger.warning("   To enable Google CSE:")
        logger.warning("   1. Go to https://console.cloud.google.com/")
        logger.warning("   2. Enable 'Custom Search JSON API' in your project")
        logger.warning("   3. Create an API Key")
        logger.warning("   4. Update instaleads/.env with GOOGLE_API_KEY and GOOGLE_CSE_ID")

    restored = await load_session()
    if restored:
        logger.info("✅ Instagram session restored from disk — Mode B (Followers) available")
    else:
        logger.warning("⚠️ No Instagram session — authenticated mode unavailable")
        logger.warning("   To enable Mode B: Use UI → Autenticación → Iniciar sesión Instagram")

    await account_pool.load_all_sessions()
    if account_pool.count() > 0:
        logger.info("✅ Account pool loaded: %d accounts available", account_pool.count())
    else:
        logger.info("ℹ️ Account pool empty — add accounts via UI for higher throughput")

    yield

    logger.info("InstaLeads backend shutting down")
    await ig_client.close_session()


app = FastAPI(
    title="InstaLeads API",
    description="Instagram lead scraper — extract emails from profiles",
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
