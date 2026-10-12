"""Offline unit-test fixtures. Uses an in-memory Mongo (mongomock) and never touches the network."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MONGO_URL", "mongomock://")
os.environ.setdefault("DB_NAME", "nomos_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-with-enough-entropy-for-hs256")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("UPLOAD_DIR", str(Path(__file__).parent / ".uploads"))
os.environ.setdefault("AUTOPILOT_SCHEDULER", "0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _motor_to_list_honours_length(monkeypatch):
    """Ο πραγματικός Motor επιστρέφει από το `to_list(length)` το πολύ `length` έγγραφα· ο διπλός της mongomock_motor
    αγνοεί το `length` και επιστρέφει όλα. Έτσι κάθε σιωπηλό όριο στον κώδικα (`.to_list(1000)`) ήταν αόρατο στα tests
    (βλ. τα «Ανείσπρακτα» που χάνονταν πάνω από το όριο). Εδώ τα tests συμπεριφέρονται όπως ο driver της παραγωγής."""
    import itertools
    import mongomock_motor

    def honouring(attribute: str):
        async def to_list(self, length=None, *args, **kwargs):
            cursor = getattr(self, attribute)                  # το ιδιωτικό πεδίο της κλάσης (name mangling)
            return list(cursor) if length is None else list(itertools.islice(cursor, length))
        return to_list

    for name in ("AsyncCursor", "AsyncCommandCursor", "AsyncLatentCommandCursor"):
        monkeypatch.setattr(getattr(mongomock_motor, name), "to_list", honouring(f"_{name}__cursor"))
    yield


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
