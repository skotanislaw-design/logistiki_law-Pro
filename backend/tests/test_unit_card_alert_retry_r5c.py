"""ΔΙΟΡΘΩΘΗΚΕ (εύρημα του ελέγχου μεταλλάξεων) (χαμηλής σοβαρότητας) — μία παροδική αποτυχία της ειδοποίησης δεν αφήνει την πληρωμή «εκκρεμή».

Το σχόλιο της `_apply_card_payment.alert` (και το test R5A-07 του `test_unit_card_payments_r5b`) υπόσχεται: «Αν [η ειδοποίηση]
αποτύχει, το σφάλμα φεύγει προς τα πάνω, η εφαρμογή μένει εκκρεμής (`apply_pending`) και ξαναδοκιμάζεται — αλλιώς μια πληρωμή που
χρειάζεται άνθρωπο θα έκλεινε χωρίς να το μάθει κανείς».

Το υπάρχον test κάνει την ειδοποίηση να αποτυγχάνει ΠΑΝΤΑ, οπότε το σφάλμα φεύγει. Αν όμως αποτύχει **μία φορά** (παροδική
διακοπή της βάσης), το σφάλμα πιάνεται από το γενικό `except Exception` της ίδιας συνάρτησης, που γράφει δεύτερη, γενική
ειδοποίηση («η εφαρμογή απέτυχε: …»), σημαίνει το αποτέλεσμα `failed` και ΚΛΕΙΝΕΙ την εκκρεμότητα:

* η συγκεκριμένη ειδοποίηση (μικρότερη πληρωμή / ακυρωμένο τιμολόγιο / **υπερπληρωμή με το ποσό της επιστροφής**) δεν γράφεται ποτέ·
* στην υπερπληρωμή το τιμολόγιο έχει ήδη εξοφληθεί, ενώ η γενική ειδοποίηση λέει ότι «η εφαρμογή απέτυχε» — και το ποσό της
  διαφοράς που πρέπει να επιστραφεί στον πελάτη δεν αναφέρεται πουθενά.

Η ιδιότητα «μία ορατή ειδοποίηση» τηρείται, αλλά με λάθος περιεχόμενο· η συμπεριφορά αντιβαίνει στο ρητό σχόλιο του κώδικα.
Αναμενόμενο: το σφάλμα φεύγει προς τα πάνω, η πληρωμή μένει εκκρεμής, και η επόμενη κλήση γράφει τη ΣΥΓΚΕΚΡΙΜΕΝΗ ειδοποίηση.
(Διόρθωση: το `alert(...)` να μη βρίσκεται μέσα στο `try` που μετατρέπει κάθε σφάλμα σε «failed».)

"""
import pytest

from db import db

FIRM = "firm-bug-card-alert"


async def _invoice(**over):
    return (await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "status": "issued", "total": 124.0, **over})).inserted_id


async def _tx(session, invoice_id, amount):
    await db.payment_transactions.insert_one({"session_id": session, "firm_id": FIRM, "invoice_id": str(invoice_id), "amount": amount,
                                              "status": "initiated", "payment_status": "pending"})


def _fail_once(monkeypatch):
    import automation
    real = automation.notify
    calls = []

    async def flaky(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            raise RuntimeError("σύντομη διακοπή της βάσης")
        return await real(*args, **kwargs)

    monkeypatch.setattr(automation, "notify", flaky)
    return real, calls


@pytest.mark.asyncio
async def test_a_single_failure_to_write_the_underpayment_alert_keeps_the_payment_pending_and_the_retry_writes_the_right_alert(monkeypatch):
    import automation
    import routes_payments
    inv = await _invoice()
    await _tx("cs_once", inv, 100.0)                                            # μικρότερη από το οφειλόμενο → ειδοποίηση
    real, _calls = _fail_once(monkeypatch)
    with pytest.raises(RuntimeError):
        await routes_payments._mark_paid("cs_once")                              # σήμερα: ΔΕΝ σηκώνει εξαίρεση (γράφει «failed»)
    tx = await db.payment_transactions.find_one({"session_id": "cs_once"})
    assert tx["apply_pending"] is True and "apply_result" not in tx

    monkeypatch.setattr(automation, "notify", real)
    await routes_payments._mark_paid("cs_once")
    tx = await db.payment_transactions.find_one({"session_id": "cs_once"})
    assert tx["apply_result"] == "underpaid"
    notes = await db.notifications.find({}).to_list(10)
    assert len(notes) == 1 and "μικρότερη από το οφειλόμενο" in notes[0]["title"]


@pytest.mark.asyncio
async def test_a_single_failure_to_write_the_overpayment_alert_never_loses_the_refund_amount(monkeypatch):
    import automation
    import routes_payments
    inv = await _invoice(trust_applied=100.0)                                   # οφείλονται 24 €, ο πελάτης πλήρωσε 124 €
    await _tx("cs_over_once", inv, 124.0)
    real, _calls = _fail_once(monkeypatch)
    try:
        await routes_payments._mark_paid("cs_over_once")
    except RuntimeError:
        pass                                                                    # επιτρεπτό: η επόμενη κλήση το ξαναδοκιμάζει
    monkeypatch.setattr(automation, "notify", real)
    await routes_payments._mark_paid("cs_over_once")
    notes = await db.notifications.find({}).to_list(10)
    assert [n["type"] for n in notes] == ["card_payment_overpaid"], [n["title"] for n in notes]
    assert "100,00 €" in notes[0]["message"]                                    # η διαφορά που πρέπει να επιστραφεί
    tx = await db.payment_transactions.find_one({"session_id": "cs_over_once"})
    assert tx["apply_result"] == "overpaid"
