"""API and helper tests for invoice payment status, reminders, KPIs, and regressions."""
import asyncio
import re
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

FRONTEND_ENV = dotenv_values("/app/frontend/.env")
BASE_URL = (FRONTEND_ENV.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL is missing from /app/frontend/.env")
API = f"{BASE_URL}/api"


def _credentials():
    content = Path("/app/memory/test_credentials.md").read_text(encoding="utf-8")
    email = re.search(r"(?im)^\s*[-*]\s*Email:\s*([^\s]+)", content)
    password = re.search(r"(?im)^\s*[-*]\s*Password:\s*([^\s]+)", content)
    if not email or not password:
        pytest.skip("Admin credentials missing from /app/memory/test_credentials.md")
    return email.group(1), password.group(1)


@pytest.fixture(scope="module")
def auth_client():
    session = requests.Session()
    email, password = _credentials()
    response = session.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert response.status_code == 200, response.text
    assert response.json()["email"] == email
    return session


@pytest.fixture(scope="module")
def invoice_data(auth_client):
    created_invoice_ids = []
    created_client_ids = []

    def create_client(name, email=""):
        response = auth_client.post(
            f"{API}/clients",
            json={"name": name, "email": email, "afm": "", "client_type": "individual"},
            timeout=20,
        )
        assert response.status_code == 200, response.text
        created_client_ids.append(response.json()["id"])
        return response.json()

    def create_invoice(client_id, suffix, status):
        response = auth_client.post(
            f"{API}/invoices",
            json={
                "client_id": client_id,
                "date": "2026-01-15",
                "line_items": [{"description": f"TEST_{suffix}", "quantity": 1, "unit_price": 100, "vat_rate": 24}],
                "status": status,
            },
            timeout=20,
        )
        assert response.status_code == 200, response.text
        created_invoice_ids.append(response.json()["id"])
        return response.json()

    no_email = create_client("TEST_Reminder_NoEmail")
    with_email = create_client("TEST_Reminder_WithEmail", "qa-reminder@example.test")
    data = {
        "issued": create_invoice(no_email["id"], "ISSUED_NO_EMAIL", "issued"),
        "issued_email": create_invoice(with_email["id"], "ISSUED_EMAIL", "issued"),
        "draft": create_invoice(with_email["id"], "DRAFT_REMINDER", "draft"),
        "draft_payment": create_invoice(with_email["id"], "DRAFT_PAYMENT", "draft"),
        "paid": create_invoice(with_email["id"], "PAID", "paid"),
        "cancelled": create_invoice(with_email["id"], "CANCELLED", "cancelled"),
    }
    yield data

    for invoice_id in created_invoice_ids:
        auth_client.delete(f"{API}/invoices/{invoice_id}", timeout=20)
    for client_id in created_client_ids:
        auth_client.delete(f"{API}/clients/{client_id}", timeout=20)


def _listed_invoice(auth_client, invoice_id):
    response = auth_client.get(f"{API}/invoices", timeout=20)
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == invoice_id)


# Payment state transition and persistence tests.
class TestInvoicePaymentState:
    def test_mark_paid_issued_sets_paid_at_and_get_persists(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/mark-paid", timeout=20)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "paid"
        assert isinstance(body["paid_at"], str) and body["paid_at"]

        get_response = auth_client.get(f"{API}/invoices/{invoice_id}", timeout=20)
        assert get_response.status_code == 200, get_response.text
        persisted = get_response.json()
        assert persisted["id"] == invoice_id
        assert persisted["status"] == "paid"
        assert persisted["paid_at"] == body["paid_at"]

    def test_mark_paid_already_paid_is_greek_400(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/mark-paid", timeout=20)
        assert response.status_code == 400, response.text
        assert "ήδη εξοφλημένο" in response.json()["detail"]

    def test_mark_unpaid_restores_issued_and_clears_paid_at(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/mark-unpaid", timeout=20)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "issued"
        assert response.json()["paid_at"] is None
        persisted = _listed_invoice(auth_client, invoice_id)
        assert persisted["status"] == "issued"
        assert persisted["paid_at"] is None

    def test_mark_unpaid_non_paid_is_greek_400(self, auth_client, invoice_data):
        invoice_id = invoice_data["draft"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/mark-unpaid", timeout=20)
        assert response.status_code == 400, response.text
        assert "δεν είναι σε κατάσταση εξοφλημένο" in response.json()["detail"]

    def test_mark_paid_rejects_draft_because_unpaid_means_issued(self, auth_client, invoice_data):
        invoice_id = invoice_data["draft_payment"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/mark-paid", timeout=20)
        assert response.status_code == 400, response.text
        assert response.json()["detail"]


# Reminder eligibility and configuration errors.
class TestInvoiceReminderValidation:
    @pytest.mark.parametrize("key, expected", [
        ("draft", "Μόνο εκδοθέντα τιμολόγια"),
        ("cancelled", "Μόνο εκδοθέντα τιμολόγια"),
        ("paid", "ήδη εξοφλημένο"),
    ])
    def test_reminder_rejects_ineligible_statuses(self, auth_client, invoice_data, key, expected):
        invoice_id = invoice_data[key]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/send-reminder", timeout=20)
        assert response.status_code == 400, response.text
        assert expected in response.json()["detail"]

    def test_issued_without_client_email_returns_greek_400(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/send-reminder", timeout=20)
        assert response.status_code == 400, response.text
        assert "Ο πελάτης δεν έχει καταχωρημένο email" in response.json()["detail"]

    def test_issued_with_email_without_smtp_returns_greek_400(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued_email"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/send-reminder", timeout=20)
        assert response.status_code == 400, response.text
        assert "Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email" in response.json()["detail"]


# Dashboard unpaid KPI must exactly match issued invoices.
def test_dashboard_unpaid_summary_matches_invoice_list(auth_client, invoice_data):
    invoices_response = auth_client.get(f"{API}/invoices", timeout=20)
    summary_response = auth_client.get(f"{API}/dashboard/summary", timeout=20)
    assert invoices_response.status_code == 200
    assert summary_response.status_code == 200
    issued = [item for item in invoices_response.json() if item["status"] == "issued"]
    summary = summary_response.json()
    assert summary["unpaid_count"] == len(issued)
    assert summary["unpaid_total"] == round(sum(item["total"] for item in issued), 2)


# Existing invoice integrations and PDF attachment regression tests.
class TestInvoiceRegression:
    def test_pdf_export_returns_valid_pdf(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.get(f"{API}/invoices/{invoice_id}/pdf", timeout=30)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF")

    def test_send_email_validation_still_works(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued"]["id"]
        response = auth_client.post(f"{API}/invoices/{invoice_id}/send-email", timeout=20)
        assert response.status_code == 400
        assert "Ο πελάτης δεν έχει καταχωρημένο email" in response.json()["detail"]

    def test_mydata_mock_sync_still_works(self, auth_client, invoice_data):
        invoice_id = invoice_data["issued_email"]["id"]
        response = auth_client.post(f"{API}/mydata/sync/{invoice_id}", timeout=30)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "mock"
        assert body["mark"].startswith("MOCK-")

    def test_aade_test_connection_button_endpoint_still_validates(self, auth_client):
        response = auth_client.post(f"{API}/mydata/test-connection", timeout=30)
        assert response.status_code == 400, response.text
        assert "AADE" in response.json()["detail"] or "ΑΑΔΕ" in response.json()["detail"]

    def test_voice_parse_route_is_reachable_and_returns_greek_failure_if_audio_invalid(self, auth_client):
        response = auth_client.post(
            f"{API}/invoices/voice-parse",
            files={"audio": ("TEST_invalid.webm", b"not-valid-audio", "audio/webm")},
            timeout=60,
        )
        assert response.status_code in (200, 500), response.text
        if response.status_code == 500:
            assert "Αποτυχία επεξεργασίας φωνητικής εντολής" in response.json()["detail"]


def test_email_helper_attaches_pdf(monkeypatch):
    import email_utils

    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message

    monkeypatch.setattr(email_utils.aiosmtplib, "send", fake_send)
    asyncio.run(email_utils.send_email_with_pdf(
        {"smtp_user": "sender@example.test", "smtp_password": "secret", "smtp_from_name": "TEST"},
        "recipient@example.test",
        "TEST reminder",
        "text",
        "<p>html</p>",
        pdf_bytes=b"%PDF-1.4 TEST",
        pdf_filename="TEST-invoice.pdf",
    ))
    attachments = list(captured["message"].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "application/pdf"
    assert attachments[0].get_filename() == "TEST-invoice.pdf"
