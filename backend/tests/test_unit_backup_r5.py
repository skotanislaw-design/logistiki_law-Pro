"""Γύρος 5 — το αντίγραφο ασφαλείας περιέχει ό,τι χρειάζεται για να σταθούν τα βιβλία, και η επαναφορά δεν
γράφει πάνω σε ό,τι δεν της ανήκει (#59 και τα ευρήματα που βγήκαν από την ανάλυσή του).

* Το ελεγκτικό ίχνος εξαγόταν ως κενό αρχείο: η λίστα είχε το όνομα `audit_log`, η συλλογή λέγεται `audit_logs`.
* Η εξαγωγή έκοβε σιωπηλά στις 50.000 εγγραφές ανά συλλογή — και το ίχνος είναι η συλλογή που μεγαλώνει πιο γρήγορα.
* Δεν περιλαμβάνονταν οι τραπεζικές κινήσεις (με την κατάσταση συμφωνίας), οι περίοδοι ΦΠΑ, τα μηνύματα της πύλης
  και τα πρότυπα σύνταξης· η συλλογή `documents` δεν υπάρχει καν.
* Η επαναφορά αναγνώριζε έγγραφα μόνο με `_id`: αρχείο με `_id` εγγράφου ΑΛΛΟΥ γραφείου το αντικαθιστούσε και το
  περνούσε στο γραφείο που έκανε την επαναφορά· το ίδιο ίσχυε για τους μετρητές αρίθμησης.
* Ο μετρητής αρίθμησης μπορούσε να γυρίσει πίσω από παλαιότερο αντίγραφο — ενώ τα τιμολόγια που εκδόθηκαν μετά
  έμεναν στη βάση.
* Ένα αντίγραφο που ΔΕΝ προστατεύει το ίχνος από αντικατάσταση δεν είναι ελεγκτικό ίχνος: η επαναφορά το συμπληρώνει
  μόνο (ό,τι υπάρχει ήδη δεν αγγίζεται) και αναφέρει όσες υπάρχουσες γραμμές διαφέρουν από το αντίγραφο.
"""
import asyncio
import io
import json
import pathlib
import re
import zipfile

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _admin(client, email="backup@example.gr"):
    r = client.post("/api/auth/register", json={"email": email, "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    _run(db.users.update_one({"email": email}, {"$set": {"role": "admin"}}))
    return _run(db.users.find_one({"email": email}))


def _export_bytes(client) -> bytes:
    r = client.get("/api/backup/export")
    assert r.status_code == 200, r.text
    return r.content


def _open(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def _rows(z: zipfile.ZipFile, name: str) -> list:
    return json.loads(z.read(f"{name}.json").decode("utf-8"))


def _meta(z: zipfile.ZipFile) -> dict:
    return json.loads(z.read("_meta.json").decode("utf-8"))


def _archive(files: dict) -> bytes:
    """Αντίγραφο όπως θα το έφτιαχνε κάποιος με το χέρι (ή ένας κακόβουλος διαχειριστής)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_meta.json", json.dumps({"version": 1}))
        for name, rows in files.items():
            zf.writestr(f"{name}.json", json.dumps(rows))
    return buf.getvalue()


def _import(client, content: bytes):
    return client.post("/api/backup/import", files={"file": ("backup.zip", content, "application/zip")})


def _oid(value) -> dict:
    return {"__oid__": str(value)}


def _audit_row(rows: list, action: str) -> dict:
    return next(r for r in rows if r["action"] == action)


# ============================================================= 1. Τι περιέχει η εξαγωγή ====

def test_export_contains_the_real_audit_trail_not_an_empty_file(client):
    _admin(client)
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    z = _open(_export_bytes(client))
    names = z.namelist()
    assert "audit_logs.json" in names
    assert "audit_log.json" not in names, "το παλιό (λάθος) όνομα δεν πρέπει να εξάγεται"
    rows = _rows(z, "audit_logs")
    assert any(r["action"] == "post:clients" for r in rows)
    counts = _meta(z)["counts"]
    assert counts["audit_logs"] == len(rows) > 0
    assert "audit_log" not in counts


def test_export_is_not_capped_at_fifty_thousand_rows(client):
    """Η εξαγωγή έκοβε σιωπηλά στις 50.000: ένα αντίγραφο που λείπει το τέλος του ίχνους δεν φαίνεται λειψό."""
    user = _admin(client)
    from db import db
    fid = user["firm_id"]
    _run(db.audit_logs.insert_many([
        {"firm_id": fid, "action": "post:clients", "entity": "clients", "entity_id": "", "user_email": "x@example.gr",
         "actor_type": "user", "created_at": f"2026-01-01T00:00:00.{i:06d}+00:00"}
        for i in range(50_050)]))
    z = _open(_export_bytes(client))
    stored = _run(db.audit_logs.count_documents({"firm_id": fid}))
    assert stored > 50_000
    assert len(_rows(z, "audit_logs")) == stored
    assert _meta(z)["counts"]["audit_logs"] == stored


SEEDED = {
    "bank_transactions": {"external_id": "tx-1", "amount": 124.0, "description": "Έμβασμα πελάτη",
                          "date": "2026-09-10", "status": "reconciled"},
    "vat_periods": {"period": "2026-08", "status": "filed", "vat_due": 310.5},
    "portal_messages": {"client_id": "c1", "sender": "client", "text": "Καλημέρα", "created_at": "2026-09-01T09:00:00+00:00"},
    "case_tracker_events": {"case_id": "k1", "title": "Αναβολή", "fetched_at": "2026-09-02T09:00:00+00:00"},
    "drafting_templates": {"title": "Πρότυπο αγωγής", "body": "…"},
    "drafting_clauses": {"title": "Ρήτρα εμπιστευτικότητας", "body": "…"},
}


def _seed_other_collections(firm_id: str) -> None:
    from db import db
    for name, doc in SEEDED.items():
        _run(db[name].insert_one({**doc, "firm_id": firm_id}))
        _run(db[name].insert_one({**doc, "firm_id": "firm-someone-else"}))   # άλλου γραφείου: δεν εξάγεται


def test_export_includes_bank_vat_portal_and_drafting_data_of_this_firm_only(client):
    user = _admin(client)
    _seed_other_collections(user["firm_id"])
    z = _open(_export_bytes(client))
    for name in SEEDED:
        rows = _rows(z, name)
        assert len(rows) == 1, f"{name}: αναμενόταν μία εγγραφή του γραφείου"
        assert rows[0]["firm_id"] == user["firm_id"]
        assert _meta(z)["counts"][name] == 1
    assert "documents.json" not in z.namelist(), "η συλλογή «documents» δεν υπάρχει"


def test_export_says_what_it_does_not_contain_and_why(client):
    _admin(client)
    meta = _meta(_open(_export_bytes(client)))
    assert "users" in meta["not_included"]
    assert all(reason.strip() for reason in meta["not_included"].values())


# ============================================================= 2. Επαναφορά: ανάκτηση ====

def test_restore_brings_back_everything_that_was_exported(client):
    user = _admin(client)
    from db import db
    from routes_backup import COLLECTIONS
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    _seed_other_collections(user["firm_id"])
    backup = _export_bytes(client)
    z = _open(backup)
    exported = {name: _rows(z, name) for name in COLLECTIONS if f"{name}.json" in z.namelist()}

    for name in COLLECTIONS:                       # απώλεια δεδομένων (όχι χρηστών: ο διαχειριστής πρέπει να μπορεί να συνδεθεί)
        _run(db[name].delete_many({}))

    r = _import(client, backup)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    for name, rows in exported.items():
        assert body["restored"][name] == len(rows), name
        assert _run(db[name].count_documents({})) >= len(rows), name
    assert _run(db.clients.find_one({"_id": ObjectId(cid)}))["name"] == "ΑΛΦΑ ΑΕ"
    assert _run(db.bank_transactions.count_documents({"external_id": "tx-1"})) == 1
    # Όλες οι γραμμές του ίχνους που εξήχθησαν υπάρχουν ξανά
    ids = {ObjectId(r["_id"]["__oid__"]) for r in exported["audit_logs"]}
    assert ids <= {d["_id"] for d in _run(db.audit_logs.find({}).to_list(100000))}


def test_restore_still_replaces_ordinary_documents_with_the_same_id(client):
    """Τα επιχειρησιακά δεδομένα επαναφέρονται όπως πριν (η αντικατάσταση είναι ο σκοπός τους)."""
    _admin(client)
    from db import db
    cid = client.post("/api/clients", json={"name": "ΑΡΧΙΚΟ ΟΝΟΜΑ", "afm": "123456783"}).json()["id"]
    backup = _export_bytes(client)
    _run(db.clients.update_one({"_id": ObjectId(cid)}, {"$set": {"name": "ΑΛΛΑΓΜΕΝΟ ΟΝΟΜΑ"}}))
    assert _import(client, backup).status_code == 200
    assert _run(db.clients.find_one({"_id": ObjectId(cid)}))["name"] == "ΑΡΧΙΚΟ ΟΝΟΜΑ"


def test_old_backup_with_the_misnamed_audit_file_still_restores(client):
    _admin(client)
    r = _import(client, _archive({"audit_log": [], "clients": []}))
    assert r.status_code == 200, r.text
    assert "audit_log" not in r.json()["restored"]


def test_a_collection_file_that_is_not_a_list_is_reported_not_a_crash(client):
    _admin(client)
    r = _import(client, _archive({"clients": {"not": "a list"}, "expenses": []}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("clients" in e for e in body["errors"])
    assert body["restored"]["expenses"] == 0


# ============================================================= 3. Επαναφορά: το ίχνος μόνο συμπληρώνεται ====

def test_restore_adds_only_the_missing_audit_rows_and_marks_them(client):
    user = _admin(client)
    from db import db
    for i in range(3):
        client.post("/api/clients", json={"name": f"ΠΕΛΑΤΗΣ {i}", "afm": "123456783"})
    backup = _export_bytes(client)
    exported = _rows(_open(backup), "audit_logs")
    victims = [ObjectId(r["_id"]["__oid__"]) for r in exported if r["action"] == "post:clients"]
    assert len(victims) == 3
    _run(db.audit_logs.delete_many({"_id": {"$in": victims}}))

    body = _import(client, backup).json()

    assert body["restored"]["audit_logs"] == 3
    assert body["already_present"]["audit_logs"] == len(exported) - 3
    assert body["differing"].get("audit_logs", 0) == 0
    for oid in victims:
        row = _run(db.audit_logs.find_one({"_id": oid}))
        assert row["restored_by"] == user["email"]
        assert row["restored_at"]
        assert row["firm_id"] == user["firm_id"]
    untouched = _run(db.audit_logs.find({"_id": {"$nin": victims}, "restored_at": {"$exists": True}}).to_list(100))
    assert untouched == [], "οι γραμμές που υπήρχαν ήδη δεν σημαίνονται ως επαναφερμένες"


def test_restore_never_rewrites_an_existing_audit_row_and_says_when_it_differs(client):
    _admin(client)
    from db import db
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    backup = _export_bytes(client)
    exported = _rows(_open(backup), "audit_logs")
    target = ObjectId(_audit_row(exported, "post:clients")["_id"]["__oid__"])
    _run(db.audit_logs.update_one({"_id": target}, {"$set": {"user_email": "kapoios.allos@example.gr"}}))

    body = _import(client, backup).json()

    row = _run(db.audit_logs.find_one({"_id": target}))
    assert row["user_email"] == "kapoios.allos@example.gr", "η υπάρχουσα γραμμή δεν αντικαθίσταται ποτέ"
    assert "restored_at" not in row
    assert body["restored"]["audit_logs"] == 0
    assert body["already_present"]["audit_logs"] == len(exported)
    assert body["differing"]["audit_logs"] == 1
    assert any("ΠΡΟΣΟΧΗ" in note for note in body["notes"])
    assert body["message"] == "Η επαναφορά ολοκληρώθηκε."


def test_restoring_the_same_backup_twice_changes_nothing(client):
    _admin(client)
    from db import db
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    backup = _export_bytes(client)
    first = _import(client, backup).json()
    before = _run(db.audit_logs.count_documents({}))
    second = _import(client, backup).json()
    # Το αίτημα επαναφοράς αφήνει και το ίδιο του ίχνος (μία γραμμή ανά κλήση) — τίποτε άλλο δεν προστίθεται.
    assert _run(db.audit_logs.count_documents({})) == before + 1
    assert second["restored"]["audit_logs"] == 0
    assert second["differing"].get("audit_logs", 0) == 0
    assert first["errors"] == second["errors"] == []


def test_restored_audit_rows_are_readable_through_the_audit_api(client):
    user = _admin(client)
    from db import db
    client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"})
    backup = _export_bytes(client)
    _run(db.audit_logs.delete_many({"action": "post:clients"}))
    assert _import(client, backup).status_code == 200
    r = client.get("/api/audit", params={"action": "post:clients"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["restored_by"] == user["email"]


# ============================================================= 4. Επαναφορά: μόνο τα δεδομένα του γραφείου ====

def _two_firms(client):
    """Γραφείο Α (client) και γραφείο Β (με πελάτη και γραμμή ίχνους). Επιστρέφει (user_a, user_b, cid_b, audit_b)."""
    import server
    from db import db
    user_a = _admin(client, "a@example.gr")
    with TestClient(server.app, base_url="https://testserver") as other:
        user_b = _admin(other, "b@example.gr")
        cid_b = other.post("/api/clients", json={"name": "ΠΕΛΑΤΗΣ ΤΟΥ Β", "afm": "123456783"}).json()["id"]
    audit_b = _run(db.audit_logs.find_one({"firm_id": user_b["firm_id"], "action": "post:clients"}))
    assert audit_b is not None
    return user_a, user_b, cid_b, audit_b


def test_restore_cannot_overwrite_or_take_over_another_firms_documents(client):
    from db import db
    user_a, user_b, cid_b, audit_b = _two_firms(client)
    forged = _archive({
        "clients": [{"_id": _oid(cid_b), "name": "ΚΑΤΑΛΗΦΘΗΚΕ", "firm_id": user_b["firm_id"]}],
        "audit_logs": [{"_id": _oid(audit_b["_id"]), "action": "forged", "firm_id": user_b["firm_id"]}],
    })
    r = _import(client, forged)
    assert r.status_code == 200, r.text
    body = r.json()

    victim = _run(db.clients.find_one({"_id": ObjectId(cid_b)}))
    assert victim["name"] == "ΠΕΛΑΤΗΣ ΤΟΥ Β"
    assert victim["firm_id"] == user_b["firm_id"], "το έγγραφο δεν περνά στο γραφείο που κάνει την επαναφορά"
    row = _run(db.audit_logs.find_one({"_id": audit_b["_id"]}))
    assert row["action"] == "post:clients" and row["firm_id"] == user_b["firm_id"]
    assert body["restored"]["clients"] == 0
    assert body["restored"]["audit_logs"] == 0
    assert body["already_present"].get("audit_logs", 0) == 0, "έγγραφο άλλου γραφείου δεν μετρά ως «υπήρχε ήδη»"
    assert sum("άλλο γραφείο" in e for e in body["errors"]) == 2, body["errors"]


def test_restore_cannot_touch_another_firms_numbering_counter(client):
    from db import db
    _admin(client)
    foreign = "invoice-firm-" + "0" * 32 + "-2026"
    _run(db.counters.insert_one({"_id": foreign, "seq": 7, "last_date": "2026-05-01"}))
    body = _import(client, _archive({"counters": [{"_id": foreign, "seq": 1, "last_date": "2026-01-01"}]})).json()
    counter = _run(db.counters.find_one({"_id": foreign}))
    assert counter["seq"] == 7 and counter["last_date"] == "2026-05-01"
    assert body["restored"]["counters"] == 0
    assert any("δεν ανήκει στο γραφείο" in e for e in body["errors"]), body["errors"]


def test_restore_accepts_the_counters_of_every_series_of_this_firm(client):
    from db import db
    user = _admin(client)
    fid = user["firm_id"]
    ids = [f"invoice-{fid}-2026", f"invoice-ΑΠΥ-{fid}-2026", f"invoice-ΠΙΣ-{fid}-2025"]
    body = _import(client, _archive({"counters": [{"_id": i, "seq": 4, "last_date": "2026-02-02"} for i in ids]})).json()
    assert body["errors"] == []
    assert body["restored"]["counters"] == 3
    assert {d["_id"] for d in _run(db.counters.find({}).to_list(10))} == set(ids)


def test_restore_never_moves_a_numbering_counter_backwards(client):
    """Παλαιότερο αντίγραφο πάνω σε ζωντανή βάση: τα τιμολόγια που εκδόθηκαν μετά μένουν, άρα ο μετρητής δεν γυρίζει."""
    from db import db
    user = _admin(client)
    cid = f"invoice-{user['firm_id']}-2026"
    _run(db.counters.insert_one({"_id": cid, "seq": 5, "last_date": "2026-03-01"}))
    backup = _export_bytes(client)

    _run(db.counters.update_one({"_id": cid}, {"$set": {"seq": 9, "last_date": "2026-06-01"}}))    # εκδόθηκαν 4 ακόμη
    body = _import(client, backup).json()
    counter = _run(db.counters.find_one({"_id": cid}))
    assert (counter["seq"], counter["last_date"]) == (9, "2026-06-01")
    assert body["already_present"]["counters"] == 1 and body["restored"]["counters"] == 0

    _run(db.counters.update_one({"_id": cid}, {"$set": {"seq": 2, "last_date": "2026-01-01"}}))    # απώλεια: η βάση είναι πίσω
    body = _import(client, backup).json()
    counter = _run(db.counters.find_one({"_id": cid}))
    assert (counter["seq"], counter["last_date"]) == (5, "2026-03-01"), "μπροστά μόνο"
    assert body["restored"]["counters"] == 1


# ============================================================= 5. Ταξινόμηση συλλογών ====

def test_every_collection_in_the_code_is_backed_up_or_explicitly_excluded():
    """Το #59 προήλθε από όνομα που δεν ταίριαζε με καμία συλλογή και πέρασε απαρατήρητο. Κάθε συλλογή που αγγίζει ο
    κώδικας πρέπει να είναι είτε στο αντίγραφο είτε στη λίστα των εξαιρέσεων, με λόγο· και κάθε όνομα στο αντίγραφο
    πρέπει να αντιστοιχεί σε συλλογή που όντως χρησιμοποιείται."""
    import routes_backup
    root = pathlib.Path(routes_backup.__file__).resolve().parent
    used: set[str] = set()
    for path in root.glob("*.py"):
        used |= set(re.findall(r"\bdb\.([a-z_]+)\.[a-z_]+\(", path.read_text(encoding="utf-8")))
    used.discard("client")                                    # db.client.close(): ο οδηγός, όχι συλλογή

    backed_up = set(routes_backup.COLLECTIONS)
    excluded = set(routes_backup.NOT_BACKED_UP)
    assert len(routes_backup.COLLECTIONS) == len(backed_up), "διπλό όνομα στη λίστα"
    assert not backed_up & excluded, backed_up & excluded
    assert used - backed_up - excluded == set(), f"συλλογές χωρίς απόφαση για αντίγραφο: {sorted(used - backed_up - excluded)}"
    assert backed_up - used == set(), f"ονόματα στο αντίγραφο που δεν αντιστοιχούν σε συλλογή: {sorted(backed_up - used)}"
    assert excluded - used == set(), f"εξαιρέσεις για συλλογές που δεν υπάρχουν: {sorted(excluded - used)}"
    assert all(reason.strip() for reason in routes_backup.NOT_BACKED_UP.values())
    assert "audit_logs" in backed_up and "bank_transactions" in backed_up and "vat_periods" in backed_up
