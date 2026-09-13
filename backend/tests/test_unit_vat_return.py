"""Περιοδική δήλωση ΦΠΑ (Φ2): σωστά ποσά ανά κωδικό, πιστωτικά, PDF."""
import pytest
from fastapi.testclient import TestClient

from vat_return import build_vat_return, build_vat_return_pdf, form_codes, DEFAULT_FORM_CODES

PERIOD = {"period": "2026-08", "period_start": "2026-08-01", "period_end": "2026-08-31",
          "due_date": "2026-09-30", "status": "computed"}
SETTINGS = {"company_name": "Γραφείο", "afm": "123456783", "doy": "Α΄ Αθηνών", "vat_period": "monthly"}


def _line(data, key):
    return next(l for l in data["lines"] if l["key"] == key)


def test_splits_outputs_by_rate_and_totals():
    invoices = [
        {"subtotal": 1000, "vat_amount": 240, "vat_breakdown": {"24": 240},
         "line_items": [{"quantity": 1, "unit_price": 1000, "vat_rate": 24}]},
        {"subtotal": 500, "vat_amount": 30, "vat_breakdown": {"6": 30},
         "line_items": [{"quantity": 1, "unit_price": 500, "vat_rate": 6}]},
        {"subtotal": 200, "vat_amount": 0, "vat_breakdown": {"0": 0},
         "line_items": [{"quantity": 1, "unit_price": 200, "vat_rate": 0}]},
    ]
    expenses = [{"amount": 124, "vat_amount": 24}, {"amount": 226, "vat_amount": 26}]
    data = build_vat_return(PERIOD, invoices, expenses, SETTINGS)

    assert _line(data, "outputs_base_24")["amount"] == 1000
    assert _line(data, "outputs_base_6")["amount"] == 500
    assert _line(data, "outputs_base_0")["amount"] == 200
    assert _line(data, "outputs_base_13")["amount"] == 0
    assert _line(data, "outputs_vat_24")["amount"] == 240
    assert _line(data, "outputs_vat_6")["amount"] == 30
    assert _line(data, "total_output_vat")["amount"] == 270
    assert _line(data, "inputs_vat")["amount"] == 50
    assert _line(data, "inputs_base")["amount"] == 300      # (124-24) + (226-26)
    assert _line(data, "vat_payable")["amount"] == 220      # 270 - 50
    assert _line(data, "vat_credit")["amount"] == 0
    assert data["totals"]["vat_payable"] == 220
    assert data["counts"] == {"invoices": 3, "expenses": 2}


def test_credit_notes_reduce_outputs():
    invoices = [
        {"subtotal": 1000, "vat_amount": 240, "vat_breakdown": {"24": 240},
         "line_items": [{"quantity": 1, "unit_price": 1000, "vat_rate": 24}]},
        {"subtotal": -400, "vat_amount": -96, "vat_breakdown": {"24": -96},
         "line_items": [{"quantity": 1, "unit_price": -400, "vat_rate": 24}]},
    ]
    data = build_vat_return(PERIOD, invoices, [], SETTINGS)
    assert _line(data, "outputs_base_24")["amount"] == 600
    assert _line(data, "outputs_vat_24")["amount"] == 144


def test_credit_balance_goes_to_the_carry_forward_line():
    invoices = [{"subtotal": 100, "vat_amount": 24, "vat_breakdown": {"24": 24},
                 "line_items": [{"quantity": 1, "unit_price": 100, "vat_rate": 24}]}]
    expenses = [{"amount": 620, "vat_amount": 120}]
    data = build_vat_return(PERIOD, invoices, expenses, SETTINGS)
    assert _line(data, "vat_payable")["amount"] == 0
    assert _line(data, "vat_credit")["amount"] == 96
    assert data["totals"]["vat_credit"] == 96


def test_invoice_without_breakdown_falls_back_to_standard_rate():
    data = build_vat_return(PERIOD, [{"subtotal": 100, "vat_amount": 24, "line_items": []}], [], SETTINGS)
    assert _line(data, "outputs_base_24")["amount"] == 100
    assert _line(data, "outputs_vat_24")["amount"] == 24


def test_codes_are_configurable_and_disclaimed():
    default = form_codes({})
    assert default["vat_payable"][0] == DEFAULT_FORM_CODES["vat_payable"][0]
    custom = form_codes({"vat_form_codes": {"vat_payable": "999"}})
    assert custom["vat_payable"][0] == "999"
    assert custom["outputs_base_24"] == DEFAULT_FORM_CODES["outputs_base_24"]

    data = build_vat_return(PERIOD, [], [], {**SETTINGS, "vat_form_codes": {"vat_payable": "999"}})
    assert _line(data, "vat_payable")["code"] == "999"
    assert "ΑΑΔΕ" in data["disclaimer"]      # η επιφύλαξη ταξιδεύει μαζί με τα δεδομένα


def test_pdf_renders():
    data = build_vat_return(PERIOD, [{"subtotal": 100, "vat_amount": 24, "vat_breakdown": {"24": 24},
                                      "line_items": [{"quantity": 1, "unit_price": 100, "vat_rate": 24}]}],
                            [{"amount": 62, "vat_amount": 12}], SETTINGS)
    pdf = build_vat_return_pdf(data)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


# ------------------------------------------------------------------- API ----

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def test_vat_return_endpoints(client):
    import asyncio
    from db import db
    r = client.post("/api/auth/register", json={"email": "vat@example.gr", "password": "Str0ngPass!", "name": "U"})
    assert r.status_code == 200
    firm_id = r.json()["firm_id"]
    loop = asyncio.get_event_loop_policy().new_event_loop()
    loop.run_until_complete(db.vat_periods.insert_one({**PERIOD, "firm_id": firm_id, "output_vat": 24,
                                                       "input_vat": 0, "vat_due": 24}))

    assert client.get("/api/automation/vat-periods/2026-99/return").status_code == 404

    data = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert data["period"] == "2026-08" and data["due_date"] == "2026-09-30"
    assert {l["key"] for l in data["lines"]} >= {"vat_payable", "vat_credit", "total_output_vat"}

    pdf = client.get("/api/automation/vat-periods/2026-08/return.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert "FPA-2026-08.pdf" in pdf.headers["content-disposition"]


# ------------------------------------------------- AADE sandbox harness -----

def test_sandbox_harness_builds_every_document_type_offline():
    """Το script επαλήθευσης πρέπει να παράγει έγκυρο XML για κάθε είδος πριν καν ανοίξει δίκτυο."""
    import importlib.util
    from pathlib import Path
    import mydata_client as md

    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_mydata_sandbox.py"
    spec = importlib.util.spec_from_file_location("verify_mydata_sandbox", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    settings = {"afm": "123456783", "aade_user_id": "u", "aade_subscription_key": "k",
                "withholding_enabled": True, "withholding_min_net": 0}
    company = {"name": "ΑΕ", "afm": "987654324", "client_type": "company"}
    individual = {"name": "Ιδιώτης", "client_type": "individual"}
    today = "2026-09-01"

    b2b = {"number": "ΤΠΥ-TEST-1", "date": today, "line_items": [harness._line(1, 100, 24)],
           "withholding_amount": 0}
    assert "<invoiceType>2.1</invoiceType>" in md.build_invoices_doc_xml(b2b, company, settings)

    retail = {"number": "ΑΠΥ-TEST-1", "date": today, "line_items": [harness._line(1, 50, 24)]}
    assert "<invoiceType>11.2</invoiceType>" in md.build_invoices_doc_xml(retail, individual, settings)

    withheld = {"number": "ΤΠΥ-TEST-2", "date": today, "line_items": [harness._line(1, 1000, 24)]}
    assert "<totalWithheldAmount>200.00</totalWithheldAmount>" in md.build_invoices_doc_xml(withheld, company, settings)

    credit = {"number": "ΠΙΣ-TEST-1", "date": today, "correlated_mark": "400001",
              "credit_note_for": "x", "line_items": [harness._line(1, -100, 24)], "withholding_amount": 0}
    credit_xml = md.build_invoices_doc_xml(credit, company, settings)
    assert "<invoiceType>5.1</invoiceType>" in credit_xml and "<correlatedInvoices>400001" in credit_xml


def test_sandbox_harness_report_tracks_failures():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_mydata_sandbox.py"
    spec = importlib.util.spec_from_file_location("verify_mydata_sandbox2", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)

    report = harness.Report()
    report.add("ok check", True)
    report.add("bad check", False, "κάτι έσπασε")
    assert len(report.failed) == 1 and report.failed[0]["name"] == "bad check"
    assert "1/2" in report.summary()
