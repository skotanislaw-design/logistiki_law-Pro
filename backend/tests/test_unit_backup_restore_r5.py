"""Γύρος 5 (ανασκόπηση 3) — η επαναφορά αντιγράφου ασφαλείας ως αξιόπιστη διαδρομή ανάκτησης (WP11).

Δύο ανεξάρτητοι ελεγκτές έδειξαν ότι η επαναφορά δεν ήταν ακόμη εμπιστεύσιμη:

* Η εξαγωγή έπαιρνε τους μετρητές αρίθμησης ΟΛΩΝ των σειρών (τιμολόγια, υποθέσεις, γραμμάτια, παράβολα), η επαναφορά
  δεχόταν μόνο τους μετρητές τιμολογίων: κάθε γραφείο που είχε ανοίξει έστω μία υπόθεση έβλεπε «αποτυχία» σε μια
  φυσιολογική επαναφορά και έχανε τον μετρητή των υπόλοιπων σειρών.
* Οι ρυθμίσεις του γραφείου (επωνυμία, ΑΦΜ, SMTP, ΑΑΔΕ) επανέρχονταν ως *δεύτερο* έγγραφο δίπλα στο κενό
  προεπιλεγμένο που δημιουργεί η σελίδα Ρυθμίσεων πριν την επαναφορά — και η εφαρμογή συνέχιζε να διαβάζει το κενό.
* Ένα έγγραφο που δεν γράφεται (αριθμός πέρα από 8 byte, κλειδί με NUL, `$` στο όνομα πεδίου) τίναζε ολόκληρη την
  επαναφορά με HTTP 500, αφήνοντάς την στη μέση. Ένας μετρητής με «12» στο `seq` αποθηκευόταν ως κείμενο και
  χαλούσε κάθε έκδοση τιμολογίου.
* Κανένα όριο μεγέθους στο αρχείο: ένα ZIP 300 KB που δηλώνει 315 MB έτρωγε 600 MB μνήμης· η εξαγωγή κρατούσε το
  αρχείο δύο φορές στη μνήμη.
* Οι σύνδεσμοι πύλης (που ισχύουν σαν κωδικοί) επανέρχονταν από κάθε παλιό αντίγραφο, ακόμη και αν είχαν ανακληθεί.
* Το GDPR export είχε μόνο έξι κατηγορίες και σταματούσε στα 5.000 έγγραφα.

Και πολλά tests που θα τα έπιαναν έλειπαν: αναγκαστικό `firm_id`, ρόλος admin, όριο 500 γραμμών ανά κομμάτι, σήμανση
επαναφοράς στη σύγκριση, μετρητές άλλων γραφείων, μέτρηση αποτυχιών.
"""
import asyncio
import io
import json
import pathlib
import re
import tempfile
import zipfile

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, DuplicateKeyError, WriteError


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _register(client, email):
    r = client.post("/api/auth/register", json={"email": email, "password": "Str0ngPass!", "name": "Χρήστης"})
    assert r.status_code == 200, r.text
    from db import db
    return _run(db.users.find_one({"email": email}))


def _admin(client, email="backup@example.gr"):
    _register(client, email)
    from db import db
    _run(db.users.update_one({"email": email}, {"$set": {"role": "admin"}}))
    return _run(db.users.find_one({"email": email}))


def _export(client) -> bytes:
    r = client.get("/api/backup/export")
    assert r.status_code == 200, r.text
    return r.content


def _open(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def _rows(z: zipfile.ZipFile, name: str) -> list:
    return json.loads(z.read(f"{name}.json").decode("utf-8"))


def _meta(z: zipfile.ZipFile) -> dict:
    return json.loads(z.read("_meta.json").decode("utf-8"))


def _archive(files: dict | None = None, *, meta: dict | str | None = None, raw: dict | None = None,
             compress: bool = False) -> bytes:
    """Αντίγραφο όπως θα το έφτιαχνε κάποιος με το χέρι (ή ένας κακόβουλος διαχειριστής)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED) as zf:
        zf.writestr("_meta.json", meta if isinstance(meta, str) else json.dumps({"version": 1} if meta is None else meta))
        for name, rows in (files or {}).items():
            zf.writestr(f"{name}.json", json.dumps(rows))
        for filename, text in (raw or {}).items():
            zf.writestr(filename, text)
    return buf.getvalue()


def _import(client, content: bytes):
    return client.post("/api/backup/import", files={"file": ("backup.zip", content, "application/zip")})


def _oid(value) -> dict:
    return {"__oid__": str(value)}


def _count(collection: str, query: dict | None = None) -> int:
    from db import db
    return _run(db[collection].count_documents(query or {}))


# ============================================================================ 1. Μετρητές αρίθμησης ====

def _counter_ids(firm_id: str) -> list[str]:
    return [f"invoice-{firm_id}-2026", f"invoice-ΑΠΥ-{firm_id}-2026", f"invoice-ΠΙΣ-{firm_id}-2025",
            f"case-{firm_id}-2026", f"grammatio-{firm_id}-2026", f"paravolo-{firm_id}-2026"]


FOREIGN_FIRM = "firm-" + "c" * 32


def _seed_counters(firm_id: str) -> dict:
    from db import db
    seqs = {}
    for i, counter_id in enumerate(_counter_ids(firm_id), start=3):
        seqs[counter_id] = i
        _run(db.counters.insert_one({"_id": counter_id, "seq": i, "last_date": f"2026-05-0{i - 2}"}))
    return seqs


def test_export_and_restore_cover_the_counters_of_every_series(client):
    """Η εξαγωγή έπαιρνε και τις τέσσερις οικογένειες μετρητών, η επαναφορά μόνο τα τιμολόγια."""
    from db import db
    user = _admin(client)
    seqs = _seed_counters(user["firm_id"])
    _seed_counters(FOREIGN_FIRM)                                       # μετρητές άλλου γραφείου: δεν εξάγονται

    backup = _export(client)
    z = _open(backup)
    assert {r["_id"] for r in _rows(z, "counters")} == set(seqs)
    assert _meta(z)["counts"]["counters"] == len(seqs)

    _run(db.counters.delete_many({}))
    body = _import(client, backup).json()

    assert body["errors"] == [] and body["failed"] == 0
    assert body["restored"]["counters"] == len(seqs)
    stored = {c["_id"]: c["seq"] for c in _run(db.counters.find({}).to_list(100))}
    assert stored == seqs, "οι μετρητές του άλλου γραφείου δεν επανέρχονται, του δικού μας ναι"


def test_a_firm_that_used_cases_grammatia_and_paravola_restores_without_a_false_failure(client):
    """Η αναπαραγωγή του ελεγκτή: ένα γραφείο με υπόθεση έβλεπε `failed: 1` σε κάθε φυσιολογική επαναφορά."""
    from db import db
    user = _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    assert client.post("/api/cases", json={"title": "Υπόθεση", "client_id": cid, "client_name": "ΑΛΦΑ ΑΕ"}).status_code == 200
    assert client.post("/api/grammatia", json={
        "court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": cid,
        "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0,
        "vat_amount": 24.0}).status_code == 200
    assert client.post("/api/paravola", json={"category": "Λοιπά παράβολα", "amount": 10.0}).status_code == 200
    invoice = client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()["id"]
    assert client.post(f"/api/invoices/{invoice}/issue").status_code == 200

    live = {c["_id"]: c["seq"] for c in _run(db.counters.find({}).to_list(100))}
    assert {i.split("-")[0] for i in live} == {"invoice", "case", "grammatio", "paravolo"}, live
    backup = _export(client)
    _run(db.counters.delete_many({}))

    body = _import(client, backup).json()
    assert body["failed"] == 0 and body["errors"] == [], body
    assert {c["_id"]: c["seq"] for c in _run(db.counters.find({}).to_list(100))} == live
    assert user["firm_id"] in "".join(live)


def test_every_counter_family_the_code_creates_is_covered_by_the_backup():
    """Μια νέα σειρά αρίθμησης που δεν μπαίνει στη λίστα οικογενειών θα έβγαινε πάλι εκτός αντιγράφου (ή θα
    αποτύγχανε στην επαναφορά). Το test διαβάζει τον κώδικα, όχι μια λίστα που θυμάται κάποιος."""
    import routes_backup
    root = pathlib.Path(routes_backup.__file__).resolve().parent
    found: set[str] = set()
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'f"([a-z]+)-\{firm_id\}-\{(?:year|y)\}"', text))
        found |= set(re.findall(r'f"([a-z]+)-\{prefix\}-\{firm_id\}-\{(?:year|y)\}"', text))
    assert found == {"invoice", "case", "grammatio", "paravolo"}, found
    assert set(routes_backup.COUNTER_FAMILIES) == found


def test_export_never_includes_another_firms_counters(client):
    user = _admin(client)
    seqs = _seed_counters(user["firm_id"])
    foreign = _seed_counters(FOREIGN_FIRM)
    exported = {r["_id"] for r in _rows(_open(_export(client)), "counters")}
    assert exported == set(seqs)
    assert not exported & set(foreign)


@pytest.mark.parametrize("own_firm, lookalike_firm", [("f.1", "fx1"), ("a+b", "aab"), ("x*y", "xyy")])
def test_counter_ownership_is_an_exact_match_not_a_pattern(client, own_firm, lookalike_firm):
    """Ένα αναγνωριστικό γραφείου με `.`/`+`/`*` δεν πρέπει να «ταιριάζει» με μετρητές άλλων γραφείων."""
    from db import db
    user = _admin(client)
    _run(db.users.update_one({"_id": user["_id"]}, {"$set": {"firm_id": own_firm}}))
    own, other = f"invoice-{own_firm}-2026", f"invoice-{lookalike_firm}-2026"
    _run(db.counters.insert_many([{"_id": own, "seq": 4}, {"_id": other, "seq": 9}]))

    backup = _export(client)
    assert {r["_id"] for r in _rows(_open(backup), "counters")} == {own}, "εξαγωγή: μόνο του δικού μας γραφείου"

    _run(db.counters.delete_many({}))
    body = _import(client, _archive({"counters": [{"_id": own, "seq": 4}, {"_id": other, "seq": 9}]})).json()
    assert body["restored"]["counters"] == 1 and body["failed"] == 1
    assert {c["_id"] for c in _run(db.counters.find({}).to_list(10))} == {own}


def test_a_counter_id_that_only_contains_the_firm_is_not_this_firms_counter(client):
    """Αγκυρωμένος έλεγχος: ούτε πρόθεμα ούτε κατάληξη γύρω από ένα έγκυρο αναγνωριστικό."""
    from db import db
    user = _admin(client)
    fid = user["firm_id"]
    bad = [f"x-invoice-{fid}-2026", f"invoice-{fid}-2026-extra", f"invoice-{fid}-26", f"other-{fid}-2026",
           f"invoice-a-b-{fid}-2026"]
    _run(db.counters.insert_many([{"_id": i, "seq": 1} for i in bad]))
    assert _rows(_open(_export(client)), "counters") == []
    body = _import(client, _archive({"counters": [{"_id": i, "seq": 1} for i in bad]})).json()
    assert body["restored"]["counters"] == 0 and body["failed"] == len(bad)


@pytest.mark.parametrize("seq", ["12", 1000.0, 10 ** 20, True, -1, None, [3], {"$gt": 1}, 2_000_000_000])
def test_a_counter_with_an_unusable_seq_is_refused_and_never_stored(client, seq):
    """`"12"` αποθηκευόταν ως κείμενο και κάθε έκδοση τιμολογίου μετά έπεφτε με 500· το `1e3` ως δεκαδικό
    «έκαιγε» έναν αριθμό σε κάθε προσπάθεια· ένας αριθμός πέρα από 8 byte έριχνε όλη την επαναφορά."""
    from db import db
    user = _admin(client)
    counter_id = f"invoice-{user['firm_id']}-2026"
    r = _import(client, _archive({"counters": [{"_id": counter_id, "seq": seq, "last_date": "2026-01-01"}],
                                  "clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ ΑΕ"}]}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == 1 and body["restored"]["counters"] == 0
    assert any(counter_id in e for e in body["errors"]), body["errors"]
    assert _run(db.counters.find_one({"_id": counter_id})) is None
    assert body["restored"]["clients"] == 1, "τα υπόλοιπα έγγραφα επανήλθαν κανονικά"


def test_a_counter_is_stored_with_only_the_known_fields_and_valid_values(client):
    from db import db
    user = _admin(client)
    fid = user["firm_id"]
    good, bad_date, junk = f"invoice-{fid}-2026", f"case-{fid}-2026", f"grammatio-{fid}-2026"
    body = _import(client, _archive({"counters": [
        {"_id": good, "seq": 7, "last_date": "2026-03-01", "last_alloc_at": "2026-03-01T10:00:00+00:00"},
        {"_id": bad_date, "seq": 3, "last_date": "2026-13-45", "last_alloc_at": 123},
        {"_id": junk, "seq": 2, "last_date": ["2026-01-01"], "extra": {"$set": 1}, "firm_id": "firm-other"},
    ]})).json()
    assert body["failed"] == 0 and body["restored"]["counters"] == 3
    assert _run(db.counters.find_one({"_id": good})) == {
        "_id": good, "seq": 7, "last_date": "2026-03-01", "last_alloc_at": "2026-03-01T10:00:00+00:00"}
    assert _run(db.counters.find_one({"_id": bad_date})) == {"_id": bad_date, "seq": 3}, "άκυρη ημερομηνία: απορρίπτεται"
    assert _run(db.counters.find_one({"_id": junk})) == {"_id": junk, "seq": 2}


def test_a_restored_counter_continues_the_numbering_from_where_it_was(client):
    from db import db
    from numbering import next_in_series
    user = _admin(client)
    fid = user["firm_id"]
    counter_id = f"invoice-{fid}-2026"
    assert _import(client, _archive({"counters": [{"_id": counter_id, "seq": 7, "last_date": "2026-03-01"}]})).status_code == 200
    number = _run(next_in_series(db.invoices, counter_id, fid, "ΤΠΥ", 2026, date_iso="2026-03-02"))
    assert number == "ΤΠΥ-2026-0008"


def test_a_counter_already_at_the_same_point_is_left_untouched(client):
    """Ίσο `seq` δεν «προχωρά»: ούτε η ημερομηνία του ζωντανού μετρητή αλλάζει (η `last_date` προστατεύει τη
    χρονολογική σειρά της σειράς)."""
    from db import db
    user = _admin(client)
    counter_id = f"invoice-{user['firm_id']}-2026"
    _run(db.counters.insert_one({"_id": counter_id, "seq": 5, "last_date": "2026-06-01", "last_alloc_at": "live"}))
    body = _import(client, _archive({"counters": [{"_id": counter_id, "seq": 5, "last_date": "2026-01-01"}]})).json()
    assert _run(db.counters.find_one({"_id": counter_id})) == {
        "_id": counter_id, "seq": 5, "last_date": "2026-06-01", "last_alloc_at": "live"}
    assert body["restored"]["counters"] == 0 and body["already_present"]["counters"] == 1
    assert any("μετρητές αρίθμησης" in n and "δεν γύρισαν πίσω" in n for n in body["notes"]), body["notes"]


# ============================================================================ 2. Ρυθμίσεις: ένα έγγραφο ====

def _put_settings(client, **fields) -> dict:
    r = client.put("/api/settings", json=fields)
    assert r.status_code == 200, r.text
    return r.json()


def test_restore_after_total_loss_brings_the_office_settings_back_even_after_the_settings_page_ran(client):
    """Ο ελεγκτής: μετά από απώλεια, το άνοιγμα της σελίδας Ρυθμίσεων (όπου βρίσκεται το κουμπί επαναφοράς)
    δημιουργεί κενό έγγραφο· η επαναφορά έγραφε δίπλα του και η εφαρμογή διάβαζε το κενό — με μήνυμα επιτυχίας."""
    from db import db
    user = _admin(client)
    fid = user["firm_id"]
    _put_settings(client, company_name="ΠΑΛΙΟ ΓΡΑΦΕΙΟ ΕΠΕ", afm="123456783", smtp_host="smtp.example.gr")
    backup = _export(client)

    _run(db.settings.delete_many({}))                                              # απώλεια δεδομένων
    assert client.get("/api/settings").json()["company_name"] == "Δικηγορική Εταιρεία"   # προεπιλεγμένο έγγραφο

    body = _import(client, backup).json()
    assert body["restored"]["settings"] == 1 and body["failed"] == 0, body
    assert _count("settings", {"firm_id": fid}) == 1
    restored = client.get("/api/settings").json()
    assert (restored["company_name"], restored["afm"], restored["smtp_host"]) == (
        "ΠΑΛΙΟ ΓΡΑΦΕΙΟ ΕΠΕ", "123456783", "smtp.example.gr")

    # Οι επόμενες αποθηκεύσεις αφορούν το ίδιο έγγραφο.
    assert client.patch("/api/settings", json={"company_name": "ΝΕΟ ΟΝΟΜΑ"}).status_code == 200
    assert client.get("/api/settings").json()["company_name"] == "ΝΕΟ ΟΝΟΜΑ"
    _put_settings(client, company_name="ΤΡΙΤΟ ΟΝΟΜΑ", afm="123456783")
    assert client.get("/api/settings").json()["company_name"] == "ΤΡΙΤΟ ΟΝΟΜΑ"
    assert _count("settings", {"firm_id": fid}) == 1

    assert _import(client, backup).json()["restored"]["settings"] == 1                 # και δεύτερη επαναφορά
    assert _count("settings", {"firm_id": fid}) == 1
    assert client.get("/api/settings").json()["company_name"] == "ΠΑΛΙΟ ΓΡΑΦΕΙΟ ΕΠΕ"


def test_restore_without_a_live_settings_document_keeps_the_original_id(client):
    from db import db
    user = _admin(client)
    _put_settings(client, company_name="ΠΑΛΙΟ ΓΡΑΦΕΙΟ ΕΠΕ")
    original = _run(db.settings.find_one({"firm_id": user["firm_id"]}))["_id"]
    backup = _export(client)
    _run(db.settings.delete_many({}))
    assert _import(client, backup).json()["restored"]["settings"] == 1
    assert _run(db.settings.find_one({"firm_id": user["firm_id"]}))["_id"] == original


def test_restore_collapses_duplicate_settings_documents_into_one(client):
    """Εγκαταστάσεις που ήδη έχουν δύο έγγραφα (ένα προεπιλεγμένο και ένα από παλιά επαναφορά)."""
    from db import db
    user = _admin(client)
    fid = user["firm_id"]
    _put_settings(client, company_name="ΕΠΩΝΥΜΙΑ ΑΝΤΙΓΡΑΦΟΥ")
    backup = _export(client)
    _run(db.settings.drop_indexes())                                  # όπως ήταν πριν από το μοναδικό ευρετήριο
    _run(db.settings.insert_many([{"firm_id": fid, "company_name": "ΔΙΠΛΟ 1"}, {"firm_id": fid, "company_name": "ΔΙΠΛΟ 2"}]))
    assert _count("settings", {"firm_id": fid}) == 3

    body = _import(client, backup).json()
    assert _count("settings", {"firm_id": fid}) == 1
    assert client.get("/api/settings").json()["company_name"] == "ΕΠΩΝΥΜΙΑ ΑΝΤΙΓΡΑΦΟΥ"
    assert any("Αφαιρέθηκαν" in n and "διπλά" in n for n in body["notes"]), body["notes"]


def test_restore_never_touches_another_firms_settings(client):
    import server
    from db import db
    user_a = _admin(client, "a@example.gr")
    with TestClient(server.app, base_url="https://testserver") as other:
        user_b = _admin(other, "b@example.gr")
        other.put("/api/settings", json={"company_name": "ΓΡΑΦΕΙΟ Β"})
    _put_settings(client, company_name="ΓΡΑΦΕΙΟ Α")
    backup = _export(client)
    _run(db.settings.delete_many({"firm_id": user_a["firm_id"]}))
    forged_id = _run(db.settings.find_one({"firm_id": user_b["firm_id"]}))["_id"]
    r = _import(client, _archive({"settings": [{"_id": _oid(forged_id), "firm_id": user_b["firm_id"],
                                                "company_name": "ΚΑΤΑΛΗΦΘΗΚΕ"}]}))
    assert r.json()["restored"]["settings"] == 1
    assert _run(db.settings.find_one({"firm_id": user_b["firm_id"]}))["company_name"] == "ΓΡΑΦΕΙΟ Β"
    mine = _run(db.settings.find_one({"firm_id": user_a["firm_id"]}))
    assert mine["company_name"] == "ΚΑΤΑΛΗΦΘΗΚΕ" and mine["_id"] != forged_id, "το `_id` του άλλου γραφείου δεν χρησιμοποιείται"
    assert backup


def test_a_backup_with_several_settings_documents_restores_the_first_and_says_so(client):
    user = _admin(client)
    body = _import(client, _archive({"settings": [
        {"_id": _oid(ObjectId()), "company_name": "ΠΡΩΤΟ"}, {"_id": _oid(ObjectId()), "company_name": "ΔΕΥΤΕΡΟ"}]})).json()
    assert body["restored"]["settings"] == 1
    assert _count("settings", {"firm_id": user["firm_id"]}) == 1
    assert client.get("/api/settings").json()["company_name"] == "ΠΡΩΤΟ"
    assert any("2 έγγραφα ρυθμίσεων" in n for n in body["notes"]), body["notes"]


def test_restored_secrets_that_this_installation_cannot_read_are_reported(client):
    """Το αντίγραφο φέρνει τους κωδικούς κρυπτογραφημένους με το κλειδί της παλιάς εγκατάστασης."""
    from crypto import encrypt
    _admin(client)
    readable = encrypt("o-kodikos-mou")
    unreadable = "enc:v2:" + "gAAAAAB" + "x" * 80
    ok = _import(client, _archive({"settings": [{"_id": _oid(ObjectId()), "smtp_password": readable}]})).json()
    assert not any("ΠΡΟΣΟΧΗ" in n and "SECRETS_KEY" in n for n in ok["notes"]), ok["notes"]

    bad = _import(client, _archive({"settings": [
        {"_id": _oid(ObjectId()), "smtp_password": unreadable, "imap_password": readable,
         "aade_subscription_key": unreadable}]})).json()
    warning = next(n for n in bad["notes"] if "ΠΡΟΣΟΧΗ" in n and "SECRETS_KEY" in n)
    assert "smtp_password" in warning and "aade_subscription_key" in warning and "imap_password" not in warning


def test_the_settings_index_is_unique_per_firm_and_replaces_the_plain_one(client):
    import server
    from db import db
    info = _run(db.settings.index_information())                      # το έχει ήδη δημιουργήσει η εκκίνηση
    assert [n for n, i in info.items() if i["key"] == [("firm_id", 1)]] == [server.SETTINGS_INDEX]

    _run(db.settings.drop_indexes())
    _run(db.settings.create_index("firm_id"))                          # όπως ήταν σε υπάρχουσες εγκαταστάσεις
    _run(server.ensure_settings_index())
    keyed = {n: i for n, i in _run(db.settings.index_information()).items() if i["key"] == [("firm_id", 1)]}
    assert list(keyed) == [server.SETTINGS_INDEX] and keyed[server.SETTINGS_INDEX]["unique"] is True
    _run(db.settings.insert_one({"firm_id": "f1"}))
    with pytest.raises(DuplicateKeyError):
        _run(db.settings.insert_one({"firm_id": "f1"}))


def test_existing_duplicate_settings_do_not_stop_the_startup_and_are_logged(client, caplog):
    import logging
    import server
    from db import db
    _run(db.settings.drop_indexes())
    _run(db.settings.insert_many([{"firm_id": "f1"}, {"firm_id": "f1"}]))
    with caplog.at_level(logging.ERROR):
        _run(server.ensure_settings_index())                           # δεν σηκώνει εξαίρεση
    assert any("settings" in r.getMessage() and "unique" in r.getMessage() for r in caplog.records)
    keyed = {n: i for n, i in _run(db.settings.index_information()).items() if i["key"] == [("firm_id", 1)]}
    assert keyed and not any(i.get("unique") for i in keyed.values()), "το απλό ευρετήριο ξαναδημιουργείται"


class _FailingCollection:
    """Συλλογή που αποτυγχάνει στη μέθοδο `method` όταν `when(*args)` είναι αληθές· όλα τα άλλα όπως κανονικά."""

    def __init__(self, real, method, error, when):
        self._real, self._method, self._error, self._when = real, method, error, when

    def __getattr__(self, item):
        attribute = getattr(self._real, item)
        if item != self._method:
            return attribute

        async def wrapper(*args, **kwargs):
            if self._when(*args, **kwargs):
                raise self._error
            return await attribute(*args, **kwargs)
        return wrapper


class _FailingDb:
    def __init__(self, real, collection, method, error, when=lambda *a, **k: True):
        self._real, self._collection, self._method, self._error, self._when = real, collection, method, error, when

    def __getitem__(self, name):
        coll = self._real[name]
        return _FailingCollection(coll, self._method, self._error, self._when) if name == self._collection else coll

    def __getattr__(self, name):
        return self[name]


def test_two_first_requests_at_once_do_not_create_two_settings_documents(client, monkeypatch):
    """Το δεύτερο αίτημα δεν βρίσκει έγγραφο, αλλά μέχρι να γράψει το έχει δημιουργήσει το πρώτο: το μοναδικό
    ευρετήριο το αποκλείει και το αίτημα παίρνει το υπάρχον έγγραφο αντί για σφάλμα 500."""
    import routes_settings
    from db import db
    user = _admin(client)
    assert client.get("/api/settings").status_code == 200                 # δημιουργεί το έγγραφο
    calls = {"n": 0}
    real = db.settings

    class Blind:
        async def find_one(self, *a, **k):
            calls["n"] += 1
            return None if calls["n"] == 1 else await real.find_one(*a, **k)

        def __getattr__(self, item):
            return getattr(real, item)

    class BlindDb:
        settings = Blind()

    monkeypatch.setattr(routes_settings, "db", BlindDb())
    doc = _run(routes_settings._settings_doc({"_id": "u", "firm_id": user["firm_id"]}))
    assert doc is not None and doc["firm_id"] == user["firm_id"]
    assert _count("settings", {"firm_id": user["firm_id"]}) == 1


# ============================================================================ 3. Έγγραφο που δεν γράφεται ====

BAD_DOCUMENTS = {
    "int_over_8_bytes": {"amount": 10 ** 20},
    "nul_in_key": {"a\x00b": 1},
    "dollar_field": {"$set": {"a": 1}},
    "list_id": {"_id": [1, 2]},
}


@pytest.mark.parametrize("kind", sorted(BAD_DOCUMENTS))
def test_one_unwritable_document_is_reported_and_the_restore_continues(client, kind):
    """Ένα τέτοιο έγγραφο τίναζε ολόκληρη την επαναφορά με HTTP 500 — με τις επόμενες συλλογές χωρίς επαναφορά."""
    from db import db
    _admin(client)
    ok1, ok2, bad_id = ObjectId(), ObjectId(), ObjectId()
    bad = {"_id": _oid(bad_id), "name": "ΚΑΚΟ", **BAD_DOCUMENTS[kind]}
    if "_id" in BAD_DOCUMENTS[kind]:
        bad["_id"] = BAD_DOCUMENTS[kind]["_id"]
    r = _import(client, _archive({
        "clients": [{"_id": _oid(ok1), "name": "ΚΑΛΟ 1"}, bad, {"_id": _oid(ok2), "name": "ΚΑΛΟ 2"}],
        "invoices": [{"_id": _oid(ObjectId()), "number": "ΤΠΥ-2026-0001", "status": "issued"}],
    }))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == 1 and body["restored"]["clients"] == 2, body
    assert body["restored"]["invoices"] == 1, "οι επόμενες συλλογές επανήλθαν"
    assert any("clients" in e and "δεν αποθηκεύεται" in e for e in body["errors"]), body["errors"]
    assert _run(db.clients.find_one({"_id": ok1})) and _run(db.clients.find_one({"_id": ok2}))
    assert _count("clients") == 2


def test_documents_with_nan_or_infinity_are_not_restored_and_the_rest_is(client):
    """Η `json.loads` δέχεται `NaN` και `1e999`· ένα τέτοιο σύνολο τιμολογίου χαλά κάθε αναφορά του γραφείου."""
    from db import db
    _admin(client)
    ids = [ObjectId() for _ in range(4)]
    text = ("[" + ", ".join([
        f'{{"_id": {{"__oid__": "{ids[0]}"}}, "number": "ΤΠΥ-2026-0001", "total": 124.0}}',
        f'{{"_id": {{"__oid__": "{ids[1]}"}}, "number": "ΤΠΥ-2026-0002", "total": NaN}}',
        f'{{"_id": {{"__oid__": "{ids[2]}"}}, "number": "ΤΠΥ-2026-0003", "lines": [{{"price": 1e999}}]}}',
        f'{{"_id": {{"__oid__": "{ids[3]}"}}, "number": "ΤΠΥ-2026-0004", "total": -Infinity}}']) + "]")
    body = _import(client, _archive(raw={"invoices.json": text})).json()
    assert body["restored"]["invoices"] == 1 and body["failed"] == 3, body
    assert sum("ΤΠΥ-2026-000" in e and "NaN ή άπειρο" in e for e in body["errors"]) == 3, body["errors"]
    assert [d["number"] for d in _run(db.invoices.find({}).to_list(10))] == ["ΤΠΥ-2026-0001"]


def test_a_write_error_on_one_document_is_reported_per_document(client, monkeypatch):
    import routes_backup
    from db import db
    _admin(client)
    ok, bad = ObjectId(), ObjectId()
    monkeypatch.setattr(routes_backup, "db", _FailingDb(
        db, "clients", "replace_one", WriteError("δοκιμαστικό σφάλμα εγγραφής"),
        when=lambda flt, *a, **k: flt.get("_id") == bad))
    body = _import(client, _archive({"clients": [{"_id": _oid(ok), "name": "ΚΑΛΟ"}, {"_id": _oid(bad), "name": "ΚΑΚΟ"}]})).json()
    assert body["restored"]["clients"] == 1 and body["failed"] == 1
    assert any("δοκιμαστικό σφάλμα εγγραφής" in e for e in body["errors"])


def test_a_lost_database_connection_stops_the_restore_instead_of_failing_every_document(client, monkeypatch):
    """Μόνο τα σφάλματα ενός εγγράφου αναφέρονται. Αν η βάση πέσει, συνεχίζοντας θα περίμενε χρονικό όριο για κάθε
    έγγραφο — η επαναφορά πρέπει να σταματήσει (500) και να ξαναδοκιμαστεί."""
    import routes_backup
    from db import db
    _admin(client)
    monkeypatch.setattr(routes_backup, "db", _FailingDb(db, "clients", "replace_one", AutoReconnect("η βάση δεν απαντά")))
    with pytest.raises(AutoReconnect):
        _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]}))


def test_one_unwritable_audit_row_loses_only_itself_not_its_whole_chunk(client):
    """Οι γραμμές του ίχνους γράφονται σε παρτίδες των 500· ένα έγγραφο που δεν κωδικοποιείται θα έριχνε όλη την
    παρτίδα. Ελέγχεται ένα ένα πριν γραφτεί."""
    from db import db
    _admin(client)
    rows = [{"_id": _oid(ObjectId()), "action": "post:clients", "n": i} for i in range(3)]
    rows.insert(1, {"_id": _oid(ObjectId()), "action": "post:clients", "n": 99, "amount": 10 ** 20})
    body = _import(client, _archive({"audit_logs": rows})).json()
    assert body["restored"]["audit_logs"] == 3 and body["failed"] == 1, body
    assert sorted(d["n"] for d in _run(db.audit_logs.find({"n": {"$exists": True}}).to_list(10))) == [0, 1, 2]


def test_a_batch_error_in_the_audit_trail_is_reported_with_the_number_of_rows_lost(client, monkeypatch):
    import routes_backup
    from db import db
    _admin(client)
    rows = [{"_id": _oid(ObjectId()), "action": "post:clients", "entity": "clients"} for _ in range(3)]
    monkeypatch.setattr(routes_backup, "db", _FailingDb(db, "audit_logs", "insert_many", OverflowError("πολύ μεγάλος αριθμός")))
    body = _import(client, _archive({"audit_logs": rows})).json()
    assert body["failed"] == 3 and body["restored"]["audit_logs"] == 0
    assert any("3 γραμμές" in e and "πολύ μεγάλος αριθμός" in e for e in body["errors"]), body["errors"]


def test_a_write_error_on_a_counter_or_on_settings_is_reported(client, monkeypatch):
    import routes_backup
    from db import db
    user = _admin(client)
    counter_id = f"invoice-{user['firm_id']}-2026"
    archive = _archive({"counters": [{"_id": counter_id, "seq": 3}], "settings": [{"company_name": "ΧΧΧ"}]})

    monkeypatch.setattr(routes_backup, "db", _FailingDb(db, "counters", "replace_one", WriteError("αποτυχία μετρητή")))
    body = _import(client, archive).json()
    assert body["restored"]["counters"] == 0 and any("αποτυχία μετρητή" in e for e in body["errors"])
    assert body["restored"]["settings"] == 1

    _run(db.settings.delete_many({}))                      # χωρίς ζωντανό έγγραφο: η επαναφορά το δημιουργεί με insert
    monkeypatch.setattr(routes_backup, "db", _FailingDb(db, "settings", "insert_one", ValueError("αποτυχία ρυθμίσεων")))
    body = _import(client, archive).json()
    assert body["restored"]["settings"] == 0 and any("αποτυχία ρυθμίσεων" in e for e in body["errors"])


def test_failures_are_all_counted_but_only_the_first_few_are_listed(client):
    import routes_backup
    _admin(client)
    foreign = [{"_id": f"invoice-{FOREIGN_FIRM}-{2000 + i}", "seq": 1} for i in range(120)]
    body = _import(client, _archive({"counters": foreign})).json()
    assert body["failed"] == 120
    assert len(body["errors"]) == routes_backup.MAX_ERRORS_SHOWN == 15
    assert any("120 έγγραφα δεν επαναφέρθηκαν" in n for n in body["notes"]), body["notes"]


def test_a_garbage_collection_file_is_reported_and_the_other_files_are_restored(client):
    _admin(client)
    body = _import(client, _archive(
        {"expenses": []}, raw={"clients.json": "αυτό δεν είναι JSON", "invoices.json": '[{"_id": {"__oid__": "όχι-oid"}, "x": 1}, 5]'})).json()
    assert body["restored"]["expenses"] == 0
    assert any("clients.json" in e for e in body["errors"])
    assert sum("εγγραφή που δεν είναι έγγραφο" in e for e in body["errors"]) == 2, body["errors"]


# ============================================================================ 4. Όρια μεγέθους ====

def _big_clients(n: int = 40) -> list[dict]:
    return [{"_id": _oid(ObjectId()), "name": "ΠΕΛΑΤΗΣ " * 5, "notes": "x" * 100} for _ in range(n)]


def test_an_upload_over_the_size_limit_is_refused_before_anything_is_read(client, monkeypatch):
    import routes_backup
    _admin(client)
    archive = _archive({"clients": _big_clients()})
    monkeypatch.setattr(routes_backup, "MAX_UPLOAD_BYTES", len(archive) - 1)
    r = _import(client, archive)
    assert r.status_code == 413 and "υπερβολικά μεγάλο" in r.json()["detail"]
    assert _count("clients") == 0
    monkeypatch.setattr(routes_backup, "MAX_UPLOAD_BYTES", len(archive))
    assert _import(client, archive).status_code == 200                       # στο όριο ακριβώς: περνά


def test_a_member_over_the_limit_is_refused_and_nothing_is_restored(client, monkeypatch):
    import routes_backup
    _admin(client)
    monkeypatch.setattr(routes_backup, "MAX_MEMBER_BYTES", 300)
    r = _import(client, _archive({"clients": _big_clients(), "expenses": [{"_id": _oid(ObjectId()), "description": "α"}]}))
    assert r.status_code == 413
    assert _count("clients") == 0 and _count("expenses") == 0, "η επαναφορά δεν ξεκίνησε"


def test_the_total_size_over_the_limit_is_refused(client, monkeypatch):
    import routes_backup
    _admin(client)
    archive = _archive({"clients": _big_clients(3), "expenses": [{"_id": _oid(ObjectId()), "description": "β" * 300}]})
    sizes = sorted(i.file_size for i in zipfile.ZipFile(io.BytesIO(archive)).infolist())
    monkeypatch.setattr(routes_backup, "MAX_TOTAL_BYTES", sum(sizes) - 1)
    assert _import(client, archive).status_code == 413
    monkeypatch.setattr(routes_backup, "MAX_TOTAL_BYTES", sum(sizes))
    assert _import(client, archive).status_code == 200


def test_a_small_zip_that_declares_a_huge_file_is_refused(client, monkeypatch):
    """Η αναπαραγωγή του ελεγκτή: 299 KB που δηλώνουν 315 MB (λόγος 1000:1) έτρωγαν 600 MB μνήμης."""
    import routes_backup
    _admin(client)
    bomb = _archive(raw={"clients.json": "[" + " " * 3_000_000 + "]"}, compress=True)
    assert len(bomb) < 20_000, len(bomb)
    monkeypatch.setattr(routes_backup, "MAX_MEMBER_BYTES", 1_000_000)
    r = _import(client, bomb)
    assert r.status_code == 413 and _count("clients") == 0
    monkeypatch.setattr(routes_backup, "MAX_MEMBER_BYTES", 5_000_000)
    assert _import(client, bomb).status_code == 200


def test_too_many_files_in_the_zip_are_refused(client, monkeypatch):
    import routes_backup
    _admin(client)
    archive = _archive({"clients": [], "invoices": [], "expenses": [], "cases": []})        # 5 αρχεία με το _meta
    monkeypatch.setattr(routes_backup, "MAX_MEMBERS", 4)
    assert _import(client, archive).status_code == 413
    monkeypatch.setattr(routes_backup, "MAX_MEMBERS", 5)
    assert _import(client, archive).status_code == 200


def test_the_zip_directory_is_checked_before_it_is_loaded(client, monkeypatch):
    """Η `zipfile` φορτώνει στη μνήμη μία εγγραφή για κάθε αρχείο που δηλώνει ο κατάλογος: ένα ZIP με εκατομμύρια
    άδεια αρχεία είναι «βόμβα» ακόμη και με λίγα MB — γι' αυτό ελέγχεται το μέγεθος του καταλόγου πρώτα."""
    import routes_backup
    _admin(client)
    archive = _archive({"clients": []})
    monkeypatch.setattr(routes_backup, "MAX_DIRECTORY_BYTES", 40)
    assert _import(client, archive).status_code == 413
    monkeypatch.setattr(routes_backup, "MAX_DIRECTORY_BYTES", 64 * 1024)
    assert _import(client, archive).status_code == 200


def test_the_number_of_files_is_checked_on_the_real_directory_not_only_on_what_the_end_record_claims(client, monkeypatch):
    """Η `zipfile` διαβάζει τον κατάλογο με βάση το μέγεθός του, όχι τον αριθμό που δηλώνει η εγγραφή τέλους."""
    import routes_backup
    _admin(client)
    content = bytearray(_archive({"clients": [], "invoices": [], "expenses": [], "cases": []}))      # 5 αρχεία
    eocd = len(content) - 22
    content[eocd + 8:eocd + 12] = (1).to_bytes(2, "little") * 2                                         # «έχει 1 αρχείο»
    monkeypatch.setattr(routes_backup, "MAX_MEMBERS", 4)
    assert _import(client, bytes(content)).status_code == 413


@pytest.mark.parametrize("patch", ["entries", "directory_size", "directory_offset"])
def test_zip64_markers_in_the_directory_are_refused(client, patch):
    """Η εξαγωγή δεν παράγει ποτέ ZIP64 κατάλογο (θα απαιτούσε πάνω από 65.535 αρχεία ή 4 GB)."""
    _admin(client)
    content = bytearray(_archive({"clients": []}))
    eocd = len(content) - 22
    assert bytes(content[eocd:eocd + 4]) == b"PK\x05\x06"
    spans = {"entries": (eocd + 8, 4), "directory_size": (eocd + 12, 4), "directory_offset": (eocd + 16, 4)}
    start, length = spans[patch]
    content[start:start + length] = b"\xff" * length
    assert _import(client, bytes(content)).status_code == 413


@pytest.mark.parametrize("payload,message", [
    (b"", "Το αρχείο backup είναι κενό."),
    ("αυτό δεν είναι ZIP".encode("utf-8"), "Μη έγκυρο αρχείο ZIP."),
    (b"PK\x03\x04" + b"\x00" * 100, "Μη έγκυρο αρχείο ZIP."),
])
def test_garbage_uploads_get_a_clear_400_not_a_500(client, payload, message):
    _admin(client)
    r = _import(client, payload)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == message, "ο χρήστης μαθαίνει τι ακριβώς πήγε στραβά: κενό αρχείο ή όχι ZIP"


def test_a_truncated_backup_is_refused(client):
    _admin(client)
    backup = _export(client)
    r = _import(client, backup[: len(backup) // 2])
    assert r.status_code == 400 and r.json()["detail"] == "Μη έγκυρο αρχείο ZIP."


def test_a_zip_without_meta_is_not_a_backup(client):
    _admin(client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("clients.json", "[]")
    r = _import(client, buf.getvalue())
    assert r.status_code == 400 and "_meta.json" in r.json()["detail"]


@pytest.mark.parametrize("meta", [{"version": 2}, {"version": "1"}, {"version": True}, {"version": 0}, {"version": 1.5},
                                  [], "όχι JSON", '"κείμενο"'])
def test_a_backup_with_an_unknown_or_unreadable_format_is_refused(client, meta):
    """Μια μορφή που δεν γνωρίζουμε δεν διαβάζεται «περίπου»."""
    _admin(client)
    r = _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]}, meta=meta))
    assert r.status_code == 400, r.text
    assert "έκδοση" in r.json()["detail"] or "_meta.json" in r.json()["detail"]
    assert _count("clients") == 0


@pytest.mark.parametrize("meta", [{}, {"version": 1}, {"app": "nomos-one"}, {"version": 1, "counts": "όχι λεξικό"}])
def test_older_and_minimal_meta_files_are_still_accepted(client, meta):
    _admin(client)
    assert _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]}, meta=meta)).status_code == 200


def test_the_declared_counts_are_checked_against_the_files(client):
    _admin(client)
    body = _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]},
                                    meta={"version": 1, "counts": {"clients": 5, "expenses": True}})).json()
    assert any("clients.json" in n and "δηλώνει 5" in n and "έχει 1" in n for n in body["notes"]), body["notes"]
    assert not any("expenses" in n for n in body["notes"])


# ============================================================================ 5. Εξαγωγή ====

def test_export_is_streamed_with_the_right_headers(client):
    _admin(client)
    r = client.get("/api/backup/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-length"] == str(len(r.content))
    assert re.search(r'attachment; filename="?nomos-one-backup-\d{8}-\d{6}\.zip', r.headers["content-disposition"])
    assert r.content[:2] == b"PK"


def test_export_uses_a_temporary_file_for_large_backups_and_closes_it(client, monkeypatch):
    """Το αρχείο δεν υπάρχει ποτέ ολόκληρο στη μνήμη: πάνω από ένα όριο πηγαίνει σε προσωρινό αρχείο, και κλείνει
    μόλις σταλεί ολόκληρο."""
    import routes_backup
    created = []

    class Tracking(tempfile.SpooledTemporaryFile):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(routes_backup.tempfile, "SpooledTemporaryFile", Tracking)
    monkeypatch.setattr(routes_backup, "EXPORT_SPOOL_BYTES", 256)
    _admin(client)
    for i in range(30):
        client.post("/api/clients", json={"name": f"ΠΕΛΑΤΗΣ {i}", "afm": "123456783"})
    backup = _export(client)
    assert len(created) == 1 and created[0]._rolled is True, "ξεπέρασε το όριο μνήμης: πήγε σε αρχείο"
    assert created[0].closed
    assert len(_rows(_open(backup), "clients")) == 30


def test_export_closes_its_temporary_file_when_it_fails_midway(client, monkeypatch):
    import routes_backup
    created = []

    class Tracking(tempfile.SpooledTemporaryFile):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    async def broken(zf, name, scope):
        raise RuntimeError("η βάση διέκοψε")

    monkeypatch.setattr(routes_backup.tempfile, "SpooledTemporaryFile", Tracking)
    monkeypatch.setattr(routes_backup, "_write_collection", broken)
    _admin(client)
    with pytest.raises(RuntimeError):
        client.get("/api/backup/export")
    assert len(created) == 1 and created[0].closed


def test_export_says_what_a_database_backup_cannot_contain(client):
    """Αρχεία (αποδείξεις), κλειδί κρυπτογράφησης μυστικών, σύνδεσμοι πύλης: ό,τι δεν είναι στο αντίγραφο γράφεται
    μέσα του, ώστε όποιος το κρατά να μην υποθέσει ότι είναι πλήρες."""
    _admin(client)
    meta = _meta(_open(_export(client)))
    text = " ".join(meta["caveats"])
    assert "UPLOAD_DIR" in text and "SECRETS_KEY" in text and "μόνο προς τα εμπρός" in text
    assert "client_portal_tokens" in meta["not_included"] and meta["version"] == 1


def test_every_restore_reminds_what_is_not_in_the_backup(client):
    _admin(client)
    notes = _import(client, _archive({"clients": []})).json()["notes"]
    assert notes[-1].startswith("Το αντίγραφο δεν περιλαμβάνει") and "SECRETS_KEY" in notes[-1]


# ============================================================================ 6. Σύνδεσμοι πύλης ====

def test_portal_links_are_not_in_the_backup_so_a_restore_cannot_revive_a_revoked_one(client):
    """Ο ελεγκτής: ένας ανακληθείς σύνδεσμος (ισχύει σαν κωδικός) ξαναζούσε από κάθε παλαιότερο αντίγραφο — και το
    αντίγραφο κρατούσε ζωντανά tokens σε καθαρό κείμενο."""
    from db import db
    user = _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "email": "alpha@example.gr"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]
    assert client.get(f"/api/portal/{token}").status_code == 200

    backup = _export(client)
    z = _open(backup)
    assert "client_portal_tokens.json" not in z.namelist() and token not in " ".join(
        z.read(n).decode("utf-8") for n in z.namelist())
    assert "client_portal_tokens" in _meta(z)["not_included"]

    token_id = client.get(f"/api/client-portal/tokens/{cid}").json()[0]["id"]
    assert client.delete(f"/api/client-portal/tokens/{token_id}").status_code == 200
    assert client.get(f"/api/portal/{token}").status_code in (401, 404)
    body = _import(client, backup).json()
    assert "client_portal_tokens" not in body["restored"]
    assert client.get(f"/api/portal/{token}").status_code in (401, 404), "ο ανακληθείς σύνδεσμος δεν ξαναζεί"

    # Και παλιό αντίγραφο που ΑΚΟΜΑ περιέχει το αρχείο των συνδέσμων: αγνοείται, με σχετική σημείωση.
    old = _archive({"client_portal_tokens": [{"_id": _oid(ObjectId()), "token": token, "client_id": cid,
                                              "expires_at": "2999-01-01T00:00:00+00:00", "firm_id": user["firm_id"]}]})
    body = _import(client, old).json()
    assert _count("client_portal_tokens") == 0
    assert client.get(f"/api/portal/{token}").status_code in (401, 404)
    assert any("client_portal_tokens.json" in n and "Αγνοήθηκαν" in n for n in body["notes"]), body["notes"]
    assert db is not None


def test_unknown_files_in_a_backup_are_listed_as_ignored(client):
    _admin(client)
    body = _import(client, _archive({"clients": []}, raw={"audit_log.json": "[]", "notes.txt": "x", "users.json": "[]"})).json()
    ignored = next(n for n in body["notes"] if n.startswith("Αγνοήθηκαν"))
    assert "audit_log.json (άγνωστο αρχείο)" in ignored and "users.json" in ignored and "notes.txt" not in ignored


# ============================================================================ 7. Ασφάλεια και ρόλοι (T1) ====

def _two_firms(client):
    import server
    user_a = _admin(client, "a@example.gr")
    with TestClient(server.app, base_url="https://testserver") as other:
        user_b = _admin(other, "b@example.gr")
    return user_a, user_b


def test_a_forged_archive_cannot_put_documents_into_another_firm(client):
    """Ο ελεγκτής: χωρίς το `doc["firm_id"] = firm`, ένα πλαστό ZIP με `firm_id` του γραφείου Β γράφει ΜΕΣΑ στο Β."""
    from db import db
    user_a, user_b = _two_firms(client)
    ids = {name: ObjectId() for name in ("clients", "expenses", "invoices", "cases")}
    files = {name: [{"_id": _oid(oid), "firm_id": user_b["firm_id"], "name": "ΨΕΥΤΙΚΟ"}] for name, oid in ids.items()}
    files["invoices"][0].pop("firm_id")                                     # και έγγραφο χωρίς `firm_id` καθόλου
    body = _import(client, _archive(files)).json()
    assert body["failed"] == 0 and all(body["restored"][n] == 1 for n in ids)
    for name, oid in ids.items():
        stored = _run(db[name].find_one({"_id": oid}))
        assert stored["firm_id"] == user_a["firm_id"], name
    assert _count("clients", {"firm_id": user_b["firm_id"]}) == 0
    assert _count("expenses", {"firm_id": user_b["firm_id"]}) == 0


def test_only_an_admin_can_export_or_restore(client):
    import server
    _register(client, "lawyer@example.gr")                                  # η εγγραφή δίνει ρόλο «lawyer»
    assert client.get("/api/backup/export").status_code == 403
    r = _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]}))
    assert r.status_code == 403
    assert _count("clients") == 0
    with TestClient(server.app, base_url="https://testserver") as anonymous:
        assert anonymous.get("/api/backup/export").status_code == 401
        assert anonymous.post("/api/backup/import", files={"file": ("b.zip", b"x", "application/zip")}).status_code == 401


def test_the_audit_trail_is_restored_across_the_chunk_boundary(client):
    """Κάθε 501η γραμμή χανόταν αν το βήμα του κομματιού ήταν 501 αντί για 500, και κανένα test δεν περνούσε το όριο."""
    import routes_backup
    user = _admin(client)
    n = 2 * routes_backup.RESTORE_CHUNK + 3
    rows = [{"_id": _oid(ObjectId()), "action": "post:clients", "entity": "clients", "n": i} for i in range(n)]
    body = _import(client, _archive({"audit_logs": rows})).json()
    assert body["restored"]["audit_logs"] == n and body["failed"] == 0
    assert _count("audit_logs", {"n": {"$exists": True}, "firm_id": user["firm_id"]}) == n
    again = _import(client, _archive({"audit_logs": rows})).json()
    assert again["restored"]["audit_logs"] == 0 and again["already_present"]["audit_logs"] == n
    assert again["differing"] == {}


def test_a_partly_present_chunk_compares_each_clashing_row_with_its_own_live_row(client):
    """Όταν μόνο μερικές γραμμές ενός κομματιού υπάρχουν ήδη, η αναφορά πρέπει να συγκρίνει ακριβώς αυτές με τις ζωντανές
    τους. Αν οι δείκτες των συγκρούσεων μετατοπίζονταν κατά μία θέση, θα συγκρίνονταν οι γειτονικές (που μόλις
    προστέθηκαν και ταυτίζονται με το αντίγραφο) και μια πραγματική διαφορά θα έμενε αθέατη."""
    from db import db
    user = _admin(client)
    ids = [ObjectId() for _ in range(5)]
    rows = [{"_id": _oid(oid), "action": "post:clients", "entity": "clients", "n": n} for n, oid in enumerate(ids)]
    firm = user["firm_id"]
    _run(db.audit_logs.insert_many([
        {"_id": ids[1], "action": "post:clients", "entity": "clients", "n": 1, "firm_id": firm},     # υπάρχει, ίδια
        {"_id": ids[3], "action": "post:clients", "entity": "clients", "n": 99, "firm_id": firm},    # υπάρχει, ΔΙΑΦΕΡΕΙ
    ]))

    body = _import(client, _archive({"audit_logs": rows})).json()

    assert body["restored"]["audit_logs"] == 3
    assert body["already_present"]["audit_logs"] == 2
    assert body["differing"]["audit_logs"] == 1
    assert _run(db.audit_logs.find_one({"_id": ids[3]}))["n"] == 99, "η υπάρχουσα γραμμή δεν αντικαθίσταται"
    assert [m["n"] for m in _run(db.audit_logs.find({"restored_at": {"$exists": True}, "n": {"$in": [0, 2, 4]}}).sort("n", 1).to_list(10))] == [0, 2, 4]


def test_restoring_the_same_backup_twice_after_a_loss_reports_no_false_tampering(client):
    """Οι γραμμές που επανήλθαν φέρουν `restored_at`/`restored_by`· αν η σύγκριση τα κρατούσε, η δεύτερη επαναφορά
    του ίδιου αρχείου θα έβγαζε «διαφέρουν» και ψευδή προειδοποίηση για αλλοίωση του ίχνους."""
    from db import db
    _admin(client)
    for i in range(3):
        client.post("/api/clients", json={"name": f"ΠΕΛΑΤΗΣ {i}", "afm": "123456783"})
    backup = _export(client)
    victims = [ObjectId(r["_id"]["__oid__"]) for r in _rows(_open(backup), "audit_logs") if r["action"] == "post:clients"]
    _run(db.audit_logs.delete_many({"_id": {"$in": victims}}))

    first = _import(client, backup).json()
    assert first["restored"]["audit_logs"] == 3
    second = _import(client, backup).json()
    assert second["restored"]["audit_logs"] == 0
    assert second["differing"] == {}, second["differing"]
    assert not any("ΠΡΟΣΟΧΗ" in n for n in second["notes"]), second["notes"]


# ============================================================================ 8. GDPR ====

def _gdpr(client, client_id) -> dict:
    r = client.get(f"/api/backup/gdpr/client/{client_id}")
    assert r.status_code == 200, r.text
    return json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("data.json").decode("utf-8"))


def _seed_client_data(client, user, cid: str) -> dict:
    """Δεδομένα ΕΝΟΣ πελάτη σε όλες τις συλλογές που τον αφορούν, και ίδια δεδομένα που ΔΕΝ του ανήκουν."""
    from db import db
    fid = user["firm_id"]
    invoice = client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()["id"]
    case = client.post("/api/cases", json={"title": "Υπόθεση", "client_id": cid, "client_name": "ΑΛΦΑ ΑΕ"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]

    def put(collection, **fields):
        _run(db[collection].insert_one({"firm_id": fid, **fields}))

    put("hearings", case_id=case, title="Δικάσιμος")
    put("paravola", case_id=case, category="Λοιπά παράβολα", amount=10.0)
    put("case_tracker_events", case_id=case, case_title="Υπόθεση", change_type="status_change")
    put("grammatia", client_id=cid, number="ΓΡΠ-2026-0001")
    put("trust_accounts", client_id=cid, client_name="ΑΛΦΑ ΑΕ")
    put("trust_transactions", client_id=cid, amount=50.0)
    put("kyc", client_id=cid, risk_level="low")
    put("payment_transactions", client_id=cid, session_id="cs_1", amount=124.0)
    put("portal_messages", client_id=cid, sender="client", text="Καλημέρα")
    put("bank_transactions", date="2026-09-12", description="ΕΜΒΑΣΜΑ ΑΛΦΑ ΑΕ", amount=124.0, matched_invoice_id=invoice)
    put("signatures", signer_name="ΑΛΦΑ ΑΕ", signer_email="ALPHA@example.gr", signature_data="x")

    # Ό,τι ΔΕΝ ανήκει στον πελάτη: άλλος πελάτης του ίδιου γραφείου και ίδια αναγνωριστικά σε ΑΛΛΟ γραφείο.
    other_client = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "094259216"}).json()["id"]
    for collection, extra in (("kyc", {}), ("portal_messages", {"text": "ΞΕΝΟ"}), ("payment_transactions", {"session_id": "cs_x"}),
                              ("trust_transactions", {"amount": 1.0}), ("grammatia", {"number": "ΞΕΝΟ"})):
        put(collection, client_id=other_client, **extra)
        _run(db[collection].insert_one({"firm_id": "firm-someone-else", "client_id": cid, **extra}))
    _run(db.hearings.insert_one({"firm_id": "firm-someone-else", "case_id": case, "title": "ΞΕΝΗ"}))
    _run(db.signatures.insert_one({"firm_id": fid, "signer_email": "kapoios@example.gr", "signer_name": "ΑΛΛΟΣ",
                                   "signature_data": "y"}))
    return {"invoice": invoice, "case": case, "token": token}


def test_gdpr_export_contains_everything_held_about_the_client_and_nothing_else(client):
    """Το `_readme.txt` έλεγε «όλα τα προσωπικά δεδομένα» αλλά το αρχείο είχε έξι κατηγορίες: έλειπαν KYC,
    δικάσιμοι, παράβολα, πληρωμές κάρτας, μηνύματα πύλης, τραπεζικές κινήσεις και υπογραφές."""
    user = _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "email": "alpha@example.gr"}).json()["id"]
    seeded = _seed_client_data(client, user, cid)

    data = _gdpr(client, cid)
    assert data["client"]["name"] == "ΑΛΦΑ ΑΕ"
    assert [i["_id"]["__oid__"] for i in data["invoices"]] == [seeded["invoice"]]
    assert [c["_id"]["__oid__"] for c in data["cases"]] == [seeded["case"]]
    assert [h["title"] for h in data["hearings"]] == ["Δικάσιμος"]
    assert [p["category"] for p in data["paravola"]] == ["Λοιπά παράβολα"]
    assert [e["change_type"] for e in data["case_tracker_events"]] == ["status_change"]
    assert [g["number"] for g in data["grammatia"]] == ["ΓΡΠ-2026-0001"]
    assert len(data["trust_accounts"]) == 1 and [t["amount"] for t in data["trust_transactions"]] == [50.0]
    assert [k["risk_level"] for k in data["kyc"]] == ["low"]
    assert [p["session_id"] for p in data["payment_transactions"]] == ["cs_1"]
    assert [m["text"] for m in data["portal_messages"]] == ["Καλημέρα"]
    assert [b["description"] for b in data["bank_transactions"]] == ["ΕΜΒΑΣΜΑ ΑΛΦΑ ΑΕ"]
    assert [s["signer_name"] for s in data["signatures"]] == ["ΑΛΦΑ ΑΕ"], "υπογραφές με το email του (χωρίς διάκριση πεζών)"
    assert len(data["portal_access"]) == 1


def test_gdpr_export_shows_portal_access_but_never_the_token(client):
    user = _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "email": "alpha@example.gr"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]
    data = _gdpr(client, cid)
    access = data["portal_access"]
    assert len(access) == 1 and {"created_at", "expires_at"} <= set(access[0]) and "token" not in access[0]
    assert token not in json.dumps(data)
    assert user["email"]


def test_gdpr_export_is_not_capped_at_five_thousand_rows(client):
    """Ο ελεγκτής (με Motor): 5.002 γραμμάτια → εξάγονταν 5.000, και το `_readme.txt` έλεγε «όλα»."""
    from db import db
    user = _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    n = 5_002
    for collection in ("grammatia", "trust_transactions"):
        _run(db[collection].drop_indexes())
        _run(db[collection].insert_many([{"firm_id": user["firm_id"], "client_id": cid, "n": i} for i in range(n)]))
    data = _gdpr(client, cid)
    assert len(data["grammatia"]) == n and len(data["trust_transactions"]) == n


def test_gdpr_export_finds_the_client_data_whatever_the_case_of_the_id_in_the_url(client):
    """Τα έγγραφα του πελάτη κρατούν το πεζό αναγνωριστικό· ένα URL με κεφαλαία έβρισκε τον πελάτη αλλά κανένα
    από τα τιμολόγιά του."""
    from db import db
    user = _admin(client)
    oid = ObjectId("65f0a1b2c3d4e5f60718293a")
    _run(db.clients.insert_one({"_id": oid, "firm_id": user["firm_id"], "name": "ΑΛΦΑ ΑΕ"}))
    _run(db.invoices.insert_one({"firm_id": user["firm_id"], "client_id": str(oid), "number": "ΤΠΥ-2026-0001"}))
    assert str(oid) != str(oid).upper()
    data = _gdpr(client, str(oid).upper())
    assert [i["number"] for i in data["invoices"]] == ["ΤΠΥ-2026-0001"]


def test_gdpr_export_of_another_firms_client_is_refused(client):
    import server
    _admin(client, "a@example.gr")
    with TestClient(server.app, base_url="https://testserver") as other:
        _admin(other, "b@example.gr")
        cid = other.post("/api/clients", json={"name": "ΠΕΛΑΤΗΣ ΤΟΥ Β", "afm": "123456783"}).json()["id"]
    assert client.get(f"/api/backup/gdpr/client/{cid}").status_code == 404


def test_gdpr_readme_lists_what_is_included_and_what_is_not(client):
    _admin(client)
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783"}).json()["id"]
    r = client.get(f"/api/backup/gdpr/client/{cid}")
    text = zipfile.ZipFile(io.BytesIO(r.content)).read("_readme.txt").decode("utf-8")
    assert "δικασίμους" in text and "KYC" in text and "Δεν περιλαμβάνονται αρχεία" in text
