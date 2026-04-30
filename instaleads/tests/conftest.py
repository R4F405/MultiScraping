import os

from backend.config.settings import Settings

# Redirect DB to in-memory SQLite for all tests
Settings.DB_PATH = ":memory:"
