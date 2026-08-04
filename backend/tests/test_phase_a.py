"""Phase A API tests for trust accounts, client import, backup/restore, and GDPR export."""
import csv
import io
import json
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from openpyxl import Workbook
from pymongo import MongoClient

FRONTEND_ENV = dotenv_values("/app/frontend/.env")
BACKEND_ENV = dotenv_values("/app/backend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or FRONTEND_ENV.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing")
API = f"{BASE_URL}/api"


def _credentials():
    path = Path("/app/memory/test_credentials.md")
    if not path.exists():
        pytest.skip("Missing /app/memory/test_credentials.md")
    content = path.read_text(encoding="utf-8")
    email = re.search(r"(?im)^\s*[-*]\s*Email:\s*([^\s]+)", content)
    password = re.search(r"(?im)^\s*[-*]\s*Password:\s*([^\s]+)", content)
    if not email or not password:
        pytest.skip("Admin email/password missing from test_credentials.md")
    return email.group(1), password.group(1)


@pytest.fixture(scope="module")
def auth_client():
    session = requests.Session()
    email, password = _credentials()
    response = session.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    if response.status_code != 200:
        pytest.fail(f"Authentication failed: {response.status_code} {response.text[:500]}")
    assert response.json()["email"] == email
    return session


@pytest.fixture(scope="module")
def test_state():
    state = {
        "client_ids": [],
        "invoice_ids": [],
        "client_names": [],
        "client_afms": [],
        "user_emails": [],
    }
    yield state

    mongo = MongoClient(BACKEND_ENV["MONGO_URL"])
    db = mongo[BACKEND_ENV["DB_NAME"]]
    client_ids = state["client_ids"]
    if client_ids:
        db.trust_transactions.delete_many({"client_id": {"$in": client_ids}})
        db.trust_accounts.delete_many({"client_id": {"$in": client_ids}})
        db.invoices.delete_many({"client_id": {"$in": client_ids}})
        from bson import ObjectId
        object_ids = [ObjectId(value) for value in client_ids]
        db.clients.delete_many({"_id": {"$in": object_ids}})
    if state["client_names"]:
        db.clients.delete_many({"name": {"$in": state["client_names"]}})
    if state["client_afms"]:
        db.clients.delete_many({"afm": {"$in": state["client_afms"]}})
    if state["user_emails"]:
        db.users.delete_many({"email": {"$in": state["user_emails"]}})
        db.login_attempts.delete_many({"identifier": {"$regex": "(" + "|".join(re.escape(e) for e in state["user_emails"]) + ")$"}})
    mongo.close()


@pytest.fixture(scope="module")
def trust_resources(auth_client, test_state):
    suffix = uuid.uuid4().hex[:8]
    client_name = f"TEST_PHASEA_TRUST_{suffix}"
    create_client = auth_client.post(
        f"{API}/clients",
        json={"name": client_name, "afm": f"7{suffix[:8]}", "client_type": "individual", "email": "phasea@example.test"},
        timeout=30,
    )
    assert create_client.status_code == 200, create_client.text
    client = create_client.json()
    test_state["client_ids"].append(client["id"])
    test_state["client_names"].append(client_name)

    create_invoice = auth_client.post(
        f"{API}/invoices",
        json={
            "client_id": client["id"],
            "date": "2026-07-28",
            "line_items": [{"description": "TEST_PHASEA_SERVICE", "quantity": 1, "unit_price": 50, "vat_rate": 24}],
        },
        timeout=30,
    )
    assert create_invoice.status_code == 200, create_invoice.text
    invoice = create_invoice.json()
    test_state["invoice_ids"].append(invoice["id"])
    issue = auth_client.post(f"{API}/invoices/{invoice['id']}/issue", timeout=30)
    assert issue.status_code == 200, issue.text
    assert issue.json()["status"] == "issued"
    return {"client": client, "invoice": issue.json()}


# Trust account list/summary, ledger updates, overdraft protection, and invoice settlement.
class TestTrustAccounts:
    def test_accounts_and_summary_contract(self, auth_client):
        accounts_response = auth_client.get(f"{API}/trust/accounts", timeout=30)
        summary_response = auth_client.get(f"{API}/trust/summary", timeout=30)
        assert accounts_response.status_code == 200, accounts_response.text
        assert summary_response.status_code == 200, summary_response.text
        accounts = accounts_response.json()
        summary = summary_response.json()
        assert isinstance(accounts, list)
        assert set(summary) == {"total_balance", "accounts_with_balance"}
        assert isinstance(summary["total_balance"], (int, float))
        assert isinstance(summary["accounts_with_balance"], int)
        assert summary["total_balance"] == pytest.approx(sum(float(a["balance"]) for a in accounts), abs=0.01)
        assert summary["accounts_with_balance"] == sum(float(a["balance"]) > 0.005 for a in accounts)
        assert all("_id" not in account and isinstance(account["id"], str) for account in accounts)

    def test_detail_creates_empty_account_for_valid_client(self, auth_client, trust_resources):
        client_id = trust_resources["client"]["id"]
        response = auth_client.get(f"{API}/trust/accounts/{client_id}", timeout=30)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"account", "transactions"}
        assert body["account"]["client_id"] == client_id
        assert body["account"]["client_name"] == trust_resources["client"]["name"]
        assert body["account"]["balance"] == 0
        assert body["transactions"] == []

    def test_deposit_increases_balance_and_persists_transaction(self, auth_client, trust_resources):
        client_id = trust_resources["client"]["id"]
        before_summary = auth_client.get(f"{API}/trust/summary", timeout=30).json()
        response = auth_client.post(
            f"{API}/trust/transactions",
            json={
                "client_id": client_id,
                "type": "deposit",
                "amount": 100,
                "date": "2026-07-29",
                "description": "TEST_PHASEA κατάθεση",
                "reference": "TEST-DEP-100",
            },
            timeout=30,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["message"] == "Η κίνηση καταχωρήθηκε"
        assert body["account"]["balance"] == 100
        assert body["account"]["total_deposits"] == 100
        assert body["transaction"]["type"] == "deposit"
        assert body["transaction"]["amount"] == 100
        assert body["transaction"]["balance_after"] == 100
        assert "_id" not in body["transaction"]

        detail = auth_client.get(f"{API}/trust/accounts/{client_id}", timeout=30).json()
        assert detail["account"]["balance"] == 100
        assert any(tx["reference"] == "TEST-DEP-100" and tx["balance_after"] == 100 for tx in detail["transactions"])
        after_summary = auth_client.get(f"{API}/trust/summary", timeout=30).json()
        assert after_summary["total_balance"] == pytest.approx(before_summary["total_balance"] + 100, abs=0.01)
        assert after_summary["accounts_with_balance"] == before_summary["accounts_with_balance"] + 1

    def test_overdraft_is_rejected_in_greek_without_mutation(self, auth_client, trust_resources):
        client_id = trust_resources["client"]["id"]
        before = auth_client.get(f"{API}/trust/accounts/{client_id}", timeout=30).json()
        response = auth_client.post(
            f"{API}/trust/transactions",
            json={"client_id": client_id, "type": "withdrawal", "amount": 1000, "date": "2026-07-29", "description": "TEST_OVERDRAFT"},
            timeout=30,
        )
        assert response.status_code == 400, response.text
        assert "Ανεπαρκές υπόλοιπο" in response.json()["detail"]
        after = auth_client.get(f"{API}/trust/accounts/{client_id}", timeout=30).json()
        assert after["account"]["balance"] == before["account"]["balance"]
        assert len(after["transactions"]) == len(before["transactions"])
        assert not any(tx["description"] == "TEST_OVERDRAFT" for tx in after["transactions"])

    def test_apply_to_invoice_marks_fully_covered_invoice_paid(self, auth_client, trust_resources):
        client_id = trust_resources["client"]["id"]
        invoice = trust_resources["invoice"]
        assert invoice["total"] == 62
        response = auth_client.post(
            f"{API}/trust/transactions",
            json={
                "client_id": client_id,
                "type": "apply_to_invoice",
                "amount": invoice["total"],
                "date": "2026-07-30",
                "description": "TEST_PHASEA εξόφληση",
                "invoice_id": invoice["id"],
            },
            timeout=30,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["account"]["balance"] == 38
        assert body["transaction"]["invoice_id"] == invoice["id"]
        assert body["transaction"]["invoice_number"] == invoice["number"]
        persisted_invoice = auth_client.get(f"{API}/invoices/{invoice['id']}", timeout=30)
        assert persisted_invoice.status_code == 200
        assert persisted_invoice.json()["status"] == "paid"
        assert persisted_invoice.json()["paid_at"]


# CSV/XLSX imports create valid clients and skip duplicate AFM/name rows.
class TestClientImport:
    def test_template_is_downloadable_utf8_csv_with_greek_columns(self, auth_client):
        response = auth_client.get(f"{API}/import/clients/template", timeout=30)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        assert "clients_template.csv" in response.headers.get("content-disposition", "")
        text = response.content.decode("utf-8-sig")
        header = next(csv.reader(io.StringIO(text)))
        assert header == ["Ονομα", "ΑΦΜ", "ΔΟΥ", "Email", "Τηλέφωνο", "Διεύθυνση", "Τύπος"]
        assert len(list(csv.reader(io.StringIO(text)))) >= 3

    def test_csv_import_creates_and_skips_duplicates(self, auth_client, test_state):
        suffix = uuid.uuid4().hex[:7]
        name_one = f"TEST_IMPORT_A_{suffix}"
        name_two = f"TEST_IMPORT_B_{suffix}"
        afm_one = f"81{suffix}"
        afm_two = f"82{suffix}"
        test_state["client_names"].extend([name_one, name_two, f"TEST_IMPORT_DUPAFM_{suffix}"])
        test_state["client_afms"].extend([afm_one, afm_two, f"83{suffix}"])
        csv_bytes = (
            "Όνομα,ΑΦΜ,ΔΟΥ,Email,Τηλέφωνο,Διεύθυνση,Τύπος\n"
            f"{name_one},{afm_one},ΑΘΗΝΩΝ,a-{suffix}@example.test,2100000001,Αθήνα,individual\n"
            f"{name_two},{afm_two},ΠΕΙΡΑΙΑ,b-{suffix}@example.test,2100000002,Πειραιά,company\n"
            f"TEST_IMPORT_DUPAFM_{suffix},{afm_one},ΑΘΗΝΩΝ,dup@example.test,,,company\n"
            f"{name_two},83{suffix},ΑΘΗΝΩΝ,dupname@example.test,,,individual\n"
            f",84{suffix},ΑΘΗΝΩΝ,missing@example.test,,,individual\n"
        ).encode("utf-8")
        response = auth_client.post(
            f"{API}/import/clients",
            files={"file": (f"TEST_clients_{suffix}.csv", csv_bytes, "text/csv")},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] == 2
        assert body["skipped"] == 3
        assert body["total_rows"] == 5
        assert isinstance(body["errors"], list) and len(body["errors"]) == 1
        assert "λείπει όνομα" in body["errors"][0]
        assert body["message"] == "Εισήχθησαν 2 νέοι πελάτες, παραλείφθηκαν 3."

        clients = auth_client.get(f"{API}/clients", timeout=30).json()
        created = [c for c in clients if c["name"] in (name_one, name_two)]
        assert len(created) == 2
        assert {c["afm"] for c in created} == {afm_one, afm_two}
        assert {c["client_type"] for c in created} == {"individual", "company"}
        test_state["client_ids"].extend(c["id"] for c in created)

    def test_xlsx_import_creates_client(self, auth_client, test_state):
        suffix = uuid.uuid4().hex[:7]
        name = f"TEST_XLSX_{suffix}"
        afm = f"91{suffix}"
        test_state["client_names"].append(name)
        test_state["client_afms"].append(afm)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Όνομα", "ΑΦΜ", "ΔΟΥ", "Email", "Τηλέφωνο", "Διεύθυνση", "Τύπος"])
        sheet.append([name, afm, "ΑΘΗΝΩΝ", f"xlsx-{suffix}@example.test", "2101111111", "Αθήνα", "εταιρεία"])
        stream = io.BytesIO()
        workbook.save(stream)
        response = auth_client.post(
            f"{API}/import/clients",
            files={"file": (f"TEST_clients_{suffix}.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert response.json()["created"] == 1
        assert response.json()["skipped"] == 0
        assert response.json()["total_rows"] == 1
        clients = auth_client.get(f"{API}/clients", timeout=30).json()
        created = next(c for c in clients if c["name"] == name)
        assert created["afm"] == afm
        assert created["client_type"] == "company"
        test_state["client_ids"].append(created["id"])

    def test_invalid_extension_is_greek_400(self, auth_client):
        response = auth_client.post(
            f"{API}/import/clients",
            files={"file": ("TEST_clients.txt", b"name\nTEST", "text/plain")},
            timeout=30,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Υποστηρίζονται μόνο αρχεία .csv ή .xlsx"


# Admin backup/restore, role enforcement, and per-client GDPR portability archive.
class TestBackupAndGdpr:
    def test_export_zip_has_all_expected_json_files_and_metadata(self, auth_client):
        response = auth_client.get(f"{API}/backup/export", timeout=60)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        assert ".zip" in response.headers.get("content-disposition", "")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            data_files = [name for name in names if name.endswith(".json") and name != "_meta.json"]
            assert "_meta.json" in names
            assert len(data_files) >= 15, f"Expected at least 15 collection JSON files plus _meta.json, got {names}"
            metadata = json.loads(archive.read("_meta.json").decode("utf-8"))
            assert metadata["version"] == 1
            assert metadata["generated_by"] == _credentials()[0]
            assert isinstance(metadata["counts"], dict)
            assert set(metadata["counts"]) == {name[:-5] for name in data_files}

    def test_export_then_import_round_trip_upserts_without_errors(self, auth_client):
        exported = auth_client.get(f"{API}/backup/export", timeout=60)
        assert exported.status_code == 200, exported.text
        response = auth_client.post(
            f"{API}/backup/import",
            files={"file": ("TEST_roundtrip_backup.zip", exported.content, "application/zip")},
            timeout=120,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["message"] == "Η επαναφορά ολοκληρώθηκε."
        assert body["errors"] == []
        assert isinstance(body["restored"], dict)
        assert len(body["restored"]) >= 14
        assert all(isinstance(count, int) and count >= 0 for count in body["restored"].values())

    def test_non_admin_cannot_export_or_restore(self, test_state):
        suffix = uuid.uuid4().hex[:10]
        email = f"test_phasea_lawyer_{suffix}@example.com"
        password = "TEST_PhaseA@2026!"
        test_state["user_emails"].append(email)
        non_admin = requests.Session()
        registered = non_admin.post(
            f"{API}/auth/register",
            json={"email": email, "password": password, "name": "TEST Phase A Lawyer", "role": "lawyer"},
            timeout=30,
        )
        assert registered.status_code == 200, registered.text
        assert registered.json()["role"] == "lawyer"
        export_response = non_admin.get(f"{API}/backup/export", timeout=30)
        assert export_response.status_code == 403, export_response.text
        assert "διαχειριστή" in export_response.json()["detail"]

        memory_zip = io.BytesIO()
        with zipfile.ZipFile(memory_zip, "w") as archive:
            archive.writestr("_meta.json", "{}")
        import_response = non_admin.post(
            f"{API}/backup/import",
            files={"file": ("TEST_nonadmin.zip", memory_zip.getvalue(), "application/zip")},
            timeout=30,
        )
        assert import_response.status_code == 403, import_response.text
        assert "διαχειριστή" in import_response.json()["detail"]

    def test_gdpr_archive_contains_client_and_related_data(self, auth_client, trust_resources):
        client = trust_resources["client"]
        response = auth_client.get(f"{API}/backup/gdpr/client/{client['id']}", timeout=60)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {"data.json", "_readme.txt"}
            dataset = json.loads(archive.read("data.json").decode("utf-8"))
            assert dataset["client"]["_id"] == {"__oid__": client["id"]}
            assert dataset["client"]["name"] == client["name"]
            assert any(inv["_id"] == {"__oid__": trust_resources["invoice"]["id"]} for inv in dataset["invoices"])
            assert len(dataset["trust_accounts"]) == 1
            assert len(dataset["trust_transactions"]) == 2
            readme = archive.read("_readme.txt").decode("utf-8")
            assert client["name"] in readme
            assert "GDPR" in readme and "Άρθρα 15 & 20" in readme
