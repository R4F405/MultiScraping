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
    logging.getLogger(__name__).info("InstaLeads started")


@app.get("/")
async def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "instagram.html"))
