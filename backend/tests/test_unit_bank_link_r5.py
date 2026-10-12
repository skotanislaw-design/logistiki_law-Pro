"""Γύρος 5 (ανασκόπηση 4) — BK3/BK5: κατάθεση για τιμολόγιο που έχει σημειωθεί εξοφλημένο με το χέρι.

Ο server επιτρέπει εδώ και καιρό να συνδεθεί μια κατάθεση με τιμολόγιο που ο χρήστης είχε σημειώσει εξοφλημένο (η κατάθεση
απλώς το «δικαιολογεί», δεν αλλάζει την κατάσταση)· η οθόνη της Τράπεζας όμως πρόσφερε μόνο εκδοθέντα τιμολόγια με υπόλοιπο,
οπότε η μόνη επιλογή ήταν η «Αγνόηση». Η λίστα τιμολογίων φέρνει πλέον για αυτά το `bank_link_due` — το ποσό με το οποίο
μπορεί να συνδεθεί μια κατάθεση — και μόνο για αυτά που δεν τα έχει αναλάβει ήδη άλλη κατάθεση.
"""
import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
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
                    json={"email": "banklink@example.gr", "password": "Str0ngPass!", "name": "Λογιστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "banklink@example.gr"}))
    _run(db.users.update_one({"email": "banklink@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, firm_id=user["firm_id"])


def _today() -> str:
    return athens_today().isoformat()


def _issued(client, client_id, net=100.0):
    draft = client.post("/api/invoices", json={
        "client_id": client_id, "date": _today(),
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]})
    assert draft.status_code == 200, draft.text
    out = client.post(f"/api/invoices/{draft.json()['id']}/issue")
    assert out.status_code == 200, out.text
    return out.json()


def _row_of(client, invoice_id):
    rows = client.get("/api/invoices").json()
    return next(r for r in rows if r["id"] == invoice_id)


def _set(invoice_id, **fields):
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": fields}))


def _csv(*rows):
    return ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n" + "\n".join(rows) + "\n").encode("utf-8")


def _deposit(client, amount, description="ΑΛΦΑ ΑΕ", reference=""):
    day = athens_today().strftime("%d/%m/%Y")
    row = f"{day};{description};;{amount};;{reference}"
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": ("k.csv", _csv(row), "text/csv")})
    assert r.status_code == 200, r.text


def _txs():
    from db import db
    return _run(db.bank_transactions.find({}).sort("_id", 1).to_list(100))


# ---------------------------------------------------------------- τι προσφέρεται ----

def test_a_manually_paid_invoice_carries_the_amount_a_deposit_must_have(client, office):
    inv = _issued(client, office.alpha)                                  # 100 + ΦΠΑ 24% = 124
    assert client.post(f"/api/invoices/{inv['id']}/mark-paid").status_code == 200
    row = _row_of(client, inv["id"])
    assert row["status"] == "paid" and row["outstanding"] == 0.0         # τίποτα δεν οφείλεται…
    assert row["bank_link_due"] == 124.0                                 # …αλλά μια κατάθεση 124 € μπορεί να το δικαιολογήσει


def test_the_amount_follows_the_withholding_of_the_invoice(client, office):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text
    inv = _issued(client, office.alpha, net=1000.0)                      # 1240 − παρακράτηση 200 = 1040
    before = _row_of(client, inv["id"])["outstanding"]
    assert before == 1040.0
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    assert _row_of(client, inv["id"])["bank_link_due"] == 1040.0


def test_what_was_applied_from_the_client_account_is_not_expected_from_the_bank(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _set(inv["id"], trust_applied=24.0)
    assert _row_of(client, inv["id"])["bank_link_due"] == 100.0


def test_the_amount_is_never_negative(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _set(inv["id"], trust_applied=500.0)
    assert _row_of(client, inv["id"])["bank_link_due"] == 0.0


def test_open_drafts_and_cancelled_invoices_do_not_carry_it(client, office):
    issued = _issued(client, office.alpha)
    draft = client.post("/api/invoices", json={
        "client_id": office.alpha, "date": _today(),
        "line_items": [{"description": "x", "quantity": 1, "unit_price": 10.0, "vat_rate": 24}]}).json()
    cancelled = _issued(client, office.alpha)
    _set(cancelled["id"], status="cancelled")
    for target in (issued["id"], draft["id"], cancelled["id"]):
        assert "bank_link_due" not in _row_of(client, target), target


@pytest.mark.parametrize("paid_via", ["stripe", "trust", "bank_auto", "bank_manual"])
def test_an_invoice_paid_through_another_channel_is_not_offered(client, office, paid_via):
    inv = _issued(client, office.alpha)
    _set(inv["id"], status="paid", paid_via=paid_via)
    assert "bank_link_due" not in _row_of(client, inv["id"])


@pytest.mark.parametrize("paid_via", ["manual", None, ""])
def test_an_invoice_marked_paid_by_hand_is_offered_however_the_channel_was_recorded(client, office, paid_via):
    inv = _issued(client, office.alpha)
    _set(inv["id"], status="paid", paid_via=paid_via)
    assert _row_of(client, inv["id"])["bank_link_due"] == 124.0


def test_an_invoice_with_a_credit_note_is_not_offered(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _set(inv["id"], credit_note_id=str(ObjectId()))
    assert "bank_link_due" not in _row_of(client, inv["id"])


# ------------------------------------------------ ό,τι έχει ήδη αναλάβει κατάθεση ----

def test_an_invoice_already_linked_to_a_deposit_is_no_longer_offered(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _deposit(client, "124,00")
    tx = _txs()[0]
    assert "bank_link_due" in _row_of(client, inv["id"])
    out = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert out.status_code == 200 and out.json()["status"] == "linked"
    assert "bank_link_due" not in _row_of(client, inv["id"])


def test_a_proposal_that_was_not_confirmed_does_not_hide_the_invoice(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _deposit(client, "124,00")
    from db import db
    _run(db.bank_transactions.update_one({}, {"$set": {"match_status": "auto_matched", "matched_invoice_id": inv["id"]}}))
    assert _row_of(client, inv["id"])["bank_link_due"] == 124.0


def test_a_deposit_of_another_office_does_not_hide_the_invoice(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    from db import db
    _run(db.bank_transactions.insert_one({
        "firm_id": "another-office", "date": _today(), "description": "ξένη", "amount": 124.0,
        "match_status": "manual_matched", "matched_invoice_id": inv["id"]}))
    assert _row_of(client, inv["id"])["bank_link_due"] == 124.0


def test_another_office_never_sees_the_invoice(client, office):
    from db import db
    _run(db.invoices.insert_one({
        "firm_id": "another-office", "number": "ΤΠΥ-2026-0001", "status": "paid", "paid_via": "manual",
        "total": 50.0, "subtotal": 50.0, "vat_total": 0.0, "date": _today(), "client_id": "x", "line_items": [],
        "created_at": "2026-01-01T00:00:00+00:00"}))
    assert [r for r in client.get("/api/invoices").json() if "bank_link_due" in r] == []


# ------------------------------------------ το ποσό που προσφέρεται είναι αυτό που δέχεται ο server ----

def test_a_deposit_of_exactly_the_offered_amount_links_without_accepting_a_difference(client, office):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text
    inv = _issued(client, office.alpha, net=1000.0)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    due = _row_of(client, inv["id"])["bank_link_due"]
    _deposit(client, f"{due:.2f}".replace(".", ","))
    tx = _txs()[0]
    out = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "linked" and out.json()["difference"] == 0
    assert _txs()[0]["match_status"] == "manual_matched" and "amount_difference" not in _txs()[0]


def test_a_second_deposit_is_refused_for_an_invoice_the_first_one_linked(client, office):
    inv = _issued(client, office.alpha)
    client.post(f"/api/invoices/{inv['id']}/mark-paid")
    _deposit(client, "124,00", reference="Α")
    _deposit(client, "124,00", reference="Β")
    first, second = _txs()
    assert client.post(f"/api/bank/reconcile/{first['_id']}?invoice_id={inv['id']}").status_code == 200
    again = client.post(f"/api/bank/reconcile/{second['_id']}?invoice_id={inv['id']}")
    assert again.status_code == 409 and "έχει ήδη εξοφληθεί" in again.json()["detail"]


# ------------------------------------------------------------- BK3: το μήνυμα διαφοράς ----

def test_the_amount_mismatch_message_speaks_plainly_and_still_names_the_parameter(client, office):
    inv = _issued(client, office.alpha)                                   # 124 €
    _deposit(client, "123,50")
    tx = _txs()[0]
    out = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert out.status_code == 409
    detail = out.json()["detail"]
    assert "δεν ταιριάζει με το οφειλόμενο" in detail
    assert "πρέπει να την αποδεχτείτε ρητά" in detail
    assert "accept_difference" in detail and "=true" not in detail        # όχι κώδικας μέσα στο κείμενο προς τον χρήστη
    assert client.get("/api/invoices").json()[0]["status"] == "issued"    # δεν εξοφλήθηκε τίποτα
