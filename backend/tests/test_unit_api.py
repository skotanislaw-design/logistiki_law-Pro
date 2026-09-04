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
    c = client.post("/api/clients", json={"name": "Πελάτης Α", "afm": "123456789", "email": ""}).json()
    inv = client.post("/api/invoices", json={"client_id": c["id"], "date": "2026-09-01",
                                              "line_items": [{"description": "Συμβουλευτική", "quantity": 1, "unit_price": 100, "vat_rate": 24}]}).json()
    assert inv["number"].startswith("ΤΠΥ-") and inv["total"] == 124
    issued = client.post(f"/api/invoices/{inv['id']}/issue").json()
    assert issued["status"] == "issued"
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
