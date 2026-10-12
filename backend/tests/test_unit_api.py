"""HTTP-level tests through FastAPI's TestClient with the in-memory database."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    # https base_url so the Secure auth cookies are stored by the test client
    with TestClient(server.app, base_url="https://testserver") as c:  # runs startup against mongomock
        yield c


def _login(client):
    r = client.post("/api/auth/register", json={"email": "owner@example.gr", "password": "Str0ngPass!", "name": "Owner"})
    assert r.status_code == 200, r.text
    # registration creates a non-admin lawyer in a fresh firm; promote for settings PATCH
    import asyncio
    from db import db
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        db.users.update_one({"email": "owner@example.gr"}, {"$set": {"role": "admin"}}))
    return r.json()


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["db"] is True


def test_settings_endpoint_works_and_is_tenant_scoped(client):
    _login(client)
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text  # was a NameError/500 before the tenant import fix
    assert r.json()["autopilot_enabled"] is True
    r = client.patch("/api/settings", json={"reminder_after_days": 20, "aade_user_id": "ignored?"})
    assert r.status_code == 200 and r.json()["reminder_after_days"] == 20
    assert r.json()["mydata_connected"] is False


def test_issue_without_aade_credentials_stays_pending_not_synced(client):
    _login(client)
    c = client.post("/api/clients", json={"name": "Πελάτης Α", "afm": "123456783", "email": ""}).json()
    inv = client.post("/api/invoices", json={"client_id": c["id"], "date": "2026-09-01",
                                              "line_items": [{"description": "Συμβουλευτική", "quantity": 1, "unit_price": 100, "vat_rate": 24}]}).json()
    assert inv["number"] == "" and inv["total"] == 124      # το πρόχειρο δεν έχει αριθμό
    issued = client.post(f"/api/invoices/{inv['id']}/issue").json()
    assert issued["status"] == "issued"
    assert issued["number"].startswith("ΤΠΥ-")               # ο αριθμός δίνεται στην έκδοση
    assert issued["mydata_status"] == "pending"      # no fake «synced» MOCK mark any more
    assert issued["mydata_mark"] is None
    # client has no email → autopilot raised a decision instead of failing the request
    items = client.get("/api/automation/notifications").json()
    assert any(n["type"] == "invoice_email_failed" and n["requires_action"] for n in items)
    status = client.get("/api/automation/status").json()
    assert status["open_items"] >= 1
    # re-issuing is rejected
    assert client.post(f"/api/invoices/{inv['id']}/issue").status_code == 400


def test_run_now_and_runs_history(client):
    _login(client)
    r = client.post("/api/automation/run", json={"jobs": ["stale_drafts", "vat_periods"]})
    assert r.status_code == 200
    assert {x["job"] for x in r.json()["results"]} == {"stale_drafts", "vat_periods"}
    runs = client.get("/api/automation/runs").json()
    assert len(runs) == 2 and all(run["label"] for run in runs)
    assert client.post("/api/automation/run", json={"jobs": ["nope"]}).status_code == 400


def test_ai_features_return_503_without_provider(client):
    _login(client)
    r = client.post("/api/documents/ai-draft", json={"instruction": "όχληση"})
    assert r.status_code in (400, 503)
    assert "AI" in r.json()["detail"] or "LLM" in r.json()["detail"]


def test_invoice_types_endpoint(client):
    _login(client)
    types = client.get("/api/invoices/types/list").json()
    codes = {t["code"] for t in types}
    assert {"2.1", "11.2", "5.1"} <= codes
    assert all(t["label"] for t in types)


def test_cancel_and_credit_note_endpoints_enforce_workflow(client):
    _login(client)
    c = client.post("/api/clients", json={"name": "Πελάτης Α", "afm": "123456783", "client_type": "company"}).json()
    body = {"client_id": c["id"], "date": "2026-09-01",
            "line_items": [{"description": "Συμβουλευτική", "quantity": 1, "unit_price": 100, "vat_rate": 24}]}
    inv = client.post("/api/invoices", json=body).json()
    client.post(f"/api/invoices/{inv['id']}/issue")

    # χωρίς MARK (καμία διαβίβαση στην ΑΑΔΕ) το πιστωτικό απορρίπτεται με σαφές μήνυμα
    r = client.post(f"/api/invoices/{inv['id']}/credit-note", json={"reason": "λάθος"})
    assert r.status_code == 400 and "MARK" in r.json()["detail"]

    # ακύρωση εκδοθέντος χωρίς MARK: τοπική, χωρίς κλήση ΑΑΔΕ
    r = client.post(f"/api/invoices/{inv['id']}/cancel", json={"reason": "διπλοεγγραφή"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # δεύτερη ακύρωση απορρίπτεται
    assert client.post(f"/api/invoices/{inv['id']}/cancel", json={}).status_code == 400


def test_credit_note_amount_must_be_positive(client):
    _login(client)
    c = client.post("/api/clients", json={"name": "Πελάτης Β", "afm": "123456783", "client_type": "company"}).json()
    inv = client.post("/api/invoices", json={"client_id": c["id"], "date": "2026-09-01",
                                             "line_items": [{"description": "x", "quantity": 1, "unit_price": 100, "vat_rate": 24}]}).json()
    r = client.post(f"/api/invoices/{inv['id']}/credit-note", json={"amount": -5})
    assert r.status_code == 400 and "θετικό" in r.json()["detail"]


def test_automation_status_reports_mydata_expense_capability(client):
    _login(client)
    status = client.get("/api/automation/status").json()
    assert "mydata_expenses" in status["capabilities"]
    assert status["capabilities"]["mydata_expenses"] is False  # χωρίς credentials ΑΑΔΕ
    assert "mydata_expenses" in status["jobs"]
    assert status["jobs"]["mydata_expenses"]["label"] == "Λήψη εξόδων από myDATA"
    assert "expenses_from_mydata" in status["last_7_days"]
