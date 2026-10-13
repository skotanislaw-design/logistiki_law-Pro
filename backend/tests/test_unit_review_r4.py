"""Έλεγχος γύρου 4 — διορθώσεις μετά την ανεξάρτητη επανεξέταση (3 reviewers).

Η επανεξέταση βρήκε, με εκτέλεση κώδικα, ότι τα πρώτα commits του γύρου είχαν ακόμη:

* σύνδεσμο πληρωμής Stripe στις υπενθυμίσεις που χρέωνε το πληρωτέο και όχι το οφειλόμενο
  (ο πελάτης έβλεπε στο email 24 € και πλήρωνε 124 €), χωρίς καμία ειδοποίηση·
* αριθμό τιμολογίου που δινόταν ΠΡΙΝ το ατομικό «κλείδωμα» της έκδοσης — ένα διπλό κλικ άφηνε κενό στη σειρά·
* φρουρούς κατάστασης που έλειπαν (παράβολα, επεξεργασία πρόχειρου, myDATA, ακύρωση)·
* πελατολογικό που μπορούσε να χρεώσει χρήματα του πελάτη χωρίς να κλείσει το τιμολόγιο, χωρίς ίχνος.

Κάθε test εδώ αντιστοιχεί σε ευρημα που ο έλεγχος αναπαρήγαγε.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tax_utils import athens_today


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register",
                    json={"email": "review@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "review@example.gr"}))
    _run(db.users.update_one({"email": "review@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    beta = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "987654324", "client_type": "company",
                                             "email": "beta@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, beta=beta, firm_id=user["firm_id"], user={"email": "review@example.gr",
                                                                                   "firm_id": user["firm_id"]})


def _today() -> str:
    return athens_today().isoformat()


def _draft(client, client_id, net=100.0, when=None, **over):
    body = {"client_id": client_id, "date": when or _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _issued(client, client_id, net=100.0, **over):
    out = client.post(f"/api/invoices/{_draft(client, client_id, net, **over)['id']}/issue")
    assert out.status_code == 200, out.text
    return out.json()


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


def _set(invoice_id, **fields):
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": fields}))


def _settings(firm_id):
    from db import db
    return _run(db.settings.find_one({"firm_id": firm_id})) or {}


def _notes(type_):
    from db import db
    return _run(db.notifications.find({"type": type_}).to_list(50))


def _enable_withholding(client):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text


# =============================================== 1. Stripe και πληρωμή κάρτας ====

@pytest.fixture()
def stripe_calls(monkeypatch):
    """Ψεύτικο Stripe: καταγράφει τι ζητήθηκε να χρεωθεί."""
    import stripe
    created = []

    class Session:
        id = "cs_test_link"
        url = "https://pay.example/cs_test_link"

    def fake_create(**kwargs):
        created.append(kwargs)
        return Session()

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_realistic_key")
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")      # χωρίς διεύθυνση εφαρμογής δεν δημιουργείται σύνδεσμος
    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))
    return created


def _charged_cents(stripe_calls) -> int:
    return stripe_calls[-1]["line_items"][0]["price_data"]["unit_amount"]


def test_reminder_link_charges_what_the_email_says_is_owed(client, office, stripe_calls, monkeypatch):
    """Η αστοχία που βρήκαν και οι τρεις reviewers: email «Οφειλόμενο 640 €», κάρτα 1.040 €."""
    import invoice_services as svc
    _enable_withholding(client)
    inv = _issued(client, office.alpha, net=1000.0)             # σύνολο 1.240, παρακράτηση 200 → πληρωτέο 1.040
    _set(inv["id"], trust_applied=400.0)                        # 400 έχουν εξοφληθεί από πελατολογικό → οφείλονται 640

    sent = {}

    async def fake_send(settings, to, subject, text, html, **kwargs):
        sent["text"], sent["html"] = text, html

    monkeypatch.setattr(svc, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(svc, "smtp_ready", lambda s: True)
    settings = _settings(office.firm_id)
    client_doc = _run(svc.load_client(office.firm_id, _doc(inv["id"])))
    out = _run(svc.send_reminder(office.firm_id, _doc(inv["id"]), settings, client_doc))

    assert out["payment_link"] == "https://pay.example/cs_test_link"
    assert "640,00 €" in sent["text"]
    assert _charged_cents(stripe_calls) == 64000                # ακριβώς το ποσό του email
    from db import db
    tx = _run(db.payment_transactions.find_one({"session_id": "cs_test_link"}))
    assert tx["amount"] == 640.0


@pytest.mark.parametrize("withholding,applied,expected_cents", [
    (False, None, 12400),        # καμία παρακράτηση, κανένα πελατολογικό → το σύνολο (100 + ΦΠΑ)
    (False, 100.0, 2400),        # μόνο πελατολογικό: 124 − 100
    (True, None, 104000),        # μόνο παρακράτηση: 1.240 − 200 (καθαρή 1.000 ≥ όριο 300)
    (True, 400.0, 64000),        # και τα δύο: 1.240 − 200 − 400
])
def test_payment_link_amount_matrix(client, office, stripe_calls, withholding, applied, expected_cents):
    import invoice_services as svc
    net = 1000.0 if withholding else 100.0
    if withholding:
        _enable_withholding(client)
    inv = _issued(client, office.alpha, net=net)
    if applied:
        _set(inv["id"], trust_applied=applied)
    link = _run(svc.build_payment_link(_doc(inv["id"]), _settings(office.firm_id)))
    assert link
    assert _charged_cents(stripe_calls) == expected_cents


def test_payment_link_is_not_created_when_nothing_is_owed(client, office, stripe_calls):
    import invoice_services as svc
    inv = _issued(client, office.alpha)
    _set(inv["id"], trust_applied=124.0)
    assert _run(svc.build_payment_link(_doc(inv["id"]), _settings(office.firm_id))) == ""
    assert stripe_calls == []


@pytest.mark.asyncio
async def test_card_payment_that_does_not_match_the_amount_owed_is_reported():
    import routes_payments
    from db import db
    firm = "firm-card-amounts"

    async def pay(session, total, applied, paid):
        inv = await db.invoices.insert_one({"firm_id": firm, "number": f"ΤΠΥ-2026-{session}", "status": "issued",
                                            "total": total, **({"trust_applied": applied} if applied else {})})
        await db.payment_transactions.insert_one({
            "session_id": session, "firm_id": firm, "invoice_id": str(inv.inserted_id), "amount": paid,
            "status": "initiated", "payment_status": "unpaid"})
        await routes_payments._mark_paid(session)
        return await db.invoices.find_one({"_id": inv.inserted_id})

    exact = await pay("cs_exact", 124.0, 100.0, 24.0)
    assert exact["status"] == "paid" and exact["paid_via"] == "stripe"
    assert await db.notifications.count_documents({"firm_id": firm}) == 0

    # η συνεδρία άνοιξε πριν το πελατολογικό: πλήρωσε 124, ενώ πια οφείλονται 24 → εξοφλείται + επιστροφή
    over = await pay("cs_over", 124.0, 100.0, 124.0)
    assert over["status"] == "paid"
    note = await db.notifications.find_one({"type": "card_payment_overpaid"})
    assert note and note["requires_action"] and note["level"] == "critical" and "100,00 €" in note["message"]

    # πλήρωσε λιγότερα από όσα οφείλονται → ΔΕΝ είναι εξόφληση
    under = await pay("cs_under", 124.0, None, 100.0)
    assert under["status"] == "issued"
    note = await db.notifications.find_one({"type": "card_payment_unapplied", "dedupe_key": "card_payment_unapplied:cs_under"})
    assert note and note["requires_action"] and "μικρότερη" in note["title"]


@pytest.mark.asyncio
async def test_failure_while_applying_a_card_payment_is_not_swallowed(monkeypatch):
    import invoice_services
    import routes_payments
    from db import db
    firm = "firm-card-boom"
    inv = await db.invoices.insert_one({"firm_id": firm, "number": "ΤΠΥ-2026-0001", "status": "issued", "total": 124.0})
    await db.payment_transactions.insert_one({
        "session_id": "cs_boom", "firm_id": firm, "invoice_id": str(inv.inserted_id), "amount": 124.0,
        "status": "initiated", "payment_status": "unpaid"})

    async def boom(*a, **k):
        raise RuntimeError("η βάση δεν απάντησε")

    monkeypatch.setattr(invoice_services, "mark_paid", boom)
    await routes_payments._mark_paid("cs_boom")          # δεν πρέπει να σηκώσει εξαίρεση προς το webhook
    note = await db.notifications.find_one({"type": "card_payment_unapplied"})
    assert note and note["requires_action"] and "η βάση δεν απάντησε" in note["message"]
    assert (await db.invoices.find_one({"_id": inv.inserted_id}))["status"] == "issued"


@pytest.mark.asyncio
async def test_card_payment_for_an_invoice_that_is_no_longer_open_is_reported_and_changes_nothing():
    """Η πληρωμή δεν ξανανοίγει ακυρωμένο/εξοφλημένο τιμολόγιο: το γραφείο ειδοποιείται ότι εισπράχθηκαν χρήματα."""
    import routes_payments
    from db import db
    firm = "firm-card-closed"

    async def pay(session, invoice_id):
        await db.payment_transactions.insert_one({
            "session_id": session, "firm_id": firm, "invoice_id": invoice_id, "amount": 100.0,    # < οφειλόμενο 124
            "status": "initiated", "payment_status": "unpaid"})
        await routes_payments._mark_paid(session)

    for status in ("cancelled", "paid", "draft"):
        inserted = await db.invoices.insert_one({"firm_id": firm, "number": f"ΤΠΥ-2026-{status}", "status": status,
                                                 "total": 124.0})
        await pay(f"cs_{status}", str(inserted.inserted_id))
        assert (await db.invoices.find_one({"_id": inserted.inserted_id}))["status"] == status      # δεν αλλάζει
        note = await db.notifications.find_one({"dedupe_key": f"card_payment_unapplied:cs_{status}"})
        assert note and note["requires_action"] and note["level"] == "critical", status
        assert "δεν ήταν ανοιχτό" in note["title"] and f"«{status}»" in note["message"], status

    await pay("cs_missing", str(ObjectId()))                                                       # το τιμολόγιο διαγράφηκε
    note = await db.notifications.find_one({"dedupe_key": "card_payment_unapplied:cs_missing"})
    assert note and "δεν ήταν ανοιχτό" in note["title"] and "«άγνωστη»" in note["message"]


def _webhook(client, monkeypatch, event_type, session_id, payment_status="paid"):
    import routes_payments
    event = {"type": event_type, "data": {"object": {"id": session_id, "payment_intent": "pi_1",
                                                      "payment_status": payment_status}}}
    monkeypatch.setattr(routes_payments, "STRIPE_WEBHOOK_SECRET", "whsec_test")      # χωρίς μυστικό το webhook δίνει 503
    monkeypatch.setattr(routes_payments.stripe.Webhook, "construct_event",
                        staticmethod(lambda payload, sig, secret: event))
    # Το Stripe δηλώνει πάντα Content-Type· χωρίς αυτό, αίτημα με cookie συνεδρίας και σώμα απορρίπτεται με 415 από το CsrfGuard.
    return client.post("/api/stripe/webhook", content=b"{}",
                       headers={"stripe-signature": "t=1,v1=x", "content-type": "application/json"})


def test_delayed_payment_methods_close_the_invoice_only_when_the_money_arrives(client, office, monkeypatch):
    """Παλιότερα το `async_payment_succeeded` σημείωνε τη συναλλαγή «paid» πριν καλέσει την εφαρμογή
    της πληρωμής, οπότε η εφαρμογή δεν έτρεχε ποτέ και το τιμολόγιο έμενε ανοιχτό."""
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.payment_transactions.insert_one({
        "session_id": "cs_async", "firm_id": office.firm_id, "invoice_id": inv["id"], "amount": 124.0,
        "status": "initiated", "payment_status": "pending"}))

    # Η συνεδρία ολοκληρώθηκε αλλά τα χρήματα δεν έχουν φτάσει (SEPA/τραπεζική μεταφορά).
    assert _webhook(client, monkeypatch, "checkout.session.completed", "cs_async", "unpaid").status_code == 200
    assert _doc(inv["id"])["status"] == "issued"

    assert _webhook(client, monkeypatch, "checkout.session.async_payment_succeeded", "cs_async").status_code == 200
    paid = _doc(inv["id"])
    assert paid["status"] == "paid" and paid["paid_via"] == "stripe"

    # επανάληψη του ίδιου webhook: καμία αλλαγή, καμία ειδοποίηση
    assert _webhook(client, monkeypatch, "checkout.session.async_payment_succeeded", "cs_async").status_code == 200
    assert _notes("card_payment_unapplied") == []


def test_card_checkout_completed_with_paid_status_closes_the_invoice(client, office, monkeypatch):
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.payment_transactions.insert_one({
        "session_id": "cs_card", "firm_id": office.firm_id, "invoice_id": inv["id"], "amount": 124.0,
        "status": "initiated", "payment_status": "pending"}))
    assert _webhook(client, monkeypatch, "checkout.session.completed", "cs_card", "paid").status_code == 200
    assert _doc(inv["id"])["status"] == "paid"


def test_status_polling_does_not_treat_a_complete_but_unpaid_session_as_paid(client, office, monkeypatch):
    import routes_payments
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.payment_transactions.insert_one({
        "session_id": "cs_poll", "firm_id": office.firm_id, "invoice_id": inv["id"], "amount": 124.0,
        "status": "initiated", "payment_status": "pending"}))
    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    state = SimpleNamespace(payment_status="unpaid", status="complete", payment_intent="pi_1")
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "retrieve", staticmethod(lambda sid: state))
    assert client.get("/api/payments/status/cs_poll").json()["payment_status"] == "pending"
    assert _doc(inv["id"])["status"] == "issued"

    state.payment_status = "paid"
    assert client.get("/api/payments/status/cs_poll").json()["payment_status"] == "paid"
    assert _doc(inv["id"])["status"] == "paid"


def test_checkout_refuses_invoices_that_cannot_be_collected(client, office):
    from routes_payments import _validate_invoice_and_get_amount
    draft = _draft(client, office.alpha)
    cancelled = _issued(client, office.alpha)
    _set(cancelled["id"], status="cancelled")
    paid = _issued(client, office.alpha)
    _set(paid["id"], status="paid")
    owed = _issued(client, office.alpha)
    _set(owed["id"], trust_applied=100.0)
    for bad in (draft, cancelled, paid):
        with pytest.raises(HTTPException) as exc:
            _run(_validate_invoice_and_get_amount(bad["id"], office.firm_id))
        assert exc.value.status_code == 400, bad["id"]
    _inv, amount = _run(_validate_invoice_and_get_amount(owed["id"], office.firm_id))
    assert amount == 24.0                                       # 124 − 100 από πελατολογικό
    with pytest.raises(HTTPException) as exc:                   # άλλο γραφείο
        _run(_validate_invoice_and_get_amount(owed["id"], "firm-someone-else"))
    assert exc.value.status_code == 404
    _set(owed["id"], trust_applied=124.0)
    with pytest.raises(HTTPException) as exc:                   # τίποτα δεν οφείλεται
        _run(_validate_invoice_and_get_amount(owed["id"], office.firm_id))
    assert exc.value.status_code == 400


# ====================================================== 2. Έκδοση χωρίς κενά ====

def test_two_simultaneous_issues_of_one_draft_consume_exactly_one_number(monkeypatch):
    """Ο αριθμός δινόταν πριν το κλείδωμα: δύο ταυτόχρονα αιτήματα έπαιρναν 0001 και 0002, το ένα
    έχανε, και το 0001 χανόταν για πάντα (η επιστροφή αριθμού δουλεύει μόνο για τον τελευταίο)."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-double-click"
        inserted = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        stale = await db.invoices.find_one({"_id": inserted.inserted_id})

        real_next = svc.next_invoice_number

        async def slow_next(*a, **k):
            await asyncio.sleep(0.02)                  # αναγκάζει τα δύο αιτήματα να αλληλοδιαπλακούν
            return await real_next(*a, **k)

        monkeypatch.setattr(svc, "next_invoice_number", slow_next)
        results = await asyncio.gather(svc.issue_draft(firm, dict(stale), {}), svc.issue_draft(firm, dict(stale), {}),
                                       return_exceptions=True)
        winners = [r for r in results if isinstance(r, dict)]
        losers = [r for r in results if isinstance(r, svc.InvoiceWorkflowError)]
        assert len(winners) == 1 and len(losers) == 1, results
        assert winners[0]["number"].endswith("-0001")

        # κανένας αριθμός δεν «κάηκε»: το επόμενο πρόχειρο παίρνει το 0002
        other = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        nxt = await svc.issue_draft(firm, await db.invoices.find_one({"_id": other.inserted_id}), {})
        assert nxt["number"].endswith("-0002")
        # και το εκδοθέν δεν κρατά ίχνη του κλειδώματος
        assert "issue_claim" not in (await db.invoices.find_one({"_id": inserted.inserted_id}))

    _run(scenario())


def test_failed_issue_releases_the_claim_and_the_number(monkeypatch):
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-release"
        inserted = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        draft = await db.invoices.find_one({"_id": inserted.inserted_id})

        def boom(*a, **k):
            raise RuntimeError("σφάλμα μετά την παραχώρηση αριθμού")

        monkeypatch.setattr(svc, "withholding_for", boom)
        with pytest.raises(RuntimeError):
            await svc.issue_draft(firm, dict(draft), {})
        after = await db.invoices.find_one({"_id": inserted.inserted_id})
        assert after["status"] == "draft" and "issue_claim" not in after and not after.get("number")

        monkeypatch.undo()
        ok = await svc.issue_draft(firm, after, {})
        assert ok["number"].endswith("-0001")                    # ο αριθμός επέστρεψε στη σειρά

    _run(scenario())


def test_a_cancelled_request_does_not_leave_the_draft_locked(monkeypatch):
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-cancel"
        inserted = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        draft = await db.invoices.find_one({"_id": inserted.inserted_id})

        def cancelled(*a, **k):
            raise asyncio.CancelledError()

        monkeypatch.setattr(svc, "withholding_for", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await svc.issue_draft(firm, dict(draft), {})
        after = await db.invoices.find_one({"_id": inserted.inserted_id})
        assert after["status"] == "draft" and "issue_claim" not in after

    _run(scenario())


def test_a_live_claim_blocks_issue_and_edit_but_an_expired_one_does_not(client, office):
    import invoice_services as svc
    from db import db
    draft = _draft(client, office.alpha)
    body = {"client_id": office.alpha, "date": _today(),
            "line_items": [{"description": "Νέα", "quantity": 1, "unit_price": 50.0, "vat_rate": 24}]}

    _set(draft["id"], issue_claim="tok", issue_claim_at=datetime.now(timezone.utc).isoformat())
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 400
    edit = client.put(f"/api/invoices/{draft['id']}", json=body)
    assert edit.status_code == 409 and "εκδίδεται" in edit.json()["detail"]

    stale = (datetime.now(timezone.utc) - timedelta(seconds=svc.ISSUE_CLAIM_TTL_SECONDS + 5)).isoformat()
    _set(draft["id"], issue_claim="tok", issue_claim_at=stale)       # crash στη μέση: το κλείδωμα έληξε
    assert client.put(f"/api/invoices/{draft['id']}", json=body).status_code == 200
    out = client.post(f"/api/invoices/{draft['id']}/issue")
    assert out.status_code == 200, out.text
    assert "issue_claim" not in _doc(draft["id"])


def test_issue_refuses_zero_or_wrongly_signed_totals(client, office):
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-signs"
        negative = await db.invoices.insert_one({"firm_id": firm, "number": "", "status": "draft", "client_id": "x",
                                                  "date": _today(), "subtotal": -100.0, "total": -124.0, "line_items": []})
        zero = await db.invoices.insert_one({"firm_id": firm, "number": "", "status": "draft", "client_id": "x",
                                              "date": _today(), "subtotal": 0.0, "total": 0.0, "line_items": []})
        credit_positive = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(), "invoice_type": "5.1",
            "credit_note_for": "abc", "subtotal": 100.0, "total": 124.0, "line_items": []})
        for inserted in (negative, zero, credit_positive):
            with pytest.raises(svc.InvoiceWorkflowError):
                await svc.issue_draft(firm, await db.invoices.find_one({"_id": inserted.inserted_id}), {})
            still = await db.invoices.find_one({"_id": inserted.inserted_id})
            assert still["status"] == "draft" and "issue_claim" not in still

    _run(scenario())


def test_a_request_that_lost_its_claim_cannot_finish_the_issue(monkeypatch):
    """Το κλείδωμα έληξε και το πήρε άλλο αίτημα όσο το πρώτο ήταν στη μέση: το πρώτο δεν εκδίδει το τιμολόγιο
    πάνω από το δεύτερο, δεν του αφαιρεί το κλείδωμα και δεν «καίει» αριθμό."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-lost-claim"
        inserted = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        draft = await db.invoices.find_one({"_id": inserted.inserted_id})
        real_next = svc.next_invoice_number

        async def takeover(*a, **k):
            number = await real_next(*a, **k)
            await db.invoices.update_one({"_id": inserted.inserted_id}, {"$set": {
                "issue_claim": "other-request", "issue_claim_at": datetime.now(timezone.utc).isoformat()}})
            return number

        monkeypatch.setattr(svc, "next_invoice_number", takeover)
        with pytest.raises(svc.InvoiceWorkflowError):
            await svc.issue_draft(firm, dict(draft), {})
        after = await db.invoices.find_one({"_id": inserted.inserted_id})
        assert after["status"] == "draft" and not after.get("number")
        assert after["issue_claim"] == "other-request"                 # το κλείδωμα του άλλου αιτήματος μένει

        monkeypatch.undo()                                             # ο αριθμός που πήρε το πρώτο επέστρεψε στη σειρά
        await db.invoices.update_one({"_id": inserted.inserted_id}, {"$unset": {"issue_claim": "", "issue_claim_at": ""}})
        issued = await svc.issue_draft(firm, await db.invoices.find_one({"_id": inserted.inserted_id}), {})
        assert issued["number"].endswith("-0001")

    _run(scenario())


def test_issue_checks_and_prices_the_draft_as_stored_not_as_the_caller_read_it():
    """Ανάμεσα στην ανάγνωση του καλούντος και στο κλείδωμα το πρόχειρο μπορεί να άλλαξε (π.χ. επεξεργασία)."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-stale-read"
        base = {"firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
                "invoice_type": "2.1", "line_items": []}
        flipped = await db.invoices.insert_one({**base, "subtotal": 100.0, "total": 124.0})
        stale = await db.invoices.find_one({"_id": flipped.inserted_id})                  # ό,τι διάβασε ο καλών
        await db.invoices.update_one({"_id": flipped.inserted_id}, {"$set": {"subtotal": -100.0, "total": -124.0}})
        with pytest.raises(svc.InvoiceWorkflowError):
            await svc.issue_draft(firm, dict(stale), {})
        assert (await db.invoices.find_one({"_id": flipped.inserted_id}))["status"] == "draft"

        # η παρακράτηση παγώνει από το αποθηκευμένο ποσό (καθαρή αξία 1.000 ≥ όριο 300), όχι από το παλιό (100)
        settings = {"withholding_enabled": True, "withholding_min_net": 300, "withholding_rate": 0.2}
        grown = await db.invoices.insert_one({**base, "subtotal": 100.0, "total": 124.0})
        stale = await db.invoices.find_one({"_id": grown.inserted_id})
        await db.invoices.update_one({"_id": grown.inserted_id}, {"$set": {"subtotal": 1000.0, "total": 1240.0}})
        issued = await svc.issue_draft(firm, dict(stale), settings)
        assert issued["total"] == 1240.0 and issued["withholding_amount"] == 200.0

    _run(scenario())


# ==================================== 3. Επεξεργασία πρόχειρου: κανόνες ποσού και είδους ====

def _body(client_id, unit_price, **over):
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": unit_price, "vat_rate": 24}]}
    body.update(over)
    return body


def test_a_draft_cannot_be_edited_into_a_non_positive_total(client, office):
    draft = _draft(client, office.alpha)
    for price in (-100.0, 0.0):
        r = client.put(f"/api/invoices/{draft['id']}", json=_body(office.alpha, price))
        assert r.status_code == 400, price
        assert "θετικό" in r.json()["detail"]
    assert _doc(draft["id"])["total"] == 124.0                   # δεν άλλαξε τίποτα


def test_draft_credit_note_keeps_its_type_and_must_stay_negative(client, office):
    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400001111111111")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    assert credit["invoice_type"] == "5.1" and credit["total"] < 0

    # Ο client στέλνει πίσω το έγγραφο όπως το πήρε (με το δικό του είδος 5.1): δεν απορρίπτεται.
    same = client.put(f"/api/invoices/{credit['id']}", json={
        **_body(office.alpha, -50.0), "invoice_type": "5.1"})
    assert same.status_code == 200, same.text
    assert same.json()["invoice_type"] == "5.1" and same.json()["total"] == -62.0

    # Το πιστωτικό δεν γίνεται θετικό ούτε μηδενικό.
    for price in (50.0, 0.0):
        r = client.put(f"/api/invoices/{credit['id']}", json=_body(office.alpha, price))
        assert r.status_code == 400 and "αρνητικό" in r.json()["detail"], price
    assert _doc(credit["id"])["total"] == -62.0


def test_update_cannot_touch_an_invoice_that_was_issued_in_the_meantime(client, office, monkeypatch):
    """Ανάμεσα στην ανάγνωση και στη γραφή του PUT το πρόχειρο μπορούσε να εκδοθεί, και η γραφή άλλαζε
    τα ποσά ενός ήδη εκδοθέντος παραστατικού."""
    from db import db
    draft = _draft(client, office.alpha)

    original_find_one = None
    import routes_invoices

    class Racy:
        """Το PUT διαβάζει «πρόχειρο»· μόλις προχωρήσει στη γραφή, το τιμολόγιο έχει ήδη εκδοθεί."""
        def __init__(self, real):
            self._real = real
            self._flipped = False

        def __getattr__(self, name):
            return getattr(self._real, name)

        async def update_one(self, query, update, *a, **k):
            if not self._flipped:
                self._flipped = True
                await self._real.update_one({"_id": query["_id"]}, {"$set": {"status": "issued", "number": "ΤΠΥ-X-1"}})
            return await self._real.update_one(query, update, *a, **k)

    class Proxy:
        def __init__(self, real):
            self._real = real
            self.invoices = Racy(real.invoices)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(routes_invoices, "db", Proxy(db))
    r = client.put(f"/api/invoices/{draft['id']}", json=_body(office.alpha, 5000.0))
    assert r.status_code == 409, r.text
    assert _doc(draft["id"])["total"] == 124.0                    # τα ποσά του εκδοθέντος δεν άλλαξαν


# ================================================================== 4. Παράβολα ====

def test_only_a_draft_paravolo_can_be_edited_or_issued(client, office):
    from db import db
    created = client.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": 100.0, "description": "α"})
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    assert client.put(f"/api/paravola/{pid}", json={"category": "Λοιπά παράβολα", "amount": 120.0}).status_code == 200

    issued = client.post(f"/api/paravola/{pid}/issue", json={"eparavolo_code": "EP-1"})
    assert issued.status_code == 200 and issued.json()["status"] == "issued"

    # Μετά την έκδοση: ούτε αλλαγή ποσού ούτε σβήσιμο του κωδικού e-παραβόλου.
    edit = client.put(f"/api/paravola/{pid}", json={"category": "Λοιπά παράβολα", "amount": 1.0})
    assert edit.status_code == 400
    stored = _run(db.paravola.find_one({"_id": ObjectId(pid)}))
    assert stored["amount"] == 120.0 and stored["eparavolo_code"] == "EP-1"
    assert client.post(f"/api/paravola/{pid}/issue", json={"eparavolo_code": "EP-2"}).status_code == 400
    assert _run(db.paravola.find_one({"_id": ObjectId(pid)}))["eparavolo_code"] == "EP-1"

    assert client.post(f"/api/paravola/{pid}/mark-paid").status_code == 200
    assert client.put(f"/api/paravola/{pid}", json={"category": "Λοιπά παράβολα", "amount": 1.0}).status_code == 400

    # ακυρωμένο: δεν επανεκδίδεται
    other = client.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": 5.0}).json()["id"]
    _run(db.paravola.update_one({"_id": ObjectId(other)}, {"$set": {"status": "cancelled"}}))
    out = client.post(f"/api/paravola/{other}/issue", json={})
    assert out.status_code == 400 and _run(db.paravola.find_one({"_id": ObjectId(other)}))["status"] == "cancelled"


class _FlipsBeforeWrite:
    """Συλλογή όπου «ο άλλος χρήστης» προλαβαίνει: ακριβώς πριν από την πρώτη εγγραφή αλλάζει το έγγραφο."""

    def __init__(self, real, fields):
        self._real, self._fields, self._done = real, fields, False

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def update_one(self, query, update, *a, **k):
        if not self._done:
            self._done = True
            await self._real.update_one({"_id": query["_id"]}, {"$set": self._fields})
        return await self._real.update_one(query, update, *a, **k)


class _RacyDb:
    def __init__(self, real, collection, fields):
        self._real = real
        setattr(self, collection, _FlipsBeforeWrite(getattr(real, collection), fields))

    def __getattr__(self, name):
        return getattr(self._real, name)


def _new_paravolo(client, amount=100.0):
    created = client.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": amount})
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_paravolo_refusals_say_why(client, office):
    pid = _new_paravolo(client)
    assert client.post(f"/api/paravola/{pid}/issue", json={"eparavolo_code": "EP-1"}).status_code == 200
    edit = client.put(f"/api/paravola/{pid}", json={"category": "Λοιπά παράβολα", "amount": 1.0})
    assert edit.status_code == 400 and "Μόνο πρόχειρα παράβολα τροποποιούνται" in edit.json()["detail"]
    again = client.post(f"/api/paravola/{pid}/issue", json={"eparavolo_code": "EP-2"})
    assert again.status_code == 400 and "έχει ήδη εκδοθεί" in again.json()["detail"]

    from db import db
    cancelled = _new_paravolo(client, 5.0)
    _run(db.paravola.update_one({"_id": ObjectId(cancelled)}, {"$set": {"status": "cancelled"}}))
    out = client.post(f"/api/paravola/{cancelled}/issue", json={})
    assert out.status_code == 400 and "Εκδίδεται μόνο πρόχειρο" in out.json()["detail"]


def test_paravolo_edit_cannot_overwrite_one_issued_in_the_meantime(client, office, monkeypatch):
    """Ανάμεσα στην ανάγνωση και στη γραφή του PUT το παράβολο μπορούσε να εκδοθεί — και η γραφή άλλαζε το ποσό
    του και έσβηνε τον κωδικό e-παραβόλου."""
    import routes_paravola
    from db import db
    pid = _new_paravolo(client)
    monkeypatch.setattr(routes_paravola, "db", _RacyDb(db, "paravola", {"status": "issued", "eparavolo_code": "EP-9"}))
    out = client.put(f"/api/paravola/{pid}", json={"category": "Λοιπά παράβολα", "amount": 1.0})
    assert out.status_code == 400 and "δεν είναι πια πρόχειρο" in out.json()["detail"]
    stored = _run(db.paravola.find_one({"_id": ObjectId(pid)}))
    assert stored["amount"] == 100.0 and stored["eparavolo_code"] == "EP-9" and stored["status"] == "issued"


def test_paravolo_issue_cannot_overwrite_one_settled_in_the_meantime(client, office, monkeypatch):
    import routes_paravola
    from db import db
    pid = _new_paravolo(client)
    monkeypatch.setattr(routes_paravola, "db", _RacyDb(db, "paravola", {"status": "paid", "eparavolo_code": "EP-OLD"}))
    out = client.post(f"/api/paravola/{pid}/issue", json={"eparavolo_code": "EP-NEW"})
    assert out.status_code == 400 and "ήδη εκδοθεί" in out.json()["detail"]
    stored = _run(db.paravola.find_one({"_id": ObjectId(pid)}))
    assert stored["status"] == "paid" and stored["eparavolo_code"] == "EP-OLD"


# ======================================== 4b. Γραμμάτια, αρίθμηση από τον βοηθό, σειρά ημερομηνιών ====

def _gram(client, client_id, **over):
    body = {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": client_id,
            "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0, "vat_amount": 24.0}
    body.update(over)
    r = client.post("/api/grammatia", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_grammatio_issue_cannot_overwrite_one_issued_in_the_meantime(client, office, monkeypatch):
    import routes_grammatia
    from db import db
    gid = _gram(client, office.alpha)["id"]
    monkeypatch.setattr(routes_grammatia, "db", _RacyDb(db, "grammatia", {"status": "issued", "portal_ref": "OLO-FIRST"}))
    out = client.post(f"/api/grammatia/{gid}/issue", json={"portal_ref": "OLO-SECOND"})
    assert out.status_code == 400 and "ήδη εκδοθεί" in out.json()["detail"]
    assert _run(db.grammatia.find_one({"_id": ObjectId(gid)}))["portal_ref"] == "OLO-FIRST"


def test_two_requests_cannot_turn_one_grammatio_into_two_invoices(client, office, monkeypatch):
    import routes_grammatia
    from db import db
    gid = _gram(client, office.alpha)["id"]
    assert client.post(f"/api/grammatia/{gid}/issue", json={}).status_code == 200
    monkeypatch.setattr(routes_grammatia, "db", _RacyDb(db, "grammatia", {"invoice_id": "other-request-invoice"}))
    out = client.post(f"/api/grammatia/{gid}/to-invoice")
    assert out.status_code == 400 and "ήδη ενσωματωθεί" in out.json()["detail"]
    assert _run(db.invoices.count_documents({})) == 0                       # το δεύτερο τιμολόγιο δεν έμεινε ορφανό
    assert _run(db.grammatia.find_one({"_id": ObjectId(gid)}))["invoice_id"] == "other-request-invoice"


def test_grammatia_created_by_the_assistant_share_the_numbering_of_the_form(client, office):
    import routes_copilot
    case = client.post("/api/cases", json={"title": "Υπόθεση δοκιμής", "client_id": office.alpha,
                                           "client_name": "ΑΛΦΑ ΑΕ"}).json()
    action = {"case_ref": case["number"], "court_type": "Ειρηνοδικείο", "reference_amount": 100.0}

    def by_assistant():
        created, _reply = _run(routes_copilot._execute_create_grammatio(action, office.user))
        assert created is not None, _reply
        return created

    first, second = by_assistant(), by_assistant()
    assert first.number.endswith("-0001") and second.number.endswith("-0002")
    assert client.delete(f"/api/grammatia/{first.id}").status_code == 200     # διαγραφή του πρώτου: κενό στη σειρά
    third = by_assistant()
    assert third.number.endswith("-0003"), (first.number, second.number, third.number)
    assert _gram(client, office.alpha)["number"].endswith("-0004")             # και η φόρμα συνεχίζει την ίδια σειρά


def _frozen_june(monkeypatch):
    import tax_utils

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tax_utils, "datetime", FakeDateTime)
    assert athens_today() == date(2026, 6, 15)
    return date(2026, 6, 15)


def test_a_cancelled_invoice_keeps_its_place_in_the_series(client, office, monkeypatch):
    """Ο αριθμός ενός ακυρωμένου παραστατικού δεν ξαναχρησιμοποιείται· άρα και η ημερομηνία του μετράει."""
    today = _frozen_june(monkeypatch)
    day = lambda n: (today - timedelta(days=n)).isoformat()            # noqa: E731
    _issued(client, office.alpha, when=day(5))
    second = _issued(client, office.alpha, when=day(1))
    _set(second["id"], status="cancelled")
    middle = _draft(client, office.alpha, when=day(3))
    refused = client.post(f"/api/invoices/{middle['id']}/issue")
    assert refused.status_code == 400
    assert "χρονολογική" in refused.json()["detail"] and second["number"] in refused.json()["detail"]
    assert _doc(middle["id"])["status"] == "draft"


def test_an_old_numbered_draft_does_not_hold_the_series(client, office, monkeypatch):
    """Ένα πρόχειρο με παλιό αριθμό δεν είναι εκδοθέν παραστατικό: η ημερομηνία του δεν περιορίζει τα επόμενα."""
    from db import db
    today = _frozen_june(monkeypatch)
    day = lambda n: (today - timedelta(days=n)).isoformat()            # noqa: E731
    _issued(client, office.alpha, when=day(5))
    _run(db.invoices.insert_one({"firm_id": office.firm_id, "client_id": office.alpha, "number": f"ΤΠΥ-{today.year}-0009",
                                 "status": "draft", "subtotal": 100.0, "total": 124.0, "date": today.isoformat(),
                                 "line_items": []}))
    middle = _draft(client, office.alpha, when=day(3))
    out = client.post(f"/api/invoices/{middle['id']}/issue")
    assert out.status_code == 200, out.text


def test_a_cancelled_invoice_gets_no_credit_note(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    _set(inv["id"], status="cancelled", mydata_status="synced", mydata_mark="400003333333333")   # ασυνεπή (παλιά) δεδομένα
    out = client.post(f"/api/invoices/{inv['id']}/credit-note", json={})
    assert out.status_code == 400 and "ακυρωμένο" in out.json()["detail"]
    assert _run(db.invoices.count_documents({"credit_note_for": inv["id"]})) == 0


def test_effective_invoice_prices_legacy_invoices_like_the_transmission_and_keeps_frozen_ones(monkeypatch):
    import invoice_services as svc

    async def scenario():
        settings = {"withholding_enabled": True, "withholding_min_net": 300, "withholding_rate": 0.2}
        legacy = {"firm_id": "f", "status": "issued", "invoice_type": "2.1", "subtotal": 1000.0, "total": 1240.0}
        effective = await svc.effective_invoice("f", legacy, settings)          # εκδόθηκε πριν παγώνει η παρακράτηση
        assert effective["withholding_amount"] == 200.0 and "withholding_amount" not in legacy
        frozen = {**legacy, "withholding_amount": 0.0}                          # παγωμένη: οι ρυθμίσεις δεν την αλλάζουν
        assert (await svc.effective_invoice("f", frozen, settings))["withholding_amount"] == 0.0

        # Το παγωμένο παραστατικό επιστρέφεται όπως είναι: χωρίς επανυπολογισμό (η τιμή με περισσότερα από 2
        # δεκαδικά δεν στρογγυλεύεται, ένα έγγραφο χωρίς σύνολα δεν σκάει) και χωρίς ερώτημα για τον πελάτη.
        async def no_lookup(*_a, **_k):
            raise AssertionError("το παγωμένο παραστατικό δεν χρειάζεται τον πελάτη")

        monkeypatch.setattr(svc, "load_client", no_lookup)
        three_decimals = {**legacy, "withholding_amount": 200.005}
        assert await svc.effective_invoice("f", three_decimals, settings) is three_decimals
        no_totals = {"firm_id": "f", "status": "issued", "total": None, "subtotal": None, "withholding_amount": 5.0}
        assert await svc.effective_invoice("f", no_totals, settings) is no_totals

    _run(scenario())


# =================================================== 5. myDATA: διαβίβαση και ακύρωση ====

def test_sync_is_refused_for_drafts_and_cancelled_documents(client, office, monkeypatch):
    monkeypatch.setenv("MYDATA_MOCK", "1")
    draft = _draft(client, office.alpha)
    out = client.post(f"/api/mydata/sync/{draft['id']}")
    assert out.status_code == 400
    assert _doc(draft["id"])["mydata_status"] == "not_sent" and not _doc(draft["id"]).get("mydata_mark")

    cancelled = _issued(client, office.alpha)
    _set(cancelled["id"], status="cancelled", mydata_status="not_sent", mydata_mark=None)
    assert client.post(f"/api/mydata/sync/{cancelled['id']}").status_code == 400
    assert client.post("/api/mydata/sync/not-an-id").status_code == 404


def test_sync_of_an_already_synced_invoice_does_not_resend_or_change_the_mark(client, office, monkeypatch):
    import mydata_client
    monkeypatch.setenv("MYDATA_MOCK", "1")
    inv = _issued(client, office.alpha)
    first = _doc(inv["id"])
    assert first["mydata_status"] == "synced"
    mark = first["mydata_mark"]

    sends = []
    real = mydata_client.send_invoice

    async def spy(*a, **k):
        sends.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(mydata_client, "send_invoice", spy)
    out = client.post(f"/api/mydata/sync/{inv['id']}")
    assert out.status_code == 200 and out.json()["already_synced"] is True and out.json()["mark"] == mark
    assert sends == [] and _doc(inv["id"])["mydata_mark"] == mark

    # Ένα πρώτο κλικ πάνω σε ανοιχτό «εκκρεμές» εκδοθέν παραστατικό διαβιβάζεται κανονικά.
    _set(inv["id"], mydata_status="pending", mydata_mark=None, mydata_uid=None)
    again = client.post(f"/api/mydata/sync/{inv['id']}")
    assert again.status_code == 200 and again.json()["mark"].startswith("MOCK-")


def test_cancel_refuses_a_draft_and_an_invoice_that_already_has_a_credit_note(client, office):
    import invoice_services as svc
    draft = _draft(client, office.alpha)
    out = client.post(f"/api/invoices/{draft['id']}/cancel", json={"reason": "x"})
    assert out.status_code == 400 and "διαγράψτε" in out.json()["detail"]
    assert _doc(draft["id"])["status"] == "draft"

    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400001111111111")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    out = client.post(f"/api/invoices/{original['id']}/cancel", json={"reason": "x"})
    assert out.status_code == 400 and "πιστωτικό" in out.json()["detail"]
    assert _doc(original["id"])["status"] == "issued"
    # και το πιστωτικό εκδίδεται κανονικά μετά την άρνηση (δεν διπλο-αναιρείται το τιμολόγιο)
    issued_credit = client.post(f"/api/invoices/{credit['id']}/issue")
    assert issued_credit.status_code == 200 and issued_credit.json()["number"].startswith("ΠΙΣ-")


def test_cancelling_or_crediting_an_invoice_paid_partly_from_the_client_account_raises_a_decision_card(client, office):
    inv = _issued(client, office.alpha)
    _set(inv["id"], trust_applied=100.0)
    out = client.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "λάθος"})
    assert out.status_code == 200 and out.json()["status"] == "cancelled"
    note = _notes("trust_money_stranded")
    assert len(note) == 1 and note[0]["requires_action"] and note[0]["level"] == "critical"
    assert "100,00 €" in note[0]["title"] and "ΔΕΝ επιστρέφεται αυτόματα" in note[0]["message"]

    # πιστωτικό για εξοφλημένο με πελατολογικό
    paid = _issued(client, office.alpha)
    _set(paid["id"], status="paid", trust_applied=124.0, paid_via="trust",
         mydata_status="synced", mydata_mark="400002222222222")
    credit = client.post(f"/api/invoices/{paid['id']}/credit-note", json={})
    assert credit.status_code == 200, credit.text
    assert len(_notes("trust_money_stranded")) == 2

    # τιμολόγιο χωρίς πελατολογικό: καμία ειδοποίηση
    plain = _issued(client, office.alpha)
    assert client.post(f"/api/invoices/{plain['id']}/cancel", json={}).status_code == 200
    assert len(_notes("trust_money_stranded")) == 2


# ========================================================= 6. Πελατολογικός λογαριασμός ====

def _tx(client, client_id, kind, amount, **over):
    body = {"client_id": client_id, "type": kind, "amount": amount, "date": _today(), "description": "δοκιμή"}
    body.update(over)
    return client.post("/api/trust/transactions", json=body)


def _account(client, client_id):
    return client.get(f"/api/trust/accounts/{client_id}").json()


def test_client_id_is_stored_canonically_so_letter_case_cannot_split_an_account(client, office):
    upper = office.alpha.upper()
    if upper == office.alpha:
        pytest.skip("το id δεν έχει γράμματα")
    assert _tx(client, upper, "deposit", 50.0).status_code == 200
    assert _tx(client, office.alpha, "deposit", 25.0).status_code == 200
    accounts = client.get("/api/trust/accounts").json()
    assert len(accounts) == 1 and accounts[0]["client_id"] == office.alpha and accounts[0]["balance"] == 75.0
    assert _account(client, upper)["account"]["balance"] == 75.0

    inv = _issued(client, office.alpha)
    rows = client.get(f"/api/trust/accounts/{upper}/open-invoices").json()
    assert [r["id"] for r in rows] == [inv["id"]]
    paid = _tx(client, upper, "apply_to_invoice", 70.0, invoice_id=inv["id"])
    assert paid.status_code == 200, paid.text
    assert {t["client_id"] for t in _account(client, office.alpha)["transactions"]} == {office.alpha}


def test_insufficient_balance_is_refused_before_the_invoice_is_touched(client, office):
    """Η συνηθισμένη άρνηση δεν πρέπει να αφήνει ούτε στιγμιαία «κλειδωμένο» τιμολόγιο: κανένα άλλο αίτημα
    δεν πρέπει να δει εξόφληση που δεν θα ολοκληρωθεί. Το πεδίο δεν γράφεται καν."""
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 50.0)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    assert out.status_code == 400 and "Ανεπαρκές" in out.json()["detail"]
    assert "trust_applied" not in _doc(inv["id"])


def test_application_is_undone_when_the_invoice_is_settled_elsewhere_in_the_meantime(client, office, monkeypatch):
    """Η πληρωμή από τράπεζα/κάρτα/χειροκίνητα προλαβαίνει ανάμεσα στο «κλείδωμα» και το κλείσιμο του
    τιμολογίου: τα χρήματα του πελάτη δεν πρέπει να χρεωθούν και δεύτερη φορά."""
    import routes_trust
    from db import db
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 200.0)

    async def settled_elsewhere(firm_id, invoice_id, paid_via="manual"):
        await db.invoices.update_one({"_id": invoice_id}, {"$set": {"status": "paid", "paid_via": "bank_manual"}})
        return False

    monkeypatch.setattr(routes_trust, "mark_paid", settled_elsewhere)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    assert out.status_code == 409 and "παραμένουν στον λογαριασμό" in out.json()["detail"]

    monkeypatch.undo()
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 200.0 and data["account"]["total_withdrawals"] == 0.0
    assert len(data["transactions"]) == 1 and data["ledger_check"]["consistent"] is True
    doc = _doc(inv["id"])
    assert doc["status"] == "paid" and doc["paid_via"] == "bank_manual" and not doc.get("trust_applied")


def test_application_is_undone_when_closing_the_invoice_fails(client, office, monkeypatch):
    import routes_trust
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 200.0)

    async def boom(*a, **k):
        raise RuntimeError("η βάση δεν απάντησε")

    monkeypatch.setattr(routes_trust, "mark_paid", boom)
    with pytest.raises(RuntimeError):
        _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    monkeypatch.undo()
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 200.0 and len(data["transactions"]) == 1
    assert data["ledger_check"]["consistent"] is True
    doc = _doc(inv["id"])
    assert doc["status"] == "issued" and not doc.get("trust_applied")
    # και ξαναδοκιμάζεται κανονικά
    assert _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"]).status_code == 200


@pytest.mark.asyncio
async def test_a_cancelled_request_cannot_leave_a_half_applied_payment(monkeypatch):
    """Αποσύνδεση του client ή shutdown στη μέση: το `except Exception` δεν έπιανε το CancelledError, οπότε
    έμενε η εφαρμογή στο τιμολόγιο χωρίς χρέωση του λογαριασμού και χωρίς εγγραφή στο ledger."""
    import routes_trust
    from db import db
    from models import TrustTransaction
    firm = "firm-trust-cancel"
    cid = str((await db.clients.insert_one({"firm_id": firm, "name": "Πελάτης", "afm": "123456783"})).inserted_id)
    inv = await db.invoices.insert_one({
        "firm_id": firm, "number": "ΤΠΥ-2026-0001", "client_id": cid, "client_name": "Πελάτης", "status": "issued",
        "total": 124.0, "date": _today(), "line_items": []})
    user = {"email": "u@example.gr", "firm_id": firm}
    await routes_trust.create_transaction(routes_trust.TransactionPayload(
        client_id=cid, type="deposit", amount=200.0, date=_today()), user)

    def cancelled(self, *a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(TrustTransaction, "to_mongo", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await routes_trust.create_transaction(routes_trust.TransactionPayload(
            client_id=cid, type="apply_to_invoice", amount=124.0, date=_today(), invoice_id=str(inv.inserted_id)), user)
    monkeypatch.undo()
    account = await db.trust_accounts.find_one({"firm_id": firm, "client_id": cid})
    assert account["balance"] == 200.0 and account["total_withdrawals"] == 0.0
    assert not (await db.invoices.find_one({"_id": inv.inserted_id})).get("trust_applied")
    assert await db.trust_transactions.count_documents({"firm_id": firm}) == 1


def test_losing_the_invoice_to_another_payment_is_a_409_and_the_money_goes_back(client, office, monkeypatch):
    """Το τιμολόγιο εξοφλήθηκε αλλιώς ανάμεσα στον έλεγχο και το «κλείδωμα»: καθαρή άρνηση, όχι 500, και το
    ποσό που ήδη αφαιρέθηκε από τον λογαριασμό επιστρέφει."""
    import routes_trust
    from db import db
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 200.0)
    real = routes_trust._cas_update
    state = {"flipped": False}

    async def settled_first(collection, query, compute, attempts=8):
        if collection.name == "invoices" and not state["flipped"]:
            state["flipped"] = True
            await db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"status": "paid", "paid_via": "bank_manual"}})
        return await real(collection, query, compute, attempts)

    monkeypatch.setattr(routes_trust, "_cas_update", settled_first)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    assert out.status_code == 409 and "δεν είναι πλέον ανοιχτό" in out.json()["detail"]

    monkeypatch.undo()
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 200.0 and data["account"]["total_withdrawals"] == 0.0
    assert len(data["transactions"]) == 1 and data["ledger_check"]["consistent"] is True
    doc = _doc(inv["id"])
    assert doc["status"] == "paid" and doc["paid_via"] == "bank_manual" and not doc.get("trust_applied")
    assert _notes("trust_needs_review") == []                       # η αναίρεση πέτυχε: δεν χρειάζεται ειδοποίηση


def test_an_undo_that_fails_is_reported_to_the_office_never_silent(client, office, monkeypatch):
    """Η εφαρμογή στο τιμολόγιο έγινε, το κλείσιμο απέτυχε, και η αναίρεση απέτυχε κι αυτή: μένει τιμολόγιο με
    `trust_applied` χωρίς κίνηση στο ledger. Πρέπει να υπάρχει ειδοποίηση που απαιτεί ενέργεια."""
    import routes_trust
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 200.0)
    real = routes_trust._cas_update
    seen = {"invoice_writes": 0}

    async def failing_undo(collection, query, compute, attempts=8):
        if collection.name == "invoices":
            seen["invoice_writes"] += 1
            if seen["invoice_writes"] >= 2:                         # η 1η είναι η εφαρμογή, η 2η η αναίρεσή της
                raise RuntimeError("η βάση δεν απάντησε στην αναίρεση")
        return await real(collection, query, compute, attempts)

    async def boom(*a, **k):
        raise RuntimeError("η βάση δεν απάντησε στο κλείσιμο")

    monkeypatch.setattr(routes_trust, "_cas_update", failing_undo)
    monkeypatch.setattr(routes_trust, "mark_paid", boom)
    with pytest.raises(RuntimeError, match="κλείσιμο"):             # το αρχικό σφάλμα φτάνει στον χρήστη
        _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    monkeypatch.undo()

    notes = _notes("trust_needs_review")
    assert len(notes) == 1 and notes[0]["level"] == "critical" and notes[0]["requires_action"]
    assert "δεν αναιρέθηκε πλήρως" in notes[0]["title"] and notes[0]["entity_id"] == inv["id"]
    assert _doc(inv["id"])["trust_applied"] == 124.0               # η κατάσταση που η ειδοποίηση ζητά να ελεγχθεί
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 200.0 and len(data["transactions"]) == 1       # ό,τι αναιρέθηκε, αναιρέθηκε


def test_another_office_cannot_see_or_move_this_offices_client_money(client, office):
    import server
    assert _tx(client, office.alpha, "deposit", 100.0).status_code == 200
    inv = _issued(client, office.alpha)
    with TestClient(server.app, base_url="https://testserver") as other:
        assert other.post("/api/auth/register", json={"email": "other-office@example.gr", "password": "Str0ngPass!",
                                                       "name": "Άλλο γραφείο"}).status_code == 200
        assert other.get(f"/api/trust/accounts/{office.alpha}").status_code == 404
        assert other.get(f"/api/trust/accounts/{office.alpha}/open-invoices").status_code == 404
        assert _tx(other, office.alpha, "deposit", 10.0).status_code == 404
        assert _tx(other, office.alpha, "apply_to_invoice", 10.0, invoice_id=inv["id"]).status_code == 404
        assert other.get("/api/trust/accounts").json() == []
        assert other.get("/api/trust/summary").json() == {"total_balance": 0.0, "accounts_with_balance": 0}
    assert _account(client, office.alpha)["account"]["balance"] == 100.0
    assert _doc(inv["id"])["status"] == "issued" and not _doc(inv["id"]).get("trust_applied")


def test_open_invoices_and_summary(client, office):
    first = _issued(client, office.alpha, net=100.0)
    second = _issued(client, office.alpha, net=200.0)
    paid = _issued(client, office.alpha)
    _set(paid["id"], status="paid")
    _issued(client, office.beta)                                 # άλλου πελάτη
    from db import db
    _run(db.invoices.insert_one({"firm_id": office.firm_id, "client_id": office.alpha, "number": "ΠΙΣ-2026-0001",
                                 "status": "issued", "total": -124.0, "credit_note_for": first["id"], "date": _today()}))
    rows = client.get(f"/api/trust/accounts/{office.alpha}/open-invoices").json()
    assert sorted((r["id"], r["total"], r["payable"], r["trust_applied"], r["outstanding"]) for r in rows) == sorted([
        (first["id"], 124.0, 124.0, 0.0, 124.0), (second["id"], 248.0, 248.0, 0.0, 248.0)])

    _set(first["id"], trust_applied=124.0)                       # εξοφλημένο εξ ολοκλήρου από πελατολογικό
    _set(second["id"], trust_applied=48.0)
    rows = client.get(f"/api/trust/accounts/{office.alpha}/open-invoices").json()
    assert [(r["id"], r["trust_applied"], r["outstanding"]) for r in rows] == [(second["id"], 48.0, 200.0)]

    _tx(client, office.alpha, "deposit", 100.0)
    _tx(client, office.beta, "deposit", 50.5)
    third = client.post("/api/clients", json={"name": "ΓΑΜΜΑ", "client_type": "individual"}).json()["id"]
    _account(client, third)                                      # λογαριασμός με μηδενικό υπόλοιπο
    assert client.get("/api/trust/summary").json() == {"total_balance": 150.5, "accounts_with_balance": 2}


# ============================================================ 7. Τράπεζα: αντιστοίχιση ====

def _inv_row(_id, number="ΤΠΥ-2026-0001", due=124.0, name="ΑΛΦΑ ΑΕ", client_id="c1", day=None):
    return {"_id": _id, "number": number, "client_id": client_id, "client_name": name, "_due": due,
            "date": (day or athens_today()).isoformat()}


def _deposit(amount, description="", day=None):
    return {"amount": amount, "date": (day or athens_today()).isoformat(), "description": description, "reference": ""}


@pytest.mark.parametrize("cents", list(range(1, 400, 3)))
def test_one_cent_difference_is_never_an_exact_match(cents):
    """Με float, το 3,01 − 3,00 είναι 0,009999999999999787 — «μικρότερο από 1 λεπτό». Σε λεπτά είναι 1."""
    from routes_bank import match_details
    due = cents / 100
    for amount in ((cents + 1) / 100, (cents - 1) / 100):
        if amount <= 0:
            continue
        found = match_details(_deposit(amount, "ΑΛΦΑ ΑΕ"), [_inv_row(1, due=due)])
        assert found["exact"] is False, (due, amount)
        assert found["invoice"] is None or found["confidence"] <= 75
    assert match_details(_deposit(due, "ΑΛΦΑ ΑΕ"), [_inv_row(1, due=due)])["exact"] is True


def test_amounts_are_compared_to_the_cent_not_to_float_noise():
    from routes_bank import match_details
    assert match_details(_deposit(124.0000001, "ΑΛΦΑ ΑΕ"), [_inv_row(1)])["exact"] is True
    assert match_details(_deposit(123.99, "ΑΛΦΑ ΑΕ"), [_inv_row(1)])["exact"] is False


def test_the_invoice_named_in_the_description_wins_the_tie_at_the_score_cap():
    """Όταν και τα δύο τιμολόγια ξεπερνούν το όριο 100, η επιλογή εξαρτιόταν από τη σειρά της λίστας."""
    from routes_bank import match_details
    name = "ΕΛΛΗΝΙΚΗ ΕΤΑΙΡΕΙΑ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ ΑΕ"
    first = _inv_row(1, "ΤΠΥ-2026-0001", name=name)
    second = _inv_row(2, "ΤΠΥ-2026-0002", name=name)
    deposit = _deposit(124.0, f"{name} ΤΠΥ-2026-0002")
    for pool in ([first, second], [second, first]):
        found = match_details(deposit, pool)
        assert found["invoice"]["_id"] == 2 and found["ambiguous"] is False, [p["_id"] for p in pool]
    # χωρίς αριθμό στην αιτιολογία: δύο ίδια τιμολόγια του ίδιου πελάτη → χρειάζεται άνθρωπος
    unnamed = match_details(_deposit(124.0, name), [first, second])
    assert unnamed["ambiguous"] is True and unnamed["confidence"] <= 75


def test_threshold_and_cap_of_the_match_score():
    from routes_bank import match_details, MATCH_THRESHOLD
    base = date(2026, 6, 1)
    invoice = _inv_row(1, "X-1", name="", day=base)
    assert MATCH_THRESHOLD == 60
    # ακριβές ποσό (50) + ημερομηνία 31–60 ημέρες (10) = 60 → ακριβώς στο όριο: προτείνεται
    at_limit = match_details(_deposit(124.0, "τίποτα", day=base + timedelta(days=45)), [invoice])
    assert at_limit["invoice"] is invoice and at_limit["confidence"] == 60
    # ακριβές ποσό μόνο (>60 ημέρες) = 50 → κάτω από το όριο
    below = match_details(_deposit(124.0, "τίποτα", day=base + timedelta(days=61)), [invoice])
    assert below["invoice"] is None and below["confidence"] == 50
    # μέσα στις 7 ημέρες: 80, και ποτέ πάνω από 100
    near = match_details(_deposit(124.0, "τίποτα", day=base + timedelta(days=3)), [invoice])
    assert near["confidence"] == 80
    capped = match_details(_deposit(124.0, "ΑΛΦΑ ΑΕ X-1", day=base), [{**invoice, "client_name": "ΑΛΦΑ ΑΕ"}])
    assert capped["confidence"] == 100


def test_deposit_that_is_not_a_credit_never_matches():
    from routes_bank import match_details
    assert match_details(_deposit(-124.0, "ΑΛΦΑ ΑΕ"), [_inv_row(1)])["invoice"] is None
    assert match_details(_deposit(0.0, "ΑΛΦΑ ΑΕ"), [_inv_row(1)])["invoice"] is None


@pytest.mark.parametrize("cents", [1, 2, 3, 4])
@pytest.mark.parametrize("sign", [1, -1])
def test_a_deposit_a_few_cents_off_is_refused_unless_a_human_accepts_the_difference(client, office, cents, sign):
    import invoice_services as svc
    from db import db
    inv = _issued(client, office.alpha)                          # 124,00
    amount = round(124.0 + sign * cents / 100, 2)
    tx_id = _run(db.bank_transactions.insert_one({"firm_id": office.firm_id, "date": _today(), "description": "x",
                                                  "amount": amount, "match_status": "unmatched"})).inserted_id
    tx = _run(db.bank_transactions.find_one({"_id": tx_id}))
    refused = _run(svc.apply_bank_payment(office.firm_id, tx, inv["id"], settings={}, paid_via="bank_auto",
                                          status_on_success="auto_confirmed", dry_run=True))
    assert refused["status"] == "refused" and refused["reason"] == "amount_mismatch"
    ok = _run(svc.apply_bank_payment(office.firm_id, tx, inv["id"], settings={}, paid_via="bank_manual",
                                     status_on_success="manual_matched", accept_difference=True, dry_run=True))
    assert ok["status"] == "ok" and ok["difference"] == round(sign * cents / 100, 2)


def test_sub_cent_noise_is_still_an_exact_payment(client, office):
    import invoice_services as svc
    from db import db
    inv = _issued(client, office.alpha)
    tx_id = _run(db.bank_transactions.insert_one({"firm_id": office.firm_id, "date": _today(), "description": "x",
                                                  "amount": 124.004, "match_status": "unmatched"})).inserted_id
    tx = _run(db.bank_transactions.find_one({"_id": tx_id}))
    out = _run(svc.apply_bank_payment(office.firm_id, tx, inv["id"], settings={}, paid_via="bank_auto",
                                      status_on_success="auto_confirmed", dry_run=True))
    assert out["status"] == "ok" and out["difference"] == 0.0


def test_credit_notes_and_empty_invoices_are_not_candidates_for_a_deposit(client, office):
    import routes_bank
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.invoices.insert_many([
        {"firm_id": office.firm_id, "client_id": office.alpha, "number": "ΠΙΣ-2026-0001", "status": "issued",
         "total": -124.0, "credit_note_for": inv["id"], "date": _today()},
        {"firm_id": office.firm_id, "client_id": office.alpha, "number": "ΤΠΥ-2026-0099", "status": "issued",
         "total": 0.0, "date": _today()},
        {"firm_id": office.firm_id, "client_id": office.alpha, "number": "ΤΠΥ-2026-0098", "status": "draft",
         "total": 124.0, "date": _today()},
    ]))
    pool = _run(routes_bank.build_match_pool(office.firm_id, {}))
    assert [str(p["_id"]) for p in pool] == [inv["id"]] and pool[0]["_due"] == 124.0


def test_another_office_cannot_reconcile_or_ignore_these_deposits(client, office):
    import server
    from db import db
    inv = _issued(client, office.alpha)
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": (
        "x.csv", ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n"
                  f"{athens_today():%d/%m/%Y};ΑΛΦΑ ΑΕ {inv['number']};;124,00;;\n").encode(), "text/csv")})
    assert r.status_code == 200 and r.json()["auto_matched"] == 1
    tx = _run(db.bank_transactions.find_one({}))
    with TestClient(server.app, base_url="https://testserver") as other:
        assert other.post("/api/auth/register", json={"email": "other-bank@example.gr", "password": "Str0ngPass!",
                                                       "name": "Άλλο γραφείο"}).status_code == 200
        assert other.get("/api/bank/transactions").json() == []
        assert other.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}").status_code == 404
        assert other.post(f"/api/bank/reconcile/{tx['_id']}?ignore=true").status_code == 404
        assert other.post("/api/bank/confirm-auto-matches").json()["confirmed"] == 0
        assert other.get("/api/bank/summary").json()["total"] == 0
    assert _doc(inv["id"])["status"] == "issued"
    assert _run(db.bank_transactions.find_one({"_id": tx["_id"]}))["match_status"] == "auto_matched"


class _DuplicateOnInsert:
    """Συλλογή στην οποία «άλλη διεργασία» πρόλαβε να καταχωρήσει την ίδια κίνηση."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def insert_one(self, *args, **kwargs):
        from pymongo.errors import DuplicateKeyError
        raise DuplicateKeyError("E11000 duplicate key error")


class _DbProxy:
    def __init__(self, real, collection):
        self._real, self._collection = real, collection

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _DuplicateOnInsert(attr) if name == self._collection else attr


def test_concurrent_import_of_the_same_line_is_skipped_not_a_500(client, office, monkeypatch):
    import routes_bank
    from db import db
    monkeypatch.setattr(routes_bank, "db", _DbProxy(db, "bank_transactions"))
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": (
        "x.csv", ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n"
                  f"{athens_today():%d/%m/%Y};ΕΙΣΠΡΑΞΗ;;50,00;;\n").encode(), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0 and r.json()["skipped"] == 1 and r.json()["auto_matched"] == 0


def _psd2_firm(firm):
    from db import db
    _run(db.settings.insert_one({
        "firm_id": firm, "company_name": "Γραφείο", "openbanking_enabled": True, "gocardless_secret_id": "sid",
        "gocardless_secret_key": "skey", "gocardless_institution_name": "Πειραιώς", "gocardless_account_ids": ["acc-1"]}))
    return str(_run(db.clients.insert_one({"firm_id": firm, "name": "Παπαδόπουλος"})).inserted_id)


def _psd2_invoice(firm, client_id, number):
    from db import db
    return _run(db.invoices.insert_one({
        "firm_id": firm, "number": number, "client_id": client_id, "client_name": "Παπαδόπουλος",
        "status": "issued", "total": 620.0, "date": _today(), "line_items": []})).inserted_id


def test_psd2_sync_flags_a_suggestion_with_a_twin_invoice_as_ambiguous(monkeypatch):
    import automation
    import openbanking
    from db import db
    firm = "firm-psd2-ambiguous"
    cid = _psd2_firm(firm)
    _psd2_invoice(firm, cid, "ΤΠΥ-2026-0001")
    _psd2_invoice(firm, cid, "ΤΠΥ-2026-0002")                    # ίδιος πελάτης, ίδιο ποσό

    async def fake_fetch(settings, account_id, date_from=None):
        return [{"date": _today(), "amount": 620.0, "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ ΑΜΟΙΒΗ", "reference": "", "external_id": "TX-1"}]

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    out = _run(automation.run_firm(firm, ["bank_sync"]))[0]
    assert out["status"] != "error", out.get("error")
    tx = _run(db.bank_transactions.find_one({"firm_id": firm}))
    assert tx["match_status"] == "auto_matched" and tx["match_ambiguous"] is True and tx["match_confidence"] <= 75
    # και δεν εξοφλείται αυτόματα, όσο χαμηλό κι αν είναι το όριο
    assert _run(automation.job_reconcile(firm, {"auto_reconcile_min_confidence": 10}))["confirmed"] == 0


def test_psd2_sync_skips_a_line_another_process_already_stored(monkeypatch):
    import automation
    import openbanking
    from db import db
    firm = "firm-psd2-dup"
    cid = _psd2_firm(firm)
    _psd2_invoice(firm, cid, "ΤΠΥ-2026-0001")

    async def fake_fetch(settings, account_id, date_from=None):
        return [{"date": _today(), "amount": 620.0, "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ", "reference": "", "external_id": "TX-1"}]

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    monkeypatch.setattr(automation, "db", _DbProxy(db, "bank_transactions"))
    out = _run(automation.run_firm(firm, ["bank_sync"]))[0]
    assert out["status"] != "error", out.get("error")
    assert out["stats"]["created"] == 0 and out["stats"]["already_known"] == 1


def test_to_cents_rounds_half_up_and_is_exact_for_decimal_strings():
    from invoice_services import to_cents
    assert to_cents(124.0) == 12400 and to_cents(0.29) == 29 and to_cents(19.99) == 1999
    assert to_cents(10.005) == 1001                         # μισό λεπτό → προς τα πάνω, όχι αποκοπή
    assert to_cents(1.1499999999999915) == 115              # θόρυβος float λίγο κάτω από το ακριβές ποσό
    assert to_cents(-1.005) == -101
    assert to_cents(None) == 0 and to_cents("12.30") == 1230


def test_apply_bank_payment_never_treats_a_one_cent_difference_as_exact(client, office):
    """Με σύγκριση float (`abs(a − b) < 0.01`) το 3,01 − 3,00 = 0,00999… περνούσε ως «ίδιο ποσό»."""
    import invoice_services as svc
    from db import db
    dues = [c / 100 for c in range(1, 400, 3)]
    armed = [(d, a) for d in dues for a in (round(d + 0.01, 2), round(d - 0.01, 2)) if a > 0 and abs(a - d) < 0.01]
    assert len(armed) >= 20, "το test πρέπει να περιέχει περιπτώσεις θορύβου float, αλλιώς δεν ελέγχει τίποτα"

    ids = _run(db.invoices.insert_many([
        {"firm_id": office.firm_id, "number": f"ΤΠΥ-2026-{i:04d}", "client_id": office.alpha, "status": "issued",
         "total": due, "subtotal": due, "date": _today()} for i, due in enumerate(dues, start=1)])).inserted_ids

    def check(invoice_id, amount):
        return _run(svc.apply_bank_payment(
            office.firm_id, {"_id": ObjectId(), "amount": amount}, invoice_id, settings={}, paid_via="bank_auto",
            status_on_success="auto_confirmed", dry_run=True))

    for invoice_id, due in zip(ids, dues):
        assert check(invoice_id, due)["status"] == "ok", due
        for amount in (round(due + 0.01, 2), round(due - 0.01, 2)):
            if amount <= 0:
                continue
            out = check(invoice_id, amount)
            assert out["status"] == "refused" and out["reason"] == "amount_mismatch", (due, amount)


def test_a_deposit_one_euro_or_more_away_is_not_even_a_candidate():
    from routes_bank import match_details
    inv = _inv_row(1)                                       # οφείλονται 124,00
    for amount in (124.99, 123.01):                         # κάτω από 1 € διαφορά: πρόταση για έλεγχο, ποτέ βέβαιη
        found = match_details(_deposit(amount, "ΑΛΦΑ ΑΕ"), [inv])
        assert found["invoice"] is inv and found["exact"] is False and found["confidence"] <= 75, amount
    for amount in (125.0, 123.0, 125.5, 126.0, 100.0):      # 1 € και πάνω: δεν είναι υποψήφιο
        assert match_details(_deposit(amount, "ΑΛΦΑ ΑΕ"), [inv])["invoice"] is None, amount


def test_equal_scores_are_decided_by_the_invoice_the_description_names():
    from routes_bank import match_details
    today = athens_today()
    named = _inv_row(1, "ΤΠΥ-2026-0001", name="", client_id="c1", day=today - timedelta(days=40))   # 50 + 10 + 20
    recent = _inv_row(2, "ΤΠΥ-2026-0002", name="", client_id="c2", day=today)                        # 50 + 30
    for pool in ([named, recent], [recent, named]):
        found = match_details(_deposit(124.0, "ΤΠΥ-2026-0001", day=today), pool)
        assert found["invoice"] is named and found["confidence"] == 80 and found["ambiguous"] is False, \
            [p["_id"] for p in pool]


def test_a_description_that_names_another_open_invoice_is_never_settled_on_name_and_date_alone():
    """Τρίτος πληρώνει το τιμολόγιο ενός πελάτη (η αιτιολογία έχει τον αριθμό του) και το όνομά του ταιριάζει με
    άλλο τιμολόγιο ίδιου ποσού: το όνομα και η πρόσφατη ημερομηνία έδιναν 95% και εξοφλούνταν το λάθος."""
    from routes_bank import match_details
    today = athens_today()
    named = _inv_row(1, "ΤΠΥ-2026-0001", name="ΑΛΦΑ ΑΕ", client_id="c1", day=today - timedelta(days=45))   # 80
    other = _inv_row(2, "ΤΠΥ-2026-0002", name="ΒΗΤΑ ΕΠΕ", client_id="c2", day=today)                       # 95
    deposit = _deposit(124.0, "ΒΗΤΑ ΕΠΕ ΠΛΗΡΩΜΗ ΤΠΥ-2026-0001", day=today)
    for pool in ([named, other], [other, named]):
        found = match_details(deposit, pool)
        assert found["ambiguous"] is True and found["confidence"] <= 75, [p["_id"] for p in pool]
    # αντίθετα: όταν το τιμολόγιο που κατονομάζεται είναι και αυτό που προκρίνεται, δεν υπάρχει αμφιβολία
    clear = match_details(_deposit(124.0, "ΤΠΥ-2026-0001", day=today), [named, other])
    assert clear["invoice"] is named and clear["ambiguous"] is False


def test_naming_the_matched_invoice_is_enough_even_if_another_number_is_also_quoted():
    """Η αιτιολογία μπορεί να αναφέρει και άλλον αριθμό (π.χ. «υπόλοιπο ΤΠΥ-…»). Όταν το τιμολόγιο που ταιριάζει στο
    ποσό είναι και αυτό που κατονομάζεται, ο δεύτερος αριθμός δεν το κάνει αμφίβολο."""
    from routes_bank import match_details
    today = athens_today()
    paid = _inv_row(1, "ΤΠΥ-2026-0001", due=124.0, name="", client_id="c1", day=today)                       # 50 + 30 + 20
    other = _inv_row(2, "ΤΠΥ-2026-0002", due=124.5, name="", client_id="c1", day=today - timedelta(days=45))  # 30 + 10 + 20
    found = match_details(_deposit(124.0, "ΤΠΥ-2026-0001 ΚΑΙ ΤΠΥ-2026-0002", day=today), [paid, other])
    assert found["invoice"] is paid and found["ambiguous"] is False and found["confidence"] == 100


@pytest.mark.parametrize("confidence,ambiguous,expected", [
    (100, False, True), (76, False, True),                  # ισχυρή: μονοσήμαντη, ακριβές ποσό
    (75, False, False),                                     # στο όριο αβεβαιότητας (διαφορά ποσού ή χαμηλό σκορ)
    (74, False, False), (60, False, False), (0, False, False), (None, False, False),
    (100, True, False), (80, True, False),                  # αμφίβολη: ό,τι σκορ κι αν έχει
])
def test_only_a_strong_unambiguous_suggestion_reserves_its_invoice(confidence, ambiguous, expected):
    from routes_bank import reserves_invoice
    assert reserves_invoice(confidence, ambiguous) is expected


def test_the_match_pool_hides_only_the_invoices_that_are_really_taken(client, office):
    import routes_bank
    from db import db
    ids = [_issued(client, office.alpha)["id"] for _ in range(6)]

    def tx(n, invoice_id, status, confidence, **extra):
        return {"firm_id": office.firm_id, "external_id": f"t-{n}", "amount": 124.0, "date": _today(), "description": "",
                "matched_invoice_id": invoice_id, "match_status": status, "match_confidence": confidence, **extra}

    _run(db.bank_transactions.insert_many([
        tx(0, ids[0], "auto_matched", 100, match_ambiguous=False),      # ισχυρή πρόταση → δεσμεύει
        tx(1, ids[1], "auto_matched", 75, match_ambiguous=False),       # ακριβές ποσό αλλά στο όριο αβεβαιότητας
        tx(2, ids[2], "auto_matched", 75, match_ambiguous=True),        # αμφίβολη
        tx(3, ids[3], "auto_matched", 95),                              # παλιά εγγραφή χωρίς σήμανση
        tx(4, ids[4], "manual_matched", 100),                           # επιβεβαιωμένη → δεσμεύει πάντα
        tx(5, ids[5], "auto_confirmed", 60, match_ambiguous=True),      # επιβεβαιωμένη, όσο χαμηλό κι αν ήταν το σκορ
    ]))
    pool = _run(routes_bank.build_match_pool(office.firm_id, {}))
    assert sorted(str(p["_id"]) for p in pool) == sorted(ids[1:4])


def _import(client, rows):
    body = "Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n" + "".join(
        f"{day:%d/%m/%Y};{description};;{credit};;\n" for day, description, credit in rows)
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": ("x.csv", body.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_ambiguous_suggestion_does_not_steal_the_invoice_a_later_deposit_names(client, office):
    """Δύο ίδια τιμολόγια του ίδιου πελάτη. Η πρώτη κατάθεση δεν λέει ποιο· η δεύτερη κατονομάζει το πρώτο. Παλιότερα
    η αμφίβολη πρόταση «έκρυβε» το πρώτο τιμολόγιο, η δεύτερη κατάθεση πήγαινε στο διπλανό με 95% και το εξόφλησε."""
    import automation
    from db import db
    first = _issued(client, office.alpha)
    second = _issued(client, office.alpha)
    out = _import(client, [(athens_today(), "ΑΛΦΑ ΑΕ", "124,00"),
                           (athens_today(), f"ΑΛΦΑ ΑΕ {first['number']}", "124,00")])
    assert out["created"] == 2 and out["auto_matched"] == 2
    vague = _run(db.bank_transactions.find_one({"description": "ΑΛΦΑ ΑΕ"}))
    named = _run(db.bank_transactions.find_one({"description": f"ΑΛΦΑ ΑΕ {first['number']}"}))
    assert named["matched_invoice_id"] == first["id"] and named["match_ambiguous"] is False
    assert named["match_confidence"] == 100
    assert vague["match_ambiguous"] is True and vague["match_confidence"] <= 75

    result = _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90}))
    assert result["confirmed"] == 1
    assert _doc(first["id"])["status"] == "paid" and _doc(first["id"])["paid_via"] == "bank_auto"
    assert _doc(second["id"])["status"] == "issued"                  # το διπλανό τιμολόγιο ΔΕΝ εξοφλήθηκε


def test_a_weak_old_line_does_not_block_the_exact_deposit_that_names_the_invoice(client, office):
    """Μια παλιά γραμμή με το όνομα του πελάτη (74%) έκρυβε το μοναδικό τιμολόγιο· η ακριβής κατάθεση με τον
    αριθμό του έμενε χωρίς αντιστοίχιση και χωρίς καμία ειδοποίηση."""
    import automation
    from db import db
    inv = _issued(client, office.alpha)
    out = _import(client, [(athens_today() - timedelta(days=90), "ΑΛΦΑ ΑΕ", "124,00"),
                           (athens_today(), f"ΕΜΒΑΣΜΑ {inv['number']}", "124,00")])
    assert out["created"] == 2 and out["auto_matched"] == 2
    old = _run(db.bank_transactions.find_one({"description": "ΑΛΦΑ ΑΕ"}))
    new = _run(db.bank_transactions.find_one({"description": f"ΕΜΒΑΣΜΑ {inv['number']}"}))
    assert old["match_confidence"] == 74 and old["match_ambiguous"] is False
    assert new["matched_invoice_id"] == inv["id"] and new["match_confidence"] == 100

    result = _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90}))
    assert result["confirmed"] == 1 and result["sent_for_review"] == 1       # η παλιά γραμμή πάει σε άνθρωπο
    assert _doc(inv["id"])["status"] == "paid"
    assert len(_notes("reconcile_review")) == 1


def test_psd2_sync_lets_the_exact_deposit_that_names_the_invoice_win_over_a_weak_old_line(monkeypatch):
    import automation
    import openbanking
    from db import db
    firm = "firm-psd2-weak"
    cid = _psd2_firm(firm)
    invoice_id = _psd2_invoice(firm, cid, "ΤΠΥ-2026-0001")

    async def fake_fetch(settings, account_id, date_from=None):
        return [{"date": (athens_today() - timedelta(days=90)).isoformat(), "amount": 620.0,
                 "description": "Παπαδόπουλος", "reference": "", "external_id": "TX-OLD"},
                {"date": _today(), "amount": 620.0, "description": "ΕΜΒΑΣΜΑ ΤΠΥ-2026-0001", "reference": "",
                 "external_id": "TX-NEW"}]

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    out = _run(automation.run_firm(firm, ["bank_sync"]))[0]
    assert out["status"] != "error", out.get("error")
    assert out["stats"]["created"] == 2 and out["stats"]["auto_matched"] == 2
    old = _run(db.bank_transactions.find_one({"external_id": "psd2:acc-1:TX-OLD"}))
    new = _run(db.bank_transactions.find_one({"external_id": "psd2:acc-1:TX-NEW"}))
    assert old["match_confidence"] == 74 and old["match_ambiguous"] is False
    assert new["matched_invoice_id"] == str(invoice_id) and new["match_confidence"] == 100


# ===================================== 8. Τράπεζα: κάρτες ελέγχου που δεν ισχύουν πια ====

def _review_card_for(client, office, inv, description, credit):
    """Εισαγωγή κατάθεσης που δεν εξοφλείται αυτόματα + τρέξιμο του job → κάρτα ελέγχου."""
    import automation
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": (
        "x.csv", ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n"
                  f"{athens_today():%d/%m/%Y};{description};;{credit};;\n").encode(), "text/csv")})
    assert r.status_code == 200 and r.json()["auto_matched"] == 1, r.text
    out = _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90}))
    assert out["sent_for_review"] == 1, out
    from db import db
    return _run(db.bank_transactions.find_one({})), _notes("reconcile_review")[0]


def _resolve(card, decision):
    import automation
    return _run(automation.resolve_notification(card["firm_id"], str(card["_id"]), "u@example.gr", decision))


def _card(card_id):
    from db import db
    return _run(db.notifications.find_one({"_id": card_id}))


def test_autopilot_closes_the_review_card_when_it_releases_the_deposit(client, office):
    import automation
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    assert card.get("resolved_at") is None                        # το πεδίο λείπει όσο η κάρτα είναι ανοιχτή
    _set(inv["id"], status="paid", paid_via="stripe")             # στο μεταξύ πληρώθηκε με κάρτα
    out = _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90}))
    assert out["sent_for_review"] == 1
    closed = _card(card["_id"])
    assert closed.get("resolved_at") is not None and closed["resolution"] == "superseded"
    assert len(_notes("reconcile_conflict")) == 1


def test_confirming_a_card_whose_deposit_has_no_invoice_any_more_does_nothing_and_says_so(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    _run(db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {
        "match_status": "unmatched", "matched_invoice_id": None, "matched_invoice_number": None}}))
    assert _resolve(card, "confirm") == {"resolution": "stale"}
    assert _doc(inv["id"])["status"] == "issued"
    assert _card(card["_id"])["resolution"] == "stale"


def test_confirm_is_refused_when_the_amount_owed_changed_after_the_card_was_raised(client, office):
    import automation
    inv = _issued(client, office.alpha)                           # οφείλονται 124,00
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    assert tx["review_due"] == 124.0
    _set(inv["id"], trust_applied=100.0)                          # τώρα οφείλονται 24,00
    with pytest.raises(automation.SettlementRefused) as refused:
        _resolve(card, "confirm")
    assert refused.value.reason == "due_changed" and "24,00 €" in refused.value.message
    message, labels = refused.value.message, {a["label"] for a in _card(card["_id"])["actions"]}
    assert "Τραπεζικές κινήσεις" not in message                   # οθόνη που δεν υπάρχει στην εφαρμογή
    for label in ("Όχι, άλλο τιμολόγιο", "Αγνόησε την κίνηση"):   # προτείνονται μόνο κουμπιά που έχει η κάρτα
        assert label in labels and f"«{label}»" in message
    assert _card(card["_id"]).get("resolved_at") is None          # η κάρτα μένει ανοιχτή για νέα απόφαση
    assert _doc(inv["id"])["status"] == "issued"

    _set(inv["id"], trust_applied=0.0)                            # αν το ποσό ξαναγίνει αυτό που είδε ο άνθρωπος…
    assert _resolve(card, "confirm") == {"resolution": "confirmed"}
    assert _doc(inv["id"])["status"] == "paid" and _doc(inv["id"])["paid_via"] == "bank_manual"


def test_ignoring_or_settling_a_deposit_closes_its_open_cards(client, office):
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    assert client.post(f"/api/bank/reconcile/{tx['_id']}?ignore=true").status_code == 200
    assert _card(card["_id"])["resolution"] == "ignored"

    other = _issued(client, office.beta)
    from db import db
    _run(db.notifications.delete_many({}))
    _run(db.bank_transactions.delete_many({}))
    tx2, card2 = _review_card_for(client, office, other, "ΒΗΤΑ ΕΠΕ", "123,50")
    ok = client.post(f"/api/bank/reconcile/{tx2['_id']}?invoice_id={other['id']}&accept_difference=true")
    assert ok.status_code == 200, ok.text
    assert _card(card2["_id"])["resolution"] == "already_settled"


def test_bulk_confirmation_closes_the_cards_it_settles(client, office):
    inv = _issued(client, office.alpha)
    from db import db
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": (
        "x.csv", ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n"
                  f"{athens_today():%d/%m/%Y};ΑΛΦΑ ΑΕ {inv['number']};;124,00;;\n").encode(), "text/csv")})
    assert r.json()["auto_matched"] == 1
    tx = _run(db.bank_transactions.find_one({}))
    import automation
    _run(automation.notify(office.firm_id, "reconcile_review", "warning", "t", entity="bank_tx", entity_id=tx["_id"],
                           requires_action=True, dedupe_key=f"reconcile_review:{tx['_id']}"))
    assert client.post("/api/bank/confirm-auto-matches").json()["confirmed"] == 1
    assert _notes("reconcile_review")[0]["resolution"] == "already_settled"


def test_suggestions_that_predate_the_ambiguity_check_are_flagged_at_startup():
    import server
    from db import db

    async def scenario():
        await db.bank_transactions.insert_many([
            {"firm_id": "f", "external_id": "legacy", "match_status": "auto_matched", "amount": 1.0},
            {"firm_id": "f", "external_id": "checked", "match_status": "auto_matched", "amount": 1.0, "match_ambiguous": False},
            {"firm_id": "f", "external_id": "done", "match_status": "manual_matched", "amount": 1.0},
            {"firm_id": "f", "external_id": "none", "match_status": "unmatched", "amount": 1.0},
        ])
        assert await server.flag_unchecked_bank_suggestions() == 1
        assert await server.flag_unchecked_bank_suggestions() == 0       # idempotent
        flags = {d["external_id"]: d.get("match_ambiguous") async for d in db.bank_transactions.find({})}
        assert flags == {"legacy": True, "checked": False, "done": None, "none": None}

    _run(scenario())


def test_closing_the_cards_of_one_deposit_leaves_every_other_card_open(client, office):
    import automation
    import invoice_services as svc
    from db import db
    first, second = _run(db.bank_transactions.insert_many(
        [{"firm_id": office.firm_id, "external_id": f"tx-{n}", "amount": 10.0, "match_status": "auto_matched"}
         for n in range(2)])).inserted_ids
    for tx_id in (first, second):
        _run(automation.notify(office.firm_id, "reconcile_review", "warning", "t", entity="bank_tx", entity_id=tx_id,
                               requires_action=True, dedupe_key=f"reconcile_review:{tx_id}"))
    _run(automation.notify(office.firm_id, "something_else", "info", "t", entity="bank_tx", entity_id=first,
                           dedupe_key=f"other:{first}"))                       # άλλο είδος, ίδια κίνηση
    _run(automation.notify("firm-elsewhere", "reconcile_review", "warning", "t", entity="bank_tx", entity_id=first,
                           requires_action=True, dedupe_key=f"foreign:{first}"))  # ίδιο id, άλλο γραφείο

    assert _run(svc.close_tx_notifications(office.firm_id, first, "ignored", "u@example.gr")) == 1
    cards = {n["dedupe_key"]: n for n in _run(db.notifications.find({}).to_list(20))}
    assert cards[f"reconcile_review:{first}"]["resolution"] == "ignored"
    for untouched in (f"reconcile_review:{second}", f"other:{first}", f"foreign:{first}"):
        assert cards[untouched].get("resolved_at") is None, untouched


def test_confirm_on_a_card_whose_invoice_is_no_longer_open_is_refused_with_the_reason(client, office):
    import automation
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    assert tx["review_due"] == 124.0
    _set(inv["id"], status="cancelled")                          # ακυρώθηκε αφού δημιουργήθηκε η κάρτα
    with pytest.raises(automation.SettlementRefused) as refused:
        _resolve(card, "confirm")
    assert refused.value.reason == "invoice_not_open"
    assert _card(card["_id"]).get("resolved_at") is None         # η κάρτα μένει ανοιχτή
    assert _doc(inv["id"])["status"] == "cancelled"

    # στο API: μήνυμα που εξηγεί την άρνηση, όχι ένα τεχνικό «Αποτυχία: 'due'»
    r = client.post(f"/api/automation/notifications/{card['_id']}/resolve", json={"decision": "confirm"})
    assert r.status_code == 400 and "δεν είναι ανοιχτό" in r.json()["detail"] and "'due'" not in r.json()["detail"]


def test_application_startup_flags_legacy_suggestions():
    """Η σήμανση τρέχει στην εκκίνηση της εφαρμογής — δεν αρκεί να υπάρχει η συνάρτηση."""
    import server
    from db import db
    _run(db.bank_transactions.insert_one({"firm_id": "f", "external_id": "legacy", "match_status": "auto_matched",
                                          "amount": 1.0}))
    with TestClient(server.app, base_url="https://testserver"):
        pass
    assert _run(db.bank_transactions.find_one({"external_id": "legacy"}))["match_ambiguous"] is True


# ===================================================================== 9. Έξοδα ====

def _expense(**over):
    body = {"vendor": "Προμηθευτής", "date": "2026-09-20", "amount": 124.0,
            "vat_amount": 24.0, "vat_rate": 24, "category": "Λοιπά"}
    body.update(over)
    return body


def test_duplicate_mark_is_refused_by_the_application_even_without_the_database_index(client, office):
    """Αν το unique index δεν μπορέσει να δημιουργηθεί, η εφαρμογή ξεκινά — και πρέπει να προστατεύει μόνη της."""
    from db import db
    _run(db.expenses.drop_index("uniq_firm_mydata_mark"))
    assert "uniq_firm_mydata_mark" not in _run(db.expenses.index_information())

    assert client.post("/api/expenses", json=_expense(mydata_mark="400000000001")).status_code == 200
    dup = client.post("/api/expenses", json=_expense(vendor="Άλλος", mydata_mark="400000000001"))
    assert dup.status_code == 409 and "MARK" in dup.json()["detail"]
    other = client.post("/api/expenses", json=_expense(vendor="Β", mydata_mark="400000000002")).json()
    clash = client.put(f"/api/expenses/{other['id']}", json=_expense(vendor="Β", mydata_mark="400000000001"))
    assert clash.status_code == 409
    same = client.put(f"/api/expenses/{other['id']}", json=_expense(vendor="Β νέο", mydata_mark="400000000002"))
    assert same.status_code == 200                                # το ίδιο το έξοδο δεν συγκρούεται με τον εαυτό του
    assert len(client.get("/api/expenses").json()) == 2


def test_expenses_without_a_mark_are_never_duplicates_of_each_other(client, office):
    for vendor in ("Α", "Β", "Γ"):
        assert client.post("/api/expenses", json=_expense(vendor=vendor)).status_code == 200
        assert client.post("/api/expenses", json=_expense(vendor=vendor + "2", mydata_mark="")).status_code == 200
        assert client.post("/api/expenses", json=_expense(vendor=vendor + "3", mydata_mark="  ")).status_code == 200
    assert len(client.get("/api/expenses").json()) == 9


def test_the_same_mark_in_another_office_is_not_a_duplicate(client, office):
    import server
    assert client.post("/api/expenses", json=_expense(mydata_mark="400000000777")).status_code == 200
    with TestClient(server.app, base_url="https://testserver") as other:
        assert other.post("/api/auth/register", json={"email": "other-exp@example.gr", "password": "Str0ngPass!",
                                                       "name": "Άλλο γραφείο"}).status_code == 200
        mine = other.post("/api/expenses", json=_expense(vendor="Άλλου", mydata_mark="400000000777"))
        assert mine.status_code == 200, mine.text
        assert other.post("/api/expenses", json=_expense(vendor="Ξανά", mydata_mark="400000000777")).status_code == 409


# ================================================================ 10. Πύλη πελάτη ====

def test_portal_shows_what_the_client_actually_owes(client, office):
    _enable_withholding(client)
    inv = _issued(client, office.alpha, net=1000.0)               # 1.240 με παρακράτηση 200 → 1.040
    _set(inv["id"], trust_applied=400.0)                          # → οφείλονται 640
    plain = _issued(client, office.beta, net=100.0)
    token = client.post("/api/client-portal/invite", json={"client_id": office.alpha}).json()["token"]
    data = client.get(f"/api/portal/{token}").json()
    row = data["invoices"][0]
    assert (row["total"], row["payable"], row["outstanding"]) == (1240.0, 1040.0, 640.0)
    assert data["totals"]["unpaid_total"] == 640.0 and data["totals"]["unpaid_count"] == 1
    assert plain["id"] not in [r["id"] for r in data["invoices"]]

    _set(inv["id"], status="paid")
    paid = client.get(f"/api/portal/{token}").json()
    assert paid["invoices"][0]["outstanding"] == 0.0 and paid["totals"]["unpaid_total"] == 0.0


# ====================================================================== 11. PDF ====

@pytest.fixture()
def pdf_spy(monkeypatch):
    """Συλλέγει τα κείμενα που μπαίνουν στο PDF. Το λογότυπο ζει στο path του Docker (/app/backend/assets),
    που δεν υπάρχει στα unit tests — το αντικαθιστούμε με κενό στοιχείο, δεν παίζει ρόλο για την ημερομηνία."""
    import pdf_utils
    import reportlab.platypus as platypus
    seen = []
    real = pdf_utils._p

    def spy(text, *a, **k):
        seen.append(text)
        return real(text, *a, **k)

    monkeypatch.setattr(pdf_utils, "_p", spy)
    monkeypatch.setattr(platypus, "Image", lambda *a, **k: platypus.Spacer(1, 1))
    return seen


def test_grammatio_pdf_carries_the_athens_date_of_issue_not_the_download_date(pdf_spy):
    import pdf_utils
    # 22:30 UTC της παραμονής = 00:30 της Πρωτοχρονιάς στην Αθήνα
    pdf_utils.build_grammatio_pdf({"number": "ΓΡΠ-1", "status": "issued", "issued_at": "2026-12-31T22:30:00+00:00",
                                   "client_name": "ΑΛΦΑ", "reference_amount": 100.0, "total": 124.0}, {})
    assert "1 Ιανουαρίου 2027" in pdf_spy


def test_grammatio_pdf_of_an_issued_document_does_not_change_date_when_downloaded_again(pdf_spy, monkeypatch):
    import pdf_utils
    import tax_utils
    monkeypatch.setattr(tax_utils, "athens_today", lambda: date(2030, 5, 5))      # «σήμερα» πολύ αργότερα
    pdf_utils.build_grammatio_pdf({"number": "ΓΡΠ-1", "status": "issued", "issued_at": "2026-03-10T09:00:00+00:00",
                                   "client_name": "ΑΛΦΑ", "reference_amount": 100.0, "total": 124.0}, {})
    assert "10 Μαρτίου 2026" in pdf_spy and not any("2030" in str(t) for t in pdf_spy)


def test_letter_pdf_is_dated_in_athens_not_in_utc(pdf_spy, monkeypatch):
    import pdf_utils
    import tax_utils
    monkeypatch.setattr(tax_utils, "athens_today", lambda: date(2027, 1, 1))      # 00:30 Πρωτοχρονιάς στην Αθήνα
    pdf_utils.build_letter_pdf({"company_name": "Γραφείο"}, "Προς Δοκιμή", "Θέμα", "Κείμενο.")
    assert "Αθήνα, 1 Ιανουαρίου 2027" in pdf_spy


def test_document_date_helper():
    import pdf_utils
    assert pdf_utils._document_date("2026-12-31T22:30:00+00:00") == date(2027, 1, 1)
    assert pdf_utils._document_date("2026-06-15T10:00:00") == date(2026, 6, 15)       # χωρίς ζώνη = UTC
    assert pdf_utils._document_date("2026-06-15T23:30:00Z") == date(2026, 6, 16)
    assert pdf_utils._document_date(None) == athens_today()
    assert pdf_utils._document_date("δεν-είναι-ημερομηνία") == athens_today()


# ============================== 12. Δεύτερη αναθεώρηση (ανεξάρτητος έλεγχος των διορθώσεων του γύρου) ====
#
# Δύο reviewers εξέτασαν μόνο τις αλλαγές των παραπάνω διορθώσεων και βρήκαν — με εκτέλεση κώδικα — ότι
# μερικές έφερναν νέο πρόβλημα (διπλός αριθμός τιμολογίου μετά από χαμένη απάντηση, πύλη που έκρυβε το πιστωτικό,
# αμφίβολη κατάθεση που κατονόμαζε τιμολόγιο εκτός υποψηφίων). Κάθε test εδώ ισχύει για ένα τέτοιο εύρημα.

class _HookedCollection:
    """Συλλογή με «άγκιστρα»: το hook(real_method, *args, **kwargs) αντικαθιστά τη μέθοδο και αποφασίζει τι κάνει."""

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
    """Η βάση, με άγκιστρα σε μία συλλογή. Η mongomock δημιουργεί νέο wrapper σε κάθε `db.<συλλογή>`, οπότε η
    αντικατάσταση μεθόδου πάνω στη συλλογή δεν έχει αποτέλεσμα — αντικαθίσταται το `db` της μονάδας."""

    def __init__(self, real, collection, hooks):
        self._real, self._collection, self._hooks = real, collection, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _HookedCollection(attr, self._hooks) if name == self._collection else attr


async def _bare_draft(firm, **over):
    from db import db
    doc = {"firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
           "subtotal": 100.0, "total": 124.0, "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


# ---- 12.1 Αριθμός τιμολογίου μετά από αποτυχία ή ακύρωση στο τελευταίο βήμα ----

@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_a_failure_after_the_numbered_write_never_hands_the_same_number_out_twice(monkeypatch, failure):
    """Η τελική εγγραφή (πρόχειρο → εκδοθέν με αριθμό) μπορεί να έχει ήδη εφαρμοστεί όταν χάνεται η απάντηση ή
    ακυρώνεται το αίτημα. Ο καθαρισμός επέστρεφε τότε τον αριθμό στη σειρά και το επόμενο τιμολόγιο έπαιρνε τον ίδιο
    αριθμό — διπλός αριθμός σε φορολογική σειρά.

    Το πρώτο τιμολόγιο της σειράς δεν δείχνει το σφάλμα: στην πρώτη χρήση του μετρητή η εφαρμογή ξανασαρώνει τους
    εκδοθέντες αριθμούς και διορθώνει μόνη της τον μετρητή. Γι' αυτό εκδίδεται πρώτα ένα κανονικό (-0001) και
    αποτυγχάνει το δεύτερο (-0002) — η μετάλλαξη «ο καθαρισμός επιστρέφει πάντα τον αριθμό» επιζούσε με το πρώτο."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = f"firm-lost-reply-{failure.__name__}"
        base = await svc.issue_draft(firm, await db.invoices.find_one({"_id": await _bare_draft(firm)}), {})
        assert base["number"].endswith("-0001")

        failing = await _bare_draft(firm)
        calls = {"n": 0}

        async def lost_reply(real, query, update, *args, **kwargs):
            calls["n"] += 1
            result = await real(query, update, *args, **kwargs)
            if calls["n"] == 2:                      # 1η κλήση: κλείδωμα · 2η: η τελική εγγραφή
                raise failure()
            return result

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one_and_update": lost_reply}))
        with pytest.raises(failure):
            await svc.issue_draft(firm, await db.invoices.find_one({"_id": failing}), {})
        monkeypatch.setattr(svc, "db", db)

        stored = await db.invoices.find_one({"_id": failing})
        assert stored["status"] == "issued" and stored["number"].endswith("-0002")
        third = await svc.issue_draft(firm, await db.invoices.find_one({"_id": await _bare_draft(firm)}), {})
        assert third["number"].endswith("-0003")     # όχι δεύτερο -0002

    _run(scenario())


def test_when_the_state_cannot_be_read_back_the_number_is_not_returned_to_the_series(monkeypatch):
    """Αν μετά από αποτυχία δεν μπορεί να διαβαστεί η κατάσταση του τιμολογίου, ο αριθμός δεν επιστρέφεται:
    ένα κενό στη σειρά είναι ασφαλέστερο από ένα διπλότυπο."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-unreadable-state"
        first = await _bare_draft(firm)

        async def unreadable(real, query, projection=None, *args, **kwargs):
            if projection == {"status": 1, "number": 1}:          # η ανάγνωση του καθαρισμού
                raise RuntimeError("η βάση δεν απαντά")
            return await real(query, projection, *args, **kwargs)

        def boom(*a, **k):
            raise RuntimeError("σφάλμα μετά την παραχώρηση αριθμού")

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"find_one": unreadable}))
        monkeypatch.setattr(svc, "withholding_for", boom)
        with pytest.raises(RuntimeError, match="σφάλμα μετά"):
            await svc.issue_draft(firm, await db.invoices.find_one({"_id": first}), {})
        monkeypatch.undo()

        after = await db.invoices.find_one({"_id": first})
        assert after["status"] == "draft" and "issue_claim" not in after       # το κλείδωμα λύθηκε κανονικά
        ok = await svc.issue_draft(firm, after, {})
        assert ok["number"].endswith("-0002")                                  # το -0001 έμεινε κενό, δεν ξαναδόθηκε

    _run(scenario())


def test_a_draft_credit_note_is_not_issued_once_its_original_was_cancelled(client, office):
    """Ακύρωση και πιστωτικό ταυτόχρονα από δύο χρήστες: αν εκδιδόταν και το πιστωτικό, το ίδιο ποσό θα αναιρούνταν δύο φορές."""
    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400001111111111")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    _set(original["id"], status="cancelled")                 # ακυρώθηκε στο μεταξύ από άλλον χρήστη
    out = client.post(f"/api/invoices/{credit['id']}/issue")
    assert out.status_code == 400 and "ακυρωθεί" in out.json()["detail"] and original["number"] in out.json()["detail"]
    stored = _doc(credit["id"])
    assert stored["status"] == "draft" and not stored.get("number") and "issue_claim" not in stored


def test_a_credit_note_draft_whose_original_id_cannot_be_read_does_not_crash_the_issue(client, office):
    """Το `credit_note_for` το γράφει πάντα ο server, αλλά ένα έγγραφο με χαλασμένη τιμή δεν πρέπει να δίνει HTTP 500
    στην έκδοση: ο έλεγχος «το αρχικό ακυρώθηκε» απλώς δεν μπορεί να γίνει και η έκδοση συνεχίζει όπως πριν."""
    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400001111111111")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    _set(credit["id"], credit_note_for="δεν-είναι-έγκυρο-id")
    out = client.post(f"/api/invoices/{credit['id']}/issue")
    assert out.status_code == 200, out.text
    assert _doc(credit["id"])["status"] == "issued"


# ---- 12.2 Τράπεζα: κατάθεση που κατονομάζει τιμολόγιο εκτός υποψηφίων ----

def test_a_deposit_quoting_a_bigger_invoice_is_never_settled_on_the_smaller_one():
    """Μια δόση που αναγράφει τον αριθμό ενός μεγαλύτερου τιμολογίου (άλλο ποσό, άρα όχι υποψήφιο) δεν πρέπει να
    εξοφλήσει αυτόματα το διπλανό, ίδιου ποσού με την κατάθεση. Ο έλεγχος «κατονομάζεται άλλο τιμολόγιο» εξέταζε
    μόνο τους υποψηφίους κατά ποσό."""
    from routes_bank import match_details
    today = athens_today()
    small = _inv_row(1, "ΤΠΥ-2026-0001", due=124.0, name="ΑΛΦΑ ΑΕ", client_id="c1", day=today)
    big = _inv_row(2, "ΤΠΥ-2026-0002", due=5000.0, name="ΑΛΦΑ ΑΕ", client_id="c1", day=today)
    for pool in ([small, big], [big, small]):
        found = match_details(_deposit(124.0, "ΑΛΦΑ ΑΕ ΔΟΣΗ ΤΠΥ-2026-0002", day=today), pool)
        assert found["invoice"] is small and found["ambiguous"] is True and found["confidence"] <= 75
    # μάρτυρες: όταν η αιτιολογία κατονομάζει το ίδιο το τιμολόγιο, ή κανένα, δεν υπάρχει αμφιβολία
    named = match_details(_deposit(124.0, "ΑΛΦΑ ΑΕ ΤΠΥ-2026-0001", day=today), [small, big])
    assert named["invoice"] is small and named["ambiguous"] is False
    silent = match_details(_deposit(124.0, "ΑΛΦΑ ΑΕ", day=today), [small, big])
    assert silent["invoice"] is small and silent["ambiguous"] is False


def test_an_invoice_without_a_number_is_never_taken_as_named_by_a_description():
    """Το κενό αλφαριθμητικό περιέχεται σε κάθε αιτιολογία: ένα τιμολόγιο χωρίς αριθμό (παλιά εγγραφή) θα «κατονομαζόταν»
    παντού και θα έστελνε κάθε καθαρή αντιστοίχιση σε έλεγχο από άνθρωπο."""
    from routes_bank import _number_in, match_details
    assert _number_in({"number": ""}, "αλφα αε δοση") is False
    assert _number_in({"number": None}, "αλφα αε δοση") is False
    assert _number_in({}, "αλφα αε δοση") is False
    assert _number_in({"number": "ΤΠΥ-2026-0007"}, "δοση τπυ-2026-0007") is True       # μάρτυρας: ο έλεγχος δεν χαλάει
    today = athens_today()
    clean = _inv_row(1, "ΤΠΥ-2026-0001", due=124.0, day=today)
    blank = _inv_row(2, "", due=5000.0, day=today)
    found = match_details(_deposit(124.0, "ΑΛΦΑ ΑΕ", day=today), [clean, blank])
    assert found["invoice"] is clean and found["ambiguous"] is False


def test_an_installment_quoting_another_invoice_goes_to_review_not_to_automatic_payment(client, office):
    """Το ίδιο, από την εισαγωγή CSV ως το job συμφωνίας."""
    import automation
    from db import db
    small = _issued(client, office.alpha, net=100.0)          # 124,00
    big = _issued(client, office.alpha, net=5000.0)           # 6.200,00
    out = _import(client, [(athens_today(), f"ΑΛΦΑ ΑΕ ΔΟΣΗ {big['number']}", "124,00")])
    assert out["auto_matched"] == 1 and out["needs_review"] == 1
    tx = _run(db.bank_transactions.find_one({}))
    assert tx["matched_invoice_id"] == small["id"] and tx["match_ambiguous"] is True and tx["match_confidence"] <= 75
    assert _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90})) == \
        {"confirmed": 0, "sent_for_review": 1}
    assert _doc(small["id"])["status"] == "issued" and _doc(big["id"])["status"] == "issued"
    assert "άλλα τιμολόγια" in _notes("reconcile_review")[0]["message"]


# ---- 12.3 Πύλη πελάτη: το πιστωτικό συμψηφίζεται ----

def _credit(client, invoice, **body):
    """Διαβιβασμένο τιμολόγιο → εκδοθέν πιστωτικό."""
    _set(invoice["id"], mydata_status="synced", mydata_mark="4000" + str(abs(hash(invoice["id"])))[:11].ljust(11, "0"))
    draft = client.post(f"/api/invoices/{invoice['id']}/credit-note", json=body)
    assert draft.status_code == 200, draft.text
    issued = client.post(f"/api/invoices/{draft.json()['id']}/issue")
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_portal_total_nets_a_credit_note_against_the_invoice_it_credits(client, office):
    """Το πιστωτικό (αρνητικό σύνολο) εμφανιζόταν με «οφειλόμενο 0» και το σύνολο της πύλης έδειχνε το πλήρες ποσό του
    τιμολογίου που ο πελάτης δεν χρωστά πια. Το άθροισμα είναι προσημασμένο, χωρίς να πέφτει κάτω από το μηδέν."""
    def portal(client_id):
        token = client.post("/api/client-portal/invite", json={"client_id": client_id}).json()["token"]
        return client.get(f"/api/portal/{token}").json()

    # μερικό πιστωτικό: 1.240 − 496 (καθαρή 400 + ΦΠΑ 24%) = 744
    inv = _issued(client, office.alpha, net=1000.0)
    credit = _credit(client, inv, amount=400.0)
    data = portal(office.alpha)
    rows = {r["id"]: r for r in data["invoices"]}
    assert (rows[inv["id"]]["outstanding"], rows[credit["id"]]["outstanding"]) == (1240.0, -496.0)
    # Το πλήθος μετρά τα τιμολόγια που πράγματι οφείλονται (όχι το πιστωτικό): ένα, με οφειλόμενο 744 μετά τον συμψηφισμό.
    assert data["totals"]["unpaid_total"] == 744.0 and data["totals"]["unpaid_count"] == 1

    # ολικό πιστωτικό: μηδέν
    whole = _issued(client, office.beta, net=100.0)
    _credit(client, whole)
    assert portal(office.beta)["totals"]["unpaid_total"] == 0.0

    # πιστωτικό για ήδη εξοφλημένο τιμολόγιο: αρνητικό σύνολο δεν εμφανίζεται ως χρέος του γραφείου προς τον πελάτη
    paid = _issued(client, office.beta, net=200.0)
    _credit(client, paid)
    _set(paid["id"], status="paid")
    assert portal(office.beta)["totals"]["unpaid_total"] == 0.0


# ---- 12.4 Τιμολόγια με πιστωτικό δεν εισπράττονται αυτόματα ----

def _with_credit_draft(client, office, amount=20.0):
    inv = _issued(client, office.alpha)                                    # 124,00
    _set(inv["id"], mydata_status="synced", mydata_mark="400003333333333")
    out = client.post(f"/api/invoices/{inv['id']}/credit-note", json={"amount": amount})
    assert out.status_code == 200, out.text
    return inv


def test_an_invoice_with_a_credit_note_gets_no_card_link_and_no_reminder(client, office, stripe_calls):
    import automation
    import invoice_services as svc
    from routes_payments import _validate_invoice_and_get_amount
    inv = _with_credit_draft(client, office)
    stored, settings = _doc(inv["id"]), _settings(office.firm_id)
    assert stored["credit_note_id"]

    with pytest.raises(HTTPException) as exc:                                           # κάρτα από την πύλη
        _run(_validate_invoice_and_get_amount(inv["id"], office.firm_id))
    assert exc.value.status_code == 400 and "πιστωτικό" in exc.value.detail
    assert _run(svc.build_payment_link(stored, settings)) == "" and stripe_calls == []   # σύνδεσμος πληρωμής

    old = {**stored, "date": (athens_today() - timedelta(days=90)).isoformat(), "reminder_count": 0}
    rules = {"reminder_after_days": 30}
    assert automation.reminder_due(old, rules) is False                                  # αυτόματη υπενθύμιση
    assert automation.reminder_due({k: v for k, v in old.items() if k != "credit_note_id"}, rules) is True
    with pytest.raises(svc.InvoiceEmailError) as refused:                                # χειροκίνητη υπενθύμιση
        _run(svc.send_reminder(office.firm_id, stored, settings))
    assert "πιστωτικό" in str(refused.value)

    # μάρτυρας: χωρίς πιστωτικό ο σύνδεσμος δημιουργείται κανονικά
    plain = _issued(client, office.beta)
    assert _run(svc.build_payment_link(_doc(plain["id"]), settings)) == "https://pay.example/cs_test_link"


def test_the_client_account_does_not_pay_an_invoice_that_has_a_credit_note(client, office):
    inv = _with_credit_draft(client, office)
    assert _tx(client, office.alpha, "deposit", 100.0).status_code == 200
    out = _tx(client, office.alpha, "apply_to_invoice", 50.0, invoice_id=inv["id"])
    assert out.status_code == 400 and "πιστωτικό" in out.json()["detail"]
    assert _account(client, office.alpha)["account"]["balance"] == 100.0
    assert "trust_applied" not in _doc(inv["id"]) and _doc(inv["id"])["status"] == "issued"


def test_a_deposit_for_an_invoice_with_a_credit_note_is_never_settled_automatically(client, office):
    import automation
    from db import db
    inv = _with_credit_draft(client, office)
    assert _import(client, [(athens_today(), f"ΑΛΦΑ ΑΕ {inv['number']}", "124,00")])["auto_matched"] == 1
    tx = _run(db.bank_transactions.find_one({}))
    assert tx["matched_invoice_id"] == inv["id"] and tx["match_ambiguous"] is False

    manual = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}&accept_difference=true")
    assert manual.status_code == 409 and "πιστωτικό" in manual.json()["detail"]
    assert _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90})) == \
        {"confirmed": 0, "sent_for_review": 1}
    assert _doc(inv["id"])["status"] == "issued"
    released = _run(db.bank_transactions.find_one({"_id": tx["_id"]}))
    assert released["match_status"] == "unmatched" and released["matched_invoice_id"] is None
    card = _notes("reconcile_conflict")[0]
    assert card["requires_action"] and "πιστωτικό" in card["message"] and "λογιστή" in card["message"]


# ---- 12.5 Ακύρωση που ανταγωνίζεται εξόφληση ----

@pytest.fixture()
def aade_cancel(monkeypatch):
    """Ψεύτικη ακύρωση στην ΑΑΔΕ: το `during` τρέχει όσο «περιμένουμε» την ΑΑΔΕ, δηλαδή ανταγωνίζεται την ακύρωση."""
    import invoice_services as svc
    box = {"during": None, "calls": 0}

    async def fake_cancel(settings, mark):
        box["calls"] += 1
        if box["during"]:
            await box["during"]()
        return {"cancellation_mark": "CM-0001"}

    monkeypatch.setattr(svc, "mydata_ready", lambda s: True)
    monkeypatch.setattr(svc, "cancel_invoice", fake_cancel)
    return box


def _synced(client, office):
    inv = _issued(client, office.alpha)
    _set(inv["id"], mydata_status="synced", mydata_mark="400005555555555")
    return inv


@pytest.mark.parametrize("meanwhile,expected,absent", [
    ({"status": "paid", "paid_via": "trust", "trust_applied": 124.0}, "trust_money_stranded", "cancelled_after_payment"),
    ({"status": "paid", "paid_via": "stripe"}, "cancelled_after_payment", "trust_money_stranded"),
    ({"status": "paid", "paid_via": "bank_auto"}, "cancelled_after_payment", "trust_money_stranded"),
    ({"status": "paid"}, "cancelled_after_payment", "trust_money_stranded"),             # χειροκίνητη εξόφληση
    ({"trust_applied": 60.0}, "trust_money_stranded", "cancelled_after_payment"),         # μερική εφαρμογή
])
def test_a_cancellation_that_loses_a_race_with_a_payment_raises_the_matching_decision_card(
        client, office, aade_cancel, meanwhile, expected, absent):
    """Όσο η ΑΑΔΕ απαντά, το τιμολόγιο εξοφλείται. Οι ειδοποιήσεις βγαίνουν από την κατάσταση τη στιγμή της
    εγγραφής, όχι από την ανάγνωση πριν την κλήση — αλλιώς τα χρήματα που εισπράχθηκαν περνούσαν απαρατήρητα."""
    import invoice_services as svc
    from db import db
    inv = _synced(client, office)
    stale = _doc(inv["id"])                                  # ό,τι διάβασε το αίτημα ακύρωσης

    async def payment_arrives():
        await db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": meanwhile})

    aade_cancel["during"] = payment_arrives
    _run(svc.cancel_in_mydata(office.firm_id, stale, _settings(office.firm_id)))
    final = _doc(inv["id"])
    assert aade_cancel["calls"] == 1 and final["status"] == "cancelled" and final["mydata_status"] == "cancelled"
    cards = _notes(expected)
    assert len(cards) == 1 and cards[0]["level"] == "critical" and cards[0]["requires_action"]
    assert _notes(absent) == []


def test_a_plain_cancellation_raises_no_card(client, office, aade_cancel):
    import invoice_services as svc
    inv = _synced(client, office)
    _run(svc.cancel_in_mydata(office.firm_id, _doc(inv["id"]), _settings(office.firm_id)))
    assert _doc(inv["id"])["status"] == "cancelled" and _doc(inv["id"])["mydata_cancellation_mark"] == "CM-0001"
    assert _notes("cancelled_after_payment") == [] and _notes("trust_money_stranded") == []


# ---- 12.6 Ένα πρόχειρο πιστωτικό ανά τιμολόγιο ----

def _synced_stale(client, office):
    inv = _synced(client, office)
    return inv, _doc(inv["id"])


def test_a_second_request_with_a_stale_copy_cannot_create_a_second_credit_note(client, office):
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)
    first = _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    with pytest.raises(svc.InvoiceWorkflowError) as refused:
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))      # παλιό αντίγραφο, χωρίς credit_note_id
    assert "ήδη" in str(refused.value)
    drafts = _run(db.invoices.find({"credit_note_for": inv["id"]}).to_list(10))
    original = _doc(inv["id"])
    assert len(drafts) == 1 and original["credit_note_id"] == str(first["_id"])
    assert "credit_claim" not in original and "credit_claim_at" not in original


def test_two_simultaneous_credit_note_requests_create_exactly_one(client, office, monkeypatch):
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def slow_insert(real, *args, **kwargs):
        await asyncio.sleep(0.02)                            # αναγκάζει τα δύο αιτήματα να αλληλοδιαπλακούν
        return await real(*args, **kwargs)

    async def scenario():
        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"insert_one": slow_insert}))
        return await asyncio.gather(
            svc.build_credit_note(office.firm_id, dict(stale), "a@example.gr", "", None),
            svc.build_credit_note(office.firm_id, dict(stale), "b@example.gr", "", None), return_exceptions=True)

    results = _run(scenario())
    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, svc.InvoiceWorkflowError)]
    assert len(winners) == 1 and len(losers) == 1, results
    drafts = _run(db.invoices.find({"credit_note_for": inv["id"]}).to_list(10))
    assert len(drafts) == 1 and _doc(inv["id"])["credit_note_id"] == str(winners[0]["_id"])


def test_a_credit_note_that_fails_midway_leaves_no_draft_and_no_lock(client, office, monkeypatch):
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def broken_insert(real, *args, **kwargs):
        raise RuntimeError("η βάση διακόπηκε")

    monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"insert_one": broken_insert}))
    with pytest.raises(RuntimeError):
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    monkeypatch.setattr(svc, "db", db)
    original = _doc(inv["id"])
    assert not original.get("credit_note_id") and "credit_claim" not in original
    assert _run(db.invoices.count_documents({"credit_note_for": inv["id"]})) == 0
    retry = _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))      # ξανά, κανονικά
    assert _doc(inv["id"])["credit_note_id"] == str(retry["_id"])


def test_a_credit_note_whose_lock_was_lost_is_removed_instead_of_left_orphaned(client, office, monkeypatch):
    """Αν το κλείδωμα χαθεί πριν τη σύνδεση (έληξε και το πήρε άλλο αίτημα), το πρόχειρο που μόλις μπήκε δεν πρέπει
    να μείνει ορφανό: θα μπορούσε να εκδοθεί και να πιστώσει το τιμολόγιο χωρίς να το ξέρει το ίδιο το τιμολόγιο."""
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def steal_lock(real, query, update, *args, **kwargs):
        if "credit_note_id" in update.get("$set", {}):
            await db.invoices.update_one({"_id": query["_id"]}, {"$set": {"credit_claim": "another-request"}})
        return await real(query, update, *args, **kwargs)

    monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"update_one": steal_lock}))
    with pytest.raises(svc.InvoiceWorkflowError) as refused:
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    monkeypatch.setattr(svc, "db", db)
    assert "έληξε" in str(refused.value)
    original = _doc(inv["id"])
    assert not original.get("credit_note_id") and original["credit_claim"] == "another-request"   # ξένο κλείδωμα: δεν πειράζεται
    assert _run(db.invoices.count_documents({"credit_note_for": inv["id"]})) == 0


def test_a_cancelled_credit_note_request_leaves_no_orphan_draft_and_no_lock_even_if_the_insert_was_applied(
        client, office, monkeypatch):
    """Η εισαγωγή του πρόχειρου μπορεί να εφαρμοστεί και να χαθεί μόνο η απάντηση (ακύρωση αιτήματος, διακοπή σύνδεσης).
    Χωρίς καθαρισμό θα έμενε ορφανό πρόχειρο πιστωτικό — εκδόσιμο, χωρίς το αρχικό να το γνωρίζει — και κλειδωμένο το αρχικό."""
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def lost_reply(real, doc, *args, **kwargs):
        await real(doc, *args, **kwargs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"insert_one": lost_reply}))
    with pytest.raises(asyncio.CancelledError):
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    monkeypatch.setattr(svc, "db", db)
    original = _doc(inv["id"])
    assert not original.get("credit_note_id") and "credit_claim" not in original
    assert _run(db.invoices.count_documents({"credit_note_for": inv["id"]})) == 0          # ούτε ορφανό πρόχειρο


def test_a_credit_note_whose_link_was_written_is_kept_even_if_the_reply_is_lost(client, office, monkeypatch):
    """Η σύνδεση του αρχικού με το πρόχειρο μπορεί να εφαρμοστεί και να χαθεί μόνο η απάντηση: το πρόχειρο τότε ΜΕΝΕΙ
    (το αρχικό δείχνει σε αυτό). Αν διαγραφόταν, το αρχικό θα έδειχνε σε πιστωτικό που δεν υπάρχει και δεν θα
    μπορούσε να πιστωθεί ξανά."""
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def lost_reply(real, query, update, *args, **kwargs):
        result = await real(query, update, *args, **kwargs)
        if "credit_note_id" in update.get("$set", {}):
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"update_one": lost_reply}))
    with pytest.raises(asyncio.CancelledError):
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    monkeypatch.setattr(svc, "db", db)
    original = _doc(inv["id"])
    drafts = _run(db.invoices.find({"credit_note_for": inv["id"]}).to_list(5))
    assert len(drafts) == 1 and original["credit_note_id"] == str(drafts[0]["_id"])
    assert "credit_claim" not in original


def test_a_second_cancellation_while_the_credit_note_cleanup_runs_still_releases_the_lock(client, office, monkeypatch):
    """Όπως στην έκδοση: ο καθαρισμός (αφαίρεση πρόχειρου, λύση κλειδώματος) ολοκληρώνεται ακόμη κι αν το αίτημα
    ακυρωθεί ΞΑΝΑ όσο τρέχει — αλλιώς το αρχικό έμενε κλειδωμένο για όσο διαρκεί το TTL."""
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)

    async def scenario():
        entered, cleanup_started = asyncio.Event(), asyncio.Event()

        async def hang_on_insert(real, doc, *args, **kwargs):
            entered.set()
            await asyncio.sleep(30)                          # το αίτημα «κολλάει» εδώ μέχρι να ακυρωθεί
            return await real(doc, *args, **kwargs)

        async def slow_unlock(real, query, update, *args, **kwargs):
            cleanup_started.set()
            await asyncio.sleep(0.05)                        # παράθυρο για τη δεύτερη ακύρωση
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {"insert_one": hang_on_insert, "update_one": slow_unlock}))
        task = asyncio.ensure_future(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
        await entered.wait()
        task.cancel()                                        # 1η ακύρωση: μέσα στην εισαγωγή
        await cleanup_started.wait()
        task.cancel()                                        # 2η ακύρωση: ενώ τρέχει ο καθαρισμός
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)                             # ο προφυλαγμένος καθαρισμός ολοκληρώνεται μόνος του
        monkeypatch.setattr(svc, "db", db)
        original = await db.invoices.find_one({"_id": ObjectId(inv["id"])})
        assert "credit_claim" not in original and not original.get("credit_note_id")
        assert await db.invoices.count_documents({"credit_note_for": inv["id"]}) == 0

    _run(scenario())


def test_a_live_credit_lock_blocks_a_new_request_but_an_expired_one_does_not(client, office):
    import invoice_services as svc
    inv, stale = _synced_stale(client, office)
    now = datetime.now(timezone.utc)
    _set(inv["id"], credit_claim="busy", credit_claim_at=now.isoformat())
    with pytest.raises(svc.InvoiceWorkflowError):
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    expired = now - timedelta(seconds=svc.CREDIT_CLAIM_TTL_SECONDS + 5)
    _set(inv["id"], credit_claim="dead-process", credit_claim_at=expired.isoformat())
    credit = _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    assert _doc(inv["id"])["credit_note_id"] == str(credit["_id"]) and "credit_claim" not in _doc(inv["id"])


def test_a_credit_note_is_not_created_for_an_invoice_cancelled_after_it_was_read(client, office):
    import invoice_services as svc
    from db import db
    inv, stale = _synced_stale(client, office)
    _set(inv["id"], status="cancelled")                      # ακυρώθηκε αφότου διάβασε το αίτημα το τιμολόγιο
    with pytest.raises(svc.InvoiceWorkflowError) as refused:
        _run(svc.build_credit_note(office.firm_id, stale, "u@example.gr", "", None))
    assert "άλλαξε" in str(refused.value)
    original = _doc(inv["id"])
    assert not original.get("credit_note_id") and "credit_claim" not in original
    assert _run(db.invoices.count_documents({"credit_note_for": inv["id"]})) == 0


# ---- 12.7 Διαγραφή και επεξεργασία πρόχειρων που ανταγωνίζονται την έκδοση ----

def test_delete_cannot_remove_a_draft_that_was_issued_in_the_meantime(client, office, monkeypatch):
    """Ανάμεσα στον έλεγχο «είναι πρόχειρο» και στη διαγραφή, το τιμολόγιο μπορούσε να εκδοθεί (με αριθμό)·
    η διαγραφή το έσβηνε και άφηνε κενό στη φορολογική σειρά."""
    import routes_invoices
    from db import db
    draft = _draft(client, office.alpha)

    async def issued_first(real, query, *args, **kwargs):
        await db.invoices.update_one({"_id": query["_id"]}, {"$set": {"status": "issued", "number": "ΤΠΥ-2026-0001"}})
        return await real(query, *args, **kwargs)

    monkeypatch.setattr(routes_invoices, "db", _HookedDb(db, "invoices", {"delete_one": issued_first}))
    out = client.delete(f"/api/invoices/{draft['id']}")
    assert out.status_code == 409 and "εκδόθηκε" in out.json()["detail"]
    stored = _doc(draft["id"])
    assert stored is not None and stored["status"] == "issued" and stored["number"] == "ΤΠΥ-2026-0001"


def test_delete_is_refused_while_the_draft_is_being_issued_but_not_after_the_lock_expires(client, office):
    import invoice_services as svc
    draft = _draft(client, office.alpha)
    now = datetime.now(timezone.utc)
    _set(draft["id"], issue_claim="busy", issue_claim_at=now.isoformat())
    out = client.delete(f"/api/invoices/{draft['id']}")
    assert out.status_code == 409 and _doc(draft["id"]) is not None
    _set(draft["id"], issue_claim_at=(now - timedelta(seconds=svc.ISSUE_CLAIM_TTL_SECONDS + 5)).isoformat())
    assert client.delete(f"/api/invoices/{draft['id']}").status_code == 200 and _doc(draft["id"]) is None


def test_deleting_a_draft_credit_note_frees_its_original(client, office):
    """Έλεγχος-μάρτυρας: η διαγραφή που επιτρέπεται εξακολουθεί να αποδεσμεύει το αρχικό τιμολόγιο."""
    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400006666666666")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    assert _doc(original["id"])["credit_note_id"] == credit["id"]
    assert client.delete(f"/api/invoices/{credit['id']}").status_code == 200
    assert not _doc(original["id"]).get("credit_note_id")
    assert client.post(f"/api/invoices/{original['id']}/credit-note", json={}).status_code == 200


def test_grammatio_edit_cannot_overwrite_one_issued_in_the_meantime(client, office, monkeypatch):
    import routes_grammatia
    from db import db
    gid = _gram(client, office.alpha)["id"]
    monkeypatch.setattr(routes_grammatia, "db", _RacyDb(db, "grammatia", {"status": "issued", "portal_ref": "OLO-FIRST"}))
    out = client.put(f"/api/grammatia/{gid}", json={
        "court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": office.alpha,
        "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 50.0, "contributions_amount": 10.0, "vat_amount": 12.0})
    assert out.status_code == 409 and "εκδόθηκε" in out.json()["detail"]
    stored = _run(db.grammatia.find_one({"_id": ObjectId(gid)}))
    assert stored["reference_amount"] == 100.0 and stored["status"] == "issued" and stored["portal_ref"] == "OLO-FIRST"


def test_a_draft_credit_note_keeps_the_client_of_its_original(client, office):
    original = _issued(client, office.alpha)
    _set(original["id"], mydata_status="synced", mydata_mark="400007777777777")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    moved = client.put(f"/api/invoices/{credit['id']}", json=_body(office.beta, -50.0))
    assert moved.status_code == 200, moved.text
    assert moved.json()["client_id"] == office.alpha and moved.json()["client_name"] == "ΑΛΦΑ ΑΕ"
    assert _doc(credit["id"])["client_id"] == office.alpha
    # μάρτυρας: ένα συνηθισμένο πρόχειρο αλλάζει πελάτη κανονικά
    plain = _draft(client, office.alpha)
    other = client.put(f"/api/invoices/{plain['id']}", json=_body(office.beta, 100.0))
    assert other.status_code == 200 and other.json()["client_id"] == office.beta


# ---- 12.8 «Επαναφορά σε ανεξόφλητο» ----

@pytest.mark.parametrize("paid_via,fragment", [
    ("trust", "πελατολογικό"), ("stripe", "κάρτα"), ("bank_auto", "τραπεζική"), ("bank_manual", "τραπεζική")])
def test_a_payment_that_went_through_another_ledger_is_not_undone_from_the_invoice_list(client, office, paid_via, fragment):
    """Η «επαναφορά» άφηνε το τιμολόγιο ανοιχτό ενώ η είσπραξη έμενε καταγεγραμμένη σε πελατολογικό, κάρτα ή τράπεζα:
    υπενθυμίσεις για ποσό που έχει ήδη καταβληθεί και δεύτερη πληρωμή."""
    inv = _issued(client, office.alpha)
    _set(inv["id"], status="paid", paid_via=paid_via)
    out = client.post(f"/api/invoices/{inv['id']}/mark-unpaid")
    assert out.status_code == 409 and fragment in out.json()["detail"] and "λογιστή" in out.json()["detail"]
    assert _doc(inv["id"])["status"] == "paid"


def test_trust_money_applied_to_an_invoice_blocks_the_reversal_even_without_a_recorded_channel(client, office):
    inv = _issued(client, office.alpha)
    _set(inv["id"], status="paid", trust_applied=50.0)                 # παλιά εγγραφή: χωρίς paid_via
    out = client.post(f"/api/invoices/{inv['id']}/mark-unpaid")
    assert out.status_code == 409 and "πελατολογικό" in out.json()["detail"] and _doc(inv["id"])["status"] == "paid"


def test_a_manual_payment_can_be_reversed_but_not_if_the_invoice_changed_meanwhile(client, office, monkeypatch):
    import routes_invoices
    from db import db
    inv = _issued(client, office.alpha)
    assert client.post(f"/api/invoices/{inv['id']}/mark-paid").status_code == 200
    back = client.post(f"/api/invoices/{inv['id']}/mark-unpaid")
    assert back.status_code == 200 and back.json()["status"] == "issued" and back.json()["paid_at"] is None

    other = _issued(client, office.alpha)
    assert client.post(f"/api/invoices/{other['id']}/mark-paid").status_code == 200

    async def cancelled_first(real, query, *args, **kwargs):
        await db.invoices.update_one({"_id": query["_id"]}, {"$set": {"status": "cancelled"}})
        return await real(query, *args, **kwargs)

    monkeypatch.setattr(routes_invoices, "db", _HookedDb(db, "invoices", {"update_one": cancelled_first}))
    out = client.post(f"/api/invoices/{other['id']}/mark-unpaid")
    assert out.status_code == 409 and "άλλαξε" in out.json()["detail"]
    assert _doc(other["id"])["status"] == "cancelled"                  # δεν «ανασταίνεται» ακυρωμένο παραστατικό


# ---- 12.9 Τράπεζα: παλιές προτάσεις, κάρτες χωρίς ποσό αναφοράς, μη-πεπερασμένα ποσά ----

def test_a_legacy_suggestion_without_the_ambiguity_flag_is_never_settled_automatically(client, office):
    """Πρόταση από παλιά έκδοση δεν έχει το `match_ambiguous`. Αν ο worker ξεκινήσει πριν από το API που τη σημαίνει,
    ο κύκλος συμφωνίας δεν πρέπει να την εξοφλήσει: το απόν πεδίο θεωρείται «αμφίβολη» παντού."""
    import automation
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.bank_transactions.insert_one({
        "firm_id": office.firm_id, "date": _today(), "description": f"ΑΛΦΑ ΑΕ {inv['number']}", "amount": 124.0,
        "external_id": "legacy-x", "match_status": "auto_matched", "match_confidence": 100,
        "matched_invoice_id": inv["id"], "matched_invoice_number": inv["number"]}))          # χωρίς match_ambiguous
    assert _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90})) == \
        {"confirmed": 0, "sent_for_review": 1}
    assert _doc(inv["id"])["status"] == "issued"
    assert "άλλα τιμολόγια" in _notes("reconcile_review")[0]["message"]
    bulk = client.post("/api/bank/confirm-auto-matches").json()                              # και η μαζική επιβεβαίωση
    assert bulk["confirmed"] == 0 and [i["reason"] for i in bulk["skipped_items"]] == ["ambiguous"]
    assert _doc(inv["id"])["status"] == "issued"


def test_the_worker_flags_legacy_suggestions_when_it_starts_before_the_api():
    import invoice_services
    import server
    import worker
    from db import db
    assert server.flag_unchecked_bank_suggestions is invoice_services.flag_unchecked_bank_suggestions
    _run(db.bank_transactions.insert_many([
        {"firm_id": "f", "external_id": "legacy", "match_status": "auto_matched", "amount": 1.0},
        {"firm_id": "f", "external_id": "checked", "match_status": "auto_matched", "amount": 1.0, "match_ambiguous": False}]))
    _run(worker._ensure_indexes())
    flags = {d["external_id"]: d["match_ambiguous"] for d in _run(db.bank_transactions.find({}).to_list(10))}
    assert flags == {"legacy": True, "checked": False}


def test_confirming_a_card_that_recorded_no_amount_is_refused_when_the_difference_is_large(client, office):
    """Κάρτα από παλιά έκδοση δεν έχει το οφειλόμενο που είδε ο άνθρωπος. Μικρή διαφορά (< 1 €) περνά όπως πάντα·
    μεγάλη δεν εξοφλείται με ένα «Ναι» που αφορούσε άλλο ποσό."""
    import automation
    from db import db
    inv = _issued(client, office.alpha)                           # 124,00
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    _run(db.bank_transactions.update_one({"_id": tx["_id"]}, {"$unset": {"review_due": ""}}))
    _set(inv["id"], trust_applied=100.0)                          # οφείλονται 24,00 — η κατάθεση 123,50
    with pytest.raises(automation.SettlementRefused) as refused:
        _resolve(card, "confirm")
    assert refused.value.reason == "due_unverified"
    message = refused.value.message
    assert "123,50 €" in message and "24,00 €" in message and "99,50 €" in message
    for label in ("Όχι, άλλο τιμολόγιο", "Αγνόησε την κίνηση"):
        assert f"«{label}»" in message and label in {a["label"] for a in _card(card["_id"])["actions"]}
    assert _card(card["_id"]).get("resolved_at") is None and _doc(inv["id"])["status"] == "issued"

    _set(inv["id"], trust_applied=0.0)                            # διαφορά 0,50 € < 1 €: δεκτή, καταγράφεται
    assert _resolve(card, "confirm") == {"resolution": "confirmed"}
    assert _doc(inv["id"])["status"] == "paid"
    assert _run(db.bank_transactions.find_one({"_id": tx["_id"]}))["amount_difference"] == -0.5


def test_a_card_difference_of_exactly_one_euro_is_not_small(client, office):
    import automation
    from db import db
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    _run(db.bank_transactions.update_one({"_id": tx["_id"]}, {"$unset": {"review_due": ""}, "$set": {"amount": 123.0}}))
    with pytest.raises(automation.SettlementRefused) as refused:                 # 124,00 − 123,00 = 1,00 €
        _resolve(card, "confirm")
    assert refused.value.reason == "due_unverified"
    _run(db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {"amount": 123.01}}))   # 0,99 €
    assert _resolve(card, "confirm") == {"resolution": "confirmed"}


@pytest.mark.parametrize("cell", ["nan", "NaN", "inf", "-inf", "Infinity", "1e999"])
def test_non_finite_amounts_in_a_statement_are_read_as_zero_and_never_stored(client, office, cell):
    """`float("nan")` και `float("inf")` δεν αποτυγχάνουν: μια κατάθεση «nan» έμπαινε στη βάση και κατέστρεφε τις συγκρίσεις."""
    import math
    import routes_bank
    from db import db
    assert routes_bank._parse_amount(cell) == 0.0
    _issued(client, office.alpha)
    out = _import(client, [(athens_today(), "ΑΛΦΑ ΑΕ ΕΙΣΠΡΑΞΗ", cell)])
    assert out["auto_matched"] == 0
    assert all(math.isfinite(t["amount"]) for t in _run(db.bank_transactions.find({}).to_list(10)))


def test_amount_parsing_still_reads_greek_and_english_formats():
    import routes_bank
    assert routes_bank._parse_amount("1.234,56") == 1234.56 and routes_bank._parse_amount("1,234.56") == 1234.56
    assert routes_bank._parse_amount("124,00 €") == 124.0 and routes_bank._parse_amount("-") == 0.0
    assert routes_bank._parse_amount("") == 0.0 and routes_bank._parse_amount(None) == 0.0


def test_ignoring_or_reconciling_a_deposit_by_an_uppercase_id_still_closes_its_cards(client, office):
    """Το id που στέλνει ο client μπορεί να έχει άλλη γραφή των ίδιων δεκαεξαδικών· η ειδοποίηση φέρει την κανονική."""
    from db import db
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    upper = str(tx["_id"]).upper()
    if upper == str(tx["_id"]):
        pytest.skip("το id δεν έχει γράμματα")
    assert client.post(f"/api/bank/reconcile/{upper}?ignore=true").status_code == 200
    assert _card(card["_id"])["resolution"] == "ignored"

    _run(db.notifications.delete_many({}))
    _run(db.bank_transactions.delete_many({}))
    other = _issued(client, office.beta)
    tx2, card2 = _review_card_for(client, office, other, "ΒΗΤΑ ΕΠΕ", "123,50")
    ok = client.post(f"/api/bank/reconcile/{str(tx2['_id']).upper()}?invoice_id={other['id']}&accept_difference=true")
    assert ok.status_code == 200, ok.text
    assert _card(card2["_id"])["resolution"] == "already_settled"


def test_automatic_settlement_closes_the_review_card_it_makes_obsolete(client, office):
    """Η κίνηση είχε πάει για έλεγχο (π.χ. χαμηλή βεβαιότητα)· όταν στη συνέχεια εξοφλείται αυτόματα, η κάρτα με το
    «Ναι, εξοφλήθηκε» δεν πρέπει να μένει ανοιχτή πάνω σε κίνηση που έχει ήδη συμφωνηθεί."""
    import automation
    from db import db
    inv = _issued(client, office.alpha)
    tx, card = _review_card_for(client, office, inv, "ΑΛΦΑ ΑΕ", "123,50")
    _run(db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {
        "amount": 124.0, "match_confidence": 100, "match_ambiguous": False}}))
    assert _run(automation.job_reconcile(office.firm_id, {"auto_reconcile_min_confidence": 90}))["confirmed"] == 1
    closed = _card(card["_id"])
    assert closed["resolution"] == "already_settled" and closed["resolved_by"] == "autopilot"
    assert _doc(inv["id"])["status"] == "paid" and _doc(inv["id"])["paid_via"] == "bank_auto"
    assert len(_notes("reconciled")) == 1


@pytest.mark.parametrize("thousandths", range(123994, 124007))
def test_suggestion_and_settlement_agree_on_what_an_exact_amount_is(client, office, thousandths):
    """Η πρόταση και η εξόφληση στρογγυλοποιούσαν διαφορετικά ένα ποσό με 3 δεκαδικά: η πρώτη το έβλεπε «ακριβές»
    και η δεύτερη «με διαφορά» (ή αντίστροφα). Χαρακτηριστικό είναι το 124,005: η στρογγυλοποίηση «μισού προς τα
    πάνω» δίνει 12401 λεπτά, η στρογγυλοποίηση float 12400. Η πρώτη εκδοχή του test διέτρεχε 12,395–12,405 αντί για
    123,995–124,005 και δεν διέκρινε ποτέ τη μετάλλαξη· την πρόσεξε ο έλεγχος ισχύος."""
    import invoice_services as svc
    import routes_bank
    inv = _issued(client, office.alpha)                           # οφείλονται 124,00
    amount = thousandths / 1000
    suggested = routes_bank.match_details(
        _deposit(amount, "ΑΛΦΑ ΑΕ"), [_inv_row(inv["id"], inv["number"], due=124.0)])["exact"]
    check = _run(svc.apply_bank_payment(
        office.firm_id, {"_id": ObjectId(), "amount": amount, "match_status": "unmatched"}, inv["id"],
        settings={}, paid_via="bank_manual", status_on_success="manual_matched", dry_run=True))
    settled_exact = check["status"] == "ok" and check["difference"] == 0.0
    assert suggested == settled_exact, (amount, suggested, check)


def test_each_failed_undo_gets_its_own_card_even_for_the_same_account_and_amount(client, office, monkeypatch):
    """Το κλειδί της ειδοποίησης περιείχε το id του ledger — που είναι άγνωστο όταν η αποτυχία έρχεται πριν γραφτεί.
    Δύο διαφορετικά περιστατικά του ίδιου ποσού συγχωνεύονταν σε μία κάρτα και το δεύτερο περνούσε απαρατήρητο."""
    import routes_trust
    from db import db
    assert _tx(client, office.alpha, "deposit", 500.0).status_code == 200
    real = routes_trust._cas_update
    calls = {"n": 0}

    async def undo_fails(collection, query, compute, attempts=8):
        calls["n"] += 1
        if calls["n"] % 2 == 0:                                   # 1 = η κίνηση, 2 = η αναίρεσή της
            raise RuntimeError("η βάση δεν απάντησε στην αναίρεση")
        return await real(collection, query, compute, attempts)

    async def no_ledger(real_insert, *args, **kwargs):
        raise RuntimeError("δεν γράφτηκε το ledger")

    monkeypatch.setattr(routes_trust, "_cas_update", undo_fails)
    monkeypatch.setattr(routes_trust, "db", _HookedDb(db, "trust_transactions", {"insert_one": no_ledger}))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="ledger"):
            _tx(client, office.alpha, "withdrawal", 50.0)
    monkeypatch.undo()
    cards = _notes("trust_needs_review")
    assert len(cards) == 2 and len({c["dedupe_key"] for c in cards}) == 2
    assert all(c["requires_action"] and c["level"] == "critical" for c in cards)


# ---- 12.10 Ημερομηνία και έτος σειράς: Αθήνα, όχι UTC/ώρα server ----

def _frozen_new_year(monkeypatch):
    """Στην Αθήνα είναι ήδη Πρωτοχρονιά (00:30), ενώ το UTC και ο server είναι ακόμη στην παραμονή."""
    import tax_utils

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2038, 12, 31, 22, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tax_utils, "datetime", FakeDateTime)
    assert athens_today() == date(2039, 1, 1)
    return "2039-01-01"


def test_issue_accepts_a_draft_dated_today_in_athens_while_utc_is_still_yesterday(client, office, monkeypatch):
    day = _frozen_new_year(monkeypatch)
    out = client.post(f"/api/invoices/{_draft(client, office.alpha, when=day)['id']}/issue")
    assert out.status_code == 200, out.text
    assert out.json()["number"] == "ΤΠΥ-2039-0001" and out.json()["date"] == day
    refused = client.post(f"/api/invoices/{_draft(client, office.alpha, when='2039-01-02')['id']}/issue")
    assert refused.status_code == 400 and "μελλοντική" in refused.json()["detail"]


def test_a_credit_note_created_after_midnight_in_athens_carries_the_athens_date(client, office, monkeypatch):
    day = _frozen_new_year(monkeypatch)
    original = _issued(client, office.alpha, when=day)
    _set(original["id"], mydata_status="synced", mydata_mark="400008888888888")
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={})
    assert credit.status_code == 200, credit.text
    assert credit.json()["date"] == day


def test_a_grammatio_gets_the_athens_year_and_its_invoice_the_athens_date(client, office, monkeypatch):
    day = _frozen_new_year(monkeypatch)
    gram = _gram(client, office.alpha)
    assert gram["number"] == "ΓΡΠ-2039-0001"
    assert client.post(f"/api/grammatia/{gram['id']}/issue", json={}).status_code == 200
    invoice = client.post(f"/api/grammatia/{gram['id']}/to-invoice")
    assert invoice.status_code == 200, invoice.text
    assert invoice.json()["date"] == day


def test_paravola_and_cases_get_the_athens_year(client, office, monkeypatch):
    _frozen_new_year(monkeypatch)
    paravolo = client.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": 5.0})
    assert paravolo.status_code == 200 and paravolo.json()["number"] == "ΠΡΒ-2039-0001"
    case = client.post("/api/cases", json={"title": "Δοκιμαστική υπόθεση", "client_id": office.alpha})
    assert case.status_code == 200, case.text
    assert case.json()["number"] == "ΥΠΘ-2039-0001"


def test_a_copilot_draft_carries_the_athens_date(client, office, monkeypatch):
    import routes_copilot
    day = _frozen_new_year(monkeypatch)
    created, _reply = _run(routes_copilot._execute_create_invoice(
        {"client_name": "ΑΛΦΑ", "amount": 100.0, "description": "Γνωμοδότηση"}, office.user))
    assert created is not None and created.date == day and created.status == "draft"


def test_the_default_series_year_is_the_athens_year(client, office, monkeypatch):
    import invoice_services as svc
    _frozen_new_year(monkeypatch)
    assert _run(svc.next_invoice_number(office.firm_id)) == "ΤΠΥ-2039-0001"


def test_the_client_account_future_date_check_uses_the_athens_day(client, office, monkeypatch):
    day = _frozen_new_year(monkeypatch)
    assert _tx(client, office.alpha, "deposit", 50.0, date=day).status_code == 200
    tomorrow = _tx(client, office.alpha, "deposit", 50.0, date="2039-01-02")
    assert tomorrow.status_code == 400


# ---- 12.11 Επιζήσασες μεταλλάξεις των reviewers: killers ----

def test_a_second_cancellation_while_the_issue_cleanup_runs_does_not_leave_the_draft_locked(monkeypatch):
    """Η αποδέσμευση του κλειδώματος και του αριθμού ολοκληρώνεται (asyncio.shield) ακόμη κι αν το αίτημα ακυρωθεί
    ΞΑΝΑ όσο τρέχει ο καθαρισμός — αλλιώς το πρόχειρο έμενε κλειδωμένο για όσο διαρκεί το TTL."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-double-cancel"
        draft_id = await _bare_draft(firm)
        entered, release_started = asyncio.Event(), asyncio.Event()

        async def hang_on_final_write(real, query, update, *args, **kwargs):
            if update.get("$set", {}).get("status") == "issued":
                entered.set()
                await asyncio.sleep(30)                          # το αίτημα «κολλάει» εδώ μέχρι να ακυρωθεί
            return await real(query, update, *args, **kwargs)

        async def slow_release(real, query, update, *args, **kwargs):
            release_started.set()
            await asyncio.sleep(0.05)                            # παράθυρο για τη δεύτερη ακύρωση
            return await real(query, update, *args, **kwargs)

        monkeypatch.setattr(svc, "db", _HookedDb(db, "invoices", {
            "find_one_and_update": hang_on_final_write, "update_one": slow_release}))
        task = asyncio.ensure_future(svc.issue_draft(firm, await db.invoices.find_one({"_id": draft_id}), {}))
        await entered.wait()
        task.cancel()                                            # 1η ακύρωση: μέσα στην τελική εγγραφή
        await release_started.wait()
        task.cancel()                                            # 2η ακύρωση: ενώ τρέχει ο καθαρισμός
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)                                 # ο προφυλαγμένος καθαρισμός ολοκληρώνεται μόνος του
        monkeypatch.setattr(svc, "db", db)
        after = await db.invoices.find_one({"_id": draft_id})
        assert after["status"] == "draft" and "issue_claim" not in after and not after.get("number")
        again = await svc.issue_draft(firm, after, {})
        assert again["number"].endswith("-0001")                 # ούτε ο αριθμός «κάηκε»

    _run(scenario())


@pytest.mark.asyncio
async def test_a_second_cancellation_while_a_client_account_payment_is_undone_leaves_nothing_half_applied(monkeypatch):
    import routes_trust
    from db import db
    from models import TrustTransaction
    firm = "firm-double-cancel-trust"
    cid = str((await db.clients.insert_one({"firm_id": firm, "name": "Πελάτης", "afm": "123456783"})).inserted_id)
    inv = await db.invoices.insert_one({"firm_id": firm, "number": "ΤΠΥ-2026-0001", "client_id": cid, "client_name": "Π",
                                        "status": "issued", "total": 124.0, "date": _today(), "line_items": []})
    user = {"email": "u@example.gr", "firm_id": firm}
    await routes_trust.create_transaction(
        routes_trust.TransactionPayload(client_id=cid, type="deposit", amount=200.0, date=_today()), user)
    real = routes_trust._cas_update

    async def slow(collection, query, compute, attempts=8):
        await asyncio.sleep(0.05)
        return await real(collection, query, compute, attempts)

    holder = {}

    def cancelled(self, *a, **k):
        asyncio.get_event_loop().call_later(0.02, holder["task"].cancel)     # η 2η ακύρωση πέφτει μέσα στην αναίρεση
        raise asyncio.CancelledError()

    monkeypatch.setattr(routes_trust, "_cas_update", slow)
    monkeypatch.setattr(TrustTransaction, "to_mongo", cancelled)
    holder["task"] = asyncio.ensure_future(routes_trust.create_transaction(routes_trust.TransactionPayload(
        client_id=cid, type="apply_to_invoice", amount=124.0, date=_today(), invoice_id=str(inv.inserted_id)), user))
    await asyncio.gather(holder["task"], return_exceptions=True)
    await asyncio.sleep(0.5)                                                  # ο προφυλαγμένος καθαρισμός ολοκληρώνεται
    monkeypatch.undo()
    account = await db.trust_accounts.find_one({"firm_id": firm, "client_id": cid})
    assert account["balance"] == 200.0 and account["total_withdrawals"] == 0.0
    assert not (await db.invoices.find_one({"_id": inv.inserted_id})).get("trust_applied")


def _bank_row(office, inv, **over):
    from db import db
    from models import BankTransaction
    base = dict(firm_id=office.firm_id, date=_today(), description="x", amount=124.0, matched_invoice_id=inv["id"],
                matched_invoice_number=inv["number"], match_status="auto_matched", match_confidence=80,
                external_id=f"e{ObjectId()}")
    base.update(over)
    return _run(db.bank_transactions.insert_one(BankTransaction(**base).to_mongo())).inserted_id


def test_the_amount_a_human_saw_on_a_review_card_survives_later_autopilot_cycles(client, office):
    """Το `review_due` γράφεται μία φορά, όταν δημιουργείται η κάρτα. Αν ο κύκλος των 15 λεπτών το ανανέωνε, η
    άρνηση «άλλαξε το οφειλόμενο» δεν θα ενεργοποιούνταν ποτέ."""
    import automation
    from db import db
    inv = _issued(client, office.alpha)                                        # οφείλονται 124
    tx_id = _bank_row(office, inv, match_ambiguous=True)                       # αμφίβολη → κάρτα ελέγχου
    settings = _settings(office.firm_id)
    _run(automation.job_reconcile(office.firm_id, settings))
    assert _run(db.bank_transactions.find_one({"_id": tx_id}))["review_due"] == 124.0
    assert _tx(client, office.alpha, "deposit", 200.0).status_code == 200
    assert _tx(client, office.alpha, "apply_to_invoice", 84.0, invoice_id=inv["id"]).status_code == 200   # τώρα οφείλονται 40
    _run(automation.job_reconcile(office.firm_id, settings))                   # ο επόμενος κύκλος
    assert _run(db.bank_transactions.find_one({"_id": tx_id}))["review_due"] == 124.0
    assert len(_notes("reconcile_review")) == 1                                # και καμία δεύτερη κάρτα
    card = _notes("reconcile_review")[0]
    with pytest.raises(automation.SettlementRefused) as refused:
        _resolve(card, "confirm")
    assert refused.value.reason == "due_changed" and _doc(inv["id"])["status"] == "issued"


def test_settling_the_same_deposit_twice_from_a_stale_copy_is_idempotent(client, office):
    import invoice_services as svc
    from db import db
    inv = _issued(client, office.alpha)
    tx_id = _bank_row(office, inv)
    stale = _run(db.bank_transactions.find_one({"_id": tx_id}))
    kwargs = dict(settings={}, paid_via="bank_manual", status_on_success="manual_matched")
    first = _run(svc.apply_bank_payment(office.firm_id, dict(stale), inv["id"], **kwargs))
    second = _run(svc.apply_bank_payment(office.firm_id, dict(stale), inv["id"], **kwargs))
    assert (first["status"], second["status"]) == ("paid", "linked")           # η ίδια κατάθεση δεν «διπλοπληρώνεται»


def test_a_deposit_that_loses_the_race_to_another_deposit_is_refused(client, office, monkeypatch):
    import invoice_services as svc
    from db import db
    inv = _issued(client, office.alpha)
    first = _bank_row(office, inv, external_id="first")
    second = _bank_row(office, inv, external_id="second")
    oid = ObjectId(inv["id"])

    async def racing_mark_paid(firm_id, invoice_id, paid_via="manual"):
        await db.invoices.update_one({"_id": oid}, {"$set": {"status": "paid", "paid_via": "bank_auto"}})
        await db.bank_transactions.update_one({"_id": first}, {"$set": {"match_status": "auto_confirmed"}})
        return False                                           # ο ανταγωνιστής πρόλαβε τη μετάβαση issued → paid

    monkeypatch.setattr(svc, "mark_paid", racing_mark_paid)
    out = _run(svc.apply_bank_payment(
        office.firm_id, _run(db.bank_transactions.find_one({"_id": second})), inv["id"],
        settings={}, paid_via="bank_auto", status_on_success="auto_confirmed"))
    assert out["status"] == "refused" and out["reason"] == "already_paid", out
    assert _run(db.bank_transactions.find_one({"_id": second}))["match_status"] == "auto_matched"


def test_two_failed_undos_for_different_clients_raise_two_cards(client, office, monkeypatch):
    import routes_trust
    from db import db
    real = routes_trust._cas_update
    for client_id in (office.alpha, office.beta):
        inv = _issued(client, client_id)
        assert _tx(client, client_id, "deposit", 200.0).status_code == 200
        seen = {"n": 0}

        async def failing_undo(collection, query, compute, attempts=8, _seen=seen):
            if collection.name == "invoices":
                _seen["n"] += 1
                if _seen["n"] >= 2:
                    raise RuntimeError("η βάση δεν απάντησε στην αναίρεση")
            return await real(collection, query, compute, attempts)

        async def boom(*a, **k):
            raise RuntimeError("η βάση δεν απάντησε στο κλείσιμο")

        monkeypatch.setattr(routes_trust, "_cas_update", failing_undo)
        monkeypatch.setattr(routes_trust, "mark_paid", boom)
        with pytest.raises(RuntimeError):
            _tx(client, client_id, "apply_to_invoice", 124.0, invoice_id=inv["id"])
        monkeypatch.undo()
    cards = _notes("trust_needs_review")
    assert len(cards) == 2 and len({c["dedupe_key"] for c in cards}) == 2


def test_a_client_cannot_inject_issue_lock_fields_through_an_edit(client, office):
    """Αν το PUT δεχόταν `issue_claim_at` στο μέλλον, το πρόχειρο θα φαινόταν «υπό έκδοση» για πάντα."""
    draft = _draft(client, office.alpha)
    body = {**_body(office.alpha, 100.0), "issue_claim_at": "2099-01-01T00:00:00+00:00", "issue_claim": "x",
            "credit_claim": "y", "credit_claim_at": "2099-01-01T00:00:00+00:00"}
    assert client.put(f"/api/invoices/{draft['id']}", json=body).status_code == 200
    stored = _doc(draft["id"])
    for field in ("issue_claim", "issue_claim_at", "credit_claim", "credit_claim_at"):
        assert field not in stored, field
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 200
