import os
import sys

# Tests: piso de perfiles bajo para no multiplicar `wait_turn` en jobs mock (producción usa el default del .env).
os.environ.setdefault("TIKTOKLEADS_EMAIL_GOAL_PROFILES_PER_EMAIL", "12")

from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.config.settings import Settings

TEST_DB = Path(__file__).parent / "test_tiktokleads.sqlite"
if TEST_DB.exists():
    TEST_DB.unlink()
Settings.DB_PATH = str(TEST_DB)
