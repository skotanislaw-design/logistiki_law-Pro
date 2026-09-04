"""Offline unit-test fixtures. Uses an in-memory Mongo (mongomock) and never touches the network."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MONGO_URL", "mongomock://")
os.environ.setdefault("DB_NAME", "nomos_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", str(Path(__file__).parent / ".uploads"))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    """Drop every collection before each test so unit tests stay independent."""
    import asyncio
    from db import db

    async def _drop():
        for name in await db.list_collection_names():
            await db[name].drop()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_drop())
    yield
