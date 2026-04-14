import os
import tempfile

import pytest

# Use a temporary database for all tests — never touches the real data file
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()

os.environ["DB_PATH"] = _tmp_db.name


@pytest.fixture(autouse=True, scope="session")
async def setup_test_db():
    """Initialize the test database once before any test runs."""
    from backend.storage.database import init_db
    await init_db()
    yield
    os.unlink(_tmp_db.name)
