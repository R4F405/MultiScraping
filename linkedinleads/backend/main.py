"""
LinkedIn Scraper — Backend FastAPI

Puerto por defecto: 8003

Ejecutar:
  cd linkedinleads
  uvicorn backend.main:app --port 8003 --reload
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Asegurar que el directorio raíz de linkedinleads/ esté en el path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización al arrancar."""
    from backend.log_config import setup_logging
    from backend.db import ensure_tables

    setup_logging()
    logger = logging.getLogger(__name__)

    # Asegurar tablas SQLite
    try:
        ensure_tables()
        logger.info("LinkedIn backend: tablas SQLite listas")
    except Exception as exc:
        logger.error("LinkedIn backend: error al inicializar BD: %s", exc)

    logger.info("LinkedIn backend arrancado en puerto %s", os.getenv("LINKEDIN_API_PORT", "8003"))
    yield
    logger.info("LinkedIn backend detenido")


app = FastAPI(title="LinkedIn Scraper API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8081", "http://127.0.0.1:8081", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.api.routes import router  # noqa: E402
app.include_router(router)


@app.get("/health")
async def root_health():
    return {"status": "ok", "service": "linkedin-scraper"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("LINKEDIN_API_PORT", "8003"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
