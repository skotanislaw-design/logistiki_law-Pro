"""Ελεγκτικό ίχνος: ό,τι πρέπει να ισχύει για να σταθεί σε έλεγχο.

Τα tests εδώ ελέγχουν *ιδιότητες*, όχι υδραυλική: ότι κάθε αλλαγή αφήνει ακριβώς ένα ίχνος,
ότι οι αποτυχημένες συνδέσεις αποδίδονται, ότι κανένα μυστικό δεν διαρρέει, ότι το ίχνος
ενός γραφείου δεν διαβάζεται από άλλο, και ότι αποτυχία καταγραφής δεν ρίχνει το αίτημα.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _admin(client, email="admin@example.gr"):
    r = client.post("/api/auth/register",
                    json={"email": email, "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        db.users.update_one({"email": email}, {"$set": {"role": "admin"}}))
    return r.json()


def _rows(client, **params):
    r = client.get("/api/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_state_change_leaves_exactly_one_row(client):
    _admin(client)
    before = len(_rows(client))
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    rows = _rows(client)
    created = [r for r in rows if r["action"] == "post:clients"]
    assert len(created) == 1
    assert len(rows) == before + 1
    row = created[0]
    assert row["actor_type"] == "user"
    assert row["user_email"] == "admin@example.gr"
    assert row["status_code"] == 200
    assert row["method"] == "POST"


def test_reads_are_not_logged_by_default(client):
    """Αν καταγραφόταν κάθε GET, το ίχνος θα πνιγόταν σε θόρυβο και θα ήταν άχρηστο."""
    _admin(client)
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    before = len(_rows(client))
    client.get("/api/clients")
    client.get("/api/invoices")
    assert len(_rows(client)) == before


def test_sensitive_read_is_logged_explicitly(client):
    """Ο φάκελος δέουσας επιμέλειας είναι η εξαίρεση: η ανάγνωσή του καταγράφεται."""
    _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    client.post("/api/kyc/records", json={"client_id": cid, "client_name": "ΑΛΦΑ ΑΕ",
                                          "id_type": "id_card", "id_number": "ΑΒ123456"})
    client.get(f"/api/kyc/records/{cid}")
    reads = [r for r in _rows(client) if r["action"] == "read:kyc"]
    assert len(reads) == 1
    assert reads[0]["entity_id"] == cid
    assert reads[0]["method"] == "GET"


def test_sub_action_distinguishes_issue_from_create(client):
    """Χωρίς υπο-ενέργεια, η έκδοση και η δημιουργία ήταν και οι δύο «post:invoices»."""
    _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    iid = client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}],
    }).json()["id"]
    client.post(f"/api/invoices/{iid}/issue")

    history = client.get(f"/api/audit/entity/invoices/{iid}").json()
    assert [h["action"] for h in history] == ["post:invoices:issue"]


def test_failed_login_is_recorded_and_attributed(client):
    """«Ποιος προσπάθησε να μπει και απέτυχε» είναι η πρώτη ερώτηση κάθε ελέγχου."""
    _admin(client, "owner@example.gr")
    client.post("/api/auth/login", json={"email": "owner@example.gr", "password": "λάθος-κωδικός"})
    rows = _rows(client, action="login")
    failed = [r for r in rows if r["status_code"] == 401]
    assert len(failed) == 1
    assert failed[0]["user_email"] == "owner@example.gr"
    assert failed[0]["actor_type"] == "user"


def test_no_secret_ever_reaches_the_trail(client):
    """Το ίχνος δεν καταγράφει σώμα αιτήματος — αλλιώς θα γέμιζε κωδικούς και κλειδιά ΑΑΔΕ."""
    secret = "Sup3rSecretPassw0rd!"
    client.post("/api/auth/register",
                json={"email": "leak@example.gr", "password": secret, "name": "Δοκιμή"})
    from db import db
    loop = asyncio.get_event_loop_policy().new_event_loop()
    rows = loop.run_until_complete(db.audit_logs.find({}).to_list(500))
    assert rows, "δεν καταγράφηκε τίποτα"
    blob = str(rows)
    assert secret not in blob
    assert "password" not in blob.lower()


def test_audit_trail_is_tenant_scoped(client):
    """Το ίχνος ενός γραφείου δεν διαβάζεται από διαχειριστή άλλου γραφείου."""
    import server

    _admin(client, "firm-a@example.gr")
    client.post("/api/clients", json={"name": "ΠΕΛΑΤΗΣ ΤΟΥ Α", "afm": "123456783"})
    assert any(r["action"] == "post:clients" for r in _rows(client))

    with TestClient(server.app, base_url="https://testserver") as other:
        _admin(other, "firm-b@example.gr")
        rows_b = _rows(other)
        assert all("ΠΕΛΑΤΗΣ ΤΟΥ Α" not in str(r) for r in rows_b)
        assert all(r["user_email"] != "firm-a@example.gr" for r in rows_b)


def test_non_admin_cannot_read_the_trail(client):
    client.post("/api/auth/register",
                json={"email": "lawyer@example.gr", "password": "Str0ngPass!", "name": "Δικηγόρος"})
    assert client.get("/api/audit").status_code == 403


def test_audit_write_failure_does_not_break_the_request(client, monkeypatch):
    """Αποτυχία καταγραφής είναι σοβαρό γεγονός, αλλά δεν επιτρέπεται να εμποδίσει
    τον δικηγόρο να εκδώσει τιμολόγιο."""
    import audit

    _admin(client)

    async def boom(*a, **kw):
        raise RuntimeError("η βάση του ίχνους έπεσε")

    monkeypatch.setattr(audit.db.audit_logs, "insert_one", boom)
    r = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "987654324"})
    assert r.status_code == 200, r.text


def test_summary_counts_failed_logins(client):
    _admin(client, "sum@example.gr")
    for _ in range(2):
        client.post("/api/auth/login", json={"email": "sum@example.gr", "password": "λάθος"})
    s = client.get("/api/audit/summary").json()
    assert s["failed_logins"] >= 2
    assert s["total"] >= 2


# --------------------------------------------------------------------------
# Content-Disposition: ελληνικά ονόματα αρχείων έριχναν τον server με 500.
# --------------------------------------------------------------------------

def test_greek_filenames_do_not_break_http_headers():
    from http_utils import content_disposition

    for name in ["ΠΙΣ-2026-0001.pdf", "gdpr-ΑΛΦΑ_ΑΕ.zip", "Αγωγή κατά Βήτα ΕΠΕ.docx"]:
        header = content_disposition(name)
        header.encode("latin-1")           # ό,τι έσκαγε πριν
        assert "filename*=UTF-8''" in header


def test_gdpr_export_works_for_a_greek_client_name(client):
    """Το endpoint επέστρεφε 500 για κάθε πελάτη με ελληνικό όνομα — δηλαδή για όλους."""
    _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    r = client.get(f"/api/backup/gdpr/client/{cid}")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"          # έγκυρο ZIP


def test_credit_note_pdf_downloads(client):
    """Ο αριθμός πιστωτικού ξεκινά με «ΠΙΣ», που καμία από τις παλιές αντικαταστάσεις
    δεν κάλυπτε: η λήψη επέστρεφε 500."""
    from bson import ObjectId
    from db import db

    _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783",
                                            "client_type": "company"}).json()["id"]
    iid = client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}],
    }).json()["id"]
    client.post(f"/api/invoices/{iid}/issue")
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        db.invoices.update_one({"_id": ObjectId(iid)},
                               {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{iid}/credit-note", json={"reason": "Ακύρωση"}).json()
    assert credit["number"].startswith("ΠΙΣ")

    r = client.get(f"/api/invoices/{credit['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
