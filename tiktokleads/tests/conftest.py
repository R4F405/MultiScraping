"""
Test configuration: initializes a temp SQLite DB before tests run.
The DB path is set BEFORE any backend module is imported.
"""
import os
import tempfile

import pytest

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_PATH"] = _tmp_db.name


@pytest.fixture(scope="session", autouse=True)
def init_test_db():
    """Create all tables in the temp DB before any test runs."""
    import asyncio
    from backend.storage.database import init_db

    asyncio.run(init_db())
