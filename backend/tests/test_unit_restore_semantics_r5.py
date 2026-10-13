"""Γύρος 5 (ανασκόπηση 5, WP15β) — η επαναφορά αντιγράφου δεν γυρίζει ποτέ πίσω τα βιβλία του γραφείου.

Ο ελεγκτής C έδειξε ότι η επαναφορά αντικαθιστούσε με `replace_one` κάθε έγγραφο με ίδιο `_id` — και απαντούσε «ολοκληρώθηκε»:

* τιμολόγιο `issued` με αριθμό ΤΠΥ-2026-0001 γινόταν ξανά πρόχειρο, χωρίς αριθμό και MARK (ο μετρητής έμενε στο 1, άρα
  η επανέκδοση έπαιρνε το 0002 και ο έλεγχος αρίθμησης έβγαινε «εντάξει»)·
* πληρωμή κάρτας που είχε εισπραχθεί ξαναγινόταν `pending` και το τιμολόγιο `issued` (ο πελάτης θα έπαιρνε υπενθύμιση)·
* το υπόλοιπο πελατολογικού λογαριασμού γύριζε στο παλιό, ενώ οι κινήσεις έμεναν: ο έλεγχος ledger έβγαζε διαφορά.

Το σωστό για μια επαναφορά σε γραφείο που δουλεύει είναι να **προσθέτει ό,τι λείπει** και να μην αγγίζει ό,τι υπάρχει.
Εδώ ελέγχεται η νέα συμπεριφορά:

1. από προεπιλογή (`mode=merge`) κανένα υπάρχον έγγραφο δεν αντικαθίσταται, σε καμία συλλογή·
2. με ρητή επιλογή `mode=overwrite` αντικαθίστανται ΜΟΝΟ τα δεδομένα εργασίας (πελάτες, υποθέσεις, δικάσιμοι, εργαζόμενοι,
   πρότυπα) — ποτέ τα βιβλία (τιμολόγια, πληρωμές, τράπεζα, πελατολογικός, ΦΠΑ, έξοδα, γραμμάτια, παράβολα …)·
3. η αναφορά λέει πόσα υπάρχοντα έγγραφα διέφεραν και έμειναν ως έχουν, και τι αντικαταστάθηκε·
4. μετά την επαναφορά τρέχουν ο έλεγχος αρίθμησης και ο έλεγχος πελατολογικού λογαριασμού και το αποτέλεσμα φαίνεται·
5. οι συντελεστές μνήμης: δεν αναλύεται αρχείο με υπερβολικά πολλά αντικείμενα JSON (μια «βόμβα» από `{}`).
"""
import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today

GENERIC = ["clients", "invoices", "expenses", "payroll_employees", "payroll_runs", "cases", "grammatia", "paravola",
           "hearings", "trust_accounts", "trust_transactions", "kyc", "signatures", "payment_transactions",
           "copilot_messages", "bank_transactions", "vat_periods", "portal_messages", "case_tracker_events",
           "drafting_templates", "drafting_clauses"]
BOOKS = ["invoices", "expenses", "payroll_runs", "grammatia", "paravola", "trust_accounts", "trust_transactions",
         "kyc", "signatures", "payment_transactions", "copilot_messages", "bank_transactions", "vat_periods",
         "portal_messages", "case_tracker_events"]
WORKING = ["clients", "cases", "hearings", "payroll_employees", "drafting_templates", "drafting_clauses"]


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "restore@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "restore@example.gr"}))
    _run(db.users.update_one({"email": "restore@example.gr"}, {"$set": {"role": "admin"}}))
    company = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                                "email": "alpha@example.gr"}).json()["id"]
    return SimpleNamespace(company=company, firm_id=user["firm_id"])


def _today() -> str:
    return athens_today().isoformat()


def _export(client) -> bytes:
    r = client.get("/api/backup/export")
    assert r.status_code == 200, r.text
    return r.content


def _archive(files: dict | None = None, *, raw: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_meta.json", json.dumps({"version": 1}))
        for name, rows in (files or {}).items():
            zf.writestr(f"{name}.json", json.dumps(rows))
        for filename, text in (raw or {}).items():
            zf.writestr(filename, text)
    return buf.getvalue()


def _import(client, content: bytes, **params):
    return client.post("/api/backup/import", params=params,
                       files={"file": ("backup.zip", content, "application/zip")})


def _oid(value) -> dict:
    return {"__oid__": str(value)}


def _count(collection: str, query: dict | None = None) -> int:
    from db import db
    return _run(db[collection].count_documents(query or {}))


def _stored(collection: str, doc_id) -> dict | None:
    from db import db
    return _run(db[collection].find_one({"_id": doc_id if isinstance(doc_id, ObjectId) else ObjectId(doc_id)}))


def _draft(client, client_id, net=100.0) -> str:
    created = client.post("/api/invoices", json={
        "client_id": client_id, "date": _today(),
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]})
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _issue(client, invoice_id) -> dict:
    out = client.post(f"/api/invoices/{invoice_id}/issue")
    assert out.status_code == 200, out.text
    return out.json()


# ===================================================== 1. Από προεπιλογή: μόνο προσθήκη ====

@pytest.mark.parametrize("coll", GENERIC)
@pytest.mark.parametrize("params", [{}, {"mode": "merge"}], ids=["default", "merge"])
def test_a_restore_never_replaces_an_existing_document_by_default(client, office, coll, params):
    from db import db
    doc_id = ObjectId()
    _run(db[coll].insert_one({"_id": doc_id, "firm_id": office.firm_id, "marker": "ζωντανό"}))
    body = _import(client, _archive({coll: [{"_id": _oid(doc_id), "marker": "αντίγραφο"}]}), **params).json()
    assert _stored(coll, doc_id)["marker"] == "ζωντανό"
    assert body["mode"] == "merge" and body["failed"] == 0, body
    assert body["restored"][coll] == 0
    assert body["already_present"][coll] == 1 and body["kept"][coll] == 1
    assert body.get("replaced", {}).get(coll, 0) == 0


@pytest.mark.parametrize("coll", GENERIC)
@pytest.mark.parametrize("params", [{}, {"mode": "overwrite"}], ids=["merge", "overwrite"])
def test_a_document_missing_from_the_database_is_added_in_every_mode(client, office, coll, params):
    doc_id = ObjectId()
    body = _import(client, _archive({coll: [{"_id": _oid(doc_id), "marker": "αντίγραφο"}]}), **params).json()
    stored = _stored(coll, doc_id)
    assert stored["marker"] == "αντίγραφο" and stored["firm_id"] == office.firm_id
    assert body["restored"][coll] == 1 and body["failed"] == 0, body
    assert body.get("kept", {}).get(coll, 0) == 0 and body.get("already_present", {}).get(coll, 0) == 0


def test_an_identical_existing_document_is_counted_as_present_but_not_as_kept(client, office):
    from db import db
    doc_id = ObjectId()
    _run(db.clients.insert_one({"_id": doc_id, "firm_id": office.firm_id, "name": "ΙΔΙΟ"}))
    body = _import(client, _archive({"clients": [{"_id": _oid(doc_id), "name": "ΙΔΙΟ"}]})).json()
    assert body["already_present"]["clients"] == 1 and body["restored"]["clients"] == 0
    assert body.get("kept", {}).get("clients", 0) == 0


def test_an_issued_invoice_keeps_its_number_status_and_mark_when_an_older_backup_is_restored(client, office):
    """Ο ελεγκτής C, πείραμα R1/R2: το τιμολόγιο ξαναγινόταν πρόχειρο χωρίς αριθμό και MARK."""
    from db import db
    invoice_id = _draft(client, office.company)
    backup = _export(client)                                              # το αντίγραφο έχει πρόχειρο
    issued = _issue(client, invoice_id)
    assert issued["status"] == "issued" and issued["number"]
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": {"mydata_status": "synced", "mydata_mark": "400000000000123"}}))
    before = _stored("invoices", invoice_id)

    for params in ({}, {"mode": "overwrite"}):
        body = _import(client, backup, **params).json()
        assert body["failed"] == 0, body
        after = _stored("invoices", invoice_id)
        assert after == before, params
        assert (after["status"], after["number"], after["mydata_mark"]) == ("issued", issued["number"], "400000000000123")
        assert body["kept"]["invoices"] == 1 and body["restored"]["invoices"] == 0

    nxt = _issue(client, _draft(client, office.company))
    assert nxt["number"].rsplit("-", 1)[-1] == "0002"                     # η αρίθμηση συνεχίζει χωρίς άλμα ή επανάληψη
    check = client.get("/api/invoices/numbering-check").json()
    assert check["ok"] is True and check["series"][0]["first"] == 1 and check["series"][0]["gaps_total"] == 0


def test_a_card_payment_received_after_the_backup_is_not_forgotten(client, office):
    """Ο ελεγκτής C, πείραμα R3: πληρωμή που είχε εισπραχθεί ξαναγινόταν εκκρεμής και το τιμολόγιο ανείσπρακτο."""
    from db import db
    invoice_id = _draft(client, office.company)
    _issue(client, invoice_id)
    tx_id = ObjectId()
    _run(db.payment_transactions.insert_one({"_id": tx_id, "firm_id": office.firm_id, "invoice_id": invoice_id,
                                             "session_id": "cs_test_1", "amount": 124.0, "payment_status": "pending",
                                             "status": "initiated"}))
    backup = _export(client)
    _run(db.payment_transactions.update_one({"_id": tx_id}, {"$set": {"payment_status": "paid", "status": "completed"}}))
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": {"status": "paid", "payment_method": "stripe"}}))

    for params in ({}, {"mode": "overwrite"}):
        body = _import(client, backup, **params).json()
        assert body["failed"] == 0, body
        assert _stored("payment_transactions", tx_id)["payment_status"] == "paid", params
        assert _stored("invoices", invoice_id)["status"] == "paid", params


def test_client_money_stays_consistent_with_its_ledger_after_an_older_backup_is_restored(client, office):
    """Ο ελεγκτής C, πείραμα R4: το υπόλοιπο γύριζε στα 100 ενώ οι κινήσεις έδιναν 150."""
    def deposit(amount):
        r = client.post("/api/trust/transactions", json={"client_id": office.company, "type": "deposit", "amount": amount,
                                                         "date": _today(), "description": "δοκιμή"})
        assert r.status_code == 200, r.text

    deposit(100.0)
    backup = _export(client)
    deposit(50.0)
    assert client.get(f"/api/trust/accounts/{office.company}").json()["ledger_check"]["consistent"] is True

    for params in ({}, {"mode": "overwrite"}):
        body = _import(client, backup, **params).json()
        assert body["failed"] == 0, body
        data = client.get(f"/api/trust/accounts/{office.company}").json()
        assert data["account"]["balance"] == 150.0, params
        assert len(data["transactions"]) == 2
        assert data["ledger_check"] == {"ledger_balance": 150.0, "difference": 0.0, "consistent": True}
        assert body["checks"]["trust_ledger"]["ok"] is True


def test_a_confirmed_bank_line_and_a_submitted_vat_period_survive_an_older_backup(client, office):
    from db import db
    line, period = ObjectId(), ObjectId()
    _run(db.bank_transactions.insert_one({"_id": line, "firm_id": office.firm_id, "external_id": "tx-1", "amount": 124.0,
                                          "match_status": "unmatched"}))
    _run(db.vat_periods.insert_one({"_id": period, "firm_id": office.firm_id, "period": "2026-06", "status": "computed"}))
    backup = _export(client)
    _run(db.bank_transactions.update_one({"_id": line}, {"$set": {"match_status": "manual_matched"}}))
    _run(db.vat_periods.update_one({"_id": period}, {"$set": {"status": "submitted"}}))
    for params in ({}, {"mode": "overwrite"}):
        assert _import(client, backup, **params).json()["failed"] == 0
        assert _stored("bank_transactions", line)["match_status"] == "manual_matched", params
        assert _stored("vat_periods", period)["status"] == "submitted", params


def test_a_backup_invoice_whose_number_belongs_to_another_invoice_is_reported_not_overwritten(client, office):
    """Δύο διαφορετικά τιμολόγια δεν μπορούν να έχουν τον ίδιο αριθμό· η επαναφορά δεν σταματά και δεν αντικαθιστά."""
    from db import db
    live = ObjectId()
    _run(db.invoices.insert_one({"_id": live, "firm_id": office.firm_id, "number": "ΤΠΥ-2026-0001", "status": "issued"}))
    foreign_number = {"_id": _oid(ObjectId()), "number": "ΤΠΥ-2026-0001", "status": "issued"}
    other = {"_id": _oid(ObjectId()), "number": "ΤΠΥ-2026-0002", "status": "issued"}
    body = _import(client, _archive({"invoices": [foreign_number, other]})).json()
    assert body["restored"]["invoices"] == 1 and body["failed"] == 1, body
    assert any("ΤΠΥ-2026-0001" in e and "μοναδικό κλειδί" in e for e in body["errors"]), body["errors"]
    assert _stored("invoices", live)["status"] == "issued"


# ================================================== 2. Ρητή αντικατάσταση: μόνο δεδομένα εργασίας ====

@pytest.mark.parametrize("coll", WORKING)
def test_overwrite_replaces_working_data_and_says_how_many(client, office, coll):
    from db import db
    doc_id = ObjectId()
    _run(db[coll].insert_one({"_id": doc_id, "firm_id": office.firm_id, "marker": "ζωντανό"}))
    body = _import(client, _archive({coll: [{"_id": _oid(doc_id), "marker": "αντίγραφο"}]}), mode="overwrite").json()
    assert _stored(coll, doc_id)["marker"] == "αντίγραφο"
    assert body["mode"] == "overwrite" and body["failed"] == 0
    assert body["restored"][coll] == 1 and body["replaced"][coll] == 1
    assert body.get("kept", {}).get(coll, 0) == 0
    assert any("αντικαταστάθηκαν" in n and "1" in n for n in body["notes"]), body["notes"]


@pytest.mark.parametrize("coll", BOOKS)
def test_overwrite_never_replaces_the_books(client, office, coll):
    from db import db
    doc_id = ObjectId()
    _run(db[coll].insert_one({"_id": doc_id, "firm_id": office.firm_id, "marker": "ζωντανό"}))
    body = _import(client, _archive({coll: [{"_id": _oid(doc_id), "marker": "αντίγραφο"}]}), mode="overwrite").json()
    assert _stored(coll, doc_id)["marker"] == "ζωντανό"
    assert body["restored"][coll] == 0 and body["kept"][coll] == 1
    assert body.get("replaced", {}).get(coll, 0) == 0


def test_the_overwritable_collections_are_exactly_the_working_data():
    import routes_backup
    assert set(routes_backup.OVERWRITABLE) == set(WORKING)
    assert set(routes_backup.OVERWRITABLE) <= set(routes_backup.COLLECTIONS)
    assert not set(routes_backup.OVERWRITABLE) & {"audit_logs", "counters", "settings", *BOOKS}
    assert set(GENERIC) | {"audit_logs", "counters", "settings"} == set(routes_backup.COLLECTIONS)
    assert routes_backup.RESTORE_MODES == ("merge", "overwrite")


@pytest.mark.parametrize("mode", ["", "replace", "OVERWRITE", "all", "1"])
def test_an_unknown_mode_is_refused_before_anything_is_written(client, office, mode):
    doc_id = ObjectId()
    r = _import(client, _archive({"clients": [{"_id": _oid(doc_id), "name": "ΝΕΟΣ"}]}), mode=mode)
    assert r.status_code == 422, r.text
    assert _stored("clients", doc_id) is None


def test_overwrite_still_keeps_the_audit_trail_append_only_and_the_counters_forward_only(client, office):
    from db import db
    row, counter_id = ObjectId(), f"invoice-{office.firm_id}-2026"
    _run(db.audit_logs.insert_one({"_id": row, "firm_id": office.firm_id, "action": "ζωντανό"}))
    _run(db.counters.insert_one({"_id": counter_id, "seq": 9, "last_date": "2026-06-01"}))
    archive = _archive({"audit_logs": [{"_id": _oid(row), "action": "αντίγραφο"}],
                        "counters": [{"_id": counter_id, "seq": 3, "last_date": "2026-01-01"}]})
    body = _import(client, archive, mode="overwrite").json()
    assert _stored("audit_logs", row)["action"] == "ζωντανό" and body["differing"]["audit_logs"] == 1
    counter = _run(db.counters.find_one({"_id": counter_id}))
    assert (counter["seq"], counter["last_date"]) == (9, "2026-06-01")


def test_foreign_documents_are_never_replaced_in_any_mode(client, office):
    from db import db
    foreign = ObjectId()
    _run(db.clients.insert_one({"_id": foreign, "firm_id": "firm-" + "d" * 32, "name": "ΞΕΝΟΣ"}))
    for params in ({}, {"mode": "overwrite"}):
        body = _import(client, _archive({"clients": [{"_id": _oid(foreign), "name": "ΚΑΤΑΛΗΦΘΗΚΕ"}]}), **params).json()
        assert _stored("clients", foreign)["name"] == "ΞΕΝΟΣ"
        assert body["restored"]["clients"] == 0 and body["failed"] == 1
        assert any("άλλο γραφείο" in e for e in body["errors"])


# ==================================================================== 3. Ρυθμίσεις γραφείου ====

def test_a_restore_keeps_configured_settings_unless_overwrite_is_chosen(client, office):
    from db import db
    assert client.patch("/api/settings", json={"company_name": "ΖΩΝΤΑΝΗ ΕΠΩΝΥΜΙΑ", "afm": "123456783"}).status_code == 200
    archive = _archive({"settings": [{"_id": _oid(ObjectId()), "company_name": "ΕΠΩΝΥΜΙΑ ΑΝΤΙΓΡΑΦΟΥ", "afm": "094259216"}]})

    body = _import(client, archive).json()
    assert client.get("/api/settings").json()["company_name"] == "ΖΩΝΤΑΝΗ ΕΠΩΝΥΜΙΑ"
    assert body["restored"]["settings"] == 0 and body["kept"]["settings"] == 1 and body["failed"] == 0
    assert any("ρυθμίσεις" in n.lower() and "δεν αντικαταστάθηκαν" in n for n in body["notes"]), body["notes"]
    assert _count("settings", {"firm_id": office.firm_id}) == 1

    body = _import(client, archive, mode="overwrite").json()
    assert client.get("/api/settings").json()["company_name"] == "ΕΠΩΝΥΜΙΑ ΑΝΤΙΓΡΑΦΟΥ"
    assert body["restored"]["settings"] == 1 and body["replaced"]["settings"] == 1
    assert _count("settings", {"firm_id": office.firm_id}) == 1
    assert _run(db.settings.find_one({"firm_id": office.firm_id}))["afm"] == "094259216"


def test_a_restore_replaces_the_blank_settings_a_new_installation_creates(client, office):
    """Μετά από απώλεια ο διαχειριστής ανοίγει τις Ρυθμίσεις (δημιουργείται κενό έγγραφο) και κάνει επαναφορά."""
    assert client.get("/api/settings").json()["company_name"] == "Δικηγορική Εταιρεία"
    body = _import(client, _archive({"settings": [{"_id": _oid(ObjectId()), "company_name": "ΠΑΛΙΟ ΓΡΑΦΕΙΟ", "afm": "094259216"}]})).json()
    assert body["restored"]["settings"] == 1 and body.get("kept", {}).get("settings", 0) == 0
    assert client.get("/api/settings").json()["company_name"] == "ΠΑΛΙΟ ΓΡΑΦΕΙΟ"
    assert _count("settings", {"firm_id": office.firm_id}) == 1


def test_duplicate_settings_documents_are_collapsed_even_when_the_live_settings_are_kept(client, office):
    from db import db
    client.patch("/api/settings", json={"company_name": "ΖΩΝΤΑΝΗ"})
    _run(db.settings.drop_indexes())
    _run(db.settings.insert_many([{"firm_id": office.firm_id, "company_name": "ΔΙΠΛΟ 1"}, {"firm_id": office.firm_id, "company_name": "ΔΙΠΛΟ 2"}]))
    body = _import(client, _archive({"settings": [{"_id": _oid(ObjectId()), "company_name": "ΑΝΤΙΓΡΑΦΟ"}]})).json()
    assert _count("settings", {"firm_id": office.firm_id}) == 1
    assert client.get("/api/settings").json()["company_name"] == "ΖΩΝΤΑΝΗ"
    assert any("Αφαιρέθηκαν" in n and "διπλά" in n for n in body["notes"]), body["notes"]


# =========================================================================== 4. Η αναφορά ====

def test_the_report_says_which_existing_documents_differ_and_were_left_alone(client, office):
    from db import db
    ids = [ObjectId() for _ in range(3)]
    _run(db.clients.insert_many([{"_id": ids[0], "firm_id": office.firm_id, "name": "ΑΛΛΑΓΜΕΝΟ 1"},
                                 {"_id": ids[1], "firm_id": office.firm_id, "name": "ΑΛΛΑΓΜΕΝΟ 2"},
                                 {"_id": ids[2], "firm_id": office.firm_id, "name": "ΙΔΙΟ"}]))
    archive = _archive({"clients": [{"_id": _oid(ids[0]), "name": "ΠΑΛΙΟ 1"}, {"_id": _oid(ids[1]), "name": "ΠΑΛΙΟ 2"},
                                    {"_id": _oid(ids[2]), "name": "ΙΔΙΟ"}, {"_id": _oid(ObjectId()), "name": "ΝΕΟΣ"}]})
    body = _import(client, archive).json()
    assert body["restored"]["clients"] == 1
    assert body["already_present"]["clients"] == 3 and body["kept"]["clients"] == 2
    note = next(n for n in body["notes"] if "δεν άλλαξαν" in n or "δεν αντικαταστάθηκαν" in n)
    assert "2" in note and "clients" not in note, note
    assert body["message"] == "Η επαναφορά ολοκληρώθηκε."


def test_a_second_restore_of_the_same_backup_adds_nothing_and_changes_nothing(client, office):
    _draft(client, office.company)
    backup = _export(client)
    first = _import(client, backup).json()
    second = _import(client, backup).json()
    assert first["failed"] == second["failed"] == 0
    assert all(n == 0 for n in second["restored"].values()), second["restored"]
    assert second["kept"] == {} and second["differing"] == {}


# =============================================================== 5. Έλεγχοι μετά την επαναφορά ====

def test_the_numbering_check_and_the_client_money_check_run_after_a_restore(client, office):
    invoice_id = _draft(client, office.company)
    _issue(client, invoice_id)
    body = _import(client, _export(client)).json()
    assert body["checks"]["numbering"]["ok"] is True
    assert body["checks"]["trust_ledger"] == {"ok": True, "accounts": 0, "inconsistent": []}


def test_a_restored_ledger_row_that_the_balance_does_not_cover_is_flagged(client, office):
    from db import db
    client.post("/api/trust/transactions", json={"client_id": office.company, "type": "deposit", "amount": 100.0,
                                                 "date": _today(), "description": "δοκιμή"})
    account = _run(db.trust_accounts.find_one({"firm_id": office.firm_id}))
    orphan = {"_id": _oid(ObjectId()), "account_id": str(account["_id"]), "client_id": office.company, "type": "deposit",
              "amount": 40.0, "date": _today(), "description": "χαμένη κίνηση"}
    body = _import(client, _archive({"trust_transactions": [orphan]})).json()
    check = body["checks"]["trust_ledger"]
    assert check["ok"] is False and check["inconsistent"][0]["difference"] == -40.0
    assert check["inconsistent"][0]["client_name"] == "ΑΛΦΑ ΑΕ"
    assert any("ΠΡΟΣΟΧΗ" in n and "πελατολογικ" in n for n in body["notes"]), body["notes"]


def test_a_restored_series_with_a_gap_is_reported_by_the_numbering_check(client, office):
    from db import db
    _run(db.invoices.insert_many([
        {"_id": ObjectId(), "firm_id": office.firm_id, "number": "ΤΠΥ-2026-0001", "status": "issued", "date": "2026-01-10"},
        {"_id": ObjectId(), "firm_id": office.firm_id, "number": "ΤΠΥ-2026-0004", "status": "issued", "date": "2026-01-12"}]))
    body = _import(client, _archive({})).json()
    assert body["checks"]["numbering"]["ok"] is False and body["checks"]["numbering"]["problems"]["gaps"] >= 2
    assert any("ΠΡΟΣΟΧΗ" in n and "αρίθμησης" in n for n in body["notes"]), body["notes"]


def test_a_failing_check_never_fails_the_restore(client, office, monkeypatch):
    import invoice_services
    import routes_trust

    async def broken(*a, **k):
        raise RuntimeError("σπασμένος έλεγχος")

    monkeypatch.setattr(invoice_services, "numbering_report", broken)
    monkeypatch.setattr(routes_trust, "ledger_inconsistencies", broken)
    doc_id = ObjectId()
    r = _import(client, _archive({"clients": [{"_id": _oid(doc_id), "name": "ΝΕΟΣ"}]}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert _stored("clients", doc_id)["name"] == "ΝΕΟΣ" and body["restored"]["clients"] == 1
    assert body["checks"]["numbering"]["ok"] is None and "σπασμένος έλεγχος" in body["checks"]["numbering"]["error"]
    assert body["checks"]["trust_ledger"]["ok"] is None
    assert any("δεν ολοκληρώθηκε" in n for n in body["notes"]), body["notes"]


# ================================================================ 6. Όρια αρχείου (μνήμη) ====

def test_a_member_with_too_many_json_containers_is_refused_before_it_is_parsed(client, office, monkeypatch):
    """Ένα αρχείο 20 MB από `{}` έγινε 6,6 εκατ. αντικείμενα και 558 MB μνήμης (ο ελεγκτής C, μέτρηση)."""
    import routes_backup
    monkeypatch.setattr(routes_backup, "MAX_JSON_CONTAINERS", 50)
    r = _import(client, _archive({"clients": [{} for _ in range(60)]}))
    assert r.status_code == 413 and "υπερβολικά μεγάλο" in r.json()["detail"], r.text
    assert _count("clients", {"firm_id": office.firm_id}) == 1                       # μόνο ο πελάτης του fixture
    ok = _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"} for _ in range(10)]}))
    assert ok.status_code == 200, ok.text


def test_empty_lists_count_as_containers_too(client, office, monkeypatch):
    import routes_backup
    monkeypatch.setattr(routes_backup, "MAX_JSON_CONTAINERS", 30)
    r = _import(client, _archive({}, raw={"clients.json": "[" + ",".join(["[]"] * 100) + "]"}))
    assert r.status_code == 413, r.text


def test_the_default_container_limit_is_far_above_a_real_office_and_far_below_a_bomb():
    import routes_backup
    assert 1_000_000 <= routes_backup.MAX_JSON_CONTAINERS <= 5_000_000


def test_preparing_the_documents_runs_off_the_event_loop(client, office, monkeypatch):
    import routes_backup
    seen = []
    real = routes_backup.run_in_threadpool

    async def spy(func, *args, **kwargs):
        seen.append(getattr(func, "__name__", str(func)))
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(routes_backup, "run_in_threadpool", spy)
    assert _import(client, _archive({"clients": [{"_id": _oid(ObjectId()), "name": "ΑΛΦΑ"}]})).status_code == 200
    assert "_prepare_documents" in seen, seen


# ====================================================================== 7. Κείμενα προς τον χρήστη ====

def test_the_backup_caveats_no_longer_claim_that_a_restore_is_harmless_to_numbering(client, office):
    meta = json.loads(zipfile.ZipFile(io.BytesIO(_export(client))).read("_meta.json").decode("utf-8"))
    text = " ".join(meta["caveats"])
    assert "δεν αντικαθιστά" in text and "τιμολόγι" in text and "πληρωμ" in text
    assert "δεν γυρίζει ποτέ πίσω τον αριθμό ενός τιμολογίου" not in text


def test_an_export_of_one_office_restores_into_an_empty_one_without_loss(client, office):
    """Η βασική διαδρομή ανάκτησης: κενή βάση, επαναφορά, όλα επιστρέφουν."""
    from db import db
    invoice_id = _draft(client, office.company)
    issued = _issue(client, invoice_id)
    client.post("/api/trust/transactions", json={"client_id": office.company, "type": "deposit", "amount": 80.0,
                                                 "date": _today(), "description": "δοκιμή"})
    backup = _export(client)
    for name in ("invoices", "trust_accounts", "trust_transactions", "counters", "clients", "settings"):
        _run(db[name].delete_many({}))
    body = _import(client, backup).json()
    assert body["failed"] == 0, body
    restored = _stored("invoices", invoice_id)
    assert (restored["status"], restored["number"]) == ("issued", issued["number"])
    assert _count("clients") == 1 and _count("trust_transactions") == 1
    assert body["checks"]["numbering"]["ok"] is True and body["checks"]["trust_ledger"]["ok"] is True
    assert _issue(client, _draft(client, office.company))["number"].rsplit("-", 1)[-1] == "0002"
