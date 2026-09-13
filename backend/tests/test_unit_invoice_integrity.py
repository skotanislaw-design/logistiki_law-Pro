"""Ακεραιότητα λογιστικού αρχείου: επικύρωση εισόδου, κύκλος ζωής παραστατικού.

Κάθε test εδώ αντιστοιχεί σε συμπεριφορά που ο έλεγχος βρήκε ανοιχτή: τιμολόγια με
αρνητικές ποσότητες ή ανύπαρκτους συντελεστές ΦΠΑ γίνονταν δεκτά, εκδοθέντα
παραστατικά διαγράφονταν οριστικά, και ο client μπορούσε να δηλώσει μόνος του ότι
το παραστατικό διαβιβάστηκε στην ΑΑΔΕ.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def owner(client):
    r = client.post("/api/auth/register",
                    json={"email": "acct@example.gr", "password": "Str0ngPass!", "name": "Λογιστής"})
    assert r.status_code == 200, r.text
    return client.post("/api/clients",
                       json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company"}).json()["id"]


LINE = [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}]


def _draft(client, client_id, **over):
    body = {"client_id": client_id, "date": "2026-09-10", "line_items": LINE}
    body.update(over)
    return client.post("/api/invoices", json=body)


@pytest.mark.parametrize("label,line_items,date", [
    ("αρνητική ποσότητα", [{"description": "x", "quantity": -5, "unit_price": 1000.0, "vat_rate": 24}], "2026-09-10"),
    ("μηδενική ποσότητα", [{"description": "x", "quantity": 0, "unit_price": 1000.0, "vat_rate": 24}], "2026-09-10"),
    ("ανύπαρκτος ΦΠΑ", [{"description": "x", "quantity": 1, "unit_price": 100.0, "vat_rate": 99}], "2026-09-10"),
    ("κενή περιγραφή", [{"description": "   ", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}], "2026-09-10"),
    ("χωρίς γραμμές", [], "2026-09-10"),
    ("άκυρη ημερομηνία", LINE, "ΟΧΙ-ΗΜΕΡΟΜΗΝΙΑ"),
])
def test_invalid_invoices_are_rejected(client, owner, label, line_items, date):
    r = client.post("/api/invoices", json={"client_id": owner, "date": date, "line_items": line_items})
    assert r.status_code == 400, f"{label}: έγινε δεκτό ({r.status_code})"
    assert r.json()["detail"]


def test_valid_vat_rates_are_accepted(client, owner):
    for rate in (0, 6, 13, 24):
        r = _draft(client, owner, line_items=[{"description": "Υπηρεσία", "quantity": 1,
                                               "unit_price": 100.0, "vat_rate": rate}])
        assert r.status_code == 200, f"συντελεστής {rate}%: {r.text}"


def test_client_cannot_forge_mydata_transmission(client, owner):
    """Ο client δήλωνε μόνος του synced + MARK, δηλαδή παραστατικό «διαβιβασμένο»
    στην ΑΑΔΕ που δεν στάλθηκε ποτέ."""
    inv = _draft(client, owner, status="paid", mydata_status="synced",
                 mydata_mark="400001234567830", paid_at="2020-01-01T00:00:00Z").json()
    assert inv["status"] == "draft"
    assert inv["mydata_status"] == "not_sent"
    assert inv["mydata_mark"] is None
    assert inv.get("paid_at") is None


def test_issued_invoice_cannot_be_deleted(client, owner):
    """ΕΛΠ Ν.4308/2014: η σειρά αρίθμησης μένει αδιάσπαστη."""
    inv = _draft(client, owner).json()
    assert client.post(f"/api/invoices/{inv['id']}/issue").status_code == 200
    r = client.delete(f"/api/invoices/{inv['id']}")
    assert r.status_code == 400
    assert "δεν διαγράφεται" in r.json()["detail"]
    assert client.get(f"/api/invoices/{inv['id']}").status_code == 200


def test_draft_invoice_can_still_be_deleted(client, owner):
    inv = _draft(client, owner).json()
    assert client.delete(f"/api/invoices/{inv['id']}").status_code == 200
    assert client.get(f"/api/invoices/{inv['id']}").status_code == 404


def test_issued_invoice_cannot_be_edited(client, owner):
    inv = _draft(client, owner).json()
    client.post(f"/api/invoices/{inv['id']}/issue")
    r = client.put(f"/api/invoices/{inv['id']}",
                   json={"client_id": owner, "date": "2026-09-10",
                         "line_items": [{"description": "Αλλαγμένο", "quantity": 1,
                                         "unit_price": 5.0, "vat_rate": 24}]})
    assert r.status_code == 400
    assert client.get(f"/api/invoices/{inv['id']}").json()["total"] == 1240


def test_update_cannot_move_invoice_back_to_draft_or_forge_mark(client, owner):
    inv = _draft(client, owner).json()
    r = client.put(f"/api/invoices/{inv['id']}",
                   json={"client_id": owner, "date": "2026-09-10", "line_items": LINE,
                         "status": "paid", "mydata_status": "synced", "mydata_mark": "400009999999999"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"
    assert r.json()["mydata_mark"] is None


def test_credit_note_still_produces_a_negative_document(client, owner):
    """Η επικύρωση δεν επιτρέπεται να σπάσει το πιστωτικό, που χτίζεται
    server-side με θετική ποσότητα και αρνητική τιμή μονάδας."""
    from bson import ObjectId
    from db import db

    inv = _draft(client, owner).json()
    client.post(f"/api/invoices/{inv['id']}/issue")
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        db.invoices.update_one({"_id": ObjectId(inv["id"])},
                               {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    r = client.post(f"/api/invoices/{inv['id']}/credit-note", json={"reason": "Ακύρωση παροχής"})
    assert r.status_code == 200, r.text
    credit = r.json()
    assert credit["total"] < 0
    assert credit["line_items"][0]["quantity"] > 0
    assert credit["line_items"][0]["unit_price"] < 0
