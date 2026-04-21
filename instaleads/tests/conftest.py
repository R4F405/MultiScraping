import os

# Must be set before any backend imports that read Settings
os.environ.setdefault("IG_SESSION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXQ=")

from backend.config.settings import Settings

# Redirect DB to in-memory SQLite for all tests
Settings.DB_PATH = ":memory:"
