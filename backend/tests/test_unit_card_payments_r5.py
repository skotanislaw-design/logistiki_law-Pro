"""Έλεγχος γύρου 5 — Πακέτο 2: το παράθυρο κατάρρευσης στην πληρωμή με κάρτα.

Η πληρωμή καταγραφόταν πρώτα («paid» στη συναλλαγή) και εφαρμοζόταν μετά στο τιμολόγιο. Αν η διεργασία
έπεφτε ανάμεσα στα δύο βήματα (επανεκκίνηση, διακοπή αιτήματος), τα χρήματα είχαν εισπραχθεί, το τιμολόγιο
έμενε ανοιχτό και **κανείς δεν ειδοποιούνταν**: κάθε επόμενη κλήση (webhook που ξαναστέλνεται, polling) έβλεπε
τη συναλλαγή ήδη «paid» και δεν έκανε τίποτα.

Τώρα η συναλλαγή κρατά δικό της δείκτη «εκκρεμεί εφαρμογή» (`apply_pending`), που σβήνει μόνο όταν η εφαρμογή
ολοκληρωθεί (ή ειδοποιηθεί άνθρωπος). Κάθε κλήση, και ο Autopilot, τελειώνει ό,τι έμεινε στη μέση.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from automation import JOB_FUNCS, JOB_LABELS, JOBS, run_firm
from db import db

FIRM = "firm-card-r5"
OTHER = "firm-card-r5-other"


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
    """Συναλλαγή που καταγράφηκε ως εισπραγμένη και δεν εφαρμόστηκε ποτέ."""
    await _tx(session, invoice_id, firm=firm, status="completed", payment_status="paid", apply_pending=True,
              paid_at=over.pop("paid_at", _ago(minutes=30)), **over)


async def _invoice_doc(invoice_id):
    return await db.invoices.find_one({"_id": invoice_id})


async def _tx_doc(session):
    return await db.payment_transactions.find_one({"session_id": session})


# ===================================================== 1. Κατάρρευση ανάμεσα στα δύο βήματα ====

@pytest.mark.asyncio
async def test_a_crash_between_recording_and_applying_is_finished_by_the_next_call(monkeypatch):
    import routes_payments
    inv = await _invoice()
    await _tx("cs_crash", inv)
    real = routes_payments._apply_card_payment

    async def crash(tx):
        raise asyncio.CancelledError()            # η διεργασία/το αίτημα σταματά ακριβώς εδώ

    monkeypatch.setattr(routes_payments, "_apply_card_payment", crash)
    with pytest.raises(asyncio.CancelledError):
        await routes_payments._mark_paid("cs_crash", "pi_1")
    tx = await _tx_doc("cs_crash")
    assert tx["payment_status"] == "paid" and tx["apply_pending"] is True and "apply_claim" not in tx
    assert (await _invoice_doc(inv))["status"] == "issued"          # τα χρήματα εισπράχθηκαν, το τιμολόγιο μένει ανοιχτό

    monkeypatch.setattr(routes_payments, "_apply_card_payment", real)
    await routes_payments._mark_paid("cs_crash", "pi_1")            # το webhook ξαναστέλνεται
    paid = await _invoice_doc(inv)
    assert paid["status"] == "paid" and paid["paid_via"] == "stripe" and paid["paid_ref"] == "cs_crash"
    tx = await _tx_doc("cs_crash")
    assert tx["apply_pending"] is False and tx["apply_result"] == "settled" and tx["applied_at"]
    assert "apply_claim" not in tx and await db.notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_a_process_that_died_without_cleanup_is_covered_once_its_claim_expires():
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_claim", inv, apply_claim="dead-worker", apply_claim_at=_ago(seconds=10), apply_attempts=1)
    await routes_payments._mark_paid("cs_claim")
    assert (await _invoice_doc(inv))["status"] == "issued"           # κάποιος την εφαρμόζει αυτή τη στιγμή

    await db.payment_transactions.update_one(
        {"session_id": "cs_claim"},
        {"$set": {"apply_claim_at": _ago(seconds=routes_payments.APPLY_CLAIM_TTL_SECONDS + 5)}})
    await routes_payments._mark_paid("cs_claim")                    # το κλείδωμα έληξε: η πληρωμή εφαρμόζεται
    assert (await _invoice_doc(inv))["status"] == "paid"
    assert (await _tx_doc("cs_claim"))["apply_attempts"] == 2


@pytest.mark.asyncio
async def test_a_payment_closed_by_a_crashed_attempt_is_not_reported_as_a_problem():
    """Η πρώτη προσπάθεια έκλεισε το τιμολόγιο και έπεσε πριν γράψει το αποτέλεσμα: η επανάληψη δεν βλέπει «άλλο πρόβλημα»."""
    import routes_payments
    inv = await _invoice(status="paid", paid_via="stripe", paid_ref="cs_done", paid_at=_ago(minutes=20))
    await _stuck_tx("cs_done", inv, apply_claim="dead-worker", apply_claim_at=_ago(minutes=20), apply_attempts=1)
    await routes_payments._mark_paid("cs_done")
    assert await db.notifications.count_documents({}) == 0
    tx = await _tx_doc("cs_done")
    assert tx["apply_pending"] is False and tx["apply_result"] == "settled"


@pytest.mark.asyncio
async def test_a_second_payment_for_an_invoice_another_session_already_paid_is_still_reported():
    """Ο πελάτης άνοιξε δύο συνεδρίες και πλήρωσε και τις δύο: η δεύτερη είναι διπλοπληρωμή, όχι «ήδη εφαρμοσμένη»."""
    import routes_payments
    inv = await _invoice(status="paid", paid_via="stripe", paid_ref="cs_first")
    await _tx("cs_second", inv)
    await routes_payments._mark_paid("cs_second")
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_second"})
    assert note and note["level"] == "critical" and "δεν ήταν ανοιχτό" in note["title"]


@pytest.mark.asyncio
async def test_an_overpayment_missed_by_a_crashed_attempt_is_reported_on_the_retry():
    import routes_payments
    inv = await _invoice(status="paid", paid_via="stripe", paid_ref="cs_over", trust_applied=100.0)
    await _stuck_tx("cs_over", inv, amount=124.0, apply_claim="dead-worker", apply_claim_at=_ago(minutes=20))
    await routes_payments._mark_paid("cs_over")
    note = await db.notifications.find_one({"type": "card_payment_overpaid"})
    assert note and note["requires_action"] and "100,00 €" in note["message"]
    assert (await _tx_doc("cs_over"))["apply_result"] == "overpaid"


@pytest.mark.asyncio
async def test_two_simultaneous_callbacks_apply_the_payment_once(monkeypatch):
    import invoice_services
    import routes_payments
    inv = await _invoice()
    await _tx("cs_race", inv)
    calls = []
    real = invoice_services.mark_paid

    async def spy(*a, **k):
        calls.append(k)
        return await real(*a, **k)

    class Interleaved:
        def __init__(self, real_db):
            self._db = real_db

        def __getattr__(self, name):
            attr = getattr(self._db, name)
            if name != "payment_transactions":
                return attr

            class Collection:
                def __getattr__(_self, method):
                    target = getattr(attr, method)
                    if method != "find_one_and_update":
                        return target

                    async def slow(*a, **k):
                        await asyncio.sleep(0)             # ταυτόχρονα αιτήματα αλληλοδιαπλέκονται σε κάθε βήμα
                        return await target(*a, **k)
                    return slow
            return Collection()

    monkeypatch.setattr(invoice_services, "mark_paid", spy)
    monkeypatch.setattr(routes_payments, "db", Interleaved(db))
    await asyncio.gather(routes_payments._mark_paid("cs_race"), routes_payments._mark_paid("cs_race"))
    monkeypatch.setattr(routes_payments, "db", db)
    assert len(calls) == 1
    assert (await _invoice_doc(inv))["status"] == "paid" and await db.notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_after_too_many_attempts_a_human_is_told_and_the_retries_stop():
    import routes_payments
    inv = await _invoice()
    await _stuck_tx("cs_stuck", inv, apply_attempts=routes_payments.APPLY_MAX_ATTEMPTS,
                    apply_claim="dead-worker", apply_claim_at=_ago(minutes=20))
    await routes_payments._mark_paid("cs_stuck")
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_stuck"})
    assert note and note["level"] == "critical" and note["requires_action"] is True
    assert str(routes_payments.APPLY_MAX_ATTEMPTS) in note["message"]
    tx = await _tx_doc("cs_stuck")
    assert tx["apply_pending"] is False and tx["apply_result"] == "given_up"
    assert (await _invoice_doc(inv))["status"] == "issued"          # δεν εφαρμόστηκε τίποτα σιωπηλά


@pytest.mark.asyncio
async def test_a_payment_recorded_before_this_change_is_never_applied_again():
    """Οι παλιές συναλλαγές δεν έχουν δείκτη εκκρεμότητας: τις είχε ήδη επεξεργαστεί ο παλιός κώδικας."""
    import routes_payments
    inv = await _invoice(status="paid", paid_via="stripe")
    await _tx("cs_legacy", inv, status="completed", payment_status="paid")
    await routes_payments._mark_paid("cs_legacy")
    assert await db.notifications.count_documents({}) == 0
    assert "applied_at" not in await _tx_doc("cs_legacy")


# ============================================================== 2. Επανάληψη από polling και Autopilot ====

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def test_status_polling_finishes_an_unapplied_payment(client):
    loop = asyncio.get_event_loop_policy().new_event_loop()
    inv = loop.run_until_complete(_invoice())
    loop.run_until_complete(_stuck_tx("cs_poll", inv))
    r = client.get("/api/payments/status/cs_poll")
    assert r.status_code == 200 and r.json()["payment_status"] == "paid"
    assert loop.run_until_complete(_invoice_doc(inv))["status"] == "paid"


def test_the_card_payments_job_is_registered():
    assert "card_payments" in JOBS and "card_payments" in JOB_FUNCS and JOB_LABELS["card_payments"]


@pytest.mark.asyncio
async def test_the_job_finishes_old_unapplied_payments_and_leaves_everything_else_alone():
    old_inv, fresh_inv, legacy_inv, other_inv = await _invoice(), await _invoice(), await _invoice(), await _invoice(firm=OTHER)
    await _stuck_tx("cs_old", old_inv, paid_at=_ago(minutes=30))
    await _stuck_tx("cs_fresh", fresh_inv, paid_at=_ago(seconds=30))            # η ζωντανή κλήση ίσως δουλεύει ακόμη
    await _tx("cs_legacy", legacy_inv, status="completed", payment_status="paid")
    await _stuck_tx("cs_other", other_inv, firm=OTHER, paid_at=_ago(minutes=30))

    r = (await run_firm(FIRM, ["card_payments"]))[0]
    # Η πληρωμή της παλιάς έκδοσης δεν εφαρμόζεται ποτέ αυτόματα· αναφέρεται μία φορά (βλ. test_unit_card_payments_r5b).
    # Τα polled/recovered/expired/poll_errors είναι του polling του Stripe (βλ. test_unit_card_poll_r5)· εδώ δεν υπάρχει
    # εκκρεμής συνεδρία, άρα μένουν στο μηδέν.
    assert r["status"] == "ok" and r["stats"] == {"pending": 1, "applied": 1, "legacy_reported": 1,
                                                  "polled": 0, "recovered": 0, "expired": 0, "poll_errors": 0}
    assert (await _invoice_doc(old_inv))["status"] == "paid"
    assert (await _invoice_doc(fresh_inv))["status"] == "issued"
    assert (await _invoice_doc(legacy_inv))["status"] == "issued"
    assert (await _invoice_doc(other_inv))["status"] == "issued"
    assert await db.notifications.count_documents({"dedupe_key": "card_payment_unapplied:cs_legacy"}) == 1

    again = (await run_firm(FIRM, ["card_payments"]))[0]
    assert again["stats"] == {"pending": 0, "applied": 0, "legacy_reported": 0,
                              "polled": 0, "recovered": 0, "expired": 0, "poll_errors": 0}


@pytest.mark.asyncio
async def test_mark_paid_records_the_reference_only_when_it_is_given():
    from invoice_services import mark_paid
    plain, referenced = await _invoice(), await _invoice()
    assert await mark_paid(FIRM, plain) is True
    assert await mark_paid(FIRM, referenced, paid_via="stripe", ref="cs_ref") is True
    assert "paid_ref" not in await _invoice_doc(plain)
    assert (await _invoice_doc(referenced))["paid_ref"] == "cs_ref"
