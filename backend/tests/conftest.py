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

import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402


# ---------------------------------------------------------------------------------------------------------------------
# Ζώνη ώρας του μηχανήματος των tests: ΠΑΝΤΑ διαφορετική ημερομηνία από την ελληνική.
#
# Ο server της παραγωγής τρέχει σε UTC, ο προγραμματιστής σε ώρα Ελλάδας, το CI πάλι σε UTC. Ο κώδικας πρέπει να βρίσκει
# τη «σημερινή» ημερομηνία από την ώρα Ελλάδας (`tax_utils.athens_today`) και ΠΟΤΕ από το ρολόι του μηχανήματος. Όσο το
# μηχάνημα των tests έδινε την ίδια ημερομηνία με την Ελλάδα (σχεδόν πάντα), κάθε ανάγνωση του τοπικού ρολογιού ήταν
# «σωστή» και το λάθος φαινόταν μόνο 00:00–03:00 ώρα Ελλάδας, όταν δεν τρέχει κανείς tests: έτσι έμειναν 20 σημεία
# στον γύρο 5. Εδώ το μηχάνημα μπαίνει σε ζώνη που αυτή τη στιγμή έχει ΑΛΛΗ ημερομηνία (UTC+14 ή UTC−11 — η μία από τις
# δύο διαφέρει πάντα), οπότε οποιαδήποτε εξάρτηση από το τοπικό ρολόι αποτυγχάνει αμέσως, όποια ώρα κι αν τρέξει η suite.
# Με `NOMOS_TEST_TZ=<ζώνη>` επιλέγεται ρητά άλλη (π.χ. για να αναπαραχθεί μια αποτυχία).
# ---------------------------------------------------------------------------------------------------------------------

def _hostile_timezone() -> str:
    explicit = os.environ.get("NOMOS_TEST_TZ")
    if explicit:
        return explicit
    athens = datetime.now(ZoneInfo("Europe/Athens")).date()
    for candidate in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
        if datetime.now(ZoneInfo(candidate)).date() != athens:
            return candidate
    return "Pacific/Pago_Pago"


if hasattr(time, "tzset"):
    os.environ["TZ"] = _hostile_timezone()
    time.tzset()


@pytest.fixture()
def athens_clock(monkeypatch):
    """Παγώνει το ρολόι του `tax_utils`: 01:30 της 1/1/2027 στην Ελλάδα, 23:30 της 31/12/2026 σε UTC.

    Η ώρα αυτή είναι η δύσκολη: η ημερομηνία UTC είναι χθεσινή και, ταυτόχρονα, αλλάζει ο μήνας, το έτος και η σειρά
    αρίθμησης. Ό,τι βρίσκει «σήμερα» με `athens_today()` βλέπει 2027-01-01· ό,τι διαβάζει το ρολόι του μηχανήματος ή την
    ημερομηνία UTC βλέπει κάτι άλλο."""
    import tax_utils

    frozen = datetime(2027, 1, 1, 1, 30, tzinfo=ZoneInfo("Europe/Athens"))          # = 2026-12-31 23:30 UTC

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.astimezone(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(tax_utils, "datetime", FrozenDatetime)
    assert tax_utils.athens_today().isoformat() == "2027-01-01"
    return frozen


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
