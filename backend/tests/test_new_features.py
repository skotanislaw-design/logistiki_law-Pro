"""Tests for new features: myDATA live/mock, dashboard extension, Cases ΓΑΚ/ΕΑΚ."""
import os
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "https://logistiki-pro.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@logistiki.gr"
ADMIN_PASSWORD = "Admin@2026!"


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return s


def _get_settings(auth):
    r = auth.get(f"{API}/settings")
    assert r.status_code == 200, r.text
    return r.json()


def _put_settings(auth, **overrides):
    """PUT settings preserving all fields, applying overrides."""
    current = _get_settings(auth)
    current.pop("id", None)
    current.pop("updated_at", None)
    current.pop("mydata_connected", None)
    current.update(overrides)
    r = auth.put(f"{API}/settings", json=current)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module", autouse=True)
def restore_mock_at_end(auth):
    """Ensure at end of module tests, credentials cleared -> mock mode."""
    yield
    _put_settings(auth, aade_user_id="", aade_subscription_key="", aade_environment="sandbox")


# ---------- myDATA status (mock initially) ----------
class TestMyDataStatusMock:
    def test_ensure_mock_mode(self, auth):
        _put_settings(auth, aade_user_id="", aade_subscription_key="", aade_environment="sandbox")
        r = auth.get(f"{API}/mydata/status")
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "mock"
        assert d["environment"] == "sandbox"
        assert d["connected"] is False
        assert "pending_count" in d
        assert "failed_count" in d
        assert "synced_count" in d
        assert isinstance(d["pending_count"], int)


# ---------- test-connection ----------
class TestMyDataTestConnection:
    def test_test_connection_no_creds_400(self, auth):
        _put_settings(auth, aade_user_id="", aade_subscription_key="")
        r = auth.post(f"{API}/mydata/test-connection")
        assert r.status_code == 400
        # Greek error message
        assert "AADE" in r.json()["detail"] or "ΑΑΔΕ" in r.json()["detail"] or "Ρυθμίσ" in r.json()["detail"]

    def test_save_creds_then_status_and_test_conn_fails_gracefully(self, auth):
        s = _put_settings(auth, aade_user_id="testuser", aade_subscription_key="testkey", aade_environment="sandbox")
        assert s["mydata_connected"] is True
        r = auth.get(f"{API}/mydata/status")
        assert r.status_code == 200
        assert r.json()["mode"] == "live"
        # test-connection: expected 400 with Greek error (invalid creds -> AADE returns 401)
        r2 = auth.post(f"{API}/mydata/test-connection")
        assert r2.status_code == 400, r2.text
        detail = r2.json()["detail"]
        # Should mention ΑΑΔΕ credentials issue in Greek
        assert "ΑΑΔΕ" in detail or "διαπιστευτ" in detail or "AADE" in detail

    def test_cleanup_back_to_mock(self, auth):
        _put_settings(auth, aade_user_id="", aade_subscription_key="")
        r = auth.get(f"{API}/mydata/status")
        assert r.json()["mode"] == "mock"


# ---------- Sync invoice mock + live ----------
class TestMyDataSync:
    @pytest.fixture(scope="class")
    def invoice_id(self, auth):
        # need a client with ΑΦΜ
        rc = auth.post(f"{API}/clients", json={
            "name": "TEST_ SyncClient", "afm": "123456783", "client_type": "company"
        })
        assert rc.status_code == 200
        cid = rc.json()["id"]
        ri = auth.post(f"{API}/invoices", json={
            "client_id": cid, "date": "2026-01-15",
            "line_items": [{"description": "svc", "quantity": 1, "unit_price": 100, "vat_rate": 24}],
        })
        assert ri.status_code == 200
        return ri.json()["id"]

    def test_sync_mock_mode(self, auth, invoice_id):
        _put_settings(auth, aade_user_id="", aade_subscription_key="")
        r = auth.post(f"{API}/mydata/sync/{invoice_id}")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["mode"] == "mock"
        assert d["mark"].startswith("MOCK-")
        # verify invoice updated
        inv = next(i for i in auth.get(f"{API}/invoices").json() if i["id"] == invoice_id)
        assert inv["mydata_status"] == "synced"
        assert inv["mydata_mark"].startswith("MOCK-")

    def test_sync_live_fails_and_marks_failed(self, auth, invoice_id):
        # Save AFM to settings so we get to AADE call
        _put_settings(auth, aade_user_id="testuser", aade_subscription_key="testkey",
                      aade_environment="sandbox", afm="123456783")
        r = auth.post(f"{API}/mydata/sync/{invoice_id}")
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert len(detail) > 0
        # invoice should have status=failed with error populated
        inv = next(i for i in auth.get(f"{API}/invoices").json() if i["id"] == invoice_id)
        assert inv["mydata_status"] == "failed"
        assert inv.get("mydata_error")

    def test_re_sync_mock_recovers(self, auth, invoice_id):
        _put_settings(auth, aade_user_id="", aade_subscription_key="")
        r = auth.post(f"{API}/mydata/sync/{invoice_id}")
        assert r.status_code == 200
        assert r.json()["mode"] == "mock"
        inv = next(i for i in auth.get(f"{API}/invoices").json() if i["id"] == invoice_id)
        assert inv["mydata_status"] == "synced"
        assert inv["mydata_mark"].startswith("MOCK-")


# ---------- Issue invoice in mock mode ----------
class TestIssueMock:
    def test_issue_draft_mock(self, auth):
        _put_settings(auth, aade_user_id="", aade_subscription_key="")
        rc = auth.post(f"{API}/clients", json={
            "name": "TEST_ IssueMock", "afm": "222333440", "client_type": "company"
        })
        cid = rc.json()["id"]
        ri = auth.post(f"{API}/invoices", json={
            "client_id": cid, "date": "2026-01-20",
            "line_items": [{"description": "svc", "quantity": 1, "unit_price": 200, "vat_rate": 24}],
        })
        iid = ri.json()["id"]
        r = auth.post(f"{API}/invoices/{iid}/issue")
        assert r.status_code == 200, r.text
        inv = r.json()
        assert inv["status"] == "issued"
        assert inv["mydata_status"] == "synced"
        assert inv["mydata_mark"].startswith("MOCK-")


# ---------- Dashboard summary extensions ----------
class TestDashboardExtension:
    def test_extended_fields(self, auth):
        r = auth.get(f"{API}/dashboard/summary")
        assert r.status_code == 200
        d = r.json()
        for k in ("year_income", "year_expense", "year_profit",
                  "unpaid_total", "unpaid_count", "advice", "obligations"):
            assert k in d, f"missing {k}"
        assert isinstance(d["advice"], str) and len(d["advice"]) > 0
        assert isinstance(d["obligations"], list)
        # sorted by date
        dates = [o["date"] for o in d["obligations"]]
        assert dates == sorted(dates)
        # legacy fields still there
        for k in ("income_total", "expense_total", "chart_data"):
            assert k in d


# ---------- Cases ΓΑΚ/ΕΑΚ ----------
class TestCasesSolonFields:
    def test_create_update_delete_with_solon(self, auth):
        # Need client
        rc = auth.post(f"{API}/clients", json={
            "name": "TEST_ CaseClient", "afm": "555666770", "client_type": "individual"
        })
        cid = rc.json()["id"]
        rcase = auth.post(f"{API}/cases", json={
            "title": "TEST_SOLON", "case_type": "Αστικό",
            "client_id": cid, "client_name": "TEST_ CaseClient",
            "gak": "123", "eak": "45", "gak_year": "2026",
        })
        assert rcase.status_code == 200, rcase.text
        case = rcase.json()
        assert case["gak"] == "123"
        assert case["eak"] == "45"
        assert case["gak_year"] == "2026"
        case_id = case["id"]

        # verify persisted via GET
        rg = auth.get(f"{API}/cases/{case_id}")
        assert rg.status_code == 200
        assert rg.json()["case"]["gak"] == "123"

        # update
        ru = auth.put(f"{API}/cases/{case_id}", json={
            **{k: case.get(k, "") for k in ("title", "case_type", "client_id", "client_name",
                                             "court", "opposing_party", "status")},
            "gak": "999", "eak": "77", "gak_year": "2025",
        })
        assert ru.status_code == 200, ru.text
        assert ru.json()["gak"] == "999"
        assert ru.json()["eak"] == "77"
        assert ru.json()["gak_year"] == "2025"

        # delete
        rd = auth.delete(f"{API}/cases/{case_id}")
        assert rd.status_code == 200
