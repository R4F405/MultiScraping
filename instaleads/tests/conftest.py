"""
Test configuration: initializes an in-memory SQLite DB before tests run
so the app has the required tables without needing the full lifespan.
"""
import os
import tempfile

import pytest

# Point DB to a temp file BEFORE any backend module is imported
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name
os.environ["SESSION_FILE"] = "/tmp/test_session.json"


@pytest.fixture(scope="session", autouse=True)
def init_test_db():
    """Create all tables in the temp DB before any test runs."""
    import asyncio
    from backend.storage.database import init_db

    asyncio.run(init_db())
