import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent   # linkedinleads/
_BACKEND_DIR = Path(__file__).resolve().parent.parent      # linkedinleads/backend/
DATA_DIR = _BACKEND_DIR / "data"                           # linkedinleads/backend/data/ — mismo que db.py
SESSIONS_DIR = BASE_DIR / "sessions"                       # linkedinleads/sessions/
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"

# Asegurar que existen los directorios de datos
for _d in (DATA_DIR, SESSIONS_DIR, OUTPUT_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = os.getenv("DB_PATH") or str(DATA_DIR / "contacts.db")

PORT = int(os.getenv("LINKEDIN_API_PORT", "8003"))
