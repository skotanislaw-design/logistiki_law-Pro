"""Γύρος 5 (ανασκόπηση 4) — WP13: η ώρα Ελλάδας σε ΚΑΘΕ σημείο που βρίσκει «σήμερα» ή διαβάζει ημερομηνία από σφραγίδα.

Ο πρώτος έλεγχος (`test_unit_athens_clock_r5.py`) κάλυψε τα σημεία που άλλαξαν από `date.today()` σε `athens_today()`. Η
ανασκόπηση βρήκε ότι:

* ένα μέρος των σημείων που είχαν ήδη αλλάξει δεν είχε κανέναν έλεγχο συμπεριφοράς — αν κάποιος τα γύριζε πίσω στο
  ρολόι του μηχανήματος, καμία δοκιμή δεν θα αποτύγχανε·
* δύο σημεία έμεναν λάθος: η υπενθύμιση τιμολογίου έκρινε το «πότε στάλθηκε η προηγούμενη» με την ημερομηνία UTC της
  σφραγίδας (μία μέρα νωρίτερα για ό,τι στάλθηκε 00:00–03:00 ώρα Ελλάδας) και η σύνδεση τράπεζας υπολόγιζε τη λήξη της
  συγκατάθεσης με `date.today()`· η ειδοποίηση «πρόχειρο περιμένει έκδοση» έδειχνε επίσης την ημερομηνία UTC·
* οι ίδιες οι δοκιμές διάβαζαν το ρολόι του μηχανήματος (`date.today()`) και θα αποτύγχαναν τυχαία στο CI (UTC) τις ώρες
  00:00–03:00 ώρα Ελλάδας.

Εδώ: (1) το `tax_utils.athens_date_of` για κάθε μορφή σφραγίδας και κάθε ζώνη μηχανήματος, (2) το μηχάνημα των tests είναι
πάντα σε ζώνη με άλλη ημερομηνία από την ελληνική (βλ. conftest), (3) κάθε σημείο με παγωμένο ρολόι 01:30 της 1/1/2027
Ελλάδας = 23:30 της 31/12/2026 UTC.
"""
import asyncio
import os
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from db import db

FIRM = "firm-clock-b"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ============================================================ 1. το μηχάνημα των tests ====

@pytest.fixture(params=["UTC", "Europe/Athens", "Pacific/Kiritimati", "Pacific/Pago_Pago", "America/Los_Angeles"])
def machine_zone(request):
    """Αλλάζει τη ζώνη ώρας του ΜΗΧΑΝΗΜΑΤΟΣ (όχι του ρολογιού): ό,τι εξαρτάται από αυτήν αλλάζει αποτέλεσμα."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = request.param
    time.tzset()
    try:
        yield request.param
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def test_the_test_machine_is_in_a_zone_whose_date_is_not_the_greek_one():
    """Η προστασία του conftest: αν χαθεί, κάθε ανάγνωση του τοπικού ρολογιού ξαναγίνεται αόρατη."""
    from tax_utils import athens_today
    assert date.today() != athens_today(), (
        f"Η ζώνη του μηχανήματος των tests ({os.environ.get('TZ')}) έχει την ίδια ημερομηνία με την Ελλάδα· "
        "το conftest πρέπει να τη βάζει σε ζώνη με άλλη ημερομηνία.")


# ============================================================ 2. athens_date_of ====

@pytest.mark.parametrize("stamp, expected", [
    ("2026-12-31T22:30:00+00:00", date(2027, 1, 1)),        # 00:30 στην Ελλάδα (χειμερινή ώρα, UTC+2)
    ("2026-12-31T22:00:00+00:00", date(2027, 1, 1)),        # ακριβώς τα μεσάνυχτα Ελλάδας
    ("2026-12-31T21:59:59+00:00", date(2026, 12, 31)),      # ένα δευτερόλεπτο πριν
    ("2026-06-30T21:00:00+00:00", date(2026, 7, 1)),        # καλοκαίρι: UTC+3, τα μεσάνυχτα Ελλάδας είναι 21:00 UTC
    ("2026-06-30T20:59:59+00:00", date(2026, 6, 30)),
    ("2026-12-31T22:30:00Z", date(2027, 1, 1)),
    ("2026-12-31T22:30:00", date(2027, 1, 1)),              # χωρίς ζώνη = UTC (όχι η ζώνη του μηχανήματος)
    ("2027-01-01T00:30:00+02:00", date(2027, 1, 1)),        # ήδη ώρα Ελλάδας
    ("2026-12-31T23:30:00-05:00", date(2027, 1, 1)),        # 04:30 UTC της 1/1 → 06:30 Ελλάδας
    ("2026-12-31", date(2026, 12, 31)),                     # ημερομηνία χωρίς ώρα: μένει ίδια
    ("  2026-12-31T22:30:00+00:00  ", date(2027, 1, 1)),
])
def test_athens_date_of_converts_a_stamp_to_the_greek_date_on_any_machine(machine_zone, stamp, expected):
    from tax_utils import athens_date_of
    assert athens_date_of(stamp) == expected


@pytest.mark.parametrize("junk", [None, "", "   ", "δεν-είναι-ημερομηνία", "2026-13-45T00:00:00", 20261231, 1.5, b"2026-12-31",
                                  datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc), [], {}])
def test_athens_date_of_returns_none_for_anything_that_is_not_a_readable_stamp(junk):
    """Μόνο κείμενο ISO. Ό,τι άλλο (None, αριθμός, bytes, ήδη datetime) δεν υποθέτουμε τι σημαίνει."""
    from tax_utils import athens_date_of
    assert athens_date_of(junk) is None


@pytest.mark.parametrize("stamp", [
    "9999-12-31T23:59:59+00:00",        # σε ώρα Ελλάδας είναι ήδη το έτος 10000
    "9999-12-31T23:59:59Z",
    "9999-12-31T23:59:59",              # χωρίς ζώνη = UTC
    "0001-01-01T00:00:00+05:00",        # σε UTC είναι ήδη το έτος 0
    "0001-01-01T00:00:00+14:00",
])
def test_athens_date_of_returns_none_for_a_stamp_the_calendar_cannot_hold_instead_of_raising(stamp):
    """Έγκυρη μορφή, αλλά η μετατροπή ζώνης βγάζει εκτός εύρους της `datetime` (OverflowError, όχι ValueError). Η συνάρτηση
    υπόσχεται «None αν το κείμενο δεν διαβάζεται»· μια σφραγίδα από εισαγωγή δεδομένων δεν πρέπει να ρίχνει τον κύκλο του Autopilot."""
    from tax_utils import athens_date_of
    assert athens_date_of(stamp) is None


@pytest.mark.parametrize("stamp, expected", [
    ("9999-12-31", date(9999, 12, 31)),                 # ημερομηνία χωρίς ώρα: μένει ίδια
    ("0001-01-01", date(1, 1, 1)),
    ("9999-12-31T00:00:00+14:00", date(9999, 12, 30)),  # στο άκρο, αλλά μέσα στο εύρος
])
def test_athens_date_of_reads_dates_at_the_edge_of_the_calendar(stamp, expected):
    from tax_utils import athens_date_of
    assert athens_date_of(stamp) == expected


# ============================================================ 3. υπενθύμιση: «πότε στάλθηκε» ====

LAST_REMINDER_UTC = "2026-12-16T22:30:00+00:00"             # = 00:30 της 17/12 στην Ελλάδα · σήμερα (1/1) απέχει 15 ημέρες


def _reminded(sent_at, **over):
    return {"status": "issued", "date": "2026-10-01", "reminder_count": 1, "reminder_sent_at": sent_at, **over}


def test_a_reminder_sent_after_midnight_in_greece_counts_from_the_greek_day(athens_clock):
    import automation
    # Ημερομηνία UTC της σφραγίδας: 16/12 → 16 ημέρες· ελληνική: 17/12 → 15 ημέρες.
    assert automation.reminder_due(_reminded(LAST_REMINDER_UTC), {"reminder_interval_days": 15}) is True
    assert automation.reminder_due(_reminded(LAST_REMINDER_UTC), {"reminder_interval_days": 16}) is False


def test_a_stamp_without_a_zone_is_utc_whatever_the_machine_zone(athens_clock, machine_zone):
    import automation
    naive = LAST_REMINDER_UTC[:-6]                          # ίδια στιγμή, γραμμένη χωρίς ζώνη
    assert automation.reminder_due(_reminded(naive), {"reminder_interval_days": 16}) is False
    assert automation.reminder_due(_reminded(naive), {"reminder_interval_days": 15}) is True


@pytest.mark.parametrize("junk", ["σκουπίδι", "2026-99-99", 12345])
def test_an_unreadable_reminder_stamp_means_the_reminder_is_due(athens_clock, junk):
    import automation
    assert automation.reminder_due(_reminded(junk), {"reminder_interval_days": 15}) is True


# ============================================================ 4. ημερήσιο / εβδομαδιαίο email ====

# 23:30 UTC της Πέμπτης 31/12/2026 = 01:30 της Παρασκευής 1/1/2027 στην Ελλάδα
NOW_UTC = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
MONDAY_UTC = datetime(2027, 1, 3, 22, 30, tzinfo=timezone.utc)                 # = Δευτέρα 4/1/2027 00:30 στην Ελλάδα


def test_daily_digest_is_judged_by_the_greek_day(machine_zone):
    from automation import digest_due
    settings = {"digest_frequency": "daily", "digest_hour": 0}
    assert digest_due(settings, "2026-12-31T22:30:00+00:00", NOW_UTC) is False     # 00:30 της 1/1: ήδη στάλθηκε σήμερα
    assert digest_due(settings, "2026-12-31T21:30:00+00:00", NOW_UTC) is True      # 23:30 της 31/12: χθεσινό
    assert digest_due(settings, "2026-12-31T22:30:00", NOW_UTC) is False           # χωρίς ζώνη = UTC
    assert digest_due(settings, None, NOW_UTC) is True
    assert digest_due(settings, "σκουπίδι", NOW_UTC) is True                       # δυσανάγνωστο = ποτέ δεν στάλθηκε


def test_weekly_digest_counts_six_greek_days(machine_zone):
    from automation import digest_due
    settings = {"digest_frequency": "weekly", "digest_hour": 0}
    # Δευτέρα 4/1 (ώρα Ελλάδας). Στάλθηκε Τετάρτη 30/12 00:30 Ελλάδας (= 29/12 22:30 UTC): 5 ημέρες → όχι ακόμη.
    assert digest_due(settings, "2026-12-29T22:30:00+00:00", MONDAY_UTC) is False
    assert digest_due(settings, "2026-12-29T22:30:00", MONDAY_UTC) is False
    # Στάλθηκε Τρίτη 29/12 00:30 Ελλάδας (= 28/12 22:30 UTC): 6 ημέρες → ναι.
    assert digest_due(settings, "2026-12-28T22:30:00+00:00", MONDAY_UTC) is True
    # Την Τρίτη 5/1 δεν στέλνεται ποτέ.
    assert digest_due(settings, None, MONDAY_UTC + timedelta(days=1)) is False


# ============================================================ 5. ειδοποίηση πρόχειρου ====

def test_the_stale_draft_notice_shows_the_greek_creation_date(machine_zone):
    import automation
    created = (datetime.now(timezone.utc) - timedelta(days=10)).replace(hour=22, minute=30, second=0, microsecond=0)
    greek_day = (created + timedelta(days=1)).strftime("%d/%m/%Y")                 # 22:30 UTC = 00:30/01:30 την επόμενη
    utc_day = created.strftime("%d/%m/%Y")

    async def scenario():
        await db.invoices.insert_one({"firm_id": FIRM, "status": "draft", "number": "", "client_name": "ΑΛΦΑ ΑΕ",
                                      "total": 124.0, "created_at": created.isoformat()})
        await db.invoices.insert_one({"firm_id": FIRM, "status": "draft", "number": "", "client_name": "ΒΗΤΑ ΑΕ",
                                      "total": 62.0, "created_at": "0000-δυσανάγνωστη-σφραγίδα"})
        assert await automation.job_stale_drafts(FIRM, {"stale_draft_days": 7}) == {"flagged": 2}
        return {n["message"].split(" — ")[0]: n["message"] async for n in db.notifications.find({"firm_id": FIRM})}

    messages = _run(scenario())
    assert f"δημιουργήθηκε {greek_day}" in messages["ΑΛΦΑ ΑΕ"] and utc_day not in messages["ΑΛΦΑ ΑΕ"]
    assert messages["ΒΗΤΑ ΑΕ"].endswith("δημιουργήθηκε —")


# ============================================================ 6. Autopilot με παγωμένο ρολόι ====

def test_deadline_alerts_count_days_from_the_greek_date(athens_clock):
    import automation

    async def scenario():
        # Οι προθεσμίες ΦΠΑ και ΕΦΚΑ είναι 29/1/2027: 28 ημέρες από την 1/1 (Ελλάδα), 29 από τις 31/12 (UTC).
        assert await automation.job_deadlines(FIRM, {"deadline_alert_days": [28], "vat_period": "monthly"}) == {"raised": 2}
        keys = sorted([n["dedupe_key"] async for n in db.notifications.find({"firm_id": FIRM})])
        assert keys == ["deadline:efka:2027-01-29:28", "deadline:vat:2027-01-29:28"]
        assert await automation.job_deadlines(FIRM, {"deadline_alert_days": [29], "vat_period": "monthly"}) == {"raised": 0}

    _run(scenario())


def test_vat_periods_closed_so_far_follow_the_greek_calendar(athens_clock):
    import automation

    async def scenario():
        await automation.job_vat_periods(FIRM, {"vat_period": "monthly"})
        return sorted([vp["period"] async for vp in db.vat_periods.find({"firm_id": FIRM})])

    # Με ημερομηνία UTC (31/12) η περίοδος 12/2026 δεν θα είχε κλείσει ακόμη.
    assert _run(scenario()) == ["2026-10", "2026-11", "2026-12"]


def test_reminder_job_uses_the_greek_date(athens_clock, monkeypatch):
    import automation
    monkeypatch.setattr(automation, "smtp_ready", lambda settings: True)

    async def scenario():
        client_id = str((await db.clients.insert_one({"firm_id": FIRM, "name": "ΑΛΦΑ ΑΕ", "email": "a@example.gr"})).inserted_id)
        await db.invoices.insert_one({"firm_id": FIRM, "status": "issued", "number": "ΤΠΥ-2026-0001", "client_id": client_id,
                                      "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-12-28", "total": 124.0, "reminder_count": 0})
        # 4 ημέρες από 28/12 έως 1/1 (Ελλάδα)· 3 αν «σήμερα» ήταν 31/12 (UTC).
        return await automation.job_reminders(FIRM, {"reminder_after_days": 4})

    assert _run(scenario()) == {"queued": 1, "not_due": 0}


def test_a_sent_reminder_returns_exactly_the_stamp_it_stored(athens_clock, monkeypatch):
    """Η σφραγίδα που γράφεται στο τιμολόγιο και αυτή που επιστρέφεται στον καλούντα είναι η ίδια στιγμή· και είναι πλήρης
    σφραγίδα (όχι κομμένη ημερομηνία), ώστε το `reminder_due` να την ξαναδιαβάζει σωστά."""
    import invoice_services as svc
    import pdf_utils
    from tax_utils import athens_date_of

    async def fake_send(*args, **kwargs):
        return None

    async def no_link(*args, **kwargs):
        return ""

    monkeypatch.setattr(svc, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(svc, "build_payment_link", no_link)
    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", lambda *a, **k: b"%PDF-fake")
    settings = {"firm_id": FIRM, "company_name": "Γραφείο", "smtp_user": "office@example.gr", "smtp_password": "x"}

    async def scenario():
        client = await db.clients.insert_one({"firm_id": FIRM, "name": "ΑΛΦΑ ΑΕ", "email": "a@example.gr"})
        invoice = await db.invoices.insert_one({
            "firm_id": FIRM, "status": "issued", "number": "ΤΠΥ-2026-0001", "client_id": str(client.inserted_id),
            "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-10-01", "total": 124.0, "subtotal": 100.0, "vat_amount": 24.0,
            "reminder_count": 0, "line_items": []})
        returned = await svc.send_reminder(FIRM, await db.invoices.find_one({"_id": invoice.inserted_id}), settings)
        return returned, await db.invoices.find_one({"_id": invoice.inserted_id})

    returned, stored = _run(scenario())
    assert returned["reminder_sent_at"] == stored["reminder_sent_at"] and stored["reminder_count"] == 1
    assert len(stored["reminder_sent_at"]) > 10 and athens_date_of(stored["reminder_sent_at"]) is not None


def test_bank_sync_window_and_watermark_use_the_greek_date(athens_clock, monkeypatch):
    import automation
    import openbanking
    calls = []

    async def fake_fetch(settings, account_id, date_from=None):
        calls.append(date_from)
        return []

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    settings = {"firm_id": FIRM, "company_name": "Γραφείο", "autopilot_enabled": True, "openbanking_enabled": True,
                "gocardless_secret_id": "sid", "gocardless_secret_key": "skey", "gocardless_account_ids": ["acc-1"]}
    _run(db.settings.insert_one(dict(settings)))

    assert _run(automation.run_firm(FIRM, ["bank_sync"]))[0]["status"] != "error"
    assert calls == ["2026-10-03"]                          # πρώτος συγχρονισμός: 90 ημέρες πριν την 1/1/2027
    assert _run(db.settings.find_one({"firm_id": FIRM}))["bank_sync_last_date"] == "2027-01-01"

    assert _run(automation.run_firm(FIRM, ["bank_sync"]))[0]["status"] != "error"
    assert calls[1] == "2026-12-27"                         # επικάλυψη 5 ημερών από τη σημερινή ημερομηνία Ελλάδας


def test_an_inbox_receipt_without_a_date_gets_the_greek_date(athens_clock, monkeypatch):
    import automation
    attachment = {"message_id": "m-1", "subject": "Απόδειξη", "sender": "shop@example.gr", "filename": "a.jpg",
                  "content_type": "image/jpeg", "data": b"not-really-an-image"}
    monkeypatch.setattr(automation, "_fetch_unseen_attachments", lambda settings: [attachment])
    settings = {"imap_enabled": True, "imap_user": "u@example.gr", "imap_password": "p"}

    assert _run(automation.job_inbox_receipts(FIRM, settings))["created"] == 1
    expense = _run(db.expenses.find_one({"firm_id": FIRM}))
    assert expense["date"] == "2027-01-01" and expense["review_status"] == "needs_review"


def test_the_next_invoice_number_starts_the_greek_year(athens_clock):
    import invoice_services
    assert _run(invoice_services.next_invoice_number(FIRM)) == "ΤΠΥ-2027-0001"


def test_a_credit_note_is_dated_on_the_greek_day(athens_clock):
    import invoice_services as svc

    async def scenario():
        original = await db.invoices.insert_one({
            "firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": "c-1", "client_name": "ΑΛΦΑ ΑΕ", "status": "paid",
            "mydata_status": "synced", "mydata_mark": "400001", "total": 124.0,
            "line_items": [{"description": "Συμβουλευτική", "quantity": 1, "unit_price": 100, "vat_rate": 24}]})
        doc = await db.invoices.find_one({"_id": original.inserted_id})
        return await svc.build_credit_note(FIRM, doc, "user@example.gr", reason="Λάθος χρέωση")

    assert _run(scenario())["date"] == "2027-01-01"


def test_the_document_date_helper_falls_back_to_the_greek_day(athens_clock):
    import pdf_utils
    assert pdf_utils._document_date(None) == date(2027, 1, 1)
    assert pdf_utils._document_date("σκουπίδι") == date(2027, 1, 1)
    assert pdf_utils._document_date("2026-12-31T22:30:00+00:00") == date(2027, 1, 1)
    assert pdf_utils._document_date("2026-12-31T21:30:00+00:00") == date(2026, 12, 31)


# ============================================================ 7. μέσα από το API ====

@pytest.fixture()
def office(athens_clock):
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "clockb@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        user = _run(db.users.find_one({"email": "clockb@example.gr"}))
        _run(db.users.update_one({"email": "clockb@example.gr"}, {"$set": {"role": "admin"}}))
        alpha = c.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                             "email": "alpha@example.gr"}).json()["id"]
        yield SimpleNamespace(api=c, alpha=alpha, firm_id=user["firm_id"],
                              user={"email": "clockb@example.gr", "firm_id": user["firm_id"], "role": "admin"})


def test_case_grammatio_and_paravolo_numbers_start_the_greek_year(office):
    api = office.api
    case = api.post("/api/cases", json={"title": "Υπόθεση", "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ"}).json()
    grammatio = api.post("/api/grammatia", json={
        "court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": office.alpha,
        "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0, "vat_amount": 24.0}).json()
    paravolo = api.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": 10.0}).json()
    assert case["number"] == "ΥΠΘ-2027-0001"
    assert grammatio["number"] == "ΓΡΠ-2027-0001"
    assert paravolo["number"] == "ΠΡΒ-2027-0001"


def test_an_invoice_made_from_a_grammatio_carries_the_greek_date(office):
    api = office.api
    grammatio = api.post("/api/grammatia", json={
        "court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": office.alpha,
        "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0, "vat_amount": 24.0}).json()
    assert api.post(f"/api/grammatia/{grammatio['id']}/issue", json={"portal_ref": "OLO-1"}).status_code == 200
    invoice = api.post(f"/api/grammatia/{grammatio['id']}/to-invoice")
    assert invoice.status_code == 200, invoice.text
    assert invoice.json()["date"] == "2027-01-01"


def test_a_draft_dated_today_in_greece_can_be_issued_in_the_first_hours_of_the_day(office):
    """Με ημερομηνία UTC (31/12) το παραστατικό της 1/1 θα απορριπτόταν ως «μελλοντικό»."""
    api = office.api
    draft = api.post("/api/invoices", json={
        "client_id": office.alpha, "date": "2027-01-01",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()
    issued = api.post(f"/api/invoices/{draft['id']}/issue")
    assert issued.status_code == 200, issued.text
    assert issued.json()["number"] == "ΤΠΥ-2027-0001"
    tomorrow = api.post("/api/invoices", json={
        "client_id": office.alpha, "date": "2027-01-02",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()
    assert api.post(f"/api/invoices/{tomorrow['id']}/issue").status_code == 400


def test_profitability_covers_the_greek_year_to_date(office):
    api = office.api
    _run(db.invoices.insert_many([
        {"firm_id": office.firm_id, "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ", "number": "ΤΠΥ-2026-0001",
         "date": "2026-12-31", "subtotal": 500.0, "vat_amount": 120.0, "total": 620.0, "status": "issued", "line_items": [],
         "created_at": datetime.now(timezone.utc).isoformat()},
        {"firm_id": office.firm_id, "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ", "number": "ΤΠΥ-2027-0001",
         "date": "2027-01-01", "subtotal": 100.0, "vat_amount": 24.0, "total": 124.0, "status": "issued", "line_items": [],
         "created_at": datetime.now(timezone.utc).isoformat()},
    ]))
    body = api.get("/api/analytics/profitability").json()
    assert body["period"] == {"start": "2027-01-01", "end": "2027-01-01"}
    assert body["totals"]["revenue"] == 100.0               # μόνο η 1/1/2027· το 2026 δεν ανήκει στο τρέχον έτος


def test_the_assistant_context_starts_the_month_on_the_greek_date(office):
    import copilot
    _run(db.invoices.insert_many([
        {"firm_id": office.firm_id, "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ", "number": "ΤΠΥ-2026-0001",
         "date": "2026-12-20", "subtotal": 500.0, "vat_amount": 120.0, "total": 620.0, "status": "issued", "line_items": [],
         "created_at": datetime.now(timezone.utc).isoformat()},
        {"firm_id": office.firm_id, "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ", "number": "ΤΠΥ-2027-0001",
         "date": "2027-01-01", "subtotal": 100.0, "vat_amount": 24.0, "total": 124.0, "status": "issued", "line_items": [],
         "created_at": datetime.now(timezone.utc).isoformat()},
    ]))
    context = _run(copilot.build_context(office.user))
    assert context["σήμερα"] == "2027-01-01"
    assert context["τρέχων_μήνας"]["καθαρά_έσοδα"] == "100,00 €"           # όχι 600: ο Δεκέμβριος δεν είναι ο τρέχων μήνας
    assert context["επόμενη_προθεσμία_ΕΦΚΑ"] == "2027-01-29"


def test_an_invoice_drafted_by_the_assistant_carries_the_greek_date(office):
    import routes_copilot
    created, _reply = _run(routes_copilot._execute_create_invoice(
        {"client_name": "ΑΛΦΑ ΑΕ", "description": "Γνωμοδότηση", "amount": 100.0, "vat_rate": 24}, office.user))
    assert created.date == "2027-01-01"


def test_a_trust_entry_dated_today_in_greece_is_not_in_the_future(athens_clock):
    """Η κίνηση της 1/1 πρέπει να γίνεται δεκτή στις 01:30 της 1/1 (Ελλάδα), η της 2/1 όχι."""
    import routes_trust
    assert routes_trust._entry_date("2027-01-01") == "2027-01-01"
    with pytest.raises(HTTPException) as refused:
        routes_trust._entry_date("2027-01-02")
    assert refused.value.status_code == 400 and "μελλοντική" in refused.value.detail


def test_bank_consent_runs_ninety_days_from_the_greek_date(office, monkeypatch):
    import openbanking

    async def get_requisition(settings, requisition_id):
        return {"status": "LN", "accounts": ["acc-1"]}

    async def account_details(settings, account_id):
        return {"iban": "GR1601101250000000012300695", "name": "Λογαριασμός"}

    monkeypatch.setattr(openbanking, "get_requisition", get_requisition)
    monkeypatch.setattr(openbanking, "account_details", account_details)
    _run(db.settings.update_one({"firm_id": office.firm_id}, {"$set": {"gocardless_requisition_id": "req-1"}}, upsert=True))
    r = office.api.post("/api/bank/openbanking/complete")
    assert r.status_code == 200, r.text
    assert _run(db.settings.find_one({"firm_id": office.firm_id}))["gocardless_access_valid_until"] == "2027-04-01"


def test_the_backup_file_name_carries_the_greek_date_and_time(office, athens_clock, monkeypatch):
    """Η εξαγωγή 01:30 της 1/1/2027 (23:30 UTC της 31/12/2026) έδινε όνομα αρχείου με ημερομηνία UTC: «…-20261231-233000.zip»."""
    import routes_backup

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return athens_clock.astimezone(tz) if tz else athens_clock.astimezone(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(routes_backup, "datetime", Frozen)
    response = office.api.get("/api/backup/export")
    assert response.status_code == 200, response.text[:200]
    assert "nomos-one-backup-20270101-013000.zip" in response.headers["content-disposition"]
