"""Γύρος 5 (ανασκόπηση) — ο μετρητής αριθμών ως ΜΙΑ ατομική εγγραφή (compare-and-set).

Ο ανεξάρτητος έλεγχος του γύρου 5 έδειξε ότι η αρίθμηση ΔΕΝ εγγυόταν «ένας μεταγενέστερος αριθμός δεν παίρνει ποτέ
προγενέστερη ημερομηνία»: τρεις διαδρομές έγραφαν τον μετρητή χωρίς τον έλεγχο ημερομηνίας (η διόρθωση της
«τελευταίας ημερομηνίας» όσο μια άλλη έκδοση ήταν εν εξελίξει, η μετακίνηση πάνω από το `floor` και η σάρωση της
πρώτης χρήσης), και η σάρωση μπορούσε να «χάσει» τον αριθμό 0001 χωρίς ίχνος. Με τυχαίες ταυτόχρονες εκδόσεις
εμφανίζονταν 8–23 ζεύγη εκτός σειράς και 12–22 χαμένα 0001 ανά 200 γύρους.

Τώρα κάθε παραχώρηση είναι ένα compare-and-set πάνω στο έγγραφο του μετρητή. Τα tests εδώ (α) αναγκάζουν τις
παρεμβολές που βρήκε ο έλεγχος, (β) κλειδώνουν τα δίχτυα ασφαλείας γύρω από την παραχώρηση (καμένοι αριθμοί,
υπό όρο επιστροφή, token-guarded διόρθωση ημερομηνίας), (γ) τρέχουν τυχαίες παρεμβολές με σταθερό seed, και
(δ) ελέγχουν την αναφορά αρίθμησης (κενά χωρίς τεράστιες λίστες, διπλότυπα ανά αύξοντα αριθμό) και την κάρτα του
Autopilot που δεν ξανανοίγει όταν την έχει κλείσει άνθρωπος.
"""
import asyncio
import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from automation import run_firm
from db import db
from tax_utils import athens_today

FIRM = "firm-numbering-cas"
OTHER_FIRM = "firm-numbering-cas-other"
CID = f"invoice-{FIRM}-2026"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture()
def frozen_today(monkeypatch):
    """«Σήμερα» (Αθήνα) = 15/06/2026."""
    import tax_utils

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tax_utils, "datetime", FakeDateTime)
    assert athens_today() == date(2026, 6, 15)
    return date(2026, 6, 15)


@pytest.fixture()
def protected():
    from server import ensure_invoice_number_index
    _run(ensure_invoice_number_index())


class _HookedCollection:
    def __init__(self, real, hooks):
        self._real, self._hooks = real, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        hook = self._hooks.get(name)
        if hook is None:
            return attr

        async def call(*args, **kwargs):
            return await hook(attr, *args, **kwargs)
        return call


class _HookedDb:
    def __init__(self, real, collection, hooks):
        self._real, self._collection, self._hooks = real, collection, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _HookedCollection(attr, self._hooks) if name == self._collection else attr


def _race(monkeypatch, module, collection, method, before, times=1):
    """Η πρώτη κλήση της `method` της `collection` προηγείται από το `before()`: άλλο αίτημα προλαβαίνει ανάμεσα
    στην ανάγνωση και την εγγραφή του πρώτου. Μέσα στο `before` το `module.db` είναι η πραγματική βάση."""
    state = {"calls": 0}

    async def hook(real, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] <= times:
            monkeypatch.setattr(module, "db", db)
            try:
                await before()
            finally:
                monkeypatch.setattr(module, "db", hooked)
        return await real(*args, **kwargs)

    hooked = _HookedDb(db, collection, {method: hook})
    monkeypatch.setattr(module, "db", hooked)
    return state


async def _put(number, *, status="issued", when="2026-03-01", firm=FIRM, **over):
    doc = {"firm_id": firm, "number": number, "status": status, "date": when, "total": 124.0, "subtotal": 100.0,
           "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _draft_doc(firm=FIRM, when=None, **over):
    from mydata_client import TYPE_SERVICES_B2B
    doc = {"firm_id": firm, "number": "", "status": "draft", "client_id": "x", "invoice_type": TYPE_SERVICES_B2B,
           "date": when or athens_today().isoformat(), "subtotal": 100.0, "total": 124.0, "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _counter(cid=CID):
    return await db.counters.find_one({"_id": cid})


async def _next(date_iso=None, floor=0, firm=FIRM, prefix="ΤΠΥ", year=2026):
    import numbering
    cid = f"invoice-{firm}-{year}"
    return await numbering.next_in_series(db.invoices, cid, firm, prefix, year, date_iso=date_iso, floor=floor)


# ============================================== 1. Ο μετρητής: compare-and-set ====

def test_a_lost_compare_and_set_consumes_no_number(monkeypatch):
    """Άλλο αίτημα παίρνει αριθμό ανάμεσα στην ανάγνωση και στην εγγραφή μας: η εγγραφή μας δεν ταιριάζει, δεν
    καταναλώνεται κανένας αριθμός και ξαναδοκιμάζουμε — δύο αιτήματα, δύο διαδοχικοί αριθμοί."""
    import numbering

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-10"})
        competitor = []

        async def other_request():
            competitor.append(await _next("2026-06-12"))

        state = _race(monkeypatch, numbering, "counters", "update_one", other_request)
        mine = await _next("2026-06-12")
        monkeypatch.setattr(numbering, "db", db)
        assert competitor == ["ΤΠΥ-2026-0004"] and mine == "ΤΠΥ-2026-0005"
        assert state["calls"] == 2                          # η πρώτη εγγραφή χάθηκε, η δεύτερη κέρδισε
        assert (await _counter())["seq"] == 5               # κανένας αριθμός δεν «κάηκε» από τη χαμένη εγγραφή

    _run(scenario())


def test_an_earlier_date_loses_to_a_request_that_took_a_later_date_in_between(monkeypatch):
    """Η παρεμβολή του ελέγχου (R5A-02 α/β): το αίτημα με ημερομηνία 12/06 διάβασε τον μετρητή όταν η τελευταία
    ημερομηνία ήταν 01/06· πριν γράψει, άλλο αίτημα πήρε αριθμό με 14/06. Δεν επιτρέπεται να πάρει μεταγενέστερο
    αριθμό με προγενέστερη ημερομηνία."""
    import numbering
    from numbering import SeriesDateOutOfOrder

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-01"})
        later = []

        async def other_request():
            later.append(await _next("2026-06-14"))

        _race(monkeypatch, numbering, "counters", "update_one", other_request)
        with pytest.raises(SeriesDateOutOfOrder) as refused:
            await _next("2026-06-12")
        monkeypatch.setattr(numbering, "db", db)
        assert later == ["ΤΠΥ-2026-0004"]
        assert refused.value.last_date == "2026-06-14" and refused.value.seq == 4
        assert refused.value.allocated_at                                       # πότε δόθηκε ο τελευταίος αριθμός
        c = await _counter()
        assert c["seq"] == 4 and c["last_date"] == "2026-06-14"                 # η άρνηση δεν άλλαξε τίποτα

    _run(scenario())


def test_a_counter_behind_the_floor_cannot_be_used_to_slip_an_earlier_date_in(monkeypatch):
    """Η παλιά μετακίνηση πάνω από το floor κατέληγε σε `$inc` χωρίς έλεγχο ημερομηνίας: το 12/06 έπαιρνε το 0007
    αφού το 14/06 είχε πάρει το 0006."""
    import numbering
    from numbering import SeriesDateOutOfOrder

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 2, "last_date": "2026-06-01"})
        later = []

        async def other_request():
            later.append(await _next("2026-06-14", floor=5))

        _race(monkeypatch, numbering, "counters", "update_one", other_request)
        with pytest.raises(SeriesDateOutOfOrder):
            await _next("2026-06-12", floor=5)
        monkeypatch.setattr(numbering, "db", db)
        assert later == ["ΤΠΥ-2026-0006"]
        assert (await _counter())["seq"] == 6

    _run(scenario())


@pytest.mark.parametrize("legacy_numbers", [0, 3])
def test_the_first_use_of_a_series_never_loses_an_earlier_date_or_number_one(monkeypatch, legacy_numbers):
    """Πρώτη χρήση (R5A-02 β/γ): το αίτημα με 12/06 σταματά πριν δημιουργήσει τον μετρητή· άλλο με 14/06 τον δημιουργεί και
    παίρνει αριθμό. Παλιά η σάρωση του πρώτου έβαζε τον μετρητή πάνω από τον δεύτερο — ο 12/06 έπαιρνε μεταγενέστερο
    αριθμό από τον 14/06, ή ο αριθμός 0001 χανόταν χωρίς ίχνος."""
    import numbering
    from numbering import SeriesDateOutOfOrder

    async def scenario():
        for n in range(1, legacy_numbers + 1):
            await _put(f"ΤΠΥ-2026-{n:04d}", when="2026-06-01")
        first_new = f"ΤΠΥ-2026-{legacy_numbers + 1:04d}"
        later = []

        async def other_request():
            later.append(await _next("2026-06-14"))

        _race(monkeypatch, numbering, "counters", "insert_one", other_request)
        with pytest.raises(SeriesDateOutOfOrder) as refused:
            await _next("2026-06-12")
        monkeypatch.setattr(numbering, "db", db)
        assert later == [first_new]
        assert refused.value.last_date == "2026-06-14"
        assert (await _counter())["seq"] == legacy_numbers + 1

    _run(scenario())


def test_two_first_requests_of_the_same_day_get_consecutive_numbers_from_one(monkeypatch):
    import numbering

    async def scenario():
        other = []

        async def other_request():
            other.append(await _next("2026-06-14"))

        _race(monkeypatch, numbering, "counters", "insert_one", other_request)
        mine = await _next("2026-06-14")
        monkeypatch.setattr(numbering, "db", db)
        assert other == ["ΤΠΥ-2026-0001"] and mine == "ΤΠΥ-2026-0002"
        assert (await _counter())["seq"] == 2

    _run(scenario())


def test_the_first_use_continues_above_numbers_that_already_exist():
    """Έγγραφα από την εποχή του «πλήθος + 1» (χωρίς μετρητή): ο μετρητής ξεκινά πάνω από τον μεγαλύτερο αριθμό — και
    όταν αυτός είναι ο πρώτος (0001). Ό,τι δεν ανήκει στη σειρά δεν μετράει."""
    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        assert await _next() == "ΤΠΥ-2026-0002"

        await db.counters.delete_many({})
        await _put("ΤΠΥ-2026-0003", status="draft")                   # πρόχειρο με παλιό αριθμό: μετράει
        await _put("ΤΠΥ-2026-0900", firm=OTHER_FIRM)                  # άλλο γραφείο
        await _put("ΤΠΥ-2025-0800")                                   # άλλο έτος
        await _put("ΑΠΥ-2026-0700")                                   # άλλη σειρά
        await _put("ΤΠΥ-2026-abc")                                    # εκτός μορφής
        assert await _next() == "ΤΠΥ-2026-0004"

    _run(scenario())


def test_the_first_use_applies_the_floor_as_well():
    async def scenario():
        assert await _next(floor=9) == "ΤΠΥ-2026-0010"
        assert (await _counter())["seq"] == 10

    _run(scenario())


def test_a_counter_equal_to_the_floor_hands_out_the_next_number_not_an_existing_one():
    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 9})
        assert await _next(floor=9) == "ΤΠΥ-2026-0010"
        assert await _next(floor=9) == "ΤΠΥ-2026-0011"                # ο μετρητής είναι πλέον πάνω από το floor
        assert await _next(floor=3) == "ΤΠΥ-2026-0012"

    _run(scenario())


def test_a_counter_with_damaged_values_is_repaired_by_the_next_allocation():
    """Χειροκίνητη επέμβαση ή παλιά επαναφορά: κείμενο ή δεκαδικός στη θέση του ακεραίου, άγνωστη ημερομηνία."""
    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": "12", "last_date": 20260610})
        assert await _next("2026-06-12") == "ΤΠΥ-2026-0013"
        c = await _counter()
        assert c["seq"] == 13 and isinstance(c["seq"], int) and c["last_date"] == "2026-06-12"

        await db.counters.update_one({"_id": CID}, {"$set": {"seq": 40.0}})
        assert await _next("2026-06-12") == "ΤΠΥ-2026-0041"
        assert isinstance((await _counter())["seq"], int)

        await db.counters.update_one({"_id": CID}, {"$set": {"seq": "not a number"}})
        assert await _next("2026-06-12", floor=50) == "ΤΠΥ-2026-0051"     # ό,τι υπάρχει ήδη προστατεύεται από το floor

    _run(scenario())


def test_a_dated_allocation_stamps_the_date_and_the_time_an_undated_one_leaves_the_date_alone():
    async def scenario():
        before = datetime.now(timezone.utc)
        await _next("2026-06-12")
        c = await _counter()
        assert c["last_date"] == "2026-06-12"
        stamped = datetime.fromisoformat(c["last_alloc_at"])
        assert before - timedelta(seconds=1) <= stamped <= datetime.now(timezone.utc) + timedelta(seconds=1)
        assert await _next() == "ΤΠΥ-2026-0002"                       # χωρίς ημερομηνία: δεν αγγίζει την ημερομηνία
        assert (await _counter())["last_date"] == "2026-06-12"

    _run(scenario())


def test_the_other_series_that_have_no_date_are_unaffected():
    """Υποθέσεις, γραμμάτια, παράβολα: ίδιος μετρητής, χωρίς ημερομηνία και χωρίς floor."""
    import numbering

    async def scenario():
        cid = "case-firm-x-2026"
        assert await numbering.next_in_series(db.cases, cid, "firm-x", "ΥΠΘ", 2026) == "ΥΠΘ-2026-0001"
        assert await numbering.next_in_series(db.cases, cid, "firm-x", "ΥΠΘ", 2026) == "ΥΠΘ-2026-0002"
        assert "last_date" not in await db.counters.find_one({"_id": cid})
        await db.cases.insert_one({"firm_id": "firm-y", "number": "ΥΠΘ-2026-0040"})
        assert await numbering.next_in_series(db.cases, "case-firm-y-2026", "firm-y", "ΥΠΘ", 2026) == "ΥΠΘ-2026-0041"

    _run(scenario())


def test_the_allocator_gives_up_cleanly_when_it_keeps_losing(monkeypatch):
    import numbering
    from numbering import SeriesBusy

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-01"})
        monkeypatch.setattr(numbering, "CAS_ATTEMPTS", 4)
        calls = {"n": 0}

        async def never_wins(real, *args, **kwargs):
            calls["n"] += 1
            return SimpleNamespace(matched_count=0)

        monkeypatch.setattr(numbering, "db", _HookedDb(db, "counters", {"update_one": never_wins}))
        with pytest.raises(SeriesBusy):
            await _next("2026-06-12")
        monkeypatch.setattr(numbering, "db", db)
        assert calls["n"] == 4                                          # ακριβώς CAS_ATTEMPTS προσπάθειες
        assert (await _counter())["seq"] == 3                           # δεν καταναλώθηκε αριθμός

    _run(scenario())


def test_the_default_number_of_attempts_is_generous():
    import numbering
    assert numbering.CAS_ATTEMPTS >= 20


def test_reset_series_date_only_acts_on_the_state_that_was_read():
    from numbering import reset_series_date

    async def scenario():
        await db.counters.insert_one({"_id": "c1", "seq": 3, "last_date": "2026-06-12"})
        assert await reset_series_date("c1", expected="2026-06-10", to=None) is False        # άλλη ημερομηνία
        assert (await db.counters.find_one({"_id": "c1"}))["last_date"] == "2026-06-12"
        assert await reset_series_date("c1", expected="2026-06-12", to="2026-06-01", seq=2) is False   # άλλος αριθμός
        assert (await db.counters.find_one({"_id": "c1"}))["last_date"] == "2026-06-12"
        assert await reset_series_date("c1", expected="2026-06-12", to="2026-06-01", seq=3) is True
        assert (await db.counters.find_one({"_id": "c1"}))["last_date"] == "2026-06-01"
        assert await reset_series_date("c1", expected="2026-06-01", to="2026-05-20") is True    # χωρίς seq: όπως πριν
        assert (await db.counters.find_one({"_id": "c1"}))["last_date"] == "2026-05-20"

    _run(scenario())


def test_resetting_the_date_to_none_removes_the_field():
    from numbering import reset_series_date

    async def scenario():
        await db.counters.insert_one({"_id": "c2", "seq": 3, "last_date": "2026-06-12"})
        assert await reset_series_date("c2", expected="2026-06-12", to=None) is True
        assert "last_date" not in await db.counters.find_one({"_id": "c2"})
        assert await reset_series_date("c2", expected=None, to="2026-06-13") is True          # απόν πεδίο = None
        assert (await db.counters.find_one({"_id": "c2"}))["last_date"] == "2026-06-13"

    _run(scenario())


def test_a_counter_from_an_older_version_without_date_or_allocation_time_works():
    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 7})
        assert await _next("2026-06-12") == "ΤΠΥ-2026-0008"
        c = await _counter()
        assert c["last_date"] == "2026-06-12" and c["last_alloc_at"]

    _run(scenario())


# ===================================================== 2. Έκδοση: διόρθωση και συγχρονισμός ====

def test_the_date_of_an_issue_still_in_flight_is_not_rewound(monkeypatch, frozen_today):
    """R5A-01: το B (10/06) απορρίπτεται επειδή το A (12/06) έχει πάρει αριθμό αλλά δεν τον έχει γράψει ακόμη στο
    παραστατικό. Η «τελευταία ημερομηνία» του A δεν είναι ορφανή — παλιά το B την έσβηνε και έπαιρνε το 0006 με
    ημερομηνία 10/06, πριν από το 0005 (12/06) του A."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-01", "last_alloc_at": _ago(days=3)})
        a = await _draft_doc(when="2026-06-12")
        b = await _draft_doc(when="2026-06-10")
        a_has_number, let_a_finish = asyncio.Event(), asyncio.Event()

        async def hold_a(real, query, update, *args, **kwargs):
            if query.get("_id") == a and update.get("$set", {}).get("status") == "issued":
                a_has_number.set()
                await let_a_finish.wait()
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": hold_a}))
        task_a = asyncio.create_task(svc.issue_draft(FIRM, await db.invoices.find_one({"_id": a}), {}))
        await a_has_number.wait()
        with pytest.raises(svc.InvoiceWorkflowError) as busy:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": b}), {})
        assert busy.value.args[0] == svc.SERIES_BUSY_MESSAGE
        assert (await _counter())["last_date"] == "2026-06-12" and (await _counter())["seq"] == 5
        let_a_finish.set()
        done = await task_a
        monkeypatch.setattr(svc, "db", db)
        assert done["number"] == "ΤΠΥ-2026-0005" and (await _counter())["last_date"] == "2026-06-12"

        # Αφού το A γράφτηκε, το B δεν είναι πια «ταυτόχρονο»: η ημερομηνία του είναι απλώς προγενέστερη.
        with pytest.raises(svc.InvoiceWorkflowError) as early:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": b}), {})
        assert "προγενέστερη" in early.value.args[0]

    _run(scenario())


def test_a_date_left_by_a_crashed_issue_is_healed_once_the_issue_lock_has_expired(frozen_today):
    """Ένας αριθμός που δόθηκε και δεν γράφτηκε ποτέ (διεργασία που έπεσε) αφήνει «ορφανή» ημερομηνία. Αφού λήξει το
    κλείδωμα έκδοσης, δεν εκκρεμεί πια τίποτα και η ημερομηνία διορθώνεται — ο αριθμός που χάθηκε μένει κενό."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 5, "last_date": "2026-06-12",
                                      "last_alloc_at": _ago(seconds=svc.ISSUE_CLAIM_TTL_SECONDS + 30)})
        draft = await _draft_doc(when="2026-06-10")
        done = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert done["number"] == "ΤΠΥ-2026-0006" and done["date"] == "2026-06-10"
        assert (await _counter())["last_date"] == "2026-06-10"

    _run(scenario())


def test_a_counter_without_an_allocation_time_is_treated_as_stale(frozen_today):
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 5, "last_date": "2026-06-12"})        # παλιά έκδοση
        draft = await _draft_doc(when="2026-06-10")
        done = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert done["number"] == "ΤΠΥ-2026-0006"

    _run(scenario())


def test_a_fresh_orphan_date_is_not_healed_while_the_allocation_may_still_be_running(frozen_today):
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 5, "last_date": "2026-06-12", "last_alloc_at": _ago(seconds=10)})
        draft = await _draft_doc(when="2026-06-10")
        with pytest.raises(svc.InvoiceWorkflowError) as busy:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert busy.value.args[0] == svc.SERIES_BUSY_MESSAGE
        c = await _counter()
        assert c["seq"] == 5 and c["last_date"] == "2026-06-12"                # τίποτα δεν άλλαξε
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and "issue_claim" not in stored      # το κλείδωμα αποδεσμεύτηκε

    _run(scenario())


def test_when_nothing_is_pending_a_fresh_date_is_healed_at_once(frozen_today):
    """Αποτυχημένη έκδοση που επέστρεψε τον αριθμό της: ο μετρητής είναι στο τελευταίο γραμμένο παραστατικό και η
    ημερομηνία που άφησε δεν εκκρεμεί πουθενά — δεν χρειάζεται αναμονή."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-12", "last_alloc_at": _ago(seconds=2)})
        draft = await _draft_doc(when="2026-06-10")
        done = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert done["number"] == "ΤΠΥ-2026-0005"
        assert (await _counter())["last_date"] == "2026-06-10"

    _run(scenario())


def test_the_allocation_age_check():
    import invoice_services as svc
    ttl = svc.ISSUE_CLAIM_TTL_SECONDS
    assert svc._allocation_is_stale(None) is True
    assert svc._allocation_is_stale("") is True
    assert svc._allocation_is_stale("not a date") is True
    assert svc._allocation_is_stale(_ago(seconds=5)) is False
    assert svc._allocation_is_stale(_ago(seconds=ttl - 20)) is False
    assert svc._allocation_is_stale(_ago(seconds=ttl + 20)) is True
    naive = (datetime.now(timezone.utc) - timedelta(seconds=ttl + 60)).replace(tzinfo=None).isoformat()
    assert svc._allocation_is_stale(naive) is True                        # χωρίς ζώνη ώρας: θεωρείται UTC


def test_the_heal_never_writes_a_future_date_into_the_counter(frozen_today):
    """Παλιό παραστατικό με μελλοντική ημερομηνία δεν πρέπει να μπλοκάρει τη σειρά: ο μετρητής διορθώνεται το πολύ
    ως «σήμερα»."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-12-01")                  # από παλιά έκδοση, μελλοντική ημερομηνία
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-20"})
        draft = await _draft_doc(when="2026-06-15")
        done = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert done["number"] == "ΤΠΥ-2026-0005"
        assert (await _counter())["last_date"] == "2026-06-15"

    _run(scenario())


def test_after_the_heal_the_numbers_that_appeared_meanwhile_are_respected(frozen_today):
    """Το `previous` που διάβασε η έκδοση μπορεί να είναι παλιό: αν ο μετρητής είναι πίσω από όσα γράφτηκαν στο
    μεταξύ, μετά τη διόρθωση ο αριθμός πρέπει να πάει πάνω από όλους τους υπάρχοντες."""
    import invoice_services as svc

    async def scenario():
        for n in range(1, 6):
            await _put(f"ΤΠΥ-2026-{n:04d}", when="2026-06-10")
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-13"})
        stale_previous = (3, "2026-06-01", "ΤΠΥ-2026-0003")
        number = await svc._allocate_number(FIRM, ("ΤΠΥ", 2026), date(2026, 6, 12), date(2026, 6, 15), stale_previous)
        assert number == "ΤΠΥ-2026-0006"                                # όχι 0004 (υπάρχει ήδη)

    _run(scenario())


def test_a_second_refusal_after_the_heal_is_reported_not_swallowed(monkeypatch, frozen_today):
    """Η διόρθωση πετυχαίνει, αλλά πριν τη δεύτερη προσπάθεια η ημερομηνία ξαναμεγαλώνει (άλλη έκδοση): ο χρήστης
    παίρνει καθαρό μήνυμα και το πρόχειρο μένει πρόχειρο."""
    import invoice_services as svc
    import numbering

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-12"})
        draft = await _draft_doc(when="2026-06-10")
        real_reset = numbering.reset_series_date

        async def reset_then_somebody_moves_the_date(*args, **kwargs):
            ok = await real_reset(*args, **kwargs)
            await db.counters.update_one({"_id": CID}, {"$set": {"last_date": "2026-06-14"}})
            return ok

        monkeypatch.setattr(numbering, "reset_series_date", reset_then_somebody_moves_the_date)
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        assert err.value.args[0] == svc.SERIES_BUSY_MESSAGE
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and not stored["number"] and "issue_claim" not in stored
        assert (await _counter())["seq"] == 4                           # δεν καταναλώθηκε αριθμός

    _run(scenario())


def test_a_counter_that_stays_busy_leaves_the_draft_a_draft(monkeypatch, frozen_today):
    import invoice_services as svc
    import numbering

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-01", "last_alloc_at": _ago(days=1)})
        draft = await _draft_doc(when="2026-06-12")
        monkeypatch.setattr(numbering, "CAS_ATTEMPTS", 2)

        async def never_wins(real, *args, **kwargs):
            return SimpleNamespace(matched_count=0)

        monkeypatch.setattr(numbering, "db", _HookedDb(db, "counters", {"update_one": never_wins}))
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(numbering, "db", db)
        assert err.value.args[0] == svc.SERIES_BUSY_MESSAGE
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and "issue_claim" not in stored
        assert (await _counter())["seq"] == 3

    _run(scenario())


def test_after_a_collision_the_date_is_checked_again_against_what_the_collision_revealed(monkeypatch, frozen_today, protected):
    """Ο αριθμός που πήρε το πρόχειρο υπήρχε ήδη, με μεταγενέστερη ημερομηνία: το πρόχειρο δεν πρέπει να εκδοθεί με
    επόμενο αριθμό και προγενέστερη ημερομηνία — ελέγχεται ξανά η σειρά και ο χρήστης παίρνει το σφάλμα ημερομηνίας."""
    import invoice_services as svc

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 0})
        draft = await _draft_doc(when="2026-06-10")

        async def somebody_wrote_number_one(real, query, update, *args, **kwargs):
            if update.get("$set", {}).get("status") == "issued":
                await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "status": "issued",
                                              "date": "2026-06-14", "total": 1.0})
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": somebody_wrote_number_one}))
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(svc, "db", db)
        assert "προγενέστερη" in err.value.args[0] and "14/06/2026" in err.value.args[0]
        assert (await db.invoices.find_one({"_id": draft}))["status"] == "draft"
        assert (await _counter())["seq"] == 1                           # ο αριθμός που συγκρούστηκε δεν επιστράφηκε

    _run(scenario())


def test_numbers_refused_by_the_database_are_never_handed_back_even_when_every_retry_fails(monkeypatch, frozen_today):
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")
        calls = {"n": 0}

        async def taken(real, query, update, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return await real(query, update, *args, **kwargs)
            raise DuplicateKeyError("E11000 duplicate key error")

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": taken}))
        with pytest.raises(svc.InvoiceWorkflowError):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(svc, "db", db)
        assert (await _counter())["seq"] == svc.NUMBER_ATTEMPTS         # τρεις αριθμοί «κάηκαν», κανένας δεν επιστράφηκε

    _run(scenario())


def test_release_never_rewinds_a_counter_that_has_moved_on():
    import invoice_services as svc

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 5})
        await svc.release_invoice_number(FIRM, "ΤΠΥ-2026-0004")          # το 0004 δεν είναι ο τελευταίος αριθμός
        assert (await _counter())["seq"] == 5
        await svc.release_invoice_number(FIRM, "ΤΠΥ-2026-0005")
        assert (await _counter())["seq"] == 4

    _run(scenario())


def test_a_request_cancelled_after_the_final_write_landed_does_not_hand_its_number_back(monkeypatch, frozen_today):
    """Το παραστατικό εκδόθηκε (η εγγραφή εφαρμόστηκε) αλλά ο αναμένων ακυρώθηκε: ο αριθμός ανήκει στο παραστατικό."""
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")

        async def apply_then_cancel(real, query, update, *args, **kwargs):
            result = await real(query, update, *args, **kwargs)
            if update.get("$set", {}).get("status") == "issued":
                raise asyncio.CancelledError()
            return result

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": apply_then_cancel}))
        with pytest.raises(asyncio.CancelledError):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(svc, "db", db)
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "issued" and stored["number"] == "ΤΠΥ-2026-0001"
        assert (await _counter())["seq"] == 1

    _run(scenario())


def test_a_request_cancelled_before_the_final_write_hands_its_number_back(monkeypatch, frozen_today):
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")

        async def cancel_before_write(real, query, update, *args, **kwargs):
            if update.get("$set", {}).get("status") == "issued":
                raise asyncio.CancelledError()
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": cancel_before_write}))
        with pytest.raises(asyncio.CancelledError):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(svc, "db", db)
        assert (await db.invoices.find_one({"_id": draft}))["status"] == "draft"
        assert (await _counter())["seq"] == 0                           # ο αριθμός επιστράφηκε

    _run(scenario())


# ================================================ 3. Τυχαίες παρεμβολές (σταθερό seed) ====

class _JitterCursor:
    def __init__(self, cursor, rng):
        self._cursor, self._rng = cursor, rng

    def __aiter__(self):
        async def generate():
            await asyncio.sleep(self._rng.random() * 0.002)
            async for d in self._cursor:
                yield d
        return generate()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _JitterCollection:
    METHODS = {"find_one", "find_one_and_update", "update_one", "insert_one", "delete_one", "count_documents"}

    def __init__(self, real, rng):
        self._real, self._rng = real, rng

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        rng = self._rng
        if name in self.METHODS:
            async def call(*args, **kwargs):
                await asyncio.sleep(rng.random() * 0.002)
                result = await attr(*args, **kwargs)
                await asyncio.sleep(rng.random() * 0.002)
                return result
            return call
        if name == "find":
            return lambda *args, **kwargs: _JitterCursor(attr(*args, **kwargs), rng)
        return attr


class _JitterDb:
    def __init__(self, real, rng):
        self._real, self._rng = real, rng

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _JitterCollection(attr, self._rng) if name in ("invoices", "counters") else attr


@pytest.mark.parametrize("mode,seed,rounds", [("fresh", 11, 40), ("lag", 12, 40), ("same_day", 13, 30)])
def test_random_concurrent_issues_never_break_the_series(monkeypatch, frozen_today, mode, seed, rounds):
    """2–3 ταυτόχρονες εκδόσεις με τυχαίες ημερομηνίες και τυχαίες καθυστερήσεις σε κάθε κλήση προς τη βάση.
    Όποια κι αν είναι η παρεμβολή ισχύουν πάντα: κανένας διπλός αριθμός, καμία ημερομηνία εκτός σειράς, καμία
    «τρύπα» (ούτε χαμένο 0001), ο μετρητής ποτέ πίσω, κανένα κλείδωμα που να μένει, κανένα απρόβλεπτο σφάλμα."""
    import invoice_services as svc
    import numbering
    from server import ensure_invoice_number_index

    async def scenario():
        rng = random.Random(seed)
        stats = Counter()
        for it in range(rounds):
            await db.invoices.delete_many({"firm_id": FIRM})
            await db.counters.delete_many({})
            await ensure_invoice_number_index()
            base = {"fresh": rng.choice([0, 3]), "lag": 5, "same_day": 0}[mode]
            for n in range(1, base + 1):
                await _put(f"ΤΠΥ-2026-{n:04d}", when="2026-06-01")
            if mode == "lag":
                await db.counters.insert_one({"_id": CID, "seq": 2, "last_date": "2026-06-01"})     # μετρητής πίσω
            elif base:
                await db.counters.insert_one({"_id": CID, "seq": base, "last_date": "2026-06-01"})
            k = rng.choice([2, 3])
            choices = ["2026-06-12"] if mode == "same_day" else ["2026-06-10", "2026-06-12", "2026-06-14", "2026-06-12"]
            ids = [await _draft_doc(when=rng.choice(choices)) for _ in range(k)]
            jitter = _JitterDb(db, random.Random(rng.random()))
            monkeypatch.setattr(svc, "db", jitter)
            monkeypatch.setattr(numbering, "db", jitter)
            results = await asyncio.gather(
                *[svc.issue_draft(FIRM, await db.invoices.find_one({"_id": i}), {}) for i in ids], return_exceptions=True)
            monkeypatch.setattr(svc, "db", db)
            monkeypatch.setattr(numbering, "db", db)

            issued = await db.invoices.find({"firm_id": FIRM, "status": {"$ne": "draft"}}).to_list(100)
            numbers = [d["number"] for d in issued]
            stats["duplicates"] += len(numbers) != len(set(numbers))
            ordered = sorted(issued, key=lambda d: int(d["number"][-4:]))
            stats["out_of_order"] += any(b["date"] < a["date"] for a, b in zip(ordered, ordered[1:]))
            seqs = {int(n[-4:]) for n in numbers}
            top = max(seqs, default=0)
            stats["holes"] += bool(set(range(1, top + 1)) - seqs)
            stats["counter_behind"] += ((await _counter()) or {}).get("seq", 0) < top
            stats["leaked_claims"] += await db.invoices.count_documents(
                {"firm_id": FIRM, "status": "draft", "issue_claim": {"$exists": True}})
            stats["unexpected"] += sum(1 for r in results if isinstance(r, Exception) and not isinstance(r, svc.InvoiceWorkflowError))
            stats["issued"] += sum(1 for r in results if isinstance(r, dict))
        assert stats["issued"] >= rounds                         # κάθε γύρος εκδίδει τουλάχιστον ένα
        for problem in ("duplicates", "out_of_order", "holes", "counter_behind", "leaked_claims", "unexpected"):
            assert stats[problem] == 0, dict(stats)

    _run(scenario())


# =========================================================== 4. Αναφορά αρίθμησης ====

def test_a_huge_number_does_not_build_a_huge_list():
    """R5A-12: ένας αριθμός με πολλά ψηφία (εισαγωγή, επαναφορά) έχτιζε λίστα εκατομμυρίων στον βρόχο συμβάντων."""
    from invoice_services import GAP_LIST_LIMIT, numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-999999999")
        await db.counters.insert_one({"_id": CID, "seq": 999_999_999})        # το μεγαλύτερο έγκυρο: ο μετρητής ισούται με τον τελευταίο
        started = time.perf_counter()
        report = await numbering_report(FIRM)
        assert time.perf_counter() - started < 1.0
        s = report["series"][0]
        assert s["gaps_total"] == 999999997 and s["counter"]["state"] == "ok"
        assert len(s["gaps"]) == GAP_LIST_LIMIT and s["gaps"][:3] == [2, 3, 4] and s["ok"] is False
        assert report["problems"]["gaps"] == s["gaps_total"]
        # Μετρητής πέρα από το όριο των 9 ψηφίων: δεν είναι αριθμός σειράς, μετράει ως 0 (numbering.as_int) και
        # εμφανίζεται ως «πίσω» — δεν χτίζεται λίστα κενών ούτε διαδίδεται υπέρογκος αριθμός.
        await db.counters.update_one({"_id": CID}, {"$set": {"seq": 10 ** 15}})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps_total"] == 999999997 and s["counter"]["state"] == "behind" and s["counter"]["seq"] == 0

    _run(scenario())


def test_the_gap_list_is_cut_across_segments_but_the_total_is_exact():
    from invoice_services import GAP_LIST_LIMIT, numbering_report

    async def scenario():
        for n in (1, 30, 61):
            await _put(f"ΤΠΥ-2026-{n:04d}", when=f"2026-03-{(n % 28) + 1:02d}")
        await db.counters.insert_one({"_id": CID, "seq": 70})                 # ο μετρητής είναι 9 πάνω από το 61
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps_total"] == 28 + 30 + 9
        assert len(s["gaps"]) == GAP_LIST_LIMIT
        assert s["gaps"][:3] == [2, 3, 4] and s["gaps"][27] == 29 and s["gaps"][28] == 31 and s["gaps"][-1] == 52

    _run(scenario())


def test_numbers_handed_out_but_never_used_are_listed_when_there_is_room():
    from invoice_services import numbering_report

    async def scenario():
        for n in (1, 2, 4):
            await _put(f"ΤΠΥ-2026-{n:04d}", when=f"2026-03-0{n}")
        await db.counters.insert_one({"_id": CID, "seq": 7})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps"] == [3, 5, 6, 7] and s["gaps_total"] == 4
        assert s["counter"] == {"seq": 7, "state": "ahead", "ahead_by": 3}

    _run(scenario())


def test_the_same_sequence_written_two_ways_is_a_duplicate():
    """R5A-13: «0007» και «007» είναι ο ίδιος αριθμός· το μοναδικό ευρετήριο συγκρίνει κείμενα και δέχεται και τα δύο."""
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0006")
        first = await _put("ΤΠΥ-2026-0007")
        second = await _put("ΤΠΥ-2026-007")
        s = (await numbering_report(FIRM))["series"][0]
        assert s["ok"] is False and len(s["duplicates"]) == 1
        dup = s["duplicates"][0]
        assert dup["count"] == 2 and dup["number"] == "ΤΠΥ-2026-0007" and set(dup["ids"]) == {str(first), str(second)}
        assert s["gaps_total"] == 0

    _run(scenario())


def test_a_number_that_is_not_a_plausible_sequence_is_reported_as_out_of_format():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-" + "9" * 12)
        await _put("ΤΠΥ-2026-" + "9" * 5000)                                   # ξεπερνά το όριο μετατροπής του int
        await _put("ΤΠΥ-2026-٠٠٠٧")                                           # ψηφία άλλου συστήματος γραφής
        report = await numbering_report(FIRM)
        assert len(report["unparseable"]) == 3
        s = report["series"][0]
        assert s["count"] == 1 and s["gaps_total"] == 0

    _run(scenario())


def test_a_damaged_counter_does_not_break_the_report():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-0002")
        await db.counters.insert_one({"_id": CID, "seq": "garbage"})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["counter"]["state"] == "behind" and s["ok"] is False

    _run(scenario())


def test_a_counter_one_behind_is_reported_and_a_date_problem_alone_makes_the_series_not_ok():
    from invoice_services import numbering_report

    async def scenario():
        for n, when in ((1, "2026-03-01"), (2, "2026-03-09"), (3, "2026-03-05")):
            await _put(f"ΤΠΥ-2026-{n:04d}", when=when)
        await db.counters.insert_one({"_id": CID, "seq": 2})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["counter"]["state"] == "behind" and s["date_order"] and s["ok"] is False

        await db.counters.update_one({"_id": CID}, {"$set": {"seq": 3}})
        await db.invoices.update_one({"number": "ΤΠΥ-2026-0003"}, {"$set": {"date": "2026-03-10"}})
        await _put("ΤΠΥ-2026-0004", when="2026-03-11")
        await _put("ΤΠΥ-2026-0005", when="2026-03-10")                          # τώρα φταίει μόνο η ημερομηνία
        await db.counters.update_one({"_id": CID}, {"$set": {"seq": 5}})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps_total"] == 0 and s["duplicates"] == [] and s["counter"]["state"] == "ok"
        assert s["date_order"] and s["ok"] is False

    _run(scenario())


# ==================================================== 5. Κάρτες του Autopilot ====

async def _human_close(card):
    await db.notifications.update_one(
        {"_id": card["_id"]}, {"$set": {"resolved_at": _ago(minutes=1), "resolved_by": "lawyer@example.gr", "resolution": "done"}})


async def _numbering_cards(firm=FIRM):
    return await db.notifications.find({"firm_id": firm, "type": "numbering"}).to_list(50)


@pytest.mark.asyncio
async def test_a_card_a_person_has_closed_is_not_raised_again(protected):
    """R5A-11: ένα κενό σε εκδοθείσα σειρά δεν διορθώνεται ποτέ — η κάρτα επέστρεφε κάθε κύκλο αφού την έκλεινε ο λογιστής."""
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await run_firm(FIRM, ["numbering_check"])
    [card] = await _numbering_cards()
    await _human_close(card)
    for _ in range(3):
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 0 and r["stats"]["ok"] is False
    assert len(await _numbering_cards()) == 1


@pytest.mark.asyncio
async def test_a_new_problem_after_a_person_closed_a_card_still_raises_a_new_one(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await run_firm(FIRM, ["numbering_check"])
    [card] = await _numbering_cards()
    await _human_close(card)
    await _put("ΤΠΥ-2026-0006", when="2026-03-06")                              # νέο κενό: 4 και 5
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["raised"] == 1
    open_cards = [c for c in await _numbering_cards() if c.get("resolved_at") is None]
    assert len(open_cards) == 1 and "0004" in open_cards[0]["message"]


@pytest.mark.asyncio
async def test_a_problem_that_comes_back_after_autopilot_closed_its_card_raises_a_new_card(protected):
    first = await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await run_firm(FIRM, ["numbering_check"])
    middle = await _put("ΤΠΥ-2026-0002", when="2026-03-02")
    await run_firm(FIRM, ["numbering_check"])                                   # διορθώθηκε: κλείνει μόνη της
    assert (await _numbering_cards())[0]["resolved_by"] == "autopilot"
    await db.invoices.delete_one({"_id": middle})                               # και το κενό ξαναεμφανίζεται
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["raised"] == 1
    assert len([c for c in await _numbering_cards() if c.get("resolved_at") is None]) == 1
    assert first


@pytest.mark.asyncio
async def test_a_card_closed_in_another_firm_does_not_silence_this_firm(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await _put("ΤΠΥ-2026-0001", when="2026-03-01", firm=OTHER_FIRM)
    await _put("ΤΠΥ-2026-0003", when="2026-03-03", firm=OTHER_FIRM)
    await run_firm(OTHER_FIRM, ["numbering_check"])
    [other] = await _numbering_cards(OTHER_FIRM)
    await _human_close(other)                                                   # ίδιο πρόβλημα, ίδιο κλειδί
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["raised"] == 1 and len(await _numbering_cards()) == 1
