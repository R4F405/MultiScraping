import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from backend.api.routes import router
from backend.config.settings import settings
from backend.storage.database import init_db

logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = ("/api/health",)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.api_key:
        logger.info("MapLeads API ready — API key authentication enabled")
    else:
        logger.info("MapLeads API ready — API key not set (open access)")
    yield


app = FastAPI(title="MapLeads API", lifespan=lifespan)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """
    Optional API key guard. Only active when API_KEY is set in .env.

    Clients must send:  X-API-Key: <your-key>
    or as query param:  ?api_key=<your-key>
    """
    if not settings.api_key:
        return await call_next(request)

    path = request.url.path
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if provided != settings.api_key:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or missing API key. Send X-API-Key header."},
        )

    return await call_next(request)


app.include_router(router)
