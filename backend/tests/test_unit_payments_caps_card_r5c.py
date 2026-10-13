"""Γύρος 5 (έλεγχος μεταλλάξεων) — πληρωμές κάρτας: όσα έπρεπε να προσέχει κάποιο test και δεν το έκανε.

Κάθε test εδώ υπάρχει επειδή μια μικρή, εύλογη αλλαγή στον κώδικα (μετάλλαξη) δεν έριχνε κανένα από τα υπάρχοντα tests:

* το ποσό προς το Stripe κόβεται αντί να στρογγυλοποιείται (19,99 € → 1998 λεπτά)· η συναλλαγή γράφεται σε λάθος γραφείο·
* σφάλμα του Stripe στο polling γίνεται 500· το `payment_intent` (χρειάζεται για επιστροφές) δεν αποθηκεύεται·
* η αποδέσμευση του κλειδώματος δεν αντέχει δεύτερη ακύρωση του αιτήματος· ο τίτλος της «εγκατάλειψης» λέει 0,00 €·
* η υπερπληρωμή προτείνει επιστροφή «-100,00 €»· η αποτυχία εφαρμογής καταγράφεται ως «not_open»·
* η εφαρμογή βρίσκει τιμολόγιο ή ρυθμίσεις (παρακράτηση) ΑΛΛΟΥ γραφείου·
* το webhook δέχεται (200) σώμα που δεν διαβάζεται, ξεχνά το `no_payment_required`, χάνει το `payment_intent`,
  μπερδεύει τις καταστάσεις «failed» / «expired»·
* η λίστα συναλλαγών δείχνει πληρωμές άλλων γραφείων ή κόβει τις νεότερες·
* η ειδοποίηση μιας πληρωμής που δεν εφαρμόστηκε χάνει τη σύνδεσή της με το τιμολόγιο (`entity_id`)·
* η εργασία του Autopilot μετρά ως «εφαρμοσμένες» πληρωμές που δεν εφάρμοσε, ή σκάει σε χαλασμένο id τιμολογίου,
  ή ειδοποιεί για τιμολόγιο άλλου γραφείου· το dedupe των ειδοποιήσεων δεν ξεχωρίζει γραφεία.
"""
import asyncio
import hashlib
import hmac
import itertools
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from automation import notify, run_firm
from db import db

FIRM = "firm-card-r5c"
OTHER = "firm-card-r5c-other"


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


_numbers = itertools.count(1)


async def _invoice(status="issued", total=124.0, firm=FIRM, **over):
    """Κάθε τιμολόγιο παίρνει δικό του αριθμό (όταν ξεκινά ο server υπάρχει μοναδικό ευρετήριο ανά γραφείο+αριθμό)."""
    doc = {"firm_id": firm, "number": f"ΤΠΥ-2026-{next(_numbers):04d}", "status": status, "total": total, **over}
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


# ================================================== 1. Το ποσό που χρεώνεται στο Stripe ====

@pytest.fixture()
def stripe_calls(monkeypatch):
    import routes_payments
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=f"cs_cents_{len(calls)}", url=f"https://checkout.stripe.test/{len(calls)}")

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "create", staticmethod(fake_create))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("amount, cents", [
    (19.99, 1999), (0.29, 29), (1.15, 115), (4.35, 435), (8.2, 820), (0.57, 57), (1240.01, 124001), (124.0, 12400)])
async def test_the_card_is_charged_exactly_the_owed_amount_in_cents(stripe_calls, amount, cents):
    """`int(19.99 * 100)` είναι 1998: ο πελάτης θα πλήρωνε ένα λεπτό λιγότερο και το τιμολόγιο δεν θα εξοφλούνταν ποτέ."""
    import routes_payments
    inv = {"_id": ObjectId(), "firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": "c-1", "client_name": "ΑΛΦΑ ΑΕ"}
    out = await routes_payments._create_session(inv, amount, "https://app.example/ok", "https://app.example/no")
    assert stripe_calls[0]["line_items"][0]["price_data"]["unit_amount"] == cents
    assert stripe_calls[0]["line_items"][0]["price_data"]["currency"] == "eur"
    assert out["session_id"] == "cs_cents_1" and out["checkout_url"] == "https://checkout.stripe.test/1"


@pytest.mark.asyncio
async def test_the_session_is_recorded_under_the_firm_of_the_invoice(stripe_calls):
    """Η συναλλαγή που ανήκει στο γραφείο Α δεν πρέπει να γραφτεί στο «legacy»: ο Autopilot και η λίστα του γραφείου
    ψάχνουν με το δικό του `firm_id`, οπότε η πληρωμή που θα κολλούσε δεν θα την έβλεπε κανείς."""
    import routes_payments
    inv = {"_id": ObjectId(), "firm_id": OTHER, "number": "ΤΠΥ-2026-0007", "client_id": "c-9", "client_name": "ΒΗΤΑ ΑΕ"}
    out = await routes_payments._create_session(inv, 62.5, "https://app.example/ok", "https://app.example/no")
    tx = await _tx_doc(out["session_id"])
    assert tx["firm_id"] == OTHER and tx["invoice_id"] == str(inv["_id"]) and tx["invoice_number"] == "ΤΠΥ-2026-0007"
    assert tx["amount"] == 62.5 and tx["currency"] == "eur"
    assert tx["status"] == "initiated" and tx["payment_status"] == "pending"
    assert tx["client_id"] == "c-9" and tx["client_name"] == "ΒΗΤΑ ΑΕ"
    assert await db.payment_transactions.count_documents({"firm_id": FIRM}) == 0


# ============================================================= 2. Polling της σελίδας επιτυχίας ====

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def test_a_stripe_outage_while_polling_leaves_the_payment_pending_instead_of_failing_the_page(client, monkeypatch):
    """Η σελίδα που ρωτά «πληρώθηκε;» δεν πρέπει να σκάει με 500 επειδή το Stripe δεν απαντά τη στιγμή εκείνη."""
    import routes_payments
    import stripe
    inv = _run(_invoice())
    _run(_tx("cs_outage", inv))

    def down(session_id):
        raise stripe.error.APIConnectionError("το Stripe δεν απαντά")

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "retrieve", staticmethod(down))
    r = client.get("/api/payments/status/cs_outage")
    assert r.status_code == 200 and r.json()["payment_status"] == "pending"
    assert _run(_invoice_doc(inv))["status"] == "issued"


def test_polling_keeps_the_payment_intent_so_that_a_refund_is_possible_later(client, monkeypatch):
    import routes_payments
    inv = _run(_invoice())
    _run(_tx("cs_poll_pi", inv))
    state = SimpleNamespace(payment_status="paid", status="complete", payment_intent="pi_from_polling")
    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "retrieve", staticmethod(lambda sid: state))
    r = client.get("/api/payments/status/cs_poll_pi")
    assert r.status_code == 200 and r.json()["payment_status"] == "paid"
    tx = _run(_tx_doc("cs_poll_pi"))
    assert tx["stripe_payment_intent_id"] == "pi_from_polling" and tx["apply_result"] == "settled"
    assert _run(_invoice_doc(inv))["paid_ref"] == "cs_poll_pi"


# =============================== 3. Κλείδωμα εφαρμογής: δεύτερη ακύρωση, επανάληψη μετά το κλείσιμο ====

class _SlowRelease:
    """Το `db` του routes_payments, με αργή την εγγραφή που λύνει το κλείδωμα (ώστε να προλάβει μια 2η ακύρωση)."""

    def __init__(self, real_db, started: asyncio.Event):
        self._db = real_db
        self._started = started

    def __getattr__(self, name):
        attr = getattr(self._db, name)
        if name != "payment_transactions":
            return attr
        started = self._started

        class Collection:
            def __getattr__(_self, method):
                target = getattr(attr, method)
                if method != "update_one":
                    return target

                async def slow(*a, **k):
                    started.set()
                    await asyncio.sleep(0.2)
                    return await target(*a, **k)
                return slow
        return Collection()


@pytest.mark.asyncio
async def test_the_claim_is_released_even_if_the_request_is_cancelled_a_second_time_while_releasing(monkeypatch):
    """Ο πελάτης κλείνει τη σελίδα (ακύρωση αιτήματος) και ο server, που ήδη λύνει το κλείδωμα, δέχεται δεύτερη ακύρωση
    (τερματισμός). Χωρίς `asyncio.shield` η αποδέσμευση διακόπτεται και η πληρωμή μένει κλειδωμένη για 120 δευτερόλεπτα."""
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_cancel2", inv)
    working = asyncio.Event()

    async def blocked(tx):
        working.set()
        await asyncio.sleep(30)
        return "settled"

    monkeypatch.setattr(routes_payments, "_apply_card_payment", blocked)
    release_started = asyncio.Event()
    task = asyncio.ensure_future(routes_payments.apply_pending_card_payment("cs_cancel2"))
    await asyncio.wait_for(working.wait(), 5)
    assert "apply_claim" in await _tx_doc("cs_cancel2")                       # κρατά το κλείδωμα όσο δουλεύει

    monkeypatch.setattr(routes_payments, "db", _SlowRelease(db, release_started))
    task.cancel()                                                             # 1η ακύρωση: ξεκινά η αποδέσμευση
    await asyncio.wait_for(release_started.wait(), 5)
    task.cancel()                                                             # 2η ακύρωση: στη μέση της αποδέσμευσης
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    monkeypatch.setattr(routes_payments, "db", db)
    tx = await _tx_doc("cs_cancel2")
    for _ in range(150):                                                      # η προστατευμένη εγγραφή ολοκληρώνεται μόνη της
        if "apply_claim" not in tx:
            break
        await asyncio.sleep(0.02)
        tx = await _tx_doc("cs_cancel2")
    assert "apply_claim" not in tx and tx["apply_pending"] is True


@pytest.mark.asyncio
async def test_a_replayed_callback_after_a_person_closed_the_alert_changes_nothing(monkeypatch):
    """Η υπερπληρωμή εφαρμόστηκε και ο άνθρωπος έκλεισε την ειδοποίηση. Το Stripe ξαναστέλνει το webhook: ούτε νέα
    εφαρμογή, ούτε νέα ειδοποίηση, ούτε αύξηση του μετρητή προσπαθειών."""
    import routes_payments
    inv = await _invoice(trust_applied=100.0)                                 # οφείλονται 24 €, ο πελάτης πλήρωσε 124 €
    await _tx("cs_replay", inv)
    await routes_payments._mark_paid("cs_replay", "pi_1")
    first = await _tx_doc("cs_replay")
    assert first["apply_result"] == "overpaid" and first["apply_pending"] is False and first["apply_attempts"] == 1
    await db.notifications.update_many({}, {"$set": {"resolved_at": _ago(minutes=1), "resolved_by": "lawyer@example.gr"}})

    for _ in range(2):
        await routes_payments._mark_paid("cs_replay", "pi_1")
    assert await routes_payments.apply_pending_card_payment("cs_replay") is False
    again = await _tx_doc("cs_replay")
    assert again["apply_attempts"] == 1 and again["applied_at"] == first["applied_at"] and again["paid_at"] == first["paid_at"]
    assert await db.notifications.count_documents({}) == 1                    # μόνο η αρχική (ήδη κλεισμένη) ειδοποίηση


# ================================================= 4. Τι ακριβώς λέει η ειδοποίηση ====

@pytest.mark.asyncio
async def test_giving_up_names_the_amount_that_was_collected_and_the_invoice():
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_giveup", inv, amount=124.0, apply_attempts=routes_payments.APPLY_MAX_ATTEMPTS)
    await routes_payments._mark_paid("cs_giveup")
    notes = await db.notifications.find({"dedupe_key": "card_payment_unapplied:cs_giveup"}).to_list(10)
    assert len(notes) == 1
    note = notes[0]
    assert note["title"] == "Πληρωμή κάρτας 124,00 € που δεν εφαρμόστηκε"
    assert note["entity"] == "invoice" and note["entity_id"] == str(inv) and note["firm_id"] == FIRM
    assert note["level"] == "critical" and note["requires_action"] is True and "cs_giveup" in note["message"]


@pytest.mark.asyncio
async def test_the_overpayment_alert_states_the_refund_as_a_positive_amount():
    import routes_payments
    inv = await _invoice(trust_applied=100.0)
    await _tx("cs_refund", inv, amount=124.0)
    await routes_payments._mark_paid("cs_refund")
    note = await db.notifications.find_one({"dedupe_key": "card_payment_overpaid:cs_refund"})
    assert "Επιστρέψτε στον πελάτη τη διαφορά 100,00 €" in note["message"]
    assert "οφειλόμενο ήταν 24,00 €" in note["message"] and "εισπράχθηκαν 124,00 €" in note["message"]


@pytest.mark.asyncio
async def test_a_failure_while_applying_is_recorded_as_failed_and_raises_exactly_one_alert(monkeypatch):
    import invoice_services
    import routes_payments
    inv = await _invoice()
    await _tx("cs_fail", inv)

    async def boom(*a, **k):
        raise RuntimeError("η βάση δεν απάντησε")

    monkeypatch.setattr(invoice_services, "mark_paid", boom)
    await routes_payments._mark_paid("cs_fail")
    await routes_payments._mark_paid("cs_fail")                               # επανάληψη: δεν ξαναδοκιμάζεται, δεν ξαναειδοποιεί
    tx = await _tx_doc("cs_fail")
    assert tx["apply_result"] == "failed" and tx["apply_pending"] is False and tx["apply_attempts"] == 1
    notes = await db.notifications.find({"type": "card_payment_unapplied"}).to_list(10)
    assert len(notes) == 1 and notes[0]["requires_action"] is True and "η βάση δεν απάντησε" in notes[0]["message"]
    assert (await _invoice_doc(inv))["status"] == "issued"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["underpaid", "overpaid", "not_open", "failed"])
async def test_every_payment_alert_points_at_the_invoice_and_asks_for_action(monkeypatch, kind):
    """Η ειδοποίηση είναι ο μόνος τρόπος να μάθει ο άνθρωπος ότι χρήματα εισπράχθηκαν χωρίς να κλείσει κανονικά ένα
    τιμολόγιο: πρέπει να είναι «critical», να απαιτεί ενέργεια και να οδηγεί στο ΣΥΓΚΕΚΡΙΜΕΝΟ τιμολόγιο."""
    import invoice_services
    import routes_payments
    amount = 124.0
    if kind == "underpaid":
        inv, amount = await _invoice(), 100.0
    elif kind == "overpaid":
        inv = await _invoice(trust_applied=100.0)
    elif kind == "not_open":
        inv = await _invoice(status="cancelled")
    else:
        inv = await _invoice()

        async def boom(*a, **k):
            raise RuntimeError("η βάση δεν απάντησε")
        monkeypatch.setattr(invoice_services, "mark_paid", boom)
    await _tx("cs_alert", inv, amount=amount)
    await routes_payments._mark_paid("cs_alert")
    notes = await db.notifications.find({}).to_list(10)
    assert len(notes) == 1
    note = notes[0]
    assert note["type"] == ("card_payment_overpaid" if kind == "overpaid" else "card_payment_unapplied")
    assert note["entity"] == "invoice" and note["entity_id"] == str(inv) and note["firm_id"] == FIRM
    assert note["level"] == "critical" and note["requires_action"] is True
    assert note["dedupe_key"] == f"{note['type']}:cs_alert" and "cs_alert" in note["message"]


# =========================== 5. Η εφαρμογή δεν βλέπει τιμολόγια ούτε ρυθμίσεις άλλου γραφείου ====

@pytest.mark.asyncio
async def test_a_payment_never_touches_nor_describes_an_invoice_of_another_firm():
    """Συναλλαγή του γραφείου FIRM με `invoice_id` τιμολογίου του OTHER (χαλασμένο/παραποιημένο έγγραφο): το τιμολόγιο του
    OTHER μένει ανέγγιχτο και ο αριθμός του δεν διαρρέει στην ειδοποίηση του FIRM."""
    import routes_payments
    foreign = await _invoice(firm=OTHER, number="ΤΠΥ-ΞΕΝΟ-9999")
    await _tx("cs_foreign", foreign, firm=FIRM)
    await routes_payments._mark_paid("cs_foreign")
    assert (await _invoice_doc(foreign))["status"] == "issued"
    assert (await _tx_doc("cs_foreign"))["apply_result"] == "not_open"
    notes = await db.notifications.find({}).to_list(10)
    assert len(notes) == 1 and notes[0]["firm_id"] == FIRM
    assert "ΤΠΥ-ΞΕΝΟ-9999" not in notes[0]["message"] and "«άγνωστη»" in notes[0]["message"]


async def _two_offices():
    """Το γραφείο OTHER κρατά παρακράτηση 20%, το FIRM όχι. Τα έγγραφα ρυθμίσεων μπαίνουν με αυτή τη σειρά ώστε
    οποιοδήποτε ερώτημα χωρίς φίλτρο γραφείου να βρίσκει πρώτα τις ρυθμίσεις του OTHER. Επιστρέφει (τιμολόγιο του
    OTHER, τιμολόγιο του FIRM), και τα δύο 1.240 € με καθαρή αξία 1.000 €."""
    await db.settings.insert_one({"firm_id": OTHER, "withholding_enabled": True})
    await db.settings.insert_one({"firm_id": FIRM, "withholding_enabled": False})
    with_hold = await _invoice(firm=OTHER, number="ΤΠΥ-A", total=1240.0, subtotal=1000.0, invoice_type="2.1")
    without = await _invoice(firm=FIRM, number="ΤΠΥ-B", total=1240.0, subtotal=1000.0, invoice_type="2.1")
    return with_hold, without


@pytest.mark.asyncio
async def test_the_payment_is_judged_with_the_withholding_rules_of_its_own_firm():
    import routes_payments
    with_hold, without = await _two_offices()
    await _tx("cs_mine", without, amount=1240.0, firm=FIRM)                  # το FIRM δεν κρατά: οφείλονται 1.240 €
    await _tx("cs_theirs", with_hold, amount=1040.0, firm=OTHER)             # το OTHER κρατά 200 €: οφείλονται 1.040 €
    await routes_payments._mark_paid("cs_mine")
    await routes_payments._mark_paid("cs_theirs")
    assert (await _tx_doc("cs_mine"))["apply_result"] == "settled"
    assert (await _tx_doc("cs_theirs"))["apply_result"] == "settled"
    assert (await _invoice_doc(without))["status"] == "paid" and (await _invoice_doc(with_hold))["status"] == "paid"
    assert await db.notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_the_amount_charged_at_checkout_uses_the_withholding_rules_of_the_invoice_firm():
    import routes_payments
    with_hold, without = await _two_offices()
    _inv, amount = await routes_payments._validate_invoice_and_get_amount(str(without), FIRM)
    assert amount == 1240.0
    _inv, amount = await routes_payments._validate_invoice_and_get_amount(str(with_hold), OTHER)
    assert amount == 1040.0


@pytest.mark.asyncio
async def test_a_fully_paid_invoice_cannot_be_checked_out():
    import routes_payments
    inv = await _invoice(trust_applied=124.0)
    with pytest.raises(HTTPException) as exc:
        await routes_payments._validate_invoice_and_get_amount(str(inv), FIRM)
    assert exc.value.status_code == 400 and "Μη έγκυρο ποσό" in exc.value.detail


# ======================================================= 6. Webhook: υπογεγραμμένα γεγονότα ====

SECRET = "whsec_r5c_secret"


def _signed(payload: bytes, key: bytes) -> dict:
    stamp = str(int(time.time()))
    digest = hmac.new(key, f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"stripe-signature": f"t={stamp},v1={digest}"}


def _event(kind: str, session_id: str, **fields) -> bytes:
    return json.dumps({"id": "evt_r5c", "object": "event", "type": kind,
                       "data": {"object": {"id": session_id, **fields}}}).encode()


@pytest.fixture()
def webhook(client, monkeypatch):
    import routes_payments
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", SECRET)

    def post(payload: bytes, key: bytes = SECRET.encode()):
        return client.post("/api/stripe/webhook", content=payload, headers=_signed(payload, key))
    return post


@pytest.mark.parametrize("body", [b"this is not json", b"\xff\xfe\x00\x01", b"", b"[1, 2"])
def test_a_correctly_signed_body_that_cannot_be_read_is_refused_so_that_stripe_retries(webhook, body):
    """Απάντηση 200 σε σώμα που δεν διαβάστηκε σημαίνει ότι το Stripe δεν θα το ξαναστείλει ποτέ: η πληρωμή χάνεται."""
    r = webhook(body)
    assert r.status_code == 400 and r.json()["detail"].startswith("Webhook error")


def test_a_wrongly_signed_event_is_refused_with_the_signature_error(webhook):
    payload = _event("checkout.session.completed", "cs_x", payment_status="paid")
    r = webhook(payload, key=b"whsec_somebody_else")
    assert r.status_code == 400 and r.json()["detail"] == "Invalid signature"


def test_a_session_that_needed_no_payment_closes_the_invoice(webhook):
    """Πλήρης έκπτωση / μηδενικό ποσό: το Stripe δηλώνει `no_payment_required` και δεν θα στείλει άλλο γεγονός."""
    inv = _run(_invoice())
    _run(_tx("cs_nopay", inv))
    assert webhook(_event("checkout.session.completed", "cs_nopay", payment_status="no_payment_required")).status_code == 200
    assert _run(_invoice_doc(inv))["status"] == "paid" and _run(_tx_doc("cs_nopay"))["payment_status"] == "paid"


def test_a_completed_session_whose_money_has_not_arrived_waits(webhook):
    inv = _run(_invoice())
    _run(_tx("cs_wait", inv))
    assert webhook(_event("checkout.session.completed", "cs_wait", payment_status="unpaid")).status_code == 200
    assert _run(_invoice_doc(inv))["status"] == "issued" and _run(_tx_doc("cs_wait"))["payment_status"] == "pending"


@pytest.mark.parametrize("kind", ["checkout.session.completed", "checkout.session.async_payment_succeeded"])
def test_the_payment_intent_of_the_event_is_kept_for_refunds(webhook, kind):
    inv = _run(_invoice())
    _run(_tx("cs_intent", inv))
    assert webhook(_event(kind, "cs_intent", payment_status="paid", payment_intent="pi_from_webhook")).status_code == 200
    tx = _run(_tx_doc("cs_intent"))
    assert tx["stripe_payment_intent_id"] == "pi_from_webhook" and tx["payment_status"] == "paid"
    assert _run(_invoice_doc(inv))["paid_ref"] == "cs_intent"


def test_failed_and_expired_sessions_are_recorded_as_such_and_only_for_their_own_transaction(webhook):
    invoices = [_run(_invoice()) for _ in range(3)]
    for session, inv in zip(("cs_failed", "cs_expired", "cs_bystander"), invoices):
        _run(_tx(session, inv))
    assert webhook(_event("checkout.session.async_payment_failed", "cs_failed")).status_code == 200
    assert webhook(_event("checkout.session.expired", "cs_expired")).status_code == 200
    failed, expired, bystander = (_run(_tx_doc(s)) for s in ("cs_failed", "cs_expired", "cs_bystander"))
    assert (failed["status"], failed["payment_status"]) == ("failed", "failed")
    assert (expired["status"], expired["payment_status"]) == ("expired", "expired")
    assert (bystander["status"], bystander["payment_status"]) == ("initiated", "pending")
    assert all(_run(_invoice_doc(i))["status"] == "issued" for i in invoices)


# ================================================== 7. Η λίστα συναλλαγών του γραφείου ====

def test_the_transactions_list_shows_only_my_office_newest_first_and_never_more_than_200(client):
    r = client.post("/api/auth/register", json={"email": "list@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    mine = _run(db.users.find_one({"email": "list@example.gr"}))["firm_id"]
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)

    def doc(n, firm):
        return {"session_id": f"cs_{firm[:4]}_{n:03d}", "firm_id": firm, "invoice_id": "", "amount": 1.0,
                "status": "initiated", "payment_status": "pending", "created_at": (base + timedelta(minutes=n)).isoformat()}

    # 201 δικές μου συναλλαγές (0 = η παλαιότερη) και 3 ΝΕΟΤΕΡΕΣ ξένες: η λίστα έχει όριο 200 και δεν τις περιέχει.
    _run(db.payment_transactions.insert_many([doc(n, mine) for n in range(201)] + [doc(1000 + n, OTHER) for n in range(3)]))
    listed = client.get("/api/payments/transactions")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 200
    assert {row["firm_id"] for row in rows} == {mine}
    stamps = [row["created_at"] for row in rows]
    assert stamps == sorted(stamps, reverse=True), "οι πιο πρόσφατες πρώτα"
    sessions = [row["session_id"] for row in rows]
    assert sessions[0].endswith("_200") and sessions[-1].endswith("_001")      # η παλαιότερη (000) κόβεται, όχι η νεότερη


# ============================ 8. Η εργασία card_payments του Autopilot και οι ειδοποιήσεις ====

@pytest.mark.asyncio
async def test_the_job_counts_only_payments_it_really_applied():
    """Μια πληρωμή που την εφαρμόζει εκείνη τη στιγμή άλλη διεργασία (ζωντανό κλείδωμα) δεν είναι «εφαρμοσμένη» από την
    εργασία: το στατιστικό που φαίνεται στο ιστορικό του Autopilot δεν πρέπει να ψεύδεται."""
    busy, free = await _invoice(), await _invoice()
    await _stuck_tx("cs_busy", busy, apply_claim="someone-else", apply_claim_at=_ago(seconds=10), apply_attempts=1)
    await _stuck_tx("cs_free", free)
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["status"] == "ok"
    assert {k: result["stats"][k] for k in ("pending", "applied", "legacy_reported")} == {"pending": 2, "applied": 1, "legacy_reported": 0}
    assert result["stats"]["polled"] == 0           # χωρίς κλειδιά Stripe ο Autopilot δεν ρωτά τίποτα
    assert (await _invoice_doc(busy))["status"] == "issued" and (await _invoice_doc(free))["status"] == "paid"
    busy_tx = await _tx_doc("cs_busy")
    assert busy_tx["apply_pending"] is True and busy_tx["apply_claim"] == "someone-else" and busy_tx["apply_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", ["", "not-an-object-id", "12345"])
async def test_an_old_payment_with_a_broken_invoice_id_does_not_break_the_job(bad_id):
    """Η εργασία τρέχει κάθε κύκλο: ένα χαλασμένο `invoice_id` δεν πρέπει να τη ρίχνει για πάντα (και να κρύβει όλες τις
    άλλες κολλημένες πληρωμές του γραφείου)."""
    await _tx("cs_broken", bad_id, firm=FIRM, status="completed", payment_status="paid", paid_at=_ago(days=3))
    real = await _invoice()
    await _tx("cs_real_old", real, status="completed", payment_status="paid", paid_at=_ago(days=3))
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["status"] == "ok" and result["stats"]["legacy_reported"] == 1
    assert (await _tx_doc("cs_broken"))["apply_result"] == "legacy_checked"
    assert (await _tx_doc("cs_real_old"))["apply_result"] == "legacy_reported"
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_unapplied:cs_broken"}) == 0


@pytest.mark.asyncio
async def test_an_old_payment_pointing_at_an_invoice_of_another_firm_raises_no_alert():
    foreign = await _invoice(firm=OTHER, number="ΤΠΥ-ΞΕΝΟ-1")
    await _tx("cs_old_foreign", foreign, firm=FIRM, status="completed", payment_status="paid", paid_at=_ago(days=3))
    result = (await run_firm(FIRM, ["card_payments"]))[0]
    assert result["stats"]["legacy_reported"] == 0
    assert await db.notifications.count_documents({}) == 0
    assert (await _tx_doc("cs_old_foreign"))["apply_result"] == "legacy_checked"


@pytest.mark.asyncio
async def test_the_same_alert_key_is_independent_for_each_firm_and_is_deduplicated_within_one():
    """Δύο γραφεία μπορούν να έχουν τον ίδιο κωδικό ειδοποίησης (π.χ. ίδιο session id σε δοκιμαστικό περιβάλλον): η
    ειδοποίηση του ενός δεν πρέπει να «καταπίνει» την ίδια του άλλου."""
    first = await notify(FIRM, "card_payment_unapplied", "critical", "Α", "μήνυμα", requires_action=True, dedupe_key="k:cs_same")
    second = await notify(OTHER, "card_payment_unapplied", "critical", "Β", "μήνυμα", requires_action=True, dedupe_key="k:cs_same")
    assert first and second and first["firm_id"] == FIRM and second["firm_id"] == OTHER
    assert await notify(FIRM, "card_payment_unapplied", "critical", "Α", "ξανά", dedupe_key="k:cs_same") is None
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 1 and await db.notifications.count_documents({"firm_id": OTHER}) == 1

    await db.notifications.update_many({"firm_id": FIRM}, {"$set": {"resolved_at": _ago(minutes=1)}})
    again = await notify(FIRM, "card_payment_unapplied", "critical", "Α", "μετά το κλείσιμο", dedupe_key="k:cs_same")
    assert again and await db.notifications.count_documents({"firm_id": FIRM}) == 2
    assert await db.notifications.count_documents({"firm_id": OTHER}) == 1
