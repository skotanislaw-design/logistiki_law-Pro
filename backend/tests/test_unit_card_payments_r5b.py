"""Γύρος 5 (ανασκόπηση) — πληρωμές κάρτας και υπενθυμίσεις: τα ευρήματα του ανεξάρτητου ελέγχου.

* Το webhook του Stripe με ΚΕΝΟ μυστικό δεχόταν γεγονός υπογεγραμμένο με κενό κλειδί: όποιος ήξερε ένα `session_id`
  μπορούσε να «εξοφλήσει» τιμολόγιο (R5A-03).
* Πληρωμή κάρτας σε τιμολόγιο που στο μεταξύ πιστώθηκε «εξοφλούσε» το τιμολόγιο χωρίς καμία ειδοποίηση (R5A-04).
* Αν η ειδοποίηση δεν μπορούσε να γραφτεί (βάση), η εκκρεμότητα έκλεινε και το πρόβλημα χανόταν (R5A-07).
* Πληρωμή που κόλλησε από την παλιά έκδοση (εισπράχθηκε, δεν εφαρμόστηκε) δεν εντοπιζόταν ποτέ (R5A-08).
* Αργός πρώτος διεκδικητής έβγαζε ψευδή κρίσιμη ειδοποίηση για πληρωμή που είχε ήδη εφαρμοστεί σωστά (R5A-09).
* Τα ίδια τα πιστωτικά λάμβαναν «υπενθύμιση εξόφλησης» με οφειλόμενο 0,00 € (R5A-10).
"""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from automation import reminder_due, run_firm
from db import db

FIRM = "firm-card-r5b"
OTHER = "firm-card-r5b-other"


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


async def _invoice(status="issued", total=124.0, firm=FIRM, **over):
    doc = {"firm_id": firm, "number": "ΤΠΥ-2026-0001", "status": status, "total": total, **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _tx(session, invoice_id, amount=124.0, firm=FIRM, **over):
    doc = {"session_id": session, "firm_id": firm, "invoice_id": str(invoice_id), "amount": amount,
           "status": "initiated", "payment_status": "pending", **over}
    await db.payment_transactions.insert_one(doc)


async def _stuck_tx(session, invoice_id, firm=FIRM, **over):
    await _tx(session, invoice_id, firm=firm, status="completed", payment_status="paid", apply_pending=True,
              paid_at=over.pop("paid_at", _ago(minutes=30)), **over)


async def _invoice_doc(invoice_id):
    return await db.invoices.find_one({"_id": invoice_id})


async def _tx_doc(session):
    return await db.payment_transactions.find_one({"session_id": session})


# ===================================================== 1. Webhook χωρίς μυστικό ====

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _signed(payload: bytes, key: bytes) -> dict:
    stamp = str(int(time.time()))
    digest = hmac.new(key, f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={stamp},v1={digest}"}


def _completed_event(session_id: str) -> bytes:
    return json.dumps({"id": "evt_test", "object": "event", "type": "checkout.session.completed",
                       "data": {"object": {"id": session_id, "payment_intent": "pi_x", "payment_status": "paid"}}}).encode()


def test_a_webhook_signed_with_an_empty_key_cannot_settle_an_invoice_when_no_secret_is_configured(client, monkeypatch):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "")
    loop = asyncio.get_event_loop_policy().new_event_loop()
    inv = loop.run_until_complete(_invoice())
    loop.run_until_complete(_tx("cs_attacker", inv))
    payload = _completed_event("cs_attacker")
    r = client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b""))
    assert r.status_code == 503
    assert loop.run_until_complete(_invoice_doc(inv))["status"] == "issued"
    assert loop.run_until_complete(_tx_doc("cs_attacker"))["payment_status"] == "pending"


def test_a_blank_secret_counts_as_no_secret(client, monkeypatch):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "   ")
    payload = _completed_event("cs_whatever")
    assert client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b"   ")).status_code == 503


def test_with_a_secret_only_a_correctly_signed_event_is_accepted(client, monkeypatch):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "whsec_real_secret")
    loop = asyncio.get_event_loop_policy().new_event_loop()
    inv = loop.run_until_complete(_invoice())
    loop.run_until_complete(_tx("cs_real", inv))
    payload = _completed_event("cs_real")

    forged = client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b""))
    assert forged.status_code == 400
    wrong = client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b"whsec_other"))
    assert wrong.status_code == 400
    assert loop.run_until_complete(_invoice_doc(inv))["status"] == "issued"

    real = client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, b"whsec_real_secret"))
    assert real.status_code == 200
    paid = loop.run_until_complete(_invoice_doc(inv))
    assert paid["status"] == "paid" and paid["paid_ref"] == "cs_real"


def test_the_startup_warning_says_what_really_happens():
    import inspect
    import server
    source = inspect.getsource(server.validate_environment)
    assert "STRIPE_WEBHOOK_SECRET" in source and "503" in source


# ============================================ 2. Πληρωμή σε πιστωμένο τιμολόγιο ====

@pytest.mark.asyncio
async def test_a_card_payment_for_an_invoice_that_was_credited_meanwhile_is_reported_not_settled():
    """Η συνεδρία πληρωμής άνοιξε πριν εκδοθεί το πιστωτικό: ο πελάτης πλήρωσε ένα παραστατικό που πιστώθηκε —
    το γραφείο χρωστά επιστροφή ή σύνδεση, και πρέπει να το μάθει."""
    import routes_payments
    inv = await _invoice(credit_note_id="cn-1")
    await _tx("cs_credited", inv)
    await routes_payments._mark_paid("cs_credited", "pi_1")
    assert (await _invoice_doc(inv))["status"] == "issued"
    tx = await _tx_doc("cs_credited")
    assert tx["apply_result"] == "not_open" and tx["apply_pending"] is False
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_credited"})
    assert note and note["level"] == "critical" and note["requires_action"] is True
    assert "πιστωτικό" in note["message"] and "124,00 €" in note["title"]


@pytest.mark.asyncio
async def test_a_credited_invoice_that_this_very_session_already_paid_is_not_a_new_problem():
    import routes_payments
    inv = await _invoice(status="paid", credit_note_id="cn-1", paid_via="stripe", paid_ref="cs_same")
    await _stuck_tx("cs_same", inv, apply_claim="dead-worker", apply_claim_at=_ago(minutes=20))
    await routes_payments._mark_paid("cs_same")
    assert (await _tx_doc("cs_same"))["apply_result"] == "settled"
    assert await db.notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_the_message_names_the_real_reason_for_a_non_open_invoice():
    import routes_payments
    inv = await _invoice(status="cancelled")
    await _tx("cs_cancelled", inv)
    await routes_payments._mark_paid("cs_cancelled")
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_cancelled"})
    assert "cancelled" in note["message"] and "πιστωτικό" not in note["message"]


# ============================================= 3. Η ειδοποίηση δεν πρέπει να χάνεται ====

@pytest.mark.asyncio
async def test_when_the_alert_cannot_be_written_the_payment_stays_pending_and_is_retried(monkeypatch):
    """Παλιά η αποτυχία της ειδοποίησης καταπινόταν και η εκκρεμότητα έκλεινε: μια πληρωμή που χρειάζεται άνθρωπο
    θα έκλεινε χωρίς να το μάθει κανείς. Τώρα το σφάλμα φεύγει προς τα πάνω (το webhook απαντά 500 και το Stripe
    ξαναστέλνει), το κλείδωμα λύνεται και ο δείκτης «εκκρεμεί» μένει."""
    import automation
    import routes_payments
    inv = await _invoice()
    await _tx("cs_alert", inv, amount=100.0)                       # λιγότερο από το οφειλόμενο → χρειάζεται ειδοποίηση
    real_notify = automation.notify

    async def broken(*args, **kwargs):
        raise RuntimeError("η βάση δεν απαντά")

    monkeypatch.setattr(automation, "notify", broken)
    with pytest.raises(RuntimeError):
        await routes_payments._mark_paid("cs_alert")
    tx = await _tx_doc("cs_alert")
    assert tx["payment_status"] == "paid" and tx["apply_pending"] is True and "apply_claim" not in tx
    assert "apply_result" not in tx and await db.notifications.count_documents({}) == 0

    monkeypatch.setattr(automation, "notify", real_notify)
    await routes_payments._mark_paid("cs_alert")                   # η επόμενη κλήση (webhook, polling, Autopilot)
    tx = await _tx_doc("cs_alert")
    assert tx["apply_pending"] is False and tx["apply_result"] == "underpaid" and tx["apply_attempts"] == 2
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_unapplied:cs_alert"}) == 1


@pytest.mark.asyncio
async def test_an_alert_failure_after_the_invoice_was_closed_is_retried_without_closing_it_twice(monkeypatch):
    import automation
    import routes_payments
    inv = await _invoice(trust_applied=100.0)                      # οφείλονται 24 €, ο πελάτης πλήρωσε 124 → υπερπληρωμή
    await _tx("cs_over2", inv)
    real_notify = automation.notify

    async def broken(*args, **kwargs):
        raise RuntimeError("η βάση δεν απαντά")

    monkeypatch.setattr(automation, "notify", broken)
    with pytest.raises(RuntimeError):
        await routes_payments._mark_paid("cs_over2")
    assert (await _invoice_doc(inv))["status"] == "paid"           # το τιμολόγιο έκλεισε, η ειδοποίηση όχι
    assert (await _tx_doc("cs_over2"))["apply_pending"] is True

    monkeypatch.setattr(automation, "notify", real_notify)
    await routes_payments._mark_paid("cs_over2")
    assert (await _tx_doc("cs_over2"))["apply_result"] == "overpaid"
    assert await db.notifications.count_documents({"type": "card_payment_overpaid"}) == 1
    assert (await _invoice_doc(inv))["paid_ref"] == "cs_over2"


# ============================================= 4. Αργός πρώτος διεκδικητής ====

@pytest.mark.asyncio
async def test_a_stalled_first_attempt_that_loses_the_race_to_the_same_payment_raises_no_alarm(monkeypatch):
    """Ο πρώτος διεκδικητής καθυστέρησε πέρα από το TTL, ο δεύτερος εξόφλησε το τιμολόγιο με ΑΥΤΗ τη συνεδρία· το
    `mark_paid` του πρώτου βρίσκει το τιμολόγιο κλειστό — αλλά από την ίδια πληρωμή. Δεν είναι πρόβλημα."""
    import invoice_services
    import routes_payments
    inv = await _invoice()
    await _tx("cs_stall", inv)
    real = invoice_services.mark_paid

    async def other_attempt_settles_first(firm_id, invoice_id, **kwargs):
        assert await real(firm_id, invoice_id, **kwargs) is True
        return await real(firm_id, invoice_id, **kwargs)           # εμείς: δεν είναι πια ανοιχτό → False

    monkeypatch.setattr(invoice_services, "mark_paid", other_attempt_settles_first)
    await routes_payments._mark_paid("cs_stall")
    assert (await _tx_doc("cs_stall"))["apply_result"] == "settled"
    assert await db.notifications.count_documents({}) == 0
    assert (await _invoice_doc(inv))["paid_ref"] == "cs_stall"


@pytest.mark.asyncio
async def test_losing_the_race_to_a_different_payment_is_still_reported(monkeypatch):
    import invoice_services
    import routes_payments
    inv = await _invoice()
    await _tx("cs_mine", inv)
    real = invoice_services.mark_paid

    async def another_session_settles_first(firm_id, invoice_id, **kwargs):
        await real(firm_id, invoice_id, paid_via="stripe", ref="cs_someone_else")
        return await real(firm_id, invoice_id, **kwargs)

    monkeypatch.setattr(invoice_services, "mark_paid", another_session_settles_first)
    await routes_payments._mark_paid("cs_mine")
    assert (await _tx_doc("cs_mine"))["apply_result"] == "not_open"
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_unapplied:cs_mine"}) == 1


# =============================== 5. Πληρωμή που κόλλησε από την παλιά έκδοση (μόνο ειδοποίηση) ====

async def _legacy_tx(session, invoice_id, firm=FIRM):
    """Όπως την άφηνε η παλιά έκδοση: εισπράχθηκε, χωρίς δείκτη εφαρμογής και χωρίς αποτέλεσμα."""
    await _tx(session, invoice_id, firm=firm, status="completed", payment_status="paid", paid_at=_ago(days=3))


@pytest.mark.asyncio
async def test_a_payment_stuck_by_the_old_version_is_reported_but_never_applied():
    inv = await _invoice()
    await _legacy_tx("cs_old", inv)
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["status"] == "ok" and result["stats"]["legacy_reported"] == 1
    assert (await _invoice_doc(inv))["status"] == "issued"            # δεν εφαρμόζεται μόνη της (απόφαση ανθρώπου)
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_old"})
    assert note and note["level"] == "critical" and note["requires_action"] is True
    assert "ΤΠΥ-2026-0001" in note["message"] and "124,00 €" in note["title"]
    assert (await _tx_doc("cs_old"))["apply_result"] == "legacy_reported"


@pytest.mark.asyncio
async def test_the_old_payment_is_reported_once_even_after_a_person_closed_the_card():
    inv = await _invoice()
    await _legacy_tx("cs_old", inv)
    await run_firm(FIRM, ["card_payments"])
    await db.notifications.update_many({}, {"$set": {"resolved_at": _ago(minutes=5), "resolved_by": "lawyer@example.gr"}})
    for _ in range(3):
        result = (await run_firm(FIRM, ["card_payments"]))[0]
        assert result["stats"]["legacy_reported"] == 0
    assert await db.notifications.count_documents({}) == 1


@pytest.mark.asyncio
async def test_old_payments_that_were_applied_or_belong_to_other_firms_or_are_modern_are_left_alone():
    paid = await _invoice(status="paid", paid_via="stripe", paid_ref="cs_fine")
    await _legacy_tx("cs_fine", paid)                                  # εφαρμόστηκε κανονικά
    cancelled = await _invoice(status="cancelled")
    await _legacy_tx("cs_cancelled_old", cancelled)                    # δεν είναι «κολλημένο»: το τιμολόγιο δεν είναι ανοιχτό
    other = await _invoice(firm=OTHER)
    await _legacy_tx("cs_other", other, firm=OTHER)                    # άλλο γραφείο
    modern = await _invoice()
    await _tx("cs_modern", modern, status="completed", payment_status="paid", apply_pending=False, apply_result="settled")
    pending_modern = await _invoice()
    await _tx("cs_unpaid", pending_modern)                             # δεν έχει εισπραχθεί
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["stats"]["legacy_reported"] == 0
    assert await db.notifications.count_documents({}) == 0
    assert (await _tx_doc("cs_fine"))["apply_result"] == "legacy_checked"
    assert (await _tx_doc("cs_cancelled_old"))["apply_result"] == "legacy_checked"
    assert "apply_result" not in await _tx_doc("cs_other")             # δεν το άγγιξε η εργασία αυτού του γραφείου
    assert (await _tx_doc("cs_modern"))["apply_result"] == "settled" and "apply_result" not in await _tx_doc("cs_unpaid")


# ================================================ 6. Tests που κλειδώνουν τα δίχτυα ασφαλείας ====

@pytest.mark.asyncio
async def test_one_cent_short_is_not_a_settlement_and_one_cent_over_is_reported():
    import routes_payments
    short = await _invoice()
    await _tx("cs_short", short, amount=123.99)
    await routes_payments._mark_paid("cs_short")
    assert (await _invoice_doc(short))["status"] == "issued"
    assert (await _tx_doc("cs_short"))["apply_result"] == "underpaid"
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_unapplied:cs_short"}) == 1

    over = await _invoice()
    await _tx("cs_over", over, amount=124.01)
    await routes_payments._mark_paid("cs_over")
    assert (await _invoice_doc(over))["status"] == "paid"
    assert (await _tx_doc("cs_over"))["apply_result"] == "overpaid"
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_overpaid:cs_over"}) == 1


@pytest.mark.asyncio
async def test_the_fifth_attempt_still_applies_and_the_sixth_gives_up():
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_5", inv, apply_attempts=routes_payments.APPLY_MAX_ATTEMPTS - 1)
    assert await routes_payments.apply_pending_card_payment("cs_5")
    assert (await _tx_doc("cs_5"))["apply_result"] == "settled"

    inv2 = await _invoice()
    await _stuck_tx("cs_6", inv2, apply_attempts=routes_payments.APPLY_MAX_ATTEMPTS)
    assert await routes_payments.apply_pending_card_payment("cs_6")
    assert (await _tx_doc("cs_6"))["apply_result"] == "given_up"
    assert (await _invoice_doc(inv2))["status"] == "issued"


@pytest.mark.asyncio
async def test_a_slow_attempt_cannot_close_or_unlock_a_payment_another_attempt_has_taken_over(monkeypatch):
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_take", inv)

    async def slow_then_taken_over(tx):
        await db.payment_transactions.update_one(
            {"session_id": "cs_take"}, {"$set": {"apply_claim": "other", "apply_claim_at": _ago(seconds=1)}})
        return "settled"

    monkeypatch.setattr(routes_payments, "_apply_card_payment", slow_then_taken_over)
    await routes_payments.apply_pending_card_payment("cs_take")
    tx = await _tx_doc("cs_take")
    assert tx["apply_pending"] is True and tx["apply_claim"] == "other"        # η τελική εγγραφή αρνείται: δεν είναι δικό μας

    async def crash_after_takeover(tx):
        await db.payment_transactions.update_one(
            {"session_id": "cs_take"}, {"$set": {"apply_claim": "third", "apply_claim_at": _ago(seconds=1)}})
        raise asyncio.CancelledError()

    await db.payment_transactions.update_one({"session_id": "cs_take"}, {"$set": {"apply_claim_at": _ago(seconds=500)}})
    monkeypatch.setattr(routes_payments, "_apply_card_payment", crash_after_takeover)
    with pytest.raises(asyncio.CancelledError):
        await routes_payments.apply_pending_card_payment("cs_take")
    assert (await _tx_doc("cs_take"))["apply_claim"] == "third"                # δεν ξεκλειδώνει κλείδωμα άλλου


@pytest.mark.asyncio
async def test_the_time_of_collection_is_recorded_and_the_job_picks_up_a_payment_left_pending_for_long(monkeypatch):
    """Ο Autopilot αφήνει 5 λεπτά περιθώριο στη ζωντανή κλήση: χωρίς `paid_at` μια εκκρεμότητα δεν θα εντοπιζόταν ποτέ."""
    import routes_payments
    inv = await _invoice()
    await _tx("cs_late", inv)
    monkeypatch.setattr(routes_payments, "_now", lambda: _ago(minutes=10))

    async def nobody_applies(session_id):
        return False

    real_apply = routes_payments.apply_pending_card_payment
    monkeypatch.setattr(routes_payments, "apply_pending_card_payment", nobody_applies)
    await routes_payments._mark_paid("cs_late", "pi_late")
    monkeypatch.setattr(routes_payments, "apply_pending_card_payment", real_apply)
    tx = await _tx_doc("cs_late")
    assert tx["paid_at"] and tx["apply_pending"] is True and tx["stripe_payment_intent_id"] == "pi_late"
    assert (await _invoice_doc(inv))["status"] == "issued"

    monkeypatch.undo()
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["stats"]["applied"] == 1 and (await _invoice_doc(inv))["status"] == "paid"


# ================================================ 7. Υπενθυμίσεις: ούτε στα πιστωτικά ====

TODAY = datetime(2026, 6, 15, tzinfo=timezone.utc).date()


def test_a_credit_note_is_never_sent_a_payment_reminder():
    settings = {"reminder_after_days": 30, "reminder_interval_days": 15, "reminder_max": 3}
    invoice = {"status": "issued", "date": "2026-01-10", "total": 124.0}
    assert reminder_due(invoice, settings, TODAY) is True
    assert reminder_due({**invoice, "credit_note_for": "abc", "total": -124.0}, settings, TODAY) is False
    assert reminder_due({**invoice, "total": -124.0}, settings, TODAY) is False       # αρνητικό σύνολο = πιστωτικό
    assert reminder_due({**invoice, "credit_note_for": "abc"}, settings, TODAY) is False
    assert reminder_due({**invoice, "total": 0.0}, settings, TODAY) is True            # το μηδενικό δεν είναι πιστωτικό
    assert reminder_due({**invoice, "total": None}, settings, TODAY) is True
    assert reminder_due({**invoice, "credit_note_id": "cn"}, settings, TODAY) is False


@pytest.mark.asyncio
async def test_the_reminders_job_queues_nothing_for_the_credit_note_itself():
    settings = {"firm_id": FIRM, "auto_reminders": True, "reminder_after_days": 30, "smtp_host": "smtp.example.gr",
                "smtp_user": "u", "smtp_password": "p", "smtp_from": "a@example.gr"}
    await db.settings.insert_one(settings)
    client_id = (await db.clients.insert_one({"firm_id": FIRM, "name": "ΑΛΦΑ", "email": "alpha@example.gr"})).inserted_id
    original = await _invoice(date="2026-01-01", client_id=str(client_id), credit_note_id="x", number="ΤΠΥ-2026-0001")
    await _invoice(date="2026-01-02", client_id=str(client_id), number="ΠΙΣ-2026-0001", total=-124.0,
                   credit_note_for=str(original))
    result = (await run_firm(FIRM, ["reminders"]))[0]
    assert result["status"] == "ok" and result["stats"]["queued"] == 0
    assert await db.outbox.count_documents({"kind": "email_reminder"}) == 0
