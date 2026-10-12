"""Έλεγχος γύρου 4 — Πακέτο 3: γραμμάτια προείσπραξης (και η αρίθμηση που μοιράζονται).

* Η αρίθμηση ήταν «πλήθος εγγράφων + 1»: μετά από μια διαγραφή δύο έγγραφα έπαιρναν τον ίδιο
  αριθμό. Το ίδιο σφάλμα υπήρχε και στα παράβολα και στις υποθέσεις.
* Η ενσωμάτωση σε τιμολόγιο έβαζε όλο το ποσό (μαζί με το ΦΠΑ) σε γραμμή 0%, άρα το ΦΠΑ
  του γραμματίου δεν έφτανε ποτέ στη δήλωση.
* Εκδοθέν γραμμάτιο μπορούσε να διαγραφεί από το API, και προσχέδιο ή ακυρωμένο να
  τιμολογηθεί.
"""
import asyncio

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
def firm(client):
    r = client.post("/api/auth/register",
                    json={"email": "gr@example.gr", "password": "Str0ngPass!", "name": "Δικηγόρος"})
    assert r.status_code == 200, r.text
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company"}).json()["id"]
    return cid


def _grammatio(client, cid, **over):
    body = {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": cid,
            "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0, "vat_amount": 24.0}
    body.update(over)
    return client.post("/api/grammatia", json=body)


def _issued(client, cid, **over):
    g = _grammatio(client, cid, **over).json()
    r = client.post(f"/api/grammatia/{g['id']}/issue", json={"portal_ref": "OLO-1"})
    assert r.status_code == 200, r.text
    return r.json()


def _suffix(number: str) -> str:
    return number.rsplit("-", 1)[-1]


# ----------------------------------------------------------- αρίθμηση ----

def test_grammatio_numbers_stay_unique_after_a_delete(client, firm):
    a = _grammatio(client, firm).json()
    b = _grammatio(client, firm).json()
    c = _grammatio(client, firm).json()
    assert client.delete(f"/api/grammatia/{b['id']}").status_code == 200
    d = _grammatio(client, firm).json()
    numbers = [a["number"], c["number"], d["number"]]
    assert len(set(numbers)) == 3, numbers
    assert [_suffix(n) for n in numbers] == ["0001", "0003", "0004"]


def test_paravolo_numbers_stay_unique_after_a_delete(client, firm):
    body = {"category": "Ένδικα βοηθήματα/μέσα (πολιτικά δικαστήρια)", "amount": 100.0}
    a = client.post("/api/paravola", json=body).json()
    b = client.post("/api/paravola", json=body).json()
    c = client.post("/api/paravola", json=body).json()
    assert client.delete(f"/api/paravola/{b['id']}").status_code == 200
    d = client.post("/api/paravola", json=body).json()
    assert len({a["number"], c["number"], d["number"]}) == 3
    assert _suffix(d["number"]) == "0004"


def test_case_numbers_stay_unique_after_a_delete(client, firm):
    ids = [client.post("/api/cases", json={"title": f"Υπόθεση {i}", "client_id": firm}).json() for i in range(3)]
    assert client.delete(f"/api/cases/{ids[1]['id']}").status_code == 200
    new = client.post("/api/cases", json={"title": "Νέα", "client_id": firm}).json()
    assert len({ids[0]["number"], ids[2]["number"], new["number"]}) == 3


def test_counter_continues_above_numbers_that_already_exist(client, firm):
    """Εγκατάσταση με έγγραφα από την εποχή του «πλήθος + 1», χωρίς μετρητή."""
    from db import db
    year = athens_today().year
    first = _grammatio(client, firm).json()          # δημιουργεί μετρητή· τον σβήνουμε για να μοιάσει με παλιά βάση
    firm_id = _run(db.grammatia.find_one({"_id": ObjectId(first["id"])}))["firm_id"]
    _run(db.counters.delete_many({"_id": {"$regex": "^grammatio-"}}))
    _run(db.grammatia.insert_one({"firm_id": firm_id, "number": f"ΓΡΠ-{year}-0007", "status": "draft",
                                  "court_type": "x", "reference_amount": 1.0, "total": 1.0}))
    nxt = _grammatio(client, firm).json()
    assert _suffix(nxt["number"]) == "0008"


# ------------------------------------------------------- ποσά και κατάσταση ----

@pytest.mark.parametrize("over", [
    {"reference_amount": -1.0},
    {"contributions_amount": -5.0},
    {"vat_amount": -0.01},
    {"reference_amount": 0.0, "contributions_amount": 0.0, "vat_amount": 0.0},
])
def test_invalid_amounts_are_rejected(client, firm, over):
    r = _grammatio(client, firm, **over)
    assert r.status_code == 400, r.text


def test_client_cannot_create_an_already_issued_or_invoiced_grammatio(client, firm):
    r = _grammatio(client, firm, status="issued", issued_at="2026-01-01", invoice_id="abc")
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["status"] == "draft" and not g.get("issued_at") and not g.get("invoice_id")


def test_only_drafts_can_be_deleted_edited_or_issued(client, firm):
    g = _issued(client, firm)
    assert client.delete(f"/api/grammatia/{g['id']}").status_code == 400
    assert client.put(f"/api/grammatia/{g['id']}", json={
        "court_type": "x", "reference_amount": 5.0}).status_code == 400
    assert client.post(f"/api/grammatia/{g['id']}/issue", json={}).status_code == 400
    assert client.get("/api/grammatia").json()[0]["id"] == g["id"]      # παραμένει στο αρχείο

    # ακυρωμένο: ούτε έκδοση ούτε τιμολόγηση
    from db import db
    _run(db.grammatia.update_one({"_id": ObjectId(g["id"])}, {"$set": {"status": "cancelled"}}))
    assert client.post(f"/api/grammatia/{g['id']}/issue", json={}).status_code == 400
    assert client.post(f"/api/grammatia/{g['id']}/to-invoice").status_code == 400


def test_draft_grammatio_can_still_be_deleted(client, firm):
    g = _grammatio(client, firm).json()
    assert client.delete(f"/api/grammatia/{g['id']}").status_code == 200


# ---------------------------------------------- ενσωμάτωση σε τιμολόγιο ----

def test_draft_grammatio_is_not_invoiced(client, firm):
    g = _grammatio(client, firm).json()
    r = client.post(f"/api/grammatia/{g['id']}/to-invoice")
    assert r.status_code == 400 and "εκδοθέν" in r.json()["detail"]
    assert client.get("/api/invoices").json() == []


def test_invoice_keeps_the_vat_of_the_grammatio_visible(client, firm):
    g = _issued(client, firm)                                # 100 αξία + 20 εισφορές + 24 ΦΠΑ = 144
    assert g["total"] == 144.0
    r = client.post(f"/api/grammatia/{g['id']}/to-invoice")
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["status"] == "draft" and inv["number"] == ""
    assert inv["date"] == athens_today().isoformat()
    rates = sorted((li["vat_rate"], li["unit_price"]) for li in inv["line_items"])
    assert rates == [(0, 20.0), (24, 100.0)]
    assert inv["subtotal"] == 120.0 and inv["vat_amount"] == 24.0 and inv["total"] == 144.0
    assert inv["total"] == g["total"]                        # ο πελάτης χρεώνεται ό,τι και στο γραμμάτιο
    assert client.get(f"/api/grammatia").json()[0]["invoice_id"] == inv["id"]


def test_second_conversion_is_refused_until_the_draft_invoice_is_deleted(client, firm):
    g = _issued(client, firm)
    inv = client.post(f"/api/grammatia/{g['id']}/to-invoice").json()
    assert client.post(f"/api/grammatia/{g['id']}/to-invoice").status_code == 400
    assert len(client.get("/api/invoices").json()) == 1       # δεν δημιουργήθηκε δεύτερο τιμολόγιο
    assert client.delete(f"/api/invoices/{inv['id']}").status_code == 200
    assert client.post(f"/api/grammatia/{g['id']}/to-invoice").status_code == 200


def test_grammatio_without_vat_is_invoiced_without_vat(client, firm):
    g = _issued(client, firm, reference_amount=64.0, contributions_amount=0.0, vat_amount=0.0)
    inv = client.post(f"/api/grammatia/{g['id']}/to-invoice").json()
    assert [li["vat_rate"] for li in inv["line_items"]] == [0]
    assert inv["total"] == 64.0 and inv["vat_amount"] == 0.0


def test_vat_that_matches_no_rate_is_refused_instead_of_buried(client, firm):
    g = _issued(client, firm, reference_amount=100.0, contributions_amount=0.0, vat_amount=10.0)
    r = client.post(f"/api/grammatia/{g['id']}/to-invoice")
    assert r.status_code == 400 and "ΦΠΑ" in r.json()["detail"]
    assert client.get("/api/invoices").json() == []
    assert "invoice_id" not in client.get("/api/grammatia").json()[0] or not client.get("/api/grammatia").json()[0]["invoice_id"]


def test_reduced_vat_rates_are_recognised(client, firm):
    g = _issued(client, firm, reference_amount=100.0, contributions_amount=0.0, vat_amount=13.0)
    inv = client.post(f"/api/grammatia/{g['id']}/to-invoice").json()
    assert [li["vat_rate"] for li in inv["line_items"]] == [13] and inv["total"] == 113.0


# ------------------------------------------------------------- παράβολα ----

def test_paravolo_state_rules(client, firm):
    body = {"category": "Ένδικα βοηθήματα/μέσα (πολιτικά δικαστήρια)", "amount": 100.0}
    forged = client.post("/api/paravola", json={**body, "status": "paid", "issued_at": "2026-01-01"}).json()
    assert forged["status"] == "draft"

    assert client.post(f"/api/paravola/{forged['id']}/mark-paid").status_code == 400      # δεν έχει εκδοθεί
    assert client.post(f"/api/paravola/{forged['id']}/issue", json={"eparavolo_code": "E1"}).status_code == 200
    assert client.delete(f"/api/paravola/{forged['id']}").status_code == 400             # εκδοθέν: μένει
    assert client.post(f"/api/paravola/{forged['id']}/mark-paid").status_code == 200
    assert client.post(f"/api/paravola/{forged['id']}/mark-paid").status_code == 400     # ήδη εξοφλημένο
    assert client.delete(f"/api/paravola/{forged['id']}").status_code == 400
