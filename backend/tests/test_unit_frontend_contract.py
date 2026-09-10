"""Συμβόλαιο frontend ↔ backend: κάθε φόρμα του UI πρέπει να γίνεται δεκτή.

Ο έλεγχος βρήκε ότι η αποθήκευση ψηφιακής υπογραφής απαντούσε **πάντα** 422, επειδή το
μοντέλο απαιτούσε πεδίο (``signature_hash``) που το frontend δεν στέλνει και ο server
υπολογίζει μόνος του. Το feature ήταν ανενεργό από την πρώτη μέρα και καμία δοκιμή δεν
το έπιασε: τα unit tests καλούσαν το API με δικά τους payloads, όχι με αυτά της φόρμας.

Τα σχήματα εδώ αντιγράφονται **αυτούσια** από τα αρχικά states των φορμών:

    Clients.jsx:13  Expenses.jsx  Payroll.jsx  Cases.jsx:21  Kyc.jsx:22
    TrustAccounts.jsx:34  Drafting.jsx  Signatures.jsx:84
    components/GrammatioDialogs.jsx:11  components/HearingFormDialog.jsx:10
    components/ParavoloDialogs.jsx

Αν αλλάξει φόρμα χωρίς να αλλάξει το endpoint (ή το ανάποδο), σκάει εδώ.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        c.post("/api/auth/register",
               json={"email": "forms@example.gr", "password": "Str0ngPass!", "name": "Γραμματεία"})
        yield c


@pytest.fixture()
def client_id(client):
    return client.post("/api/clients", json={
        "name": "ΑΛΦΑ ΑΕ", "afm": "123456789", "doy": "ΦΑΕ ΑΘΗΝΩΝ",
        "address": "Σταδίου 1", "email": "info@alfa.gr", "phone": "2101234567",
        "client_type": "company",
    }).json()["id"]


@pytest.fixture()
def case_id(client, client_id):
    return client.post("/api/cases", json={
        "title": "ΑΛΦΑ ΑΕ κατά Βήτα ΕΠΕ", "case_type": "Αστικό", "client_id": client_id,
        "client_name": "ΑΛΦΑ ΑΕ", "court": "Μονομελές Πρωτοδικείο Αθηνών",
        "opposing_party": "Βήτα ΕΠΕ", "next_action": "Κατάθεση προτάσεων",
        "gak": "12345", "eak": "678", "gak_year": "2026",
    }).json()["id"]


def test_client_form_is_accepted(client, client_id):
    assert client_id


def test_expense_form_is_accepted(client):
    """Expenses.jsx στέλνει και receipt_image_url: null."""
    r = client.post("/api/expenses", json={
        "vendor": "ΟΤΕ ΑΕ", "date": "2026-09-10", "amount": 124.0, "vat_amount": 24.0,
        "vat_rate": 24, "category": "Λοιπά", "description": "Τηλεπικοινωνίες",
        "source": "manual", "receipt_image_url": None,
    })
    assert r.status_code == 200, r.text


def test_payroll_employee_form_is_accepted(client):
    r = client.post("/api/payroll/employees", json={
        "name": "Μαρία Παπαδοπούλου", "afm": "987654321", "amka": "01019012345",
        "position": "Δικηγόρος", "gross_salary": 1500, "dependents": 0,
        "hire_date": "2024-01-15", "active": True,
    })
    assert r.status_code == 200, r.text


def test_case_form_is_accepted(client, case_id):
    assert case_id


def test_grammatio_form_is_accepted(client, client_id):
    r = client.post("/api/grammatia", json={
        "court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική",
        "reference_amount": 1000.0, "contributions_amount": 60.0, "vat_amount": 14.4,
        "client_id": client_id, "client_name": "ΑΛΦΑ ΑΕ", "notes": "",
    })
    assert r.status_code == 200, r.text


def test_hearing_form_is_accepted(client, case_id):
    r = client.post("/api/hearings", json={
        "court": "Μονομελές Πρωτοδικείο Αθηνών", "room": "Αίθουσα 3", "date": "2026-11-20",
        # HearingFormDialog.jsx:31 στέλνει Number(form.pinakio_number): είναι ο αύξων
        # αριθμός σειράς στο πινάκιο, όχι ετικέτα — το backend υπολογίζει πόσα απομένουν.
        "time": "09:00", "pinakio_number": 14, "notify_threshold": 5,
        "case_id": case_id, "case_title": "ΑΛΦΑ ΑΕ κατά Βήτα ΕΠΕ",
    })
    assert r.status_code == 200, r.text


def test_paravolo_form_is_accepted(client):
    r = client.post("/api/paravola", json={
        "category": "Έφεση", "description": "Παράβολο έφεσης", "amount": 150.0,
    })
    assert r.status_code == 200, r.text


def test_kyc_form_is_accepted(client, client_id):
    r = client.post("/api/kyc/records", json={
        "client_id": client_id, "client_name": "ΑΛΦΑ ΑΕ", "id_type": "id_card",
        "id_number": "ΑΒ123456", "id_issue_date": "2020-05-10", "id_issuer": "Α.Τ. Αθηνών",
        "birth_date": "1980-03-01", "nationality": "Ελληνική", "profession": "Επιχειρηματίας",
        "pep": False, "high_risk_country": False, "source_of_funds": "Επιχειρηματική δραστηριότητα",
        "purpose_of_business": "Νομική εκπροσώπηση", "notes": "",
    })
    assert r.status_code == 200, r.text


def test_trust_transaction_form_is_accepted(client, client_id):
    r = client.post("/api/trust/transactions", json={
        "client_id": client_id, "type": "deposit", "amount": 500.0, "date": "2026-09-10",
        "description": "Προκαταβολή πελάτη", "reference": "ΕΜΒ-1", "invoice_id": "",
    })
    assert r.status_code == 200, r.text


def test_drafting_clause_form_is_accepted(client):
    r = client.post("/api/drafting/clauses", json={
        "title": "Ρήτρα εμπιστευτικότητας", "category": "Προσωπικά",
        "body": "Τα μέρη αναλαμβάνουν την υποχρέωση εχεμύθειας.",
    })
    assert r.status_code == 200, r.text


def test_signature_form_is_accepted(client):
    """Το εύρημα που γέννησε αυτό το αρχείο: το frontend δεν στέλνει signature_hash."""
    r = client.post("/api/signatures", json={
        "document_title": "Εξουσιοδότηση", "signer_name": "Χρήστος Σκοτάνης",
        "signer_email": "c@example.gr", "signer_role": "client",
        "signature_data": "data:image/png;base64,iVBORw0KGgo=",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["signature_hash"]) == 64          # SHA-256 hex, υπολογισμένο server-side
    assert client.post(f"/api/signatures/{body['id']}/verify").json()["valid"] is True


def test_invoice_form_is_accepted(client, client_id):
    r = client.post("/api/invoices", json={
        "client_id": client_id, "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-09-10",
        "due_date": "2026-10-10", "payment_method": "Τραπεζική κατάθεση", "notes": "",
        "line_items": [{"description": "Νομικές υπηρεσίες", "quantity": 1,
                        "unit_price": 1000.0, "vat_rate": 24}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1240.0
