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
os.environ["DELAY_UNAUTH_MIN"] = "0.01"
os.environ["DELAY_UNAUTH_MAX"] = "0.02"
os.environ["RETRY_MAX_ATTEMPTS"] = "1"
os.environ["ENRICHMENT_HTTP_TIMEOUT_SEC"] = "0.5"


@pytest.fixture(scope="session", autouse=True)
def init_test_db():
    """Create all tables in the temp DB before any test runs."""
    import asyncio
    from backend.storage.database import init_db

    asyncio.run(init_db())
