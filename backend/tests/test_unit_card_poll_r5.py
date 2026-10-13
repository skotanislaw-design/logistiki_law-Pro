"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C και D) — πληρωμές κάρτας: ποιος μαθαίνει ότι ένα τιμολόγιο πληρώθηκε.

Πριν: η εξόφληση καταγραφόταν μόνο (1) από το webhook του Stripe — που απαντά 503 όταν το `STRIPE_WEBHOOK_SECRET` είναι κενό —
ή (2) όταν ο ίδιος ο πελάτης γύριζε στη σελίδα επιτυχίας. Ο πελάτης που έκλεινε την καρτέλα μετά την πληρωμή άφηνε τα χρήματα
εισπραγμένα και το τιμολόγιο ανοιχτό: ο Autopilot συνέχιζε να στέλνει «Υπενθύμιση» με νέο σύνδεσμο πληρωμής. Το σχόλιο του κώδικα και
το μήνυμα εκκίνησης έλεγαν ότι «ένα polling ρωτά το Stripe» — δεν υπήρχε τέτοιο.

Επιπλέον: η λήξη του συνδέσμου πληρωμής της υπενθύμισης ζητούσε 7 ημέρες, ενώ το Stripe δέχεται το πολύ 24 ώρες· κάθε κλήση
απορριπτόταν, το σφάλμα καταπινόταν και η υπενθύμιση έφευγε χωρίς σύνδεσμο. Και οι κλήσεις στο Stripe γίνονταν πάνω στον βρόχο
γεγονότων: ένα Stripe που αργεί πάγωνε το API όλων των γραφείων.
"""
import asyncio
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient

from automation import (CARD_POLL_AFTER_MINUTES, CARD_POLL_EVERY_MINUTES, CARD_POLL_MAX_ERRORS, CARD_POLL_PER_RUN,
                        CARD_POLL_WINDOW_DAYS, run_firm)
from db import db

FIRM = "firm-card-poll-r5"
OTHER = "firm-card-poll-r5-other"


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


async def _invoice(firm=FIRM, **over):
    doc = {"firm_id": firm, "number": "ΤΠΥ-2026-0001", "status": "issued", "total": 124.0, **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _tx(session, invoice_id, firm=FIRM, **over):
    """Συναλλαγή που περιμένει: η συνεδρία άνοιξε πριν από μία ώρα και το webhook δεν ήρθε ποτέ."""
    doc = {"session_id": session, "firm_id": firm, "invoice_id": str(invoice_id), "amount": 124.0, "status": "initiated",
           "payment_status": "pending", "created_at": _ago(hours=1), "updated_at": _ago(hours=1), **over}
    await db.payment_transactions.insert_one(doc)


async def _tx_doc(session):
    return await db.payment_transactions.find_one({"session_id": session})


async def _invoice_doc(invoice_id):
    return await db.invoices.find_one({"_id": invoice_id})


class Answer:
    """Όπως η συνεδρία που επιστρέφει το Stripe: ανοιχτή και ανεξόφλητη, εκτός αν ορίσουμε αλλιώς."""

    def __init__(self, payment_status="unpaid", status="open", payment_intent="pi_poll"):
        self.payment_status, self.status, self.payment_intent = payment_status, status, payment_intent


@pytest.fixture()
def stripe_says(monkeypatch):
    """Ψεύτικο Stripe: `answers[session_id]` είναι μια `Answer` ή μια εξαίρεση. Καταγράφει ποιες συνεδρίες ρωτήθηκαν και
    αν η ερώτηση έγινε στο νήμα του βρόχου γεγονότων (κύριο νήμα) ή σε άλλο."""
    import routes_payments
    answers, asked = {}, []

    def retrieve(session_id):
        asked.append(SimpleNamespace(session=session_id, on_loop_thread=threading.current_thread() is threading.main_thread()))
        answer = answers.get(session_id, Answer())
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "retrieve", staticmethod(retrieve))
    return SimpleNamespace(answers=answers, asked=asked, sessions=lambda: [a.session for a in asked])


async def _run_job():
    return (await run_firm(FIRM, ["card_payments"]))[0]


# ======================================================= 1. Ο Autopilot ρωτά το Stripe ====

@pytest.mark.asyncio
async def test_a_payment_the_webhook_and_the_browser_never_reported_is_found_by_the_autopilot(stripe_says):
    inv = await _invoice()
    await _tx("cs_found", inv)
    stripe_says.answers["cs_found"] = Answer("paid", "complete", "pi_found")

    result = await _run_job()

    assert result["status"] == "ok" and result["stats"]["polled"] == 1 and result["stats"]["recovered"] == 1
    paid = await _invoice_doc(inv)
    assert paid["status"] == "paid" and paid["paid_via"] == "stripe" and paid["paid_ref"] == "cs_found"
    tx = await _tx_doc("cs_found")
    assert tx["payment_status"] == "paid" and tx["apply_pending"] is False and tx["apply_result"] == "settled"
    assert tx["stripe_payment_intent_id"] == "pi_found"          # χρειάζεται για μια μελλοντική επιστροφή χρημάτων


@pytest.mark.asyncio
async def test_a_paid_invoice_is_not_asked_about_again(stripe_says):
    inv = await _invoice()
    await _tx("cs_once", inv)
    stripe_says.answers["cs_once"] = Answer("paid", "complete")
    await _run_job()
    await _run_job()
    assert stripe_says.sessions() == ["cs_once"]


@pytest.mark.asyncio
async def test_a_payment_smaller_than_the_amount_owed_found_by_the_autopilot_is_reported_not_settled(stripe_says):
    inv = await _invoice()
    await _tx("cs_small", inv, amount=100.0)
    stripe_says.answers["cs_small"] = Answer("paid", "complete")
    await _run_job()
    assert (await _invoice_doc(inv))["status"] == "issued"
    assert (await _tx_doc("cs_small"))["apply_result"] == "underpaid"
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_small"})
    assert note and note["requires_action"] is True


@pytest.mark.asyncio
async def test_a_session_younger_than_ten_minutes_is_left_alone_because_the_payer_may_be_paying_right_now(stripe_says):
    inv = await _invoice()
    await _tx("cs_new", inv, created_at=_ago(minutes=CARD_POLL_AFTER_MINUTES - 5))
    await _tx("cs_old", inv, created_at=_ago(minutes=CARD_POLL_AFTER_MINUTES + 5))
    await _run_job()
    assert stripe_says.sessions() == ["cs_old"]


@pytest.mark.asyncio
async def test_a_session_older_than_the_window_is_not_asked_any_more(stripe_says):
    inv = await _invoice()
    await _tx("cs_ancient", inv, created_at=_ago(days=CARD_POLL_WINDOW_DAYS + 1))
    await _tx("cs_inside", inv, created_at=_ago(days=CARD_POLL_WINDOW_DAYS - 1))
    await _run_job()
    assert stripe_says.sessions() == ["cs_inside"]


@pytest.mark.asyncio
async def test_each_session_is_asked_at_most_every_thirty_minutes(stripe_says):
    inv = await _invoice()
    await _tx("cs_slow", inv)
    await _run_job()
    await _run_job()                                              # αμέσως μετά: δεν ξαναρωτιέται
    assert stripe_says.sessions() == ["cs_slow"]
    await db.payment_transactions.update_one({"session_id": "cs_slow"},
                                             {"$set": {"polled_at": _ago(minutes=CARD_POLL_EVERY_MINUTES + 1)}})
    await _run_job()
    assert stripe_says.sessions() == ["cs_slow", "cs_slow"]
    assert (await _tx_doc("cs_slow"))["payment_status"] == "pending"      # ακόμη ανοιχτή


@pytest.mark.asyncio
async def test_a_session_that_expired_unpaid_is_closed_and_never_asked_again(stripe_says):
    inv = await _invoice()
    await _tx("cs_expired", inv)
    stripe_says.answers["cs_expired"] = Answer("unpaid", "expired")
    result = await _run_job()
    assert result["stats"]["expired"] == 1
    tx = await _tx_doc("cs_expired")
    assert tx["payment_status"] == "expired" and tx["status"] == "expired"
    assert (await _invoice_doc(inv))["status"] == "issued"
    await db.payment_transactions.update_one({"session_id": "cs_expired"}, {"$unset": {"polled_at": ""}})
    await _run_job()
    assert stripe_says.sessions() == ["cs_expired"]


@pytest.mark.asyncio
async def test_a_delayed_payment_method_still_in_progress_stays_pending(stripe_says):
    """Έμβασμα/SEPA: η συνεδρία ολοκληρώθηκε αλλά τα χρήματα δεν έχουν φτάσει — ούτε εξόφληση, ούτε λήξη."""
    inv = await _invoice()
    await _tx("cs_async", inv)
    stripe_says.answers["cs_async"] = Answer("unpaid", "complete")
    result = await _run_job()
    assert result["stats"]["polled"] == 1 and result["stats"]["recovered"] == 0 and result["stats"]["expired"] == 0
    assert (await _tx_doc("cs_async"))["payment_status"] == "pending"
    assert (await _invoice_doc(inv))["status"] == "issued"


@pytest.mark.asyncio
async def test_at_most_twenty_sessions_are_asked_per_run_oldest_first_and_the_rest_in_the_next_run(stripe_says):
    inv = await _invoice()
    total = CARD_POLL_PER_RUN + 5
    for i in range(total):
        await _tx(f"cs_{i:02d}", inv, created_at=_ago(hours=2, minutes=total - i))        # cs_00 η παλαιότερη
    await _run_job()
    assert stripe_says.sessions() == [f"cs_{i:02d}" for i in range(CARD_POLL_PER_RUN)]
    await _run_job()
    assert stripe_says.sessions()[CARD_POLL_PER_RUN:] == [f"cs_{i:02d}" for i in range(CARD_POLL_PER_RUN, total)]


@pytest.mark.asyncio
async def test_only_the_sessions_of_the_firm_being_run_are_asked(stripe_says):
    mine, theirs = await _invoice(), await _invoice(firm=OTHER)
    await _tx("cs_mine", mine)
    await _tx("cs_theirs", theirs, firm=OTHER)
    await _run_job()
    assert stripe_says.sessions() == ["cs_mine"]
    assert "polled_at" not in await _tx_doc("cs_theirs")


@pytest.mark.asyncio
async def test_a_stripe_outage_does_not_fail_the_job_and_stops_after_three_errors(stripe_says):
    inv = await _invoice()
    for i in range(CARD_POLL_MAX_ERRORS + 3):
        await _tx(f"cs_down_{i}", inv, created_at=_ago(hours=3, minutes=10 - i))
        stripe_says.answers[f"cs_down_{i}"] = stripe.error.APIConnectionError("Δεν ήταν δυνατή η σύνδεση με το Stripe")
    result = await _run_job()
    assert result["status"] == "ok"
    assert result["stats"]["poll_errors"] == CARD_POLL_MAX_ERRORS and len(stripe_says.asked) == CARD_POLL_MAX_ERRORS
    states = [(await _tx_doc(f"cs_down_{i}"))["payment_status"] for i in range(CARD_POLL_MAX_ERRORS + 3)]
    assert states == ["pending"] * (CARD_POLL_MAX_ERRORS + 3)
    assert (await _invoice_doc(inv))["status"] == "issued"
    # όσες δεν ρωτήθηκαν ρωτιούνται στον επόμενο κύκλο· οι τρεις που απέτυχαν περιμένουν τα 30 λεπτά τους
    await _run_job()
    assert len(stripe_says.asked) == 2 * CARD_POLL_MAX_ERRORS


@pytest.mark.asyncio
async def test_one_failing_session_does_not_prevent_the_others_from_being_found(stripe_says):
    inv_a, inv_b = await _invoice(), await _invoice()
    await _tx("cs_a", inv_a, created_at=_ago(hours=3))
    await _tx("cs_b", inv_b, created_at=_ago(hours=2))
    stripe_says.answers["cs_a"] = stripe.error.APIError("σφάλμα διακομιστή")
    stripe_says.answers["cs_b"] = Answer("paid", "complete")
    await _run_job()
    assert (await _invoice_doc(inv_b))["status"] == "paid" and (await _invoice_doc(inv_a))["status"] == "issued"


@pytest.mark.asyncio
async def test_without_stripe_keys_the_autopilot_asks_nothing(stripe_says, monkeypatch):
    import routes_payments
    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: False)
    inv = await _invoice()
    await _tx("cs_nokeys", inv)
    result = await _run_job()
    assert result["status"] == "ok" and result["stats"]["polled"] == 0 and stripe_says.asked == []


# ===================================================== 2. Το API δεν παγώνει όσο περιμένει το Stripe ====

@pytest.mark.asyncio
async def test_stripe_is_asked_from_a_worker_thread_not_from_the_event_loop(stripe_says):
    inv = await _invoice()
    await _tx("cs_thread", inv)
    await _run_job()
    assert stripe_says.asked and all(not a.on_loop_thread for a in stripe_says.asked)


@pytest.fixture()
def office_client(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("PREVIEW_ENDPOINT", raising=False)
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "poll@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _office_invoice(total=124.0) -> str:
    user = _run(db.users.find_one({"email": "poll@example.gr"}))
    doc = {"firm_id": user["firm_id"], "client_id": "client-a", "client_name": "ΠΕΛΑΤΗΣ", "number": "ΤΠΥ-2026-0007",
           "date": "2026-10-01", "line_items": [], "subtotal": total, "vat_amount": 0.0, "total": total, "vat_breakdown": {},
           "status": "issued", "mydata_status": "synced", "invoice_type": "2.1", "created_at": _ago(days=1)}
    return str(_run(db.invoices.insert_one(doc)).inserted_id)


def test_the_checkout_session_is_created_from_a_worker_thread(office_client, monkeypatch):
    import routes_payments
    threads = []

    class Session:
        id, url = "cs_threaded", "https://pay.example/cs_threaded"

    def fake_create(**kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return Session()

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    invoice_id = _office_invoice()
    r = office_client.post("/api/payments/checkout", json={"invoice_id": invoice_id, "origin_url": "https://nomos.example.gr"})
    assert r.status_code == 200, r.text
    assert threads == [False]


def test_the_payment_link_of_a_reminder_is_created_from_a_worker_thread(monkeypatch):
    import invoice_services as svc
    threads = []

    class Session:
        id, url = "cs_remind", "https://pay.example/cs_remind"

    def fake_create(**kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        return Session()

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_realistic_key")
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    inv = _run(_invoice())
    link = _run(svc.build_payment_link(_run(_invoice_doc(inv)), {"firm_id": FIRM}))
    assert link == "https://pay.example/cs_remind" and threads == [False]


# ================================== 3. Η σελίδα κατάστασης και τα γεγονότα που φτάνουν καθυστερημένα ====

def test_the_status_page_records_a_payment_the_payer_made_and_the_webhook_missed(office_client, stripe_says):
    inv = _run(_invoice())
    _run(_tx("cs_page", inv))
    stripe_says.answers["cs_page"] = Answer("paid", "complete")
    r = office_client.get("/api/payments/status/cs_page")
    assert r.status_code == 200 and r.json()["payment_status"] == "paid"
    assert _run(_invoice_doc(inv))["status"] == "paid"


def test_the_status_page_shows_an_expired_session_as_expired(office_client, stripe_says):
    inv = _run(_invoice())
    _run(_tx("cs_page_expired", inv))
    stripe_says.answers["cs_page_expired"] = Answer("unpaid", "expired")
    r = office_client.get("/api/payments/status/cs_page_expired")
    assert r.status_code == 200 and r.json()["payment_status"] == "expired"
    assert _run(_invoice_doc(inv))["status"] == "issued"


def test_the_status_page_survives_a_stripe_outage(office_client, stripe_says):
    inv = _run(_invoice())
    _run(_tx("cs_page_down", inv))
    stripe_says.answers["cs_page_down"] = stripe.error.APIConnectionError("χωρίς σύνδεση")
    r = office_client.get("/api/payments/status/cs_page_down")
    assert r.status_code == 200 and r.json()["payment_status"] == "pending"


def _signed(payload: bytes, key: bytes) -> dict:
    stamp = str(int(time.time()))
    digest = hmac.new(key, f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    # Το Stripe στέλνει πάντα Content-Type· χωρίς αυτό, αίτημα με cookie συνεδρίας και σώμα θα έπεφτε στον κανόνα 415
    # του CsrfGuard (βλ. test_unit_csrf_r5.py) πριν φτάσει στο webhook.
    return {"stripe-signature": f"t={stamp},v1={digest}", "content-type": "application/json"}


def _event(kind: str, session_id: str) -> bytes:
    return json.dumps({"id": "evt_late", "object": "event", "type": kind,
                       "data": {"object": {"id": session_id, "payment_intent": "pi_x", "payment_status": "unpaid"}}}).encode()


@pytest.mark.parametrize("kind,state", [("checkout.session.expired", "expired"), ("checkout.session.async_payment_failed", "failed")])
def test_a_late_expiry_or_failure_event_never_turns_a_recorded_payment_into_an_unpaid_one(office_client, monkeypatch, kind, state):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "whsec_poll")
    inv = _run(_invoice())
    _run(_tx("cs_paid_then_late", inv, status="completed", payment_status="paid", paid_at=_ago(minutes=3)))
    payload = _event(kind, "cs_paid_then_late")
    assert office_client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b"whsec_poll")).status_code == 200
    tx = _run(_tx_doc("cs_paid_then_late"))
    assert tx["payment_status"] == "paid" and tx["status"] == "completed"


@pytest.mark.parametrize("kind,state", [("checkout.session.expired", "expired"), ("checkout.session.async_payment_failed", "failed")])
def test_the_same_events_still_close_a_payment_that_is_really_unpaid(office_client, monkeypatch, kind, state):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "whsec_poll")
    inv = _run(_invoice())
    _run(_tx("cs_unpaid", inv))
    payload = _event(kind, "cs_unpaid")
    assert office_client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b"whsec_poll")).status_code == 200
    tx = _run(_tx_doc("cs_unpaid"))
    assert tx["payment_status"] == state and tx["status"] == state


# ============================================ 4. Ο σύνδεσμος πληρωμής της υπενθύμισης ====

@pytest.fixture()
def link_calls(monkeypatch):
    created = []

    class Session:
        id, url = "cs_link", "https://pay.example/cs_link"

    def fake_create(**kwargs):
        created.append(kwargs)
        return Session()

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_realistic_key")
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    return created


def test_the_payment_link_expires_within_the_limits_stripe_accepts(link_calls):
    """Το Stripe δέχεται `expires_at` από 30 λεπτά ως 24 ώρες μετά τη δημιουργία (docs.stripe.com/api/checkout/sessions/create).
    Με 7 ημέρες κάθε κλήση απορριπτόταν και η υπενθύμιση έφευγε σιωπηλά χωρίς σύνδεσμο."""
    import invoice_services as svc
    inv = _run(_invoice())
    before = int(datetime.now(timezone.utc).timestamp())
    assert _run(svc.build_payment_link(_run(_invoice_doc(inv)), {"firm_id": FIRM})) == "https://pay.example/cs_link"
    after = int(datetime.now(timezone.utc).timestamp())
    expires_at = link_calls[-1]["expires_at"]
    assert before + 30 * 60 <= expires_at <= after + 24 * 3600
    assert expires_at - before <= 23 * 3600 + (after - before) + 1            # με περιθώριο ασφαλείας από το όριο των 24 ωρών


def test_a_link_rejected_by_stripe_leaves_the_reminder_without_a_link_and_does_not_crash(monkeypatch):
    import invoice_services as svc

    def refuse(**_kwargs):
        raise stripe.error.InvalidRequestError("expires_at must be less than 24 hours", "expires_at")

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_realistic_key")
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(refuse))
    inv = _run(_invoice())
    assert _run(svc.build_payment_link(_run(_invoice_doc(inv)), {"firm_id": FIRM})) == ""


def test_the_reminder_tells_the_payer_that_the_card_link_expires_within_24_hours():
    from email_utils import reminder_email_html, reminder_email_text
    invoice = {"number": "ΤΠΥ-2026-0001", "client_name": "ΠΕΛΑΤΗΣ", "total": 124.0, "payment_method": "κάρτα"}
    link = "https://pay.example/cs_link"
    for build in (reminder_email_text, reminder_email_html):
        with_iban = build(invoice, {"iban": "GR1601101250000000012300695", "bank_name": "Τράπεζα"}, 3, 0, link)
        # «μέσα σε 24 ώρες»: ο σύνδεσμος ισχύει 23 (PAYMENT_LINK_VALID_HOURS), άρα το «σε 24 ώρες» θα υπόσχονταν μία ώρα παραπάνω
        assert "λήγει μέσα σε 24 ώρες" in with_iban and "έμβασμα στο IBAN" in with_iban
        no_iban = build(invoice, {}, 3, 0, link)
        assert "λήγει μέσα σε 24 ώρες" in no_iban and "IBAN" not in no_iban
        assert "λήγει" not in build(invoice, {"iban": "GR1601101250000000012300695"}, 3, 0, "")        # χωρίς σύνδεσμο, καμία αναφορά


def test_the_startup_warning_describes_the_poll_that_now_exists():
    import inspect
    import server
    source = inspect.getsource(server.validate_environment)
    assert "card_payments" in source and "Autopilot" in source and "μόνο μέσω polling" not in source


# ======================================== 5. Αποτελέσματα της εφαρμογής: ποιος ισχυρίζεται ότι ολοκλήρωσε ====

@pytest.mark.asyncio
async def test_a_call_whose_claim_expired_and_was_taken_over_does_not_claim_to_have_applied_the_payment(monkeypatch):
    import routes_payments
    inv = await _invoice()
    await _tx("cs_stolen", inv, status="completed", payment_status="paid", apply_pending=True, paid_at=_ago(minutes=30))

    async def slow_apply(tx):
        # όσο δουλεύουμε, το κλείδωμα λήγει και κάποιος άλλος το παίρνει
        await db.payment_transactions.update_one({"session_id": "cs_stolen"}, {"$set": {"apply_claim": "someone-else"}})
        return "settled"

    monkeypatch.setattr(routes_payments, "_apply_card_payment", slow_apply)
    assert await routes_payments.apply_pending_card_payment("cs_stolen") is False
    tx = await _tx_doc("cs_stolen")
    assert tx["apply_pending"] is True and tx["apply_claim"] == "someone-else"


@pytest.mark.asyncio
async def test_a_call_that_finishes_its_own_claim_reports_success():
    import routes_payments
    inv = await _invoice()
    await _tx("cs_mine_done", inv, status="completed", payment_status="paid", apply_pending=True, paid_at=_ago(minutes=30))
    assert await routes_payments.apply_pending_card_payment("cs_mine_done") is True
    tx = await _tx_doc("cs_mine_done")
    assert tx["apply_pending"] is False and tx["apply_result"] == "settled" and "apply_claim" not in tx
