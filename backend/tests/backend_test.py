"""Backend API tests for Logistiki Pro - Greek Law Firm accounting MVP."""
import os
import io
import base64
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://logistiki-pro.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@logistiki.gr"
ADMIN_PASSWORD = "Admin@2026!"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    return s


@pytest.fixture(scope="module")
def auth_client(client):
    r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data.get("email") == ADMIN_EMAIL
    assert "id" in data and "_id" not in data
    return client


# ---------- Auth ----------
class TestAuth:
    def test_login_success_sets_cookies(self, client):
        r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        assert "access_token" in r.cookies
        assert "refresh_token" in r.cookies

    def test_login_invalid(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code in (401, 429)

    def test_me(self, auth_client):
        r = auth_client.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_me_unauth(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401


# ---------- Clients CRUD ----------
class TestClients:
    def test_create_list_update_delete(self, auth_client):
        r = auth_client.post(f"{API}/clients", json={
            "name": "TEST_ Πελάτης ΑΕ", "afm": "123456789", "client_type": "company", "doy": "Α ΑΘΗΝΩΝ"
        })
        assert r.status_code == 200, r.text
        c = r.json()
        assert "id" in c and "_id" not in c
        assert c["name"] == "TEST_ Πελάτης ΑΕ"
        cid = c["id"]

        r2 = auth_client.get(f"{API}/clients")
        assert r2.status_code == 200
        assert any(x["id"] == cid for x in r2.json())

        r3 = auth_client.put(f"{API}/clients/{cid}", json={
            "name": "TEST_ Πελάτης ΑΕ Updated", "afm": "123456789", "client_type": "company"
        })
        assert r3.status_code == 200
        assert r3.json()["name"] == "TEST_ Πελάτης ΑΕ Updated"

        r4 = auth_client.delete(f"{API}/clients/{cid}")
        assert r4.status_code == 200


# ---------- Invoices ----------
class TestInvoices:
    @pytest.fixture(scope="class")
    def client_id(self, auth_client):
        r = auth_client.post(f"{API}/clients", json={
            "name": "TEST_ Πελάτης Τιμολ.", "afm": "111222333", "client_type": "company"
        })
        assert r.status_code == 200
        return r.json()["id"]

    def test_create_invoice_totals(self, auth_client, client_id):
        payload = {
            "client_id": client_id,
            "date": "2026-01-15",
            "line_items": [
                {"description": "Νομικές υπηρεσίες", "quantity": 2, "unit_price": 500, "vat_rate": 24},
                {"description": "Έξοδα φακέλου", "quantity": 1, "unit_price": 100, "vat_rate": 24},
            ],
        }
        r = auth_client.post(f"{API}/invoices", json=payload)
        assert r.status_code == 200, r.text
        inv = r.json()
        assert "id" in inv and "_id" not in inv
        assert inv["subtotal"] == 1100.0
        assert inv["vat_amount"] == 264.0
        assert inv["total"] == 1364.0
        assert inv["status"] == "draft"
        assert inv["mydata_status"] == "not_sent"
        assert inv["number"].startswith("ΤΠΥ-2026-")
        # Save for issue test
        pytest.invoice_id = inv["id"]

    def test_issue_invoice_mock_mydata(self, auth_client):
        iid = pytest.invoice_id
        r = auth_client.post(f"{API}/invoices/{iid}/issue")
        assert r.status_code == 200, r.text
        inv = r.json()
        assert inv["status"] == "issued"
        assert inv["mydata_status"] == "synced"
        assert inv["mydata_mark"].startswith("MOCK-")
        assert inv["mydata_uid"].startswith("UID-")

    def test_list_invoices(self, auth_client):
        r = auth_client.get(f"{API}/invoices")
        assert r.status_code == 200
        for i in r.json():
            assert "id" in i and "_id" not in i


# ---------- Expenses ----------
class TestExpenses:
    def test_create_expense(self, auth_client):
        r = auth_client.post(f"{API}/expenses", json={
            "vendor": "TEST_ΔΕΗ", "date": "2026-01-10", "amount": 124.0,
            "vat_amount": 24.0, "vat_rate": 24, "category": "Λογαριασμοί ΔΕΚΟ",
            "description": "Ρεύμα Ιαν"
        })
        assert r.status_code == 200, r.text
        exp = r.json()
        assert "id" in exp and "_id" not in exp
        assert exp["vendor"] == "TEST_ΔΕΗ"

    def test_ocr_parse_endpoint_exists(self, auth_client):
        # tiny 1x1 png base64
        img = base64.b64encode(bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000D4944415478DA636400010000000500010D0A2DB40000000049454E44AE426082"
        )).decode()
        r = auth_client.post(f"{API}/expenses/ocr-parse", json={"image_base64": img})
        # AI may fail on 1x1 image, but endpoint should respond (not 404/401)
        assert r.status_code in (200, 500), f"unexpected: {r.status_code} {r.text[:200]}"


# ---------- Payroll ----------
class TestPayroll:
    def test_full_payroll_flow(self, auth_client):
        r = auth_client.post(f"{API}/payroll/employees", json={
            "name": "TEST_ Εργαζόμενος", "gross_salary": 2000.0, "dependents": 0, "position": "Δικηγόρος"
        })
        assert r.status_code == 200, r.text
        emp = r.json()
        assert "id" in emp and "_id" not in emp
        eid = emp["id"]

        r2 = auth_client.post(f"{API}/payroll/run", json={"employee_id": eid, "period": "2026-01"})
        assert r2.status_code == 200, r2.text
        run = r2.json()
        # gross 2000 * 0.1387 = 277.40
        assert abs(run["employee_efka"] - 277.40) < 0.1
        # employer 2000 * 0.2229 = 445.80
        assert abs(run["employer_efka"] - 445.80) < 0.1
        assert run["net_salary"] > 0
        assert run["net_salary"] < 2000
        assert run["income_tax"] >= 0

        r3 = auth_client.get(f"{API}/payroll/runs")
        assert r3.status_code == 200

    def test_duplicate_payroll_run_blocked(self, auth_client):
        r = auth_client.post(f"{API}/payroll/employees", json={
            "name": "TEST_ Dup", "gross_salary": 1500.0, "dependents": 1
        })
        eid = r.json()["id"]
        r1 = auth_client.post(f"{API}/payroll/run", json={"employee_id": eid, "period": "2026-02"})
        assert r1.status_code == 200
        r2 = auth_client.post(f"{API}/payroll/run", json={"employee_id": eid, "period": "2026-02"})
        assert r2.status_code == 400


# ---------- Dashboard ----------
class TestDashboard:
    def test_summary(self, auth_client):
        r = auth_client.get(f"{API}/dashboard/summary")
        assert r.status_code == 200
        d = r.json()
        for k in ["income_total", "expense_total", "output_vat", "input_vat", "vat_due",
                  "next_vat_deadline", "next_efka_deadline", "chart_data",
                  "recent_invoices", "recent_expenses"]:
            assert k in d
        assert len(d["chart_data"]) == 6


# ---------- Settings ----------
class TestSettings:
    def test_get_and_update(self, auth_client):
        r = auth_client.get(f"{API}/settings")
        assert r.status_code == 200
        s = r.json()
        assert "id" in s and "_id" not in s

        r2 = auth_client.put(f"{API}/settings", json={
            "company_name": "TEST Νομική ΕΠΕ", "afm": "999888777", "doy": "Α ΑΘΗΝΩΝ",
            "vat_period": "quarterly", "aade_user_id": "mockuser", "aade_subscription_key": "mockkey"
        })
        assert r2.status_code == 200, r2.text
        updated = r2.json()
        assert updated["vat_period"] == "quarterly"
        assert updated["mydata_connected"] is True

        # Restore monthly
        auth_client.put(f"{API}/settings", json={
            "company_name": "Δικηγορική Εταιρεία", "vat_period": "monthly",
            "aade_user_id": "", "aade_subscription_key": ""
        })


# ---------- myDATA ----------
class TestMyData:
    def test_status(self, auth_client):
        r = auth_client.get(f"{API}/mydata/status")
        assert r.status_code == 200
        d = r.json()
        assert "connected" in d
        assert d["mode"] in ("mock", "mock_connected")
        assert "note" in d


# ---------- Tax Calendar ----------
class TestTaxCalendar:
    def test_list(self, auth_client):
        r = auth_client.get(f"{API}/tax-calendar")
        assert r.status_code == 200
        d = r.json()
        assert len(d["deadlines"]) >= 3
        cats = {x["category"] for x in d["deadlines"]}
        assert {"vat", "efka", "income_tax"}.issubset(cats)


# ---------- Logout ----------
class TestLogout:
    def test_logout(self, client):
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        r2 = s.post(f"{API}/auth/logout")
        assert r2.status_code == 200
