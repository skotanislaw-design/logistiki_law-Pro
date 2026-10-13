"""Γύρος 5 (δεύτερος έλεγχος) — τι κάνει ένας Autopilot που ο γενικός διακόπτης του είναι κλειστός.

Πριν: με κλειστό διακόπτη το `run_firm` γύριζε αμέσως «skipped» και δεν έτρεχε τίποτα. Αυτό ήταν σωστό για ό,τι ενεργεί
εκ μέρους του γραφείου (αποστολές σε πελάτες και ΑΑΔΕ, υπενθυμίσεις, αυτόματες συμφωνίες), αλλά άφηνε δύο πράγματα να
χαλάσουν αθόρυβα στα γραφεία που είχαν κλείσει τον διακόπτη:

* Η εξόφληση με κάρτα. Το webhook του Stripe την καταγράφει ούτως ή άλλως (δεν ρωτά τον διακόπτη)· η ερώτηση προς το Stripe
  (`card_payments`) είναι ό,τι τη σώζει όταν το webhook λείπει ή αργεί. Με κλειστό διακόπτη ο πελάτης πλήρωνε, το Stripe
  είχε το ποσό και το τιμολόγιο έμενε «ανοιχτό» στα βιβλία.
* Ο έλεγχος αρίθμησης. Ένα κενό στην αρίθμηση των τιμολογίων δεν σχετίζεται με το αν θέλει κανείς αυτόματες αποστολές.

Απόφαση: οι δύο αυτές δουλειές (`ALWAYS_ON_JOBS`) τρέχουν πάντα. Δεν στέλνουν τίποτα σε τρίτους· καταγράφουν ή αναφέρουν κάτι
που έχει ήδη συμβεί. Όλες οι υπόλοιπες μένουν σταματημένες, και το κουμπί «Εκτέλεση τώρα» (`force`) τρέχει τα πάντα όπως πριν.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import automation
import invoice_services
from automation import ALWAYS_ON_JOBS, JOB_FUNCS, JOBS, run_firm
from db import db
from tax_utils import athens_today

FIRM = "firm-autopilot-off-r5"
SKIPPED = {"job": "*", "status": "skipped", "stats": {"reason": "autopilot_disabled"}}
SETTINGS = {
    "firm_id": FIRM, "company_name": "Γραφείο", "smtp_user": "office@example.gr", "smtp_password": "x",
    "email": "office@example.gr", "vat_period": "monthly", "auto_email_on_issue": True, "auto_reminders": True,
    "reminder_after_days": 30, "reminder_interval_days": 15, "reminder_max": 3, "auto_mydata_retry": True,
    "auto_reconcile_min_confidence": 90, "stale_draft_days": 7, "deadline_alert_days": [7, 1],
}


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


async def _firm(enabled: bool):
    await db.settings.insert_one({**SETTINGS, "autopilot_enabled": enabled})
    return str((await db.clients.insert_one({"firm_id": FIRM, "name": "Παπαδόπουλος", "email": "client@example.gr",
                                              "afm": "123456783"})).inserted_id)


async def _invoice(client_id, number, **over):
    doc = {"firm_id": FIRM, "number": number, "client_id": client_id, "client_name": "Παπαδόπουλος", "status": "issued",
           "total": 124.0, "subtotal": 100.0, "vat_amount": 24.0, "date": athens_today().isoformat(), "email_sent_at": None,
           "reminder_count": 0, "mydata_status": "pending", "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


@pytest.fixture()
def mail(monkeypatch):
    """Κάθε δρόμος αποστολής email καταγράφεται· κανένα δεν πρέπει να χρησιμοποιηθεί με κλειστό διακόπτη."""
    sent = []

    async def fake_send(settings, to, subject, text, html, pdf_bytes=None, pdf_filename=""):
        sent.append({"to": to, "subject": subject})

    async def no_link(*a, **k):
        return ""

    import pdf_utils
    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(automation, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(invoice_services, "build_payment_link", no_link)
    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", lambda *a, **k: b"%PDF-fake")
    return sent


class Answer:
    def __init__(self, payment_status="unpaid", status="open", payment_intent="pi_off"):
        self.payment_status, self.status, self.payment_intent = payment_status, status, payment_intent


@pytest.fixture()
def stripe_says(monkeypatch):
    import routes_payments
    answers, asked = {}, []

    def retrieve(session_id):
        asked.append(session_id)
        return answers.get(session_id, Answer())

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "retrieve", staticmethod(retrieve))
    return SimpleNamespace(answers=answers, asked=asked)


# ------------------------------------------------------------------------------- ποιες δουλειές ----

def test_the_always_on_jobs_are_exactly_the_two_that_send_nothing_to_anyone():
    assert ALWAYS_ON_JOBS == ("numbering_check", "card_payments")
    assert all(job in JOBS and job in JOB_FUNCS for job in ALWAYS_ON_JOBS)


@pytest.mark.asyncio
async def test_a_switched_off_autopilot_runs_the_two_checks_and_nothing_else():
    await _firm(False)

    results = await run_firm(FIRM)

    assert results[0] == SKIPPED
    assert [r["job"] for r in results[1:]] == ["numbering_check", "card_payments"]
    assert all(r["status"] == "ok" for r in results[1:]), results
    assert sorted(await db.automation_runs.distinct("job", {"firm_id": FIRM})) == ["card_payments", "numbering_check"]


@pytest.mark.asyncio
async def test_a_switched_off_autopilot_acts_for_the_office_in_no_way(mail):
    client_id = await _firm(False)
    unsent = await _invoice(client_id, "ΤΠΥ-2026-0001")                                    # θα στελνόταν email
    overdue = await _invoice(client_id, "ΤΠΥ-2026-0002", total=620.0, date=(athens_today() - timedelta(days=45)).isoformat(),
                             email_sent_at="2026-01-01T00:00:00+00:00", mydata_status="synced")      # θα έφευγε υπενθύμιση
    await _invoice(client_id, "ΤΠΥ-2026-0003", status="draft", date=(athens_today() - timedelta(days=45)).isoformat(),
                   created_at="2026-01-01T00:00:00+00:00")                                 # ξεχασμένο πρόχειρο
    tx = (await db.bank_transactions.insert_one({
        "firm_id": FIRM, "amount": 620.0, "date": athens_today().isoformat(), "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ",
        "match_status": "auto_matched", "match_confidence": 95, "match_ambiguous": False,
        "matched_invoice_id": str(overdue), "matched_invoice_number": "ΤΠΥ-2026-0002"})).inserted_id

    await run_firm(FIRM)
    await run_firm(FIRM, ["send_issued_invoices", "reminders", "reconcile", "stale_drafts", "vat_periods", "mydata_retry",
                          "deadlines", "outbox", "digest"])

    assert mail == []
    assert await db.outbox.count_documents({}) == 0
    assert (await db.invoices.find_one({"_id": unsent}))["email_sent_at"] is None
    assert (await db.invoices.find_one({"_id": overdue}))["status"] == "issued"
    assert (await db.bank_transactions.find_one({"_id": tx}))["match_status"] == "auto_matched"
    assert await db.vat_periods.count_documents({"firm_id": FIRM}) == 0
    assert await db.notifications.count_documents({"firm_id": FIRM, "type": {"$ne": "numbering"}}) == 0
    assert sorted(await db.automation_runs.distinct("job", {"firm_id": FIRM})) == ["card_payments", "numbering_check"]


@pytest.mark.asyncio
async def test_a_job_asked_for_by_name_is_skipped_too_unless_it_is_one_of_the_two():
    await _firm(False)

    assert await run_firm(FIRM, ["reminders"]) == [SKIPPED]
    only_card = await run_firm(FIRM, ["reminders", "card_payments"])
    assert [r["job"] for r in only_card] == ["*", "card_payments"]
    assert await db.automation_runs.count_documents({"firm_id": FIRM, "job": "reminders"}) == 0


@pytest.mark.asyncio
async def test_the_run_now_button_runs_everything_it_was_asked_to_even_when_switched_off(mail):
    await _firm(False)

    results = await run_firm(FIRM, ["reminders", "card_payments"], force=True)

    assert [r["job"] for r in results] == ["reminders", "card_payments"]
    assert all(r["status"] == "ok" for r in results), results


@pytest.mark.asyncio
async def test_a_switched_on_autopilot_runs_every_job_as_before(mail):
    await _firm(True)

    results = await run_firm(FIRM, ["reminders", "numbering_check", "card_payments"])

    assert SKIPPED not in results
    assert [r["job"] for r in results] == ["reminders", "numbering_check", "card_payments"]


# ------------------------------------------------------------------------------- τι συνεχίζει ----

@pytest.mark.asyncio
async def test_a_card_payment_the_client_made_is_still_recorded_when_the_autopilot_is_switched_off(stripe_says):
    client_id = await _firm(False)
    invoice = await _invoice(client_id, "ΤΠΥ-2026-0001")
    await db.payment_transactions.insert_one({
        "session_id": "cs_off", "firm_id": FIRM, "invoice_id": str(invoice), "amount": 124.0, "status": "initiated",
        "payment_status": "pending", "created_at": _ago(hours=1), "updated_at": _ago(hours=1)})
    stripe_says.answers["cs_off"] = Answer("paid", "complete", "pi_off")

    results = await run_firm(FIRM)

    assert stripe_says.asked == ["cs_off"]
    assert next(r for r in results if r["job"] == "card_payments")["stats"]["recovered"] == 1
    paid = await db.invoices.find_one({"_id": invoice})
    assert paid["status"] == "paid" and paid["paid_via"] == "stripe" and paid["paid_ref"] == "cs_off"


@pytest.mark.asyncio
async def test_a_numbering_gap_is_still_reported_when_the_autopilot_is_switched_off():
    from server import ensure_invoice_number_index
    await ensure_invoice_number_index()                                                     # όπως σε κανονική εκκίνηση
    client_id = await _firm(False)
    await _invoice(client_id, "ΤΠΥ-2026-0001", date="2026-03-01")
    await _invoice(client_id, "ΤΠΥ-2026-0003", date="2026-03-02")                          # λείπει το 0002

    results = await run_firm(FIRM)

    check = next(r for r in results if r["job"] == "numbering_check")
    assert check["stats"]["ok"] is False and check["stats"]["raised"] == 1
    card = await db.notifications.find_one({"firm_id": FIRM, "type": "numbering"})
    assert card and card["requires_action"] is True and card["dedupe_key"].startswith("numbering:gaps:")
    assert "0002" in card["message"]
