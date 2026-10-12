"""Έλεγχος γύρου 5 — Πακέτο 1: αρίθμηση παραστατικών που δεν μπορεί να χαλάσει σιωπηλά.

Μέχρι τον γύρο 4 η σειρά ήταν σωστή **όσο όλα πήγαιναν καλά**. Τρία πράγματα έλειπαν:

* κανένας μηχανισμός δεν εμπόδιζε στη βάση δύο εκδοθέντα με τον ίδιο αριθμό (μόνο ο κώδικας της έκδοσης)·
* κανείς δεν έλεγχε ποτέ τη σειρά μετά την έκδοση — ένα κενό ή ένα διπλότυπο από επαναφορά αντιγράφου,
  χειροκίνητη επέμβαση ή παλιά έκδοση της εφαρμογής έμενε αθέατο μέχρι τον έλεγχο της εφορίας·
* ο έλεγχος ημερομηνίας (όχι προγενέστερη του προηγούμενου αριθμού) γινόταν πριν από την παραχώρηση
  αριθμού, όχι μαζί της: δύο πρόχειρα με διαφορετική ημερομηνία που εκδίδονταν ταυτόχρονα μπορούσαν
  να πάρουν αριθμούς με αντίστροφη χρονολογική σειρά.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from automation import JOB_FUNCS, JOB_LABELS, JOBS, run_firm
from db import db
from tax_utils import athens_today

FIRM = "firm-numbering"
OTHER_FIRM = "firm-numbering-other"


# ------------------------------------------------------------------ βοηθητικά ----

def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register",
                    json={"email": "numbering@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    user = _run(db.users.find_one({"email": "numbering@example.gr"}))
    _run(db.users.update_one({"email": "numbering@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, firm_id=user["firm_id"])


@pytest.fixture()
def frozen_today(monkeypatch):
    """«Σήμερα» (Αθήνα) = 15/06/2026· τα σενάρια με ημερομηνίες δεν εξαρτώνται από το πότε τρέχουν."""
    import tax_utils

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tax_utils, "datetime", FakeDateTime)
    assert athens_today() == date(2026, 6, 15)
    return date(2026, 6, 15)


class _HookedCollection:
    """Συλλογή με «άγκιστρα»: το hook(real_method, *args, **kwargs) αντικαθιστά τη μέθοδο."""

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
    """Η βάση με άγκιστρα σε μία συλλογή (η mongomock δίνει νέο wrapper σε κάθε `db.<συλλογή>`, γι' αυτό
    αντικαθίσταται το `db` της μονάδας και όχι η μέθοδος πάνω στη συλλογή)."""

    def __init__(self, real, collection, hooks):
        self._real, self._collection, self._hooks = real, collection, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _HookedCollection(attr, self._hooks) if name == self._collection else attr


@pytest.fixture()
def protected():
    """Η βάση όπως μετά την εκκίνηση: με το unique index των αριθμών."""
    from server import ensure_invoice_number_index
    _run(ensure_invoice_number_index())


async def _put(number, *, status="issued", when="2026-03-01", firm=FIRM, **over):
    doc = {"firm_id": firm, "number": number, "status": status, "date": when, "total": 124.0,
           "subtotal": 100.0, "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _draft_doc(firm=FIRM, when=None, **over):
    from mydata_client import TYPE_SERVICES_B2B
    doc = {"firm_id": firm, "number": "", "status": "draft", "client_id": "x", "invoice_type": TYPE_SERVICES_B2B,
           "date": when or athens_today().isoformat(), "subtotal": 100.0, "total": 124.0, "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _series(firm, count, year=2026, prefix="ΤΠΥ", start_day=1):
    for n in range(1, count + 1):
        await _put(f"{prefix}-{year}-{n:04d}", when=f"{year}-03-{start_day + n - 1:02d}", firm=firm)


async def _counter(firm=FIRM, prefix="ΤΠΥ", year=2026):
    import invoice_services as svc
    return await db.counters.find_one({"_id": svc._counter_id(firm, prefix, year)})


# ======================================================== 1. Unique index ====

def test_two_issued_documents_cannot_share_a_number_in_the_database():
    """Η βάση, όχι μόνο ο κώδικας, αρνείται δεύτερο εκδοθέν/εξοφλημένο/ακυρωμένο με τον ίδιο αριθμό."""
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _put("ΤΠΥ-2026-0001")
        for status in ("issued", "paid", "cancelled"):
            with pytest.raises(DuplicateKeyError):
                await _put("ΤΠΥ-2026-0001", status=status)
        assert await db.invoices.count_documents({"firm_id": FIRM}) == 1

    _run(scenario())


def test_the_same_number_in_another_firm_is_fine():
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _put("ΤΠΥ-2026-0001", firm=FIRM)
        await _put("ΤΠΥ-2026-0001", firm=OTHER_FIRM)

    _run(scenario())


def test_drafts_never_collide_whatever_their_number():
    """Τα πρόχειρα έχουν κενό αριθμό (ή παλιό αριθμό από παλαιότερη έκδοση)· ποτέ δεν συγκρούονται."""
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        for _ in range(3):
            await _put("", status="draft")
        await _put(None, status="draft")
        await db.invoices.insert_one({"firm_id": FIRM, "status": "draft"})             # χωρίς πεδίο αριθμού
        await _put("ΤΠΥ-2026-0007", status="draft")
        await _put("ΤΠΥ-2026-0007", status="draft")
        await _put("ΤΠΥ-2026-0007")                                                    # και ένα εκδοθέν με τον ίδιο

    _run(scenario())


def test_documents_without_a_usable_number_are_not_indexed():
    """Εκδοθέν με κενό/απών αριθμό (παλιά δεδομένα) δεν πρέπει να μπλοκάρει το επόμενο — ούτε το index."""
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _put("")
        await _put("")
        await _put(None)
        await _put(None)
        await db.invoices.insert_one({"firm_id": FIRM, "status": "issued"})
        await db.invoices.insert_one({"firm_id": FIRM, "status": "issued"})

    _run(scenario())


def test_a_draft_cannot_be_issued_into_a_number_that_is_already_taken():
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _put("ΤΠΥ-2026-0003")
        draft = await _draft_doc()
        with pytest.raises(DuplicateKeyError):
            await db.invoices.update_one({"_id": draft}, {"$set": {"status": "issued", "number": "ΤΠΥ-2026-0003"}})
        assert (await db.invoices.find_one({"_id": draft}))["status"] == "draft"

    _run(scenario())


def test_creating_the_index_twice_is_harmless():
    from server import ensure_invoice_number_index, INVOICE_NUMBER_INDEX

    async def scenario():
        await ensure_invoice_number_index()
        await ensure_invoice_number_index()
        info = await db.invoices.index_information()
        assert [n for n in info if n == INVOICE_NUMBER_INDEX] == [INVOICE_NUMBER_INDEX]
        assert info[INVOICE_NUMBER_INDEX]["unique"] is True

    _run(scenario())


def test_startup_creates_the_invoice_number_index(client):
    from server import INVOICE_NUMBER_INDEX
    info = _run(db.invoices.index_information())
    assert INVOICE_NUMBER_INDEX in info and info[INVOICE_NUMBER_INDEX]["unique"] is True


def test_existing_duplicates_do_not_stop_the_application_but_are_logged(caplog):
    """Αν υπάρχουν ήδη διπλοί αριθμοί η δημιουργία αποτυγχάνει: η εφαρμογή ξεκινά, το σφάλμα καταγράφεται."""
    from server import ensure_invoice_number_index

    class FailingCollection:
        async def index_information(self):
            return {}

        async def create_index(self, *a, **k):
            raise DuplicateKeyError("E11000 duplicate key error")

    with caplog.at_level(logging.ERROR):
        _run(ensure_invoice_number_index(FailingCollection()))
    assert any("αποτυχία δημιουργίας του unique index" in r.getMessage() and "numbering-check" in r.getMessage()
               for r in caplog.records)


# ================================================= 2. Έλεγχος της σειράς ====

def test_a_clean_series_reports_no_problem():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 3)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 3})
        rep = await numbering_report(FIRM)
        assert rep["checked"] == 3 and rep["problems"] == {
            "gaps": 0, "duplicates": 0, "date_order": 0, "counter_behind": 0, "unparseable": 0, "unnumbered": 0}
        s = rep["series"][0]
        assert (s["series"], s["prefix"], s["year"], s["count"], s["first"], s["last"]) == ("ΤΠΥ-2026", "ΤΠΥ", 2026, 3, 1, 3)
        assert s["gaps"] == [] and s["duplicates"] == [] and s["date_order"] == []
        assert s["counter"] == {"seq": 3, "state": "ok", "ahead_by": 0} and s["ok"] is True

    _run(scenario())


def test_a_missing_number_is_reported_as_a_gap():
    from invoice_services import numbering_report

    async def scenario():
        for n, d in ((1, "01"), (2, "02"), (4, "04"), (7, "07")):
            await _put(f"ΤΠΥ-2026-{n:04d}", when=f"2026-03-{d}")
        rep = await numbering_report(FIRM)
        s = rep["series"][0]
        assert s["gaps"] == [3, 5, 6] and s["gaps_total"] == 3
        assert rep["problems"]["gaps"] == 3 and rep["ok"] is False and s["ok"] is False

    _run(scenario())


def test_a_cancelled_invoice_keeps_its_place_in_the_series():
    """Ο ακυρωμένος αριθμός δεν είναι κενό: το παραστατικό υπάρχει και φέρει τον αριθμό του."""
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0002", status="cancelled", when="2026-03-02")
        await _put("ΤΠΥ-2026-0003", status="paid", when="2026-03-03")
        rep = await numbering_report(FIRM)
        assert rep["series"][0]["gaps"] == [] and rep["ok"] is True

    _run(scenario())


def test_the_same_number_twice_is_reported_with_both_documents():
    from invoice_services import numbering_report

    async def scenario():
        first = await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        second = await _put("ΤΠΥ-2026-0002", when="2026-03-02")
        third = await _put("ΤΠΥ-2026-0002", status="paid", when="2026-03-02")
        rep = await numbering_report(FIRM)
        dup = rep["series"][0]["duplicates"]
        assert len(dup) == 1 and dup[0]["number"] == "ΤΠΥ-2026-0002" and dup[0]["count"] == 2
        assert set(dup[0]["ids"]) == {str(second), str(third)} and str(first) not in dup[0]["ids"]
        assert rep["problems"]["duplicates"] == 1 and rep["ok"] is False

    _run(scenario())


def test_a_date_that_goes_backwards_is_reported_once_where_it_happens():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0002", when="2026-03-10")
        await _put("ΤΠΥ-2026-0003", when="2026-03-05")          # πίσω από το 0002
        await _put("ΤΠΥ-2026-0004", when="2026-03-06")          # σε σειρά ως προς το 0003 — δεν αναφέρεται
        rep = await numbering_report(FIRM)
        order = rep["series"][0]["date_order"]
        assert order == [{"number": "ΤΠΥ-2026-0003", "date": "2026-03-05",
                          "previous_number": "ΤΠΥ-2026-0002", "previous_date": "2026-03-10"}]
        assert rep["problems"]["date_order"] == 1

    _run(scenario())


def test_the_same_date_on_consecutive_numbers_is_normal():
    from invoice_services import numbering_report

    async def scenario():
        for n in (1, 2, 3):
            await _put(f"ΤΠΥ-2026-{n:04d}", when="2026-03-01")
        assert (await numbering_report(FIRM))["series"][0]["date_order"] == []

    _run(scenario())


def test_a_counter_behind_the_last_number_is_reported():
    """Μετά από επαναφορά αντιγράφου ο μετρητής μπορεί να μείνει πίσω· η έκδοση τον διορθώνει, αλλά πρέπει να φανεί."""
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 5)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})
        rep = await numbering_report(FIRM)
        assert rep["series"][0]["counter"]["state"] == "behind"
        assert rep["problems"]["counter_behind"] == 1 and rep["ok"] is False

    _run(scenario())


def test_numbers_handed_out_but_never_used_show_up_as_a_gap_at_the_end():
    """Κατάρρευση μεταξύ παραχώρησης αριθμού και εγγραφής: ο μετρητής προχώρησε, δεν υπάρχει παραστατικό."""
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 3)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 5})
        s = (await numbering_report(FIRM))["series"][0]
        assert s["counter"] == {"seq": 5, "state": "ahead", "ahead_by": 2}
        assert s["gaps"] == [4, 5]

    _run(scenario())


def test_a_missing_counter_is_not_a_problem():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2)
        rep = await numbering_report(FIRM)
        assert rep["series"][0]["counter"]["state"] == "missing" and rep["ok"] is True

    _run(scenario())


def test_a_series_that_starts_above_one_is_not_a_gap_but_is_shown():
    """Συνέχιση αρίθμησης από άλλο σύστημα: το 0042 ως πρώτο δεν είναι «κενό 1–41»."""
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0042", when="2026-03-01")
        await _put("ΤΠΥ-2026-0043", when="2026-03-02")
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps"] == [] and s["first"] == 42 and s["ok"] is True

    _run(scenario())


def test_series_and_years_are_checked_separately():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2, year=2026, prefix="ΤΠΥ")
        await _series(FIRM, 3, year=2026, prefix="ΑΠΥ")
        await _series(FIRM, 1, year=2025, prefix="ΤΠΥ")
        await _put("ΠΙΣ-2026-0002", when="2026-04-01")             # πιστωτικά: το 0001 λείπει
        rep = await numbering_report(FIRM)
        by_name = {s["series"]: s for s in rep["series"]}
        assert set(by_name) == {"ΤΠΥ-2026", "ΑΠΥ-2026", "ΤΠΥ-2025", "ΠΙΣ-2026"}
        assert by_name["ΤΠΥ-2026"]["count"] == 2 and by_name["ΑΠΥ-2026"]["count"] == 3
        assert by_name["ΠΙΣ-2026"]["gaps"] == [] and by_name["ΠΙΣ-2026"]["first"] == 2
        assert rep["ok"] is True

    _run(scenario())


def test_series_are_listed_in_year_then_prefix_order():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 1, year=2026, prefix="ΤΠΥ")
        await _series(FIRM, 1, year=2025, prefix="ΤΠΥ")
        await _series(FIRM, 1, year=2026, prefix="ΑΠΥ")
        names = [s["series"] for s in (await numbering_report(FIRM))["series"]]
        assert names == ["ΤΠΥ-2025", "ΑΠΥ-2026", "ΤΠΥ-2026"]

    _run(scenario())


def test_the_year_filter_limits_the_report():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2, year=2025)
        await _put("ΤΠΥ-2026-0002", when="2026-03-01")
        rep = await numbering_report(FIRM, year=2025)
        assert [s["series"] for s in rep["series"]] == ["ΤΠΥ-2025"] and rep["ok"] is True
        assert (await numbering_report(FIRM))["ok"] is True      # το 2026 ξεκινά από το 0002: όχι κενό

    _run(scenario())


def test_drafts_are_not_part_of_the_series_but_old_numbered_drafts_are_counted():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2)
        await _put("", status="draft")
        await _put("ΤΠΥ-2026-0099", status="draft")
        rep = await numbering_report(FIRM)
        assert rep["checked"] == 2 and rep["series"][0]["last"] == 2 and rep["numbered_drafts"] == 1

    _run(scenario())


def test_other_firms_are_never_included():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2)
        await _put("ΤΠΥ-2026-0001", firm=OTHER_FIRM)
        await _put("ΤΠΥ-2026-0001", firm=OTHER_FIRM)
        await _put("ΤΠΥ-2026-0009", firm=OTHER_FIRM)
        rep = await numbering_report(FIRM)
        assert rep["checked"] == 2 and rep["ok"] is True

    _run(scenario())


def test_numbers_that_do_not_follow_the_pattern_are_listed_apart():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2)
        bad = await _put("INV/17")
        rep = await numbering_report(FIRM)
        assert rep["unparseable"] == [{"number": "INV/17", "id": str(bad)}]
        assert rep["checked"] == 3 and rep["problems"]["unparseable"] == 1 and rep["ok"] is False
        assert [s["series"] for s in rep["series"]] == ["ΤΠΥ-2026"]

    _run(scenario())


def test_issued_documents_without_any_number_are_reported():
    from invoice_services import numbering_report

    async def scenario():
        await _series(FIRM, 2)
        await _put("")
        await _put(None, status="paid")
        await db.invoices.insert_one({"firm_id": FIRM, "status": "cancelled"})
        await _put("", status="draft")                      # τα πρόχειρα χωρίς αριθμό είναι φυσιολογικά
        rep = await numbering_report(FIRM)
        assert rep["problems"]["unnumbered"] == 3 and rep["ok"] is False and rep["checked"] == 2

    _run(scenario())


def test_the_report_says_whether_the_database_protects_the_series():
    from invoice_services import numbering_report
    from server import ensure_invoice_number_index

    async def scenario():
        await _series(FIRM, 1)
        assert (await numbering_report(FIRM))["unique_index"] is False
        await ensure_invoice_number_index()
        assert (await numbering_report(FIRM))["unique_index"] is True

    _run(scenario())


def test_a_long_list_of_gaps_is_cut_but_counted():
    from invoice_services import numbering_report

    async def scenario():
        await _put("ΤΠΥ-2026-0001", when="2026-03-01")
        await _put("ΤΠΥ-2026-0200", when="2026-03-02")
        s = (await numbering_report(FIRM))["series"][0]
        assert s["gaps_total"] == 198 and len(s["gaps"]) == 50 and s["gaps"][0] == 2

    _run(scenario())


# ------------------------------------------------------------------ endpoint ----

def test_numbering_check_endpoint_returns_the_report_of_the_users_firm(client, office):
    for _ in range(2):
        inv = client.post("/api/invoices", json={
            "client_id": office.alpha, "date": athens_today().isoformat(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()
        assert client.post(f"/api/invoices/{inv['id']}/issue").status_code == 200
    _run(_put("ΤΠΥ-2026-0001", firm=OTHER_FIRM))

    r = client.get("/api/invoices/numbering-check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] == 2 and body["ok"] is True and body["unique_index"] is True
    assert [x["series"] for x in body["series"]] == [f"ΤΠΥ-{athens_today().year}"]

    year = athens_today().year
    assert client.get(f"/api/invoices/numbering-check?year={year}").json()["checked"] == 2
    assert client.get(f"/api/invoices/numbering-check?year={year - 1}").json()["checked"] == 0


def test_numbering_check_endpoint_requires_a_login():
    import server
    with TestClient(server.app, base_url="https://testserver") as anonymous:
        assert anonymous.get("/api/invoices/numbering-check").status_code in (401, 403)


# ------------------------------------------------------------- Autopilot job ----

def test_the_numbering_job_is_registered_and_listed_in_the_status(client, office):
    assert "numbering_check" in JOBS and "numbering_check" in JOB_FUNCS and JOB_LABELS["numbering_check"]
    r = client.get("/api/automation/status")
    assert r.status_code == 200, r.text
    assert "numbering_check" in r.json()["jobs"]


@pytest.mark.asyncio
async def test_the_numbering_job_is_silent_when_the_series_is_clean(protected):
    await _series(FIRM, 3)
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["status"] == "ok" and r["stats"]["ok"] is True
    assert await db.notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_a_gap_raises_one_card_and_the_same_gap_never_raises_another(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    for _ in range(3):
        r = (await run_firm(FIRM, ["numbering_check"]))[0]
        assert r["status"] == "ok" and r["stats"]["ok"] is False
    cards = await db.notifications.find({"firm_id": FIRM, "type": "numbering"}).to_list(10)
    assert len(cards) == 1
    card = cards[0]
    assert card["level"] == "warning" and card["requires_action"] is True and card.get("resolved_at") is None
    assert "ΤΠΥ-2026" in card["title"] + card["message"] and "0002" in card["message"]


@pytest.mark.asyncio
async def test_a_duplicate_is_critical():
    """Διπλότυπο υπάρχει μόνο όπου δεν λειτουργεί το index — γι' αυτό εδώ δεν δημιουργείται."""
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0001", status="paid", when="2026-03-01")
    await run_firm(FIRM, ["numbering_check"])
    card = await db.notifications.find_one({"firm_id": FIRM, "dedupe_key": {"$regex": "^numbering:dup:"}})
    assert card["level"] == "critical" and card["requires_action"] is True and "ΤΠΥ-2026-0001" in card["message"]


@pytest.mark.asyncio
async def test_a_changed_problem_opens_a_new_card_and_closes_the_old_one(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await run_firm(FIRM, ["numbering_check"])
    await _put("ΤΠΥ-2026-0006", when="2026-03-06")               # τώρα λείπουν και τα 4 και 5
    await run_firm(FIRM, ["numbering_check"])

    open_cards = await db.notifications.find({"firm_id": FIRM, "type": "numbering", "resolved_at": None}).to_list(10)
    closed = await db.notifications.find({"firm_id": FIRM, "type": "numbering", "resolved_at": {"$ne": None}}).to_list(10)
    assert len(open_cards) == 1 and len(closed) == 1
    assert "0004" in open_cards[0]["message"] and closed[0]["resolved_by"] == "autopilot"


@pytest.mark.asyncio
async def test_a_repaired_series_closes_its_cards(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await run_firm(FIRM, ["numbering_check"])
    await _put("ΤΠΥ-2026-0002", when="2026-03-02")
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["ok"] is True
    card = await db.notifications.find_one({"firm_id": FIRM, "type": "numbering"})
    assert card["resolved_at"] and card["resolved_by"] == "autopilot" and card["resolution"] == "auto"


@pytest.mark.asyncio
async def test_the_job_does_not_touch_cards_of_other_kinds_or_other_firms(protected):
    await _put("ΤΠΥ-2026-0001", when="2026-03-01")
    await _put("ΤΠΥ-2026-0003", when="2026-03-03")
    await db.notifications.insert_one({"firm_id": FIRM, "type": "backup_problem", "dedupe_key": "backup_problem",
                                       "level": "critical", "title": "x", "resolved_at": None})
    await db.notifications.insert_one({"firm_id": OTHER_FIRM, "type": "numbering", "dedupe_key": "numbering:other",
                                       "level": "warning", "title": "x", "resolved_at": None})
    await run_firm(FIRM, ["numbering_check"])
    await _put("ΤΠΥ-2026-0002", when="2026-03-02")
    await run_firm(FIRM, ["numbering_check"])
    assert (await db.notifications.find_one({"dedupe_key": "backup_problem"}))["resolved_at"] is None
    assert (await db.notifications.find_one({"dedupe_key": "numbering:other"}))["resolved_at"] is None


@pytest.mark.asyncio
async def test_a_missing_protection_is_reported_once_it_is_known_to_be_missing():
    """Χωρίς unique index (διπλότυπα που εμπόδισαν τη δημιουργία, ή παλιά MongoDB) ο λογιστής πρέπει να το μάθει."""
    from server import ensure_invoice_number_index
    await _series(FIRM, 2)
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["ok"] is False and r["stats"]["unique_index"] is False
    card = await db.notifications.find_one({"firm_id": FIRM, "type": "numbering"})
    assert card["level"] == "warning" and "index" in (card["title"] + card["message"]).lower()

    await ensure_invoice_number_index()
    r = (await run_firm(FIRM, ["numbering_check"]))[0]
    assert r["stats"]["ok"] is True
    assert (await db.notifications.find_one({"firm_id": FIRM, "type": "numbering"}))["resolved_at"]


# ===================================== 3. Η ημερομηνία ελέγχεται ΜΑΖΙ με τον αριθμό ====
#
# Ο έλεγχος «όχι προγενέστερη του προηγούμενου» γινόταν πριν από την παραχώρηση αριθμού. Δύο πρόχειρα με
# διαφορετική ημερομηνία που εκδίδονταν ταυτόχρονα περνούσαν και τα δύο τον έλεγχο, και ο νεότερος
# αριθμός μπορούσε να πάρει την παλαιότερη ημερομηνία.

def test_the_counter_remembers_the_latest_date_given_to_the_series(frozen_today):
    import invoice_services as svc

    async def scenario():
        await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-10")
        await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-12")
        assert (await _counter())["last_date"] == "2026-06-12"

    _run(scenario())


def test_an_earlier_date_is_refused_by_the_counter_itself_and_consumes_no_number(frozen_today):
    import invoice_services as svc
    from numbering import SeriesDateOutOfOrder

    async def scenario():
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-12") == "ΤΠΥ-2026-0001"
        with pytest.raises(SeriesDateOutOfOrder) as exc:
            await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-10")
        assert exc.value.last_date == "2026-06-12"
        c = await _counter()
        assert c["seq"] == 1 and c["last_date"] == "2026-06-12"          # ούτε αριθμός ούτε ημερομηνία άλλαξαν
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-12") == "ΤΠΥ-2026-0002"   # ίδια ημέρα: εντάξει

    _run(scenario())


def test_calls_without_a_date_keep_working_exactly_as_before():
    import invoice_services as svc

    async def scenario():
        assert await svc.next_invoice_number(FIRM) == f"ΤΠΥ-{athens_today().year}-0001"
        assert await svc.next_invoice_number(FIRM) == f"ΤΠΥ-{athens_today().year}-0002"
        assert "last_date" not in await _counter(year=athens_today().year)

    _run(scenario())


def test_a_counter_from_an_older_version_without_a_date_accepts_the_first_dated_number(frozen_today):
    import invoice_services as svc

    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 4})
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-01-02") == "ΤΠΥ-2026-0005"
        assert (await _counter())["last_date"] == "2026-01-02"

    _run(scenario())


def test_two_drafts_issued_together_cannot_get_numbers_in_the_wrong_date_order(monkeypatch, frozen_today):
    """Και τα δύο περνούν τον πρώτο έλεγχο (δεν υπάρχει ακόμη εκδοθέν)· ο μετρητής αρνείται το παλαιότερο."""
    import invoice_services as svc

    async def scenario():
        newer = await _draft_doc(when="2026-06-12")
        older = await _draft_doc(when="2026-06-10")
        real_prev = svc._previous_in_series

        async def slow_previous(*a, **k):
            result = await real_prev(*a, **k)
            await asyncio.sleep(0.02)                 # και τα δύο ελέγχουν πριν προλάβει κανένα να πάρει αριθμό
            return result

        monkeypatch.setattr(svc, "_previous_in_series", slow_previous)
        results = await asyncio.gather(
            svc.issue_draft(FIRM, await db.invoices.find_one({"_id": newer}), {}),
            svc.issue_draft(FIRM, await db.invoices.find_one({"_id": older}), {}),
            return_exceptions=True)
        monkeypatch.undo()

        assert isinstance(results[0], dict) and results[0]["number"] == "ΤΠΥ-2026-0001"
        assert isinstance(results[1], svc.InvoiceWorkflowError) and "χρονολογική" in str(results[1])
        loser = await db.invoices.find_one({"_id": older})
        assert loser["status"] == "draft" and not loser.get("number") and "issue_claim" not in loser
        assert (await _counter())["seq"] == 1

        # διορθώνοντας την ημερομηνία, το ίδιο πρόχειρο εκδίδεται και παίρνει το 0002
        await db.invoices.update_one({"_id": older}, {"$set": {"date": "2026-06-12"}})
        ok = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": older}), {})
        assert ok["number"] == "ΤΠΥ-2026-0002"

    _run(scenario())


def test_a_failed_issue_does_not_leave_a_date_that_blocks_earlier_drafts(monkeypatch, frozen_today):
    """Αποτυχημένη έκδοση με ημερομηνία 15/06 δεν πρέπει να κάνει το 12/06 «προγενέστερο» ενός παραστατικού που δεν υπάρχει."""
    import invoice_services as svc

    async def scenario():
        first = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-05")}), {})
        assert first["number"] == "ΤΠΥ-2026-0001"

        failing = await _draft_doc(when="2026-06-15")

        def boom(*a, **k):
            raise RuntimeError("σφάλμα μετά την παραχώρηση αριθμού")

        monkeypatch.setattr(svc, "withholding_for", boom)
        with pytest.raises(RuntimeError):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": failing}), {})
        monkeypatch.undo()
        assert (await _counter())["seq"] == 1                      # ο αριθμός επέστρεψε

        later = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-12")}), {})
        assert later["number"] == "ΤΠΥ-2026-0002"
        assert (await _counter())["last_date"] == "2026-06-12"

    _run(scenario())


def test_a_real_later_date_is_still_refused_after_the_counter_is_rechecked(frozen_today):
    import invoice_services as svc

    async def scenario():
        await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-12")}), {})
        early = await _draft_doc(when="2026-06-10")
        with pytest.raises(svc.InvoiceWorkflowError, match="χρονολογική"):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": early}), {})
        assert (await _counter())["last_date"] == "2026-06-12" and (await _counter())["seq"] == 1

    _run(scenario())


def test_a_future_dated_legacy_invoice_does_not_make_the_counter_refuse_the_series(frozen_today):
    """Το παλιό παραστατικό με μελλοντική ημερομηνία δεν γράφεται ποτέ στον μετρητή· η σειρά συνεχίζει."""
    import invoice_services as svc

    async def scenario():
        await _put("ΤΠΥ-2026-0005", when="2026-12-31", firm=FIRM)
        ok = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-15")}), {})
        assert ok["number"] == "ΤΠΥ-2026-0006"

    _run(scenario())


def test_each_series_has_its_own_date(frozen_today):
    import invoice_services as svc

    async def scenario():
        await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, date_iso="2026-06-12")
        assert await svc.next_invoice_number(FIRM, "ΑΠΥ", 2026, date_iso="2026-06-01") == "ΑΠΥ-2026-0001"
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2025, date_iso="2025-06-01") == "ΤΠΥ-2025-0001"
        assert await svc.next_invoice_number(OTHER_FIRM, "ΤΠΥ", 2026, date_iso="2026-06-01") == "ΤΠΥ-2026-0001"

    _run(scenario())


# ============================== 4. Ο μετρητής δεν μένει ποτέ πίσω από υπάρχοντες αριθμούς ====

def test_a_counter_left_behind_is_moved_above_the_existing_numbers(frozen_today):
    """Επαναφορά αντιγράφου, χειροκίνητη διόρθωση, εισαγωγή: το επόμενο παραστατικό παίρνει τον επόμενο ελεύθερο αριθμό."""
    import invoice_services as svc

    async def scenario():
        await _series(FIRM, 5)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})
        ok = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-15")}), {})
        assert ok["number"] == "ΤΠΥ-2026-0006"
        assert (await _counter())["seq"] == 6

    _run(scenario())


def test_the_floor_is_enforced_by_the_allocator_itself(frozen_today):
    import invoice_services as svc

    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, floor=9) == "ΤΠΥ-2026-0010"
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, floor=9) == "ΤΠΥ-2026-0011"
        assert await svc.next_invoice_number(FIRM, "ΤΠΥ", 2026) == "ΤΠΥ-2026-0012"

    _run(scenario())


def test_two_requests_racing_over_a_counter_left_behind_get_different_numbers(monkeypatch, frozen_today):
    """Η μετακίνηση του μετρητή είναι υπό όρο: ο δεύτερος που τη βρίσκει ήδη γραμμένη δεν ξαναγράφει την ίδια
    τιμή αλλά παίρνει νέο αριθμό. Κάθε βήμα του μετρητή δίνει τη σειρά στα υπόλοιπα αιτήματα (στην πράξη
    ταυτόχρονα αιτήματα αλληλοδιαπλέκονται έτσι)."""
    import invoice_services as svc
    import numbering

    async def scenario():
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})

        async def interleaved(real, *a, **k):
            await asyncio.sleep(0)
            return await real(*a, **k)

        monkeypatch.setattr(numbering, "db", _HookedDb(db, "counters", {"find_one_and_update": interleaved}))
        out = await asyncio.gather(*[svc.next_invoice_number(FIRM, "ΤΠΥ", 2026, floor=9) for _ in range(4)])
        monkeypatch.setattr(numbering, "db", db)
        assert sorted(out) == ["ΤΠΥ-2026-0010", "ΤΠΥ-2026-0011", "ΤΠΥ-2026-0012", "ΤΠΥ-2026-0013"]
        assert (await _counter())["seq"] == 13

    _run(scenario())


def test_the_database_refuses_a_taken_number_and_the_issue_moves_on(monkeypatch, frozen_today):
    """Δεύτερη γραμμή άμυνας: ακόμη κι αν ο πρώτος έλεγχος δεν είδε τον υπάρχοντα αριθμό, η βάση τον αρνείται
    και η έκδοση συνεχίζει στον επόμενο ελεύθερο — χωρίς διπλότυπο και χωρίς κενό."""
    import invoice_services as svc
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _series(FIRM, 5)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})

        real_prev = svc._previous_in_series
        calls = {"n": 0}

        async def blind_once(*a, **k):
            calls["n"] += 1
            return None if calls["n"] == 1 else await real_prev(*a, **k)   # ο πρώτος έλεγχος «δεν είδε» τίποτα

        monkeypatch.setattr(svc, "_previous_in_series", blind_once)
        draft = await _draft_doc(when="2026-06-15")
        ok = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.undo()

        assert ok["number"] == "ΤΠΥ-2026-0006"
        numbers = sorted([d["number"] async for d in db.invoices.find({"firm_id": FIRM, "status": {"$ne": "draft"}})])
        assert numbers == [f"ΤΠΥ-2026-{n:04d}" for n in range(1, 7)]
        stored = await db.invoices.find_one({"_id": draft})
        assert "issue_claim" not in stored and stored["status"] == "issued"

    _run(scenario())


def test_when_no_free_number_can_be_found_the_draft_stays_a_draft(monkeypatch, frozen_today):
    import invoice_services as svc

    async def scenario():
        draft = await _draft_doc(when="2026-06-15")
        calls = {"n": 0}

        async def taken(real, query, update, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:                     # 1η κλήση: το κλείδωμα της έκδοσης
                return await real(query, update, *args, **kwargs)
            raise DuplicateKeyError("E11000 duplicate key error")

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": taken}))
        with pytest.raises(svc.InvoiceWorkflowError, match="υπάρχει ήδη"):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        monkeypatch.setattr(svc, "db", db)

        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and "issue_claim" not in stored and not stored.get("number")
        assert calls["n"] == 1 + svc.NUMBER_ATTEMPTS

    _run(scenario())


def test_an_old_numbered_draft_whose_number_was_taken_meanwhile_is_refused_clearly(frozen_today):
    import invoice_services as svc
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _put("ΤΠΥ-2026-0003", when="2026-03-01")
        draft = await _draft_doc(when="2026-06-15", number="ΤΠΥ-2026-0003")
        with pytest.raises(svc.InvoiceWorkflowError, match="ΤΠΥ-2026-0003.*υπάρχει ήδη|υπάρχει ήδη.*ΤΠΥ-2026-0003"):
            await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": draft}), {})
        stored = await db.invoices.find_one({"_id": draft})
        assert stored["status"] == "draft" and stored["number"] == "ΤΠΥ-2026-0003" and "issue_claim" not in stored
        assert await db.counters.find_one({"_id": f"invoice-{FIRM}-2026"}) is None     # δεν παραχωρήθηκε αριθμός

    _run(scenario())


def test_a_taken_number_is_never_handed_back_to_the_series(monkeypatch, frozen_today):
    """Ο αριθμός που η βάση αρνήθηκε ανήκει σε άλλο παραστατικό — δεν επιστρέφεται στον μετρητή."""
    import invoice_services as svc
    from server import ensure_invoice_number_index

    async def scenario():
        await ensure_invoice_number_index()
        await _series(FIRM, 3)
        await db.counters.insert_one({"_id": f"invoice-{FIRM}-2026", "seq": 2})
        real_prev = svc._previous_in_series
        calls = {"n": 0}

        async def blind_once(*a, **k):
            calls["n"] += 1
            return None if calls["n"] == 1 else await real_prev(*a, **k)

        monkeypatch.setattr(svc, "_previous_in_series", blind_once)
        ok = await svc.issue_draft(FIRM, await db.invoices.find_one({"_id": await _draft_doc(when="2026-06-15")}), {})
        monkeypatch.undo()
        assert ok["number"] == "ΤΠΥ-2026-0004" and (await _counter())["seq"] == 4

    _run(scenario())


# ----------------------------------------------------------------- επαναφορά ----

def test_restoring_a_backup_with_a_conflicting_number_skips_that_document_and_says_so(client, office):
    """Η επαναφορά δεν σταματά στη μέση με HTTP 500 όταν ένα έγγραφο συγκρούεται με το unique index."""
    import io
    import json
    import zipfile

    existing = _run(_put("ΤΠΥ-2026-0001", firm=office.firm_id))
    clash_id = ObjectId()
    ok_id = ObjectId()
    docs = [
        {"_id": {"__oid__": str(clash_id)}, "firm_id": office.firm_id, "number": "ΤΠΥ-2026-0001", "status": "issued",
         "date": "2026-03-01"},
        {"_id": {"__oid__": str(ok_id)}, "firm_id": office.firm_id, "number": "ΤΠΥ-2026-0002", "status": "issued",
         "date": "2026-03-02"},
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_meta.json", json.dumps({"app": "nomos-one"}))
        zf.writestr("invoices.json", json.dumps(docs))
    buf.seek(0)
    r = client.post("/api/backup/import", files={"file": ("backup.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"]["invoices"] == 1
    assert any("ΤΠΥ-2026-0001" in e for e in body["errors"])
    assert _run(db.invoices.find_one({"_id": ok_id})) is not None
    assert _run(db.invoices.find_one({"_id": clash_id})) is None
    assert _run(db.invoices.find_one({"_id": existing})) is not None
