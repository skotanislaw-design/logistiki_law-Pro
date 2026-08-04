"""Focused regression tests for invoice fixes: GET, payment states, email build, and numbering."""
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
def invoice_factory(auth_client):
    invoice_ids = []
    client_response = auth_client.post(
        f"{API}/clients",
        json={
            "name": "TEST_Invoice_Fixes_Client",
            "email": "invoice-fixes@example.test",
            "afm": "",
            "client_type": "individual",
        },
        timeout=20,
    )
    assert client_response.status_code == 200, client_response.text
    client_id = client_response.json()["id"]

    def create(status="draft", suffix="FIX"):
        response = auth_client.post(
            f"{API}/invoices",
            json={
                "client_id": client_id,
                "date": "2026-07-20",
                "line_items": [
                    {"description": f"TEST_{suffix}", "quantity": 2, "unit_price": 50, "vat_rate": 24}
                ],
                "payment_method": "Τραπεζική κατάθεση",
                "notes": f"TEST_{suffix}",
                "status": status,
            },
            timeout=20,
        )
        assert response.status_code == 200, response.text
        invoice_ids.append(response.json()["id"])
        return response.json()

    yield create

    for invoice_id in invoice_ids:
        auth_client.delete(f"{API}/invoices/{invoice_id}", timeout=20)
    auth_client.delete(f"{API}/clients/{client_id}", timeout=20)


# GET-by-id returns the complete persisted invoice and Greek 404 responses.
class TestGetInvoice:
    def test_get_existing_invoice_has_correct_fields(self, auth_client, invoice_factory):
        created = invoice_factory("draft", "GET_FIELDS")
        response = auth_client.get(f"{API}/invoices/{created['id']}", timeout=20)
        assert response.status_code == 200, response.text
        body = response.json()
        expected = {
            "id": created["id"],
            "number": created["number"],
            "client_id": created["client_id"],
            "client_name": "TEST_Invoice_Fixes_Client",
            "date": "2026-07-20",
            "status": "draft",
            "subtotal": 100.0,
            "vat_amount": 24.0,
            "total": 124.0,
            "notes": "TEST_GET_FIELDS",
        }
        for field, value in expected.items():
            assert body[field] == value
        assert body["line_items"][0] == {
            "description": "TEST_GET_FIELDS", "quantity": 2.0, "unit_price": 50.0, "vat_rate": 24
        }
        assert "_id" not in body

    @pytest.mark.parametrize("invoice_id", ["not-an-object-id", "000000000000000000000000"])
    def test_get_invalid_or_missing_invoice_is_greek_404(self, auth_client, invoice_id):
        response = auth_client.get(f"{API}/invoices/{invoice_id}", timeout=20)
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "Το τιμολόγιο δεν βρέθηκε"}


# mark-paid is legal only from issued and returns strict Greek validation messages.
class TestMarkPaidStateMachine:
    def test_issued_is_marked_paid_and_persisted(self, auth_client, invoice_factory):
        created = invoice_factory("issued", "MARK_ISSUED")
        response = auth_client.post(f"{API}/invoices/{created['id']}/mark-paid", timeout=20)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "paid"
        assert response.json()["paid_at"]
        persisted = auth_client.get(f"{API}/invoices/{created['id']}", timeout=20)
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "paid"
        assert persisted.json()["paid_at"] == response.json()["paid_at"]

    def test_draft_rejected_with_exact_required_message(self, auth_client, invoice_factory):
        created = invoice_factory("draft", "MARK_DRAFT")
        response = auth_client.post(f"{API}/invoices/{created['id']}/mark-paid", timeout=20)
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "Μόνο εκδοθέντα τιμολόγια μπορούν να σημανθούν ως εξοφλημένα"

    @pytest.mark.parametrize("status, expected_fragment", [
        ("cancelled", "Μόνο εκδοθέντα τιμολόγια"),
        ("paid", "ήδη εξοφλημένο"),
    ])
    def test_cancelled_and_paid_are_rejected(self, auth_client, invoice_factory, status, expected_fragment):
        created = invoice_factory(status, f"MARK_{status.upper()}")
        response = auth_client.post(f"{API}/invoices/{created['id']}/mark-paid", timeout=20)
        assert response.status_code == 400, response.text
        assert expected_fragment in response.json()["detail"]
        persisted = auth_client.get(f"{API}/invoices/{created['id']}", timeout=20).json()
        assert persisted["status"] == status


# Number allocation remains monotonic after deletion and over sequential creates.
def test_atomic_invoice_numbering_after_delete_and_sequential_creates(auth_client, invoice_factory):
    first = invoice_factory("draft", "NUMBER_DELETE")
    first_seq = int(first["number"].rsplit("-", 1)[1])
    delete_response = auth_client.delete(f"{API}/invoices/{first['id']}", timeout=20)
    assert delete_response.status_code == 200, delete_response.text

    after_delete = invoice_factory("draft", "NUMBER_AFTER_DELETE")
    sequential = [after_delete] + [invoice_factory("draft", f"NUMBER_SEQ_{index}") for index in range(3)]
    sequences = [int(item["number"].rsplit("-", 1)[1]) for item in sequential]

    assert sequences[0] == first_seq + 1
    assert sequences == list(range(sequences[0], sequences[0] + 4))
    assert len({item["number"] for item in [first] + sequential}) == 5


# EmailMessage construction serializes successfully and reaches the mocked SMTP call.
def test_email_message_build_reaches_smtp_and_serializes(monkeypatch):
    import email_utils

    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs
        captured["bytes"] = message.as_bytes()

    monkeypatch.setattr(email_utils.aiosmtplib, "send", fake_send)
    asyncio.run(email_utils.send_email_with_pdf(
        {
            "smtp_user": "sender@example.test",
            "smtp_password": "secret",
            "smtp_from_name": "TEST Αποστολέας",
            "smtp_host": "smtp.example.test",
            "smtp_port": 587,
        },
        "recipient@example.test",
        "Υπενθύμιση εξόφλησης — Τιμολόγιο ΤΠΥ-2026-9999",
        "Κείμενο δοκιμής",
        "<p>Κείμενο δοκιμής</p>",
        pdf_bytes=b"%PDF-1.4 TEST",
        pdf_filename="TEST-invoice.pdf",
    ))

    assert captured["message"]["Subject"] == "Υπενθύμιση εξόφλησης — Τιμολόγιο ΤΠΥ-2026-9999"
    assert isinstance(captured["bytes"], bytes) and len(captured["bytes"]) > 100
    assert b"TEST-invoice.pdf" in captured["bytes"]
    assert captured["kwargs"]["hostname"] == "smtp.example.test"
