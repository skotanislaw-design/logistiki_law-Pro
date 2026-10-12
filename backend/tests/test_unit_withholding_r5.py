"""Γύρος 5 — η ρητή παρακράτηση (`withholding_amount`) δεν περνά σε ΑΠΥ λιανικής ούτε σε πιστωτικό.

Ο αυτόματος υπολογισμός δεν εφαρμόζει ποτέ παρακράτηση σε λιανική ή σε πιστωτικά (την κρατά μόνο πελάτης-επιχείρηση
που εκδίδει βεβαίωση παρακράτησης). Όμως μια ρητή τιμή μέσω API υπερίσχυε και «πάγωνε» όπως δόθηκε: απόδειξη
500 € προς ιδιώτη με `withholding_amount: 100` εκδιδόταν, τυπωνόταν «ΠΛΗΡΩΤΕΟ 520 €» και διαβιβαζόταν στην ΑΑΔΕ με
παρακράτηση που δεν υπάρχει. Ο έλεγχος γίνεται στην αποθήκευση του πρόχειρου ΚΑΙ στην έκδοση (το είδος βγαίνει από τον
πελάτη και μπορεί να άλλαξε στο μεταξύ).
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
                    json={"email": "wh@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "wh@example.gr"}))
    _run(db.users.update_one({"email": "wh@example.gr"}, {"$set": {"role": "admin"}}))
    company = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                                "email": "alpha@example.gr"}).json()["id"]
    person = client.post("/api/clients", json={"name": "Ιδιώτης Πελάτης", "client_type": "individual"}).json()["id"]
    return SimpleNamespace(company=company, person=person, firm_id=user["firm_id"])


def _today() -> str:
    return athens_today().isoformat()


def _body(client_id, net=500.0, **over):
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    return body


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


def _enable_withholding(client):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text


def _insert_draft(firm_id, client_id, net=500.0, **over):
    """Πρόχειρο όπως θα το άφηνε παλαιότερη έκδοση ή άμεση εγγραφή στη βάση (παρακάμπτει τον έλεγχο του API)."""
    from db import db
    doc = {"firm_id": firm_id, "client_id": client_id, "client_name": "Πελάτης", "number": "", "status": "draft",
           "date": _today(), "subtotal": net, "vat_amount": round(net * 0.24, 2), "total": round(net * 1.24, 2),
           "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}],
           "mydata_status": "not_sent", **over}
    return str(_run(db.invoices.insert_one(doc)).inserted_id)


# ====================================================== 1. Αποθήκευση πρόχειρου ====

def test_a_retail_receipt_cannot_carry_an_explicit_withholding(client, office):
    """Το ακριβές σενάριο του ευρήματος: ΑΠΥ 500 € με παρακράτηση 100 €."""
    r = client.post("/api/invoices", json=_body(office.person, invoice_type="11.2", withholding_amount=100.0))
    assert r.status_code == 400
    assert "λιανικής" in r.json()["detail"] and "100,00" in r.json()["detail"]


def test_a_draft_for_a_private_client_is_retail_even_without_an_explicit_type(client, office):
    """Ο ιδιώτης χωρίς ΑΦΜ δίνει ΑΠΥ· η ρητή παρακράτηση απορρίπτεται και χωρίς ρητό `invoice_type`."""
    r = client.post("/api/invoices", json=_body(office.person, withholding_amount=100.0))
    assert r.status_code == 400 and "λιανικής" in r.json()["detail"]


def test_zero_or_missing_withholding_is_fine_on_a_retail_receipt(client, office):
    for extra in ({}, {"withholding_amount": 0.0}):
        r = client.post("/api/invoices", json=_body(office.person, invoice_type="11.2", **extra))
        assert r.status_code == 200, r.text


def test_editing_a_retail_draft_cannot_add_a_withholding(client, office):
    draft = client.post("/api/invoices", json=_body(office.person, invoice_type="11.2")).json()
    r = client.put(f"/api/invoices/{draft['id']}", json=_body(office.person, invoice_type="11.2", withholding_amount=50.0))
    assert r.status_code == 400 and "λιανικής" in r.json()["detail"]
    assert _doc(draft["id"]).get("withholding_amount") is None


def test_moving_a_draft_to_a_private_client_with_a_withholding_is_refused(client, office):
    draft = client.post("/api/invoices", json=_body(office.company, withholding_amount=100.0)).json()
    assert draft["withholding_amount"] == 100.0
    r = client.put(f"/api/invoices/{draft['id']}", json=_body(office.person, withholding_amount=100.0))
    assert r.status_code == 400 and "λιανικής" in r.json()["detail"]
    assert _doc(draft["id"])["client_id"] == office.company


def test_an_explicit_withholding_on_a_services_invoice_still_works(client, office):
    """Μάρτυρας: η νόμιμη χρήση δεν χάνεται — ο πελάτης-επιχείρηση κρατά διαφορετικό ποσό από τις Ρυθμίσεις."""
    _enable_withholding(client)
    draft = client.post("/api/invoices", json=_body(office.company, net=1000.0, withholding_amount=150.0)).json()
    issued = client.post(f"/api/invoices/{draft['id']}/issue")
    assert issued.status_code == 200, issued.text
    row = _doc(draft["id"])
    assert (row["withholding_amount"], row["total"]) == (150.0, 1240.0)       # όχι 200 από τις Ρυθμίσεις
    listed = {r["id"]: r for r in client.get("/api/invoices").json()}
    assert listed[draft["id"]]["outstanding"] == 1090.0                         # 1.240 − 150


def test_an_explicit_zero_means_no_withholding_even_when_the_firm_withholds(client, office):
    _enable_withholding(client)
    draft = client.post("/api/invoices", json=_body(office.company, net=1000.0, withholding_amount=0.0)).json()
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 200
    assert _doc(draft["id"])["withholding_amount"] == 0.0


@pytest.mark.parametrize("amount", [-1.0, 1000.01])
def test_an_explicit_withholding_stays_within_zero_and_the_net_value(client, office, amount):
    r = client.post("/api/invoices", json=_body(office.company, net=1000.0, withholding_amount=amount))
    assert r.status_code == 400 and "από 0 έως την καθαρή αξία" in r.json()["detail"]


# ============================================================ 2. Έκδοση ====

def test_issuing_refuses_a_retail_draft_that_already_carries_a_withholding(client, office):
    """Πρόχειρο που ξέφυγε από τον έλεγχο (παλιά έκδοση, εισαγωγή): δεν εκδίδεται και δεν καταναλώνει αριθμό."""
    draft_id = _insert_draft(office.firm_id, office.person, net=500.0, invoice_type="11.2", withholding_amount=100.0)
    r = client.post(f"/api/invoices/{draft_id}/issue")
    assert r.status_code == 400 and "λιανικής" in r.json()["detail"]
    row = _doc(draft_id)
    assert row["status"] == "draft" and row["number"] == "" and "issue_claim" not in row
    from db import db
    assert _run(db.counters.count_documents({})) == 0


def test_issuing_checks_the_type_the_client_has_now(client, office):
    """Το πρόχειρο φτιάχτηκε για εταιρεία με παρακράτηση· ο πελάτης έγινε ιδιώτης πριν την έκδοση."""
    _enable_withholding(client)
    draft = client.post("/api/invoices", json=_body(office.company, net=1000.0, withholding_amount=200.0)).json()
    from db import db
    _run(db.clients.update_one({"_id": ObjectId(office.company)}, {"$set": {"client_type": "individual", "afm": ""}}))
    r = client.post(f"/api/invoices/{draft['id']}/issue")
    assert r.status_code == 400 and "λιανικής" in r.json()["detail"]
    assert _doc(draft["id"])["status"] == "draft"


def test_issuing_refuses_a_withholding_above_the_net_value(client, office):
    draft_id = _insert_draft(office.firm_id, office.company, net=500.0, invoice_type="2.1", withholding_amount=900.0)
    r = client.post(f"/api/invoices/{draft_id}/issue")
    assert r.status_code == 400 and "καθαρή αξία" in r.json()["detail"]
    assert _doc(draft_id)["status"] == "draft"


def test_issuing_still_computes_the_automatic_withholding_when_none_is_given(client, office):
    _enable_withholding(client)
    draft = client.post("/api/invoices", json=_body(office.company, net=1000.0)).json()
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 200
    assert _doc(draft["id"])["withholding_amount"] == 200.0


def test_a_retail_receipt_with_no_explicit_withholding_issues_without_withholding(client, office):
    """Η ΑΠΥ λιανικής βγαίνει κανονικά όταν η εταιρεία κρατά παρακράτηση: ο αυτόματος υπολογισμός δεν την αγγίζει."""
    _enable_withholding(client)
    draft = client.post("/api/invoices", json=_body(office.person, net=500.0)).json()
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 200
    row = _doc(draft["id"])
    assert (row["invoice_type"], row["withholding_amount"]) == ("11.2", 0.0)


# ============================================================= 3. Πιστωτικό ====

def _credit_draft(client, office):
    from db import db
    inv = client.post("/api/invoices", json=_body(office.company, net=1000.0)).json()
    issued = client.post(f"/api/invoices/{inv['id']}/issue")
    assert issued.status_code == 200, issued.text
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400000000000123"}}))
    draft = client.post(f"/api/invoices/{inv['id']}/credit-note", json={})
    assert draft.status_code == 200, draft.text
    return inv, draft.json()


def test_a_credit_note_draft_cannot_be_given_a_withholding(client, office):
    _inv, credit = _credit_draft(client, office)
    body = _body(office.company, net=-1000.0, withholding_amount=100.0)
    body["line_items"][0]["unit_price"] = -1000.0
    r = client.put(f"/api/invoices/{credit['id']}", json=body)
    assert r.status_code == 400 and "πιστωτικό" in r.json()["detail"]
    assert _doc(credit["id"]).get("withholding_amount") is None


def test_a_credit_note_draft_that_already_carries_a_withholding_is_not_issued(client, office):
    from db import db
    _inv, credit = _credit_draft(client, office)
    _run(db.invoices.update_one({"_id": ObjectId(credit["id"])}, {"$set": {"withholding_amount": 100.0}}))
    r = client.post(f"/api/invoices/{credit['id']}/issue")
    assert r.status_code == 400 and "πιστωτικό" in r.json()["detail"]
    assert _doc(credit["id"])["status"] == "draft"


def test_a_normal_credit_note_is_issued_with_zero_withholding(client, office):
    _inv, credit = _credit_draft(client, office)
    assert client.post(f"/api/invoices/{credit['id']}/issue").status_code == 200
    assert _doc(credit["id"])["withholding_amount"] == 0.0


# ============================================================== 4. Η συνάρτηση ====

@pytest.mark.parametrize("invoice_type,amount,subtotal,ok", [
    ("2.1", None, 500.0, True),
    ("2.1", 0.0, 500.0, True),
    ("2.1", 100.0, 500.0, True),
    ("2.1", 500.0, 500.0, True),          # το άνω όριο είναι η καθαρή αξία
    ("2.1", 500.01, 500.0, False),
    ("2.1", -0.01, 500.0, False),
    ("11.2", None, 500.0, True),
    ("11.2", 0.0, 500.0, True),
    ("11.2", 0.01, 500.0, False),
    ("5.1", None, -500.0, True),
    ("5.1", 0.0, -500.0, True),
    ("5.1", 10.0, -500.0, False),
])
def test_explicit_withholding_rules(invoice_type, amount, subtotal, ok):
    import invoice_services as svc
    invoice = {"withholding_amount": amount, "subtotal": subtotal}
    if ok:
        svc.check_explicit_withholding(invoice, invoice_type)
    else:
        with pytest.raises(svc.InvoiceWorkflowError):
            svc.check_explicit_withholding(invoice, invoice_type)
