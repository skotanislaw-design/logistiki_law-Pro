"""Γύρος 5 (έλεγχος μεταλλάξεων) — τρύπες στα tests της αρίθμησης που δεν έβλεπε κανένα υπάρχον test.

Κάθε test εδώ σκοτώνει τουλάχιστον μία μετάλλαξη (ένα μικρό, εύλογο λάθος στον κώδικα) που τα υπάρχοντα tests
(`test_unit_numbering_cas_r5.py`, `test_unit_numbering_r5.py`, `test_unit_invoice_lifecycle_r4.py`, `test_unit_review_r4.py`)
άφηναν να περάσει χωρίς κανένα κόκκινο. Η ιδιότητα που προστατεύεται παντού είναι η ίδια:

    ένας μεταγενέστερος αριθμός δεν παίρνει ποτέ προγενέστερη ημερομηνία · κανένας αριθμός δεν διπλασιάζεται ·
    κάθε αριθμός που παραλείφθηκε αναφέρεται · μια αποτυχημένη έκδοση δεν αφήνει τρύπα ή γυρισμένο μετρητή.

Ομάδες: (1) ο μετρητής (compare-and-set, κατεστραμμένες τιμές), (2) η διόρθωση της «τελευταίας ημερομηνίας»,
(3) ο μετρητής ανά γραφείο/σειρά/έτος, (4) το «προηγούμενο της σειράς» (πρόχειρα, ακυρωμένα, άλλα γραφεία, κακοί
αριθμοί), (5) η έκδοση (άλλο γραφείο, πιστωτικό με μηδενικό σύνολο, πρόχειρα παλιάς έκδοσης με δικό τους αριθμό, πρόχειρο που
διαγράφηκε στη διάρκεια της έκδοσης, αποτυχίες σε κάθε στάδιο), (6) η αναφορά,
(7) οι κάρτες του Autopilot ανά είδος προβλήματος.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from bson import ObjectId

from automation import run_firm
from db import db
from tax_utils import athens_today

FIRM = "firm-numbering-r5c"
OTHER_FIRM = "firm-numbering-r5c-other"
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
    """Η βάση όπως μετά την εκκίνηση: με το unique index των αριθμών."""
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
    """Η πρώτη κλήση της `method` προηγείται από το `before()`: άλλο αίτημα προλαβαίνει ανάμεσα στην ανάγνωση και την
    εγγραφή του πρώτου. Μέσα στο `before` το `module.db` είναι η πραγματική βάση."""
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


async def _issue(draft_id, firm=FIRM):
    import invoice_services as svc
    return await svc.issue_draft(firm, await db.invoices.find_one({"_id": draft_id}), {})


async def _counter(cid=CID):
    return await db.counters.find_one({"_id": cid})


async def _next(date_iso=None, floor=0, firm=FIRM, prefix="ΤΠΥ", year=2026):
    import numbering
    return await numbering.next_in_series(db.invoices, f"invoice-{firm}-{year}", firm, prefix, year,
                                          date_iso=date_iso, floor=floor)


async def _cards(kind, firm=FIRM, open_only=False):
    query = {"firm_id": firm, "type": "numbering", "dedupe_key": {"$regex": f"^numbering:{kind}"}}
    if open_only:
        query["resolved_at"] = None
    return await db.notifications.find(query).to_list(50)


async def _human_close(card):
    await db.notifications.update_one(
        {"_id": card["_id"]}, {"$set": {"resolved_at": _ago(minutes=1), "resolved_by": "lawyer@example.gr", "resolution": "done"}})


# ======================================================= 1. Ο μετρητής ====

@pytest.mark.parametrize("date_iso,seed", [("2026-06-12", {"last_date": "2026-06-12"}), (None, {})])
def test_two_requests_with_the_same_date_or_none_cannot_win_the_same_counter_value(monkeypatch, date_iso, seed):
    """Το compare-and-set πρέπει να εξαρτάται από τον ΑΡΙΘΜΟ που διαβάστηκε, όχι μόνο από την ημερομηνία. Στα υπάρχοντα
    tests ο ανταγωνιστής έχει άλλη ημερομηνία, οπότε το φίλτρο της ημερομηνίας έκρυβε την απουσία του φίλτρου αριθμού.
    Εδώ (ίδια ημέρα, ή χωρίς ημερομηνία όπως οι υποθέσεις/γραμμάτια/παράβολα) μόνο το `seq` προστατεύει από διπλό αριθμό.

    Σημείωση: το παλιό `test_two_requests_racing_over_a_counter_left_behind_get_different_numbers` (test_unit_numbering_r5.py)
    κρεμά το hook του στο `find_one_and_update` του μετρητή, που ο κώδικας δεν καλεί πια (καλεί `find_one` + `update_one`):
    το hook δεν εκτελείται ποτέ και τα αιτήματα δεν παρεμβάλλονται — δεν θα έπιανε ούτε αυτό το λάθος."""
    import numbering

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": 3, **seed})
        competitor = []

        async def other_request():
            competitor.append(await _next(date_iso))

        state = _race(monkeypatch, numbering, "counters", "update_one", other_request)
        mine = await _next(date_iso)
        monkeypatch.setattr(numbering, "db", db)
        assert competitor == ["ΤΠΥ-2026-0004"] and mine == "ΤΠΥ-2026-0005"
        assert state["calls"] == 2                                  # η πρώτη εγγραφή μας δεν ταίριαξε
        assert (await _counter())["seq"] == 5

    _run(scenario())


def test_a_counter_value_that_was_released_and_handed_out_again_is_not_mistaken_for_the_one_that_was_read(monkeypatch):
    """Το `seq` μόνο του δεν είναι «έκδοση» του μετρητή: ένας αριθμός που επιστρέφεται (αποτυχημένη έκδοση) και δίνεται ξανά
    ξαναφέρνει το ίδιο `seq` με άλλη ημερομηνία. Ο B (14/06) διαβάζει (4, 12/06) — ο αριθμός 4 είναι εκκρεμής, της έκδοσης F.
    Πριν γράψει ο B, η F αποτυγχάνει και επιστρέφει το 4· ο A (15/06) παίρνει το 4 και το γράφει. Ο μετρητής είναι πάλι
    στο `seq` 4, αλλά με ημερομηνία 15/06. Αν το compare-and-set του B κοιτούσε μόνο το `seq`, ο B θα έπαιρνε το 5 με 14/06
    μετά το 4 του A (15/06) και θα γύριζε την ημερομηνία της σειράς πίσω. Το φίλτρο στην ΗΜΕΡΟΜΗΝΙΑ τον σταματά."""
    import invoice_services as svc
    import numbering

    async def scenario():
        await _put("ΤΠΥ-2026-0003", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-12", "last_alloc_at": _ago(seconds=3)})
        taken_by_a = []

        async def f_fails_and_a_takes_the_number():
            await svc.release_invoice_number(FIRM, "ΤΠΥ-2026-0004")             # η F επιστρέφει το 4 …
            taken_by_a.append(await _next("2026-06-15"))                          # … και ο A το παίρνει με 15/06

        state = _race(monkeypatch, numbering, "counters", "update_one", f_fails_and_a_takes_the_number)
        with pytest.raises(numbering.SeriesDateOutOfOrder) as refused:
            await _next("2026-06-14")
        monkeypatch.setattr(numbering, "db", db)
        assert taken_by_a == ["ΤΠΥ-2026-0004"]
        assert refused.value.last_date == "2026-06-15" and refused.value.seq == 4
        assert state["calls"] == 1                                                # ο B έγραψε μία φορά — και δεν ταίριαξε
        c = await _counter()
        assert c["seq"] == 4 and c["last_date"] == "2026-06-15"                  # η ημερομηνία του A δεν γύρισε πίσω

    _run(scenario())


def test_a_boolean_in_the_counter_counts_as_zero_like_any_other_non_number():
    """Το `True` είναι «ακέραιος 1» στην Python· στον μετρητή όμως είναι κατεστραμμένη τιμή και μετράει ως 0."""
    from invoice_services import numbering_report

    async def scenario():
        await db.counters.insert_one({"_id": CID, "seq": True})
        assert await _next() == "ΤΠΥ-2026-0001"

        await db.counters.delete_many({})
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-0002")
        await db.counters.insert_one({"_id": CID, "seq": True})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["counter"] == {"seq": 0, "state": "behind", "ahead_by": 0}

    _run(scenario())


def test_an_infinite_counter_value_neither_breaks_the_report_nor_the_next_allocation():
    """Χειροκίνητη επέμβαση ή κατεστραμμένο αντίγραφο: το `int(inf)` ρίχνει OverflowError. Η αναφορά πρέπει να δείξει το
    πρόβλημα (μετρητής πίσω) και η επόμενη παραχώρηση να τον διορθώσει, όπως υπόσχεται το docstring της `as_int`."""
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-0002")
        await db.counters.insert_one({"_id": CID, "seq": float("inf")})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["counter"]["state"] == "behind" and s["ok"] is False
        assert await _next(floor=2) == "ΤΠΥ-2026-0003"
        assert (await _counter())["seq"] == 3

    _run(scenario())


def test_the_first_use_of_a_series_starts_above_the_highest_existing_number_not_the_last_one_read():
    """Έγγραφα από την εποχή του «πλήθος + 1» δεν είναι απαραίτητα αποθηκευμένα κατά αύξουσα σειρά (διαγραφές, εισαγωγές,
    επαναφορά αντιγράφου). Η σάρωση της πρώτης χρήσης πρέπει να κρατά τον ΜΕΓΑΛΥΤΕΡΟ αριθμό της σειράς του γραφείου και του
    έτους — όχι τον τελευταίο που διάβασε — αλλιώς ο μετρητής ξεκινά κάτω από υπάρχοντα αριθμό και δίνει διπλότυπο."""
    async def scenario():
        await _put("ΤΠΥ-2026-0007")
        await _put("ΤΠΥ-2026-0002")
        await _put("ΤΠΥ-2026-abc")                           # κατεστραμμένος αριθμός: αγνοείται
        await _put("ΤΠΥ-2025-0099")                          # άλλο έτος: δεν μετράει
        await _put("ΤΠΥ-2026-0300", firm=OTHER_FIRM)         # άλλο γραφείο: δεν μετράει
        assert await _next() == "ΤΠΥ-2026-0008"
        assert (await _counter())["seq"] == 8

    _run(scenario())


# ========================================== 2. Η διόρθωση της τελευταίας ημερομηνίας ====

def test_the_date_is_not_healed_when_another_issue_took_a_number_in_between(monkeypatch, frozen_today):
    """Ο B (10/06) βρίσκει «ορφανή» ημερομηνία 12/06 παλιάς αποτυχημένης έκδοσης (ο μετρητής δεν έχει κινηθεί από τον
    τελευταίο γραμμένο αριθμό) και πάει να τη διορθώσει. Ανάμεσα στην απόφαση και τη διόρθωση ο A (12/06 — ΙΔΙΑ ημερομηνία
    με την ορφανή) παίρνει το 0005. Η διόρθωση πρέπει να ακυρωθεί από το φίλτρο στον αριθμό (`seq`): αν γραφόταν η
    ημερομηνία 01/06, ο B θα έπαιρνε το 0006 με 10/06 μετά το 0005 του A (12/06) — αριθμός εκτός χρονολογικής σειράς."""
    import invoice_services as svc
    import numbering

    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-12", "last_alloc_at": _ago(days=3)})
        b = await _draft_doc(when="2026-06-10")
        real_reset = numbering.reset_series_date

        async def a_takes_a_number_just_before_the_heal(*args, **kwargs):
            monkeypatch.setattr(numbering, "reset_series_date", real_reset)
            assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-12", floor=4) == "ΤΠΥ-2026-0005"
            return await real_reset(*args, **kwargs)

        monkeypatch.setattr(numbering, "reset_series_date", a_takes_a_number_just_before_the_heal)
        with pytest.raises(svc.InvoiceWorkflowError) as busy:
            await _issue(b)
        monkeypatch.setattr(numbering, "reset_series_date", real_reset)
        assert busy.value.args[0] == svc.SERIES_BUSY_MESSAGE
        c = await _counter()
        assert c["seq"] == 5 and c["last_date"] == "2026-06-12"        # η ημερομηνία του A δεν γύρισε πίσω
        stored = await db.invoices.find_one({"_id": b})
        assert stored["status"] == "draft" and not stored["number"] and "issue_claim" not in stored

    _run(scenario())


def test_the_heal_copes_with_a_last_issued_invoice_that_has_no_date(frozen_today):
    """Παλιό παραστατικό χωρίς ημερομηνία (εισαγωγή): η ημερομηνία του δεν μπορεί να οριστεί ως «τελευταία» του μετρητή —
    η διόρθωση την αφαιρεί (None) αντί να ρίξει την έκδοση με ValueError."""
    async def scenario():
        await _put("ΤΠΥ-2026-0004", when="")
        await db.counters.insert_one({"_id": CID, "seq": 4, "last_date": "2026-06-12", "last_alloc_at": _ago(days=3)})
        done = await _issue(await _draft_doc(when="2026-06-10"))
        assert done["number"] == "ΤΠΥ-2026-0005" and done["date"] == "2026-06-10"
        assert (await _counter())["last_date"] == "2026-06-10"

    _run(scenario())


# ===================================== 3. Ο μετρητής ανά γραφείο, σειρά και έτος ====

def test_every_firm_has_its_own_counter_in_every_series(frozen_today):
    """Η ΑΠΥ και το ΠΙΣ δεν έχουν το παλιό όνομα μετρητή της ΤΠΥ· αν το όνομά τους δεν περιέχει το γραφείο, δύο γραφεία
    μοιράζονται αριθμούς (και ημερομηνίες): το δεύτερο θα έπαιρνε 0002 — ή θα απορριπτόταν ως «προγενέστερο»."""
    import invoice_services as svc

    async def scenario():
        for prefix in ("ΑΠΥ", "ΠΙΣ"):
            assert await svc.next_invoice_number(FIRM, prefix, 2026, date_iso="2026-06-12") == f"{prefix}-2026-0001"
            assert await svc.next_invoice_number(OTHER_FIRM, prefix, 2026, date_iso="2026-01-02") == f"{prefix}-2026-0001"
            assert await svc.next_invoice_number(FIRM, prefix, 2026, date_iso="2026-06-12") == f"{prefix}-2026-0002"

    _run(scenario())


def test_every_year_has_its_own_counter_in_every_series(frozen_today):
    import invoice_services as svc

    async def scenario():
        for prefix in ("ΑΠΥ", "ΠΙΣ"):
            assert await svc.next_invoice_number(FIRM, prefix, 2026, date_iso="2026-06-12") == f"{prefix}-2026-0001"
            assert await svc.next_invoice_number(FIRM, prefix, 2025, date_iso="2025-03-01") == f"{prefix}-2025-0001"
            assert await svc.next_invoice_number(FIRM, prefix, 2026, date_iso="2026-06-13") == f"{prefix}-2026-0002"

    _run(scenario())


# ======================================== 4. Το «προηγούμενο της σειράς» ====

def test_the_previous_of_the_series_is_the_highest_number_not_the_first_or_the_latest_written():
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-06-01")
        await _put("ΤΠΥ-2026-0005", when="2026-06-05")
        await _put("ΤΠΥ-2026-0003", when="2026-06-03")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (5, "2026-06-05", "ΤΠΥ-2026-0005")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2025) is None
        assert await svc._previous_in_series(FIRM, "ΑΠΥ", 2026) is None

    _run(scenario())


def test_old_numbered_drafts_set_neither_the_floor_nor_the_date_of_the_series(frozen_today):
    """Ένα πρόχειρο παλιάς έκδοσης με αριθμό (0900, 14/06) δεν είναι εκδοθέν παραστατικό: δεν ανεβάζει τον επόμενο αριθμό
    (τρύπα 4–899) και δεν κάνει «προγενέστερη» μια κανονική ημερομηνία."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0003", when="2026-06-01")
        await db.counters.insert_one({"_id": CID, "seq": 3, "last_date": "2026-06-01", "last_alloc_at": _ago(days=3)})
        await _put("ΤΠΥ-2026-0900", status="draft", when="2026-06-14")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (3, "2026-06-01", "ΤΠΥ-2026-0003")
        done = await _issue(await _draft_doc(when="2026-06-10"))
        assert done["number"] == "ΤΠΥ-2026-0004"

    _run(scenario())


@pytest.mark.parametrize("status", ["issued", "paid", "cancelled"])
def test_every_numbered_status_sets_the_floor_and_the_date_of_the_series(frozen_today, status):
    """Ένα ακυρωμένο (ή εξοφλημένο) παραστατικό κρατά τον αριθμό και την ημερομηνία του: ούτε ξαναδίνεται ο αριθμός του
    ούτε επιτρέπεται νεότερος αριθμός με προγενέστερη ημερομηνία."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-06-01")
        await _put("ΤΠΥ-2026-0002", status=status, when="2026-06-14")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (2, "2026-06-14", "ΤΠΥ-2026-0002")

        early = await _draft_doc(when="2026-06-10")
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await _issue(early)
        assert "14/06/2026" in err.value.args[0] and "ΤΠΥ-2026-0002" in err.value.args[0]
        assert (await db.invoices.find_one({"_id": early}))["status"] == "draft"

        await db.counters.insert_one({"_id": CID, "seq": 1, "last_date": "2026-06-01", "last_alloc_at": _ago(days=1)})
        ok = await _issue(await _draft_doc(when="2026-06-15"))
        assert ok["number"] == "ΤΠΥ-2026-0003"                       # όχι 0002: ο αριθμός υπάρχει ήδη

    _run(scenario())


def test_the_series_of_another_firm_never_constrains_this_firm(frozen_today):
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0007", when="2026-06-14", firm=OTHER_FIRM)
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) is None
        done = await _issue(await _draft_doc(when="2026-06-10"))
        assert done["number"] == "ΤΠΥ-2026-0001"
        assert await _counter(f"invoice-{OTHER_FIRM}-2026") is None

    _run(scenario())


def test_a_malformed_number_in_the_series_does_not_block_issuing(frozen_today):
    """Αριθμός εκτός μορφής (εισαγωγή, χειροκίνητη επέμβαση) που ξεκινά όπως η σειρά δεν επιτρέπεται να μπλοκάρει κάθε
    έκδοση της σειράς με ValueError — αναφέρεται από τον έλεγχο αρίθμησης."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-06-01")
        await _put("ΤΠΥ-2026-abc", when="2026-06-02")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (1, "2026-06-01", "ΤΠΥ-2026-0001")
        done = await _issue(await _draft_doc(when="2026-06-10"))
        assert done["number"] == "ΤΠΥ-2026-0002"

    _run(scenario())


def test_a_last_invoice_without_a_date_does_not_block_issuing(frozen_today):
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0003", when="")
        assert await svc._previous_in_series(FIRM, "ΤΠΥ", 2026) == (3, "", "ΤΠΥ-2026-0003")
        done = await _issue(await _draft_doc(when="2026-06-10"))
        assert done["number"] == "ΤΠΥ-2026-0004"

    _run(scenario())


def test_the_date_error_never_suggests_a_date_that_cannot_be_issued(frozen_today):
    """Το τελευταίο παραστατικό είναι από παλιά έκδοση με ΜΕΛΛΟΝΤΙΚΗ ημερομηνία (31/12). Ο κανόνας συγκρίνει με το
    min(ημερομηνία, σήμερα)· το μήνυμα πρέπει να προτείνει «σήμερα» (15/06) — όχι το 31/12 που η έκδοση θα απέρριπτε."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0005", when="2026-12-31")
        early = await _draft_doc(when="2026-06-10")
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await _issue(early)
        message = err.value.args[0]
        assert "σε 15/06/2026 ή αργότερα" in message
        assert "σε 31/12/2026 ή αργότερα" not in message
        # και η πρόταση ισχύει πράγματι: με 15/06 το πρόχειρο εκδίδεται
        await db.invoices.update_one({"_id": early}, {"$set": {"date": "2026-06-15"}})
        assert (await _issue(early))["number"] == "ΤΠΥ-2026-0006"

    _run(scenario())


# ============================================================ 5. Η έκδοση ====

def test_a_draft_of_another_firm_cannot_be_claimed_or_issued(frozen_today):
    """Το κλείδωμα του πρόχειρου περιορίζεται στο γραφείο. Αλλιώς ένα αίτημα άλλου γραφείου κλειδώνει το πρόχειρο (που ο
    καθαρισμός του δεν ξεκλειδώνει, γιατί φιλτράρει με το δικό του γραφείο), παίρνει αριθμό στη σειρά ΤΟΥ και τον
    επιστρέφει."""
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")
        stale = await db.invoices.find_one({"_id": draft})
        with pytest.raises(svc.InvoiceWorkflowError):
            await svc.issue_draft(OTHER_FIRM, stale, {})
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and not stored["number"]
        assert "issue_claim" not in stored and "issue_claim_at" not in stored
        assert await _counter(f"invoice-{OTHER_FIRM}-2026") is None           # δεν παραχωρήθηκε αριθμός
        assert (await _issue(draft))["number"] == "ΤΠΥ-2026-0001"            # και το κανονικό γραφείο το εκδίδει αμέσως

    _run(scenario())


def test_a_draft_from_an_older_version_that_keeps_its_own_number_is_still_checked_against_the_series_date(frozen_today):
    """Το πρόχειρο παλιάς έκδοσης κρατά τον αριθμό που ήδη έχει (δεν περνά από τον μετρητή, άρα ούτε από τον έλεγχο
    ημερομηνίας του). Ο έλεγχος «όχι πριν τον προηγούμενο της σειράς» πρέπει να γίνει πριν την έκδοση: αλλιώς ένα παλιό
    πρόχειρο με προγενέστερη ημερομηνία παίρνει θέση ΜΕΣΑ στη σειρά με ημερομηνία πριν από τα ήδη εκδοθέντα."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0005", when="2026-06-14")
        old = await _draft_doc(when="2026-06-10", number="ΤΠΥ-2026-0003")
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await _issue(old)
        assert "14/06/2026" in err.value.args[0] and "ΤΠΥ-2026-0005" in err.value.args[0]
        stored = await db.invoices.find_one({"_id": old})
        assert stored["status"] == "draft" and stored["number"] == "ΤΠΥ-2026-0003" and "issue_claim" not in stored
        assert await _counter() is None                                   # δεν παραχωρήθηκε αριθμός

        await db.invoices.update_one({"_id": old}, {"$set": {"date": "2026-06-14"}})
        done = await _issue(old)
        assert done["status"] == "issued" and done["number"] == "ΤΠΥ-2026-0003"

    _run(scenario())


def test_a_draft_that_keeps_its_number_is_checked_against_the_series_of_that_number_not_of_its_type(frozen_today):
    """Πρόχειρο της παλιάς αρίθμησης (ΤΠΥ-…) που σήμερα θα ήταν απόδειξη λιανικής (ΑΠΥ): η ημερομηνία του ελέγχεται με τη
    σειρά του αριθμού που ΕΧΕΙ (ΤΠΥ). Η σειρά του είδους (ΑΠΥ) δεν έχει σχέση με τον αριθμό που θα φέρει το παραστατικό."""
    import invoice_services as svc
    from mydata_client import TYPE_RECEIPT_B2C

    async def scenario():
        await _put("ΤΠΥ-2026-0005", when="2026-06-14")
        old = await _draft_doc(when="2026-06-10", number="ΤΠΥ-2026-0003", invoice_type=TYPE_RECEIPT_B2C)
        with pytest.raises(svc.InvoiceWorkflowError) as err:
            await _issue(old)
        assert "ΤΠΥ-2026-0005" in err.value.args[0]
        assert (await db.invoices.find_one({"_id": old}))["status"] == "draft"

    _run(scenario())


def test_a_draft_deleted_while_it_is_being_issued_gives_its_number_back_and_a_clear_error(monkeypatch, frozen_today):
    """Το κλείδωμα έληξε και το πρόχειρο διαγράφηκε ενώ η έκδοση ήταν στη μέση: η τελική εγγραφή δεν βρίσκει έγγραφο. Ο
    χρήστης παίρνει το μήνυμα της έκδοσης (όχι ένα απροσδιόριστο σφάλμα από τον καθαρισμό) και ο αριθμός που δόθηκε δεν
    μένει να λείπει από τη σειρά."""
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")

        async def delete_then_write(real, query, update, *args, **kwargs):
            if update.get("$set", {}).get("status") == "issued":
                await db.invoices.delete_one({"_id": draft})
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": delete_then_write}))
        with pytest.raises(svc.InvoiceWorkflowError, match="δεν υπάρχει πια"):
            await _issue(draft)
        monkeypatch.setattr(svc, "db", db)
        assert (await _counter())["seq"] == 0
        assert await db.invoices.count_documents({"firm_id": FIRM}) == 0

    _run(scenario())


def test_a_credit_note_with_a_zero_total_is_refused_and_burns_no_number(frozen_today):
    import invoice_services as svc

    async def scenario():
        credit = await _draft_doc(when="2026-06-15", invoice_type="5.1", credit_note_for=str(ObjectId()),
                                  subtotal=0.0, total=0.0)
        with pytest.raises(svc.InvoiceWorkflowError, match="αρνητικό"):
            await _issue(credit)
        stored = await db.invoices.find_one({"_id": credit})
        assert stored["status"] == "draft" and not stored["number"] and "issue_claim" not in stored
        assert await _counter(f"invoice-ΠΙΣ-{FIRM}-2026") is None

    _run(scenario())


def test_failures_at_every_stage_of_the_issue_leave_neither_a_hole_nor_a_duplicate(monkeypatch, frozen_today, protected):
    """Η κύρια ιδιότητα της αρίθμησης με αποτυχίες που εγχέονται σε κάθε στάδιο της τελικής εγγραφής: πριν εφαρμοστεί
    (σφάλμα, ακύρωση αιτήματος) ο αριθμός επιστρέφεται· αφού εφαρμοστεί (χάθηκε η απάντηση, ακύρωση) μένει στο
    παραστατικό. Στο τέλος οι αριθμοί είναι συνεχείς, ο μετρητής ίσος με τον τελευταίο και η αναφορά καθαρή."""
    import invoice_services as svc

    async def scenario():
        drafts = [await _draft_doc(when="2026-06-15") for _ in range(6)]
        script = iter(["ok", "fail_before", "fail_after", "cancel_before", "cancel_after", "ok"])

        async def scripted(real, query, update, *args, **kwargs):
            if update.get("$set", {}).get("status") != "issued":
                return await real(query, update, *args, **kwargs)                  # το κλείδωμα
            action = next(script, "ok")
            if action == "fail_before":
                raise RuntimeError("η σύνδεση διακόπηκε πριν την εγγραφή")
            if action == "cancel_before":
                raise asyncio.CancelledError()
            result = await real(query, update, *args, **kwargs)
            if action == "fail_after":
                raise RuntimeError("χάθηκε η απάντηση της εγγραφής")
            if action == "cancel_after":
                raise asyncio.CancelledError()
            return result

        async def counter_equals_the_last_written_number(step):
            """Ο μετρητής δεν μένει μπροστά (τρύπα) ούτε πίσω (γυρισμένος) από τον τελευταίο αριθμό που γράφτηκε."""
            written = [int(d["number"].rsplit("-", 1)[-1])
                       for d in await db.invoices.find({"firm_id": FIRM, "status": "issued"}).to_list(20)]
            counter = await _counter()
            assert (counter["seq"] if counter else 0) == max(written, default=0), f"μετά το πρόχειρο {step}"

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": scripted}))
        for step, draft in enumerate(drafts, 1):
            try:
                await _issue(draft)
            except BaseException:  # noqa: BLE001 — οι εγχυμένες αποτυχίες
                pass
            await counter_equals_the_last_written_number(step)
        monkeypatch.setattr(svc, "db", db)

        states = {d: (await db.invoices.find_one({"_id": d}))["status"] for d in drafts}
        assert list(states.values()).count("draft") == 2                        # αυτά που απέτυχαν πριν την εγγραφή
        for draft in [d for d, s in states.items() if s == "draft"]:
            await _issue(draft)

        issued = await db.invoices.find({"firm_id": FIRM, "status": "issued"}).to_list(20)
        assert sorted(d["number"] for d in issued) == [f"ΤΠΥ-2026-{n:04d}" for n in range(1, 7)]
        assert (await _counter())["seq"] == 6
        assert await db.invoices.count_documents({"firm_id": FIRM, "issue_claim": {"$exists": True}}) == 0
        report = await svc.numbering_report(FIRM)
        assert report["ok"] is True and report["problems"]["gaps"] == 0 and report["problems"]["duplicates"] == 0

    _run(scenario())


# ============================================================ 6. Η αναφορά ====

def test_a_number_with_ten_digits_is_out_of_format_while_nine_digits_is_still_a_sequence():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001")
        await _put("ΤΠΥ-2026-1000000000")                                    # 10 ψηφία
        report = await numbering_report(FIRM)
        assert [u["number"] for u in report["unparseable"]] == ["ΤΠΥ-2026-1000000000"]
        s = report["series"][0]
        assert s["count"] == 1 and s["last"] == 1 and s["gaps_total"] == 0
        await _put("ΤΠΥ-2026-100000000")                                     # 9 ψηφία: αύξων αριθμός (με τεράστιο κενό)
        s = (await numbering_report(FIRM))["series"][0]
        assert s["last"] == 100000000 and s["gaps_total"] == 100000000 - 2

    _run(scenario())


def test_an_invoice_without_a_date_does_not_raise_a_false_out_of_order_alarm():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-05")
        await _put("ΤΠΥ-2026-0002", when="")
        await _put("ΤΠΥ-2026-0003", when="2026-03-06")
        await db.counters.insert_one({"_id": CID, "seq": 3})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["date_order"] == [] and s["ok"] is True

    _run(scenario())


def test_the_report_counts_only_the_documents_of_the_firm_it_is_asked_about():
    """Οι «χωρίς αριθμό» και τα «πρόχειρα με παλιό αριθμό» ενός γραφείου δεν εμφανίζονται στην αναφορά (και στις
    κρίσιμες κάρτες) ενός άλλου."""
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("", firm=OTHER_FIRM)
        await _put(None, status="paid", firm=OTHER_FIRM)
        await _put("ΤΠΥ-2026-0099", status="draft", firm=OTHER_FIRM)
        report = await numbering_report(FIRM)
        assert report["problems"]["unnumbered"] == 0 and report["numbered_drafts"] == 0
        assert report["checked"] == 1
        other = await numbering_report(OTHER_FIRM)
        assert other["problems"]["unnumbered"] == 2 and other["numbered_drafts"] == 1

    _run(scenario())


def test_the_report_survives_a_failure_to_read_the_indexes(monkeypatch):
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0001")

        async def unreadable(real, *args, **kwargs):
            raise RuntimeError("δεν επιτρέπεται η ανάγνωση των δεικτών")

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"index_information": unreadable}))
        report = await svc.numbering_report(FIRM)
        monkeypatch.setattr(svc, "db", db)
        assert report["unique_index"] is False and report["checked"] == 1

    _run(scenario())


# ================================================== 7. Οι κάρτες του Autopilot ====

@pytest.mark.parametrize("gap_count,more", [(10, False), (11, True)])
def test_the_gap_card_names_ten_numbers_and_says_so_only_when_there_are_more(protected, gap_count, more):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put(f"ΤΠΥ-2026-{gap_count + 2:04d}", when="2026-03-02")
        await run_firm(FIRM, ["numbering_check"])
        [card] = await _cards("gaps:")
        message = card["message"]
        assert all(f"{n:04d}" in message for n in range(2, 12))                  # τα πρώτα δέκα κενά: 0002 … 0011
        assert ("και άλλοι" in message) is more
        assert ("0012" in message) is False
        assert f"σύνολο {gap_count}" in message

    _run(scenario())


def test_a_new_gap_beyond_the_fifty_listed_opens_a_new_card_after_a_person_closed_the_first(protected):
    """Η λίστα κενών κόβεται στα 50, το σύνολο όχι. Ένα νέο κενό πέρα από τα 50 αλλάζει μόνο το σύνολο — πρέπει να αλλάξει
    και το κλειδί της κάρτας, αλλιώς ο λογιστής που έκλεισε την πρώτη δεν μαθαίνει ποτέ για το νέο."""
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0100", when="2026-03-02")
        await run_firm(FIRM, ["numbering_check"])
        [card] = await _cards("gaps:")
        assert "σύνολο 98" in card["message"]
        await _human_close(card)
        await _put("ΤΠΥ-2026-0102", when="2026-03-03")                          # νέο κενό: 101 (πέρα από τα 50 πρώτα)
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 1
        [fresh] = await _cards("gaps:", open_only=True)
        assert "σύνολο 99" in fresh["message"]

    _run(scenario())


def test_dates_that_go_backwards_raise_a_warning_card(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0002", when="2026-03-10")
        await _put("ΤΠΥ-2026-0003", when="2026-03-05")
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 1 and r["stats"]["ok"] is False
        [card] = await _cards("order:")
        assert card["level"] == "warning" and card["requires_action"] is True and card.get("resolved_at") is None
        assert "ΤΠΥ-2026-0003 (05/03/2026) μετά το ΤΠΥ-2026-0002 (10/03/2026)" in card["message"]
        assert "ΤΠΥ-2026" in card["title"]

    _run(scenario())


def test_a_second_out_of_order_pair_opens_a_new_card_after_a_person_closed_the_first(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0002", when="2026-03-10")
        await _put("ΤΠΥ-2026-0003", when="2026-03-05")
        await run_firm(FIRM, ["numbering_check"])
        [card] = await _cards("order:")
        await _human_close(card)
        assert (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]["raised"] == 0     # το ίδιο πρόβλημα: σιωπή
        await _put("ΤΠΥ-2026-0004", when="2026-03-02")                                   # δεύτερο ζεύγος εκτός σειράς
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 1
        [fresh] = await _cards("order:", open_only=True)
        assert "ΤΠΥ-2026-0004" in fresh["message"]

    _run(scenario())


def test_a_counter_behind_the_last_number_raises_a_warning_card_but_a_counter_ahead_does_not(protected):
    async def scenario():
        for n in range(1, 6):
            await _put(f"ΤΠΥ-2026-{n:04d}", when=f"2026-03-{n:02d}")
        await db.counters.insert_one({"_id": CID, "seq": 2})
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["ok"] is False and r["stats"]["problems"]["counter_behind"] == 1
        [card] = await _cards("counter:")
        assert card["level"] == "warning" and card["requires_action"] is True
        assert "Ο μετρητής δείχνει 2" in card["message"] and "είναι 5" in card["message"]

        await db.counters.update_one({"_id": CID}, {"$set": {"seq": 7}})        # τώρα ο μετρητής είναι ΜΠΡΟΣΤΑ
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert await _cards("counter:", open_only=True) == []                    # η κάρτα του μετρητή έκλεισε
        [gap] = await _cards("gaps:", open_only=True)                            # και οι αριθμοί 6, 7 αναφέρονται ως κενό
        assert "0006" in gap["message"] and "0007" in gap["message"]
        assert r["stats"]["raised"] == 1

    _run(scenario())


def test_numbers_out_of_format_raise_a_warning_card(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("INV/17", when="2026-03-02")
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["ok"] is False and r["stats"]["problems"]["unparseable"] == 1
        [card] = await _cards("format:")
        assert card["level"] == "warning" and card["requires_action"] is True and "INV/17" in card["message"]

    _run(scenario())


def test_issued_documents_without_a_number_raise_a_critical_card(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("", when="2026-03-02")
        await _put(None, status="paid", when="2026-03-03")
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["ok"] is False and r["stats"]["problems"]["unnumbered"] == 2
        [card] = await _cards("unnumbered:")
        assert card["level"] == "critical" and card["requires_action"] is True and "2 εκδοθέντα" in card["message"]

    _run(scenario())


def test_a_card_closed_by_a_person_keeps_its_closer_when_the_problem_changes_and_comes_back(protected):
    """Αν ο Autopilot ξανάκλεινε (ως «autopilot») κάρτες που έχουν ήδη κλείσει, θα έσβηνε την αναγνώριση του ανθρώπου:
    όταν το πρόβλημα επέστρεφε στην ίδια μορφή, η κάρτα θα ξανάνοιγε."""
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0003", when="2026-03-03")
        await run_firm(FIRM, ["numbering_check"])
        [first] = await _cards("gaps:")
        await _human_close(first)

        extra = await _put("ΤΠΥ-2026-0005", when="2026-03-05")                  # το πρόβλημα αλλάζει: κενά 2 και 4
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 1
        stored = await db.notifications.find_one({"_id": first["_id"]})
        assert stored["resolved_by"] == "lawyer@example.gr" and stored["resolution"] == "done"

        await db.invoices.delete_one({"_id": extra})                             # και επιστρέφει στην αρχική μορφή
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["stats"]["raised"] == 0                                         # ο άνθρωπος το είχε ήδη δει
        assert await _cards("gaps:", open_only=True) == []

    _run(scenario())


def test_the_job_reports_only_the_cards_it_really_opened(protected):
    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0003", when="2026-03-03")
        first = (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]
        second = (await run_firm(FIRM, ["numbering_check"]))[0]["stats"]
        assert first["raised"] == 1 and second["raised"] == 0 and second["resolved"] == 0
        assert len(await _cards("gaps:")) == 1

    _run(scenario())
