"""Pure-function tests: tax calculations, bank CSV parsing and matching, myDATA XML."""
from datetime import date
import pytest

from tax_utils import calculate_invoice_totals, calculate_payroll, next_vat_deadline, next_efka_deadline, calculate_progressive_tax
from routes_bank import _parse_amount, _parse_date, _parse_csv, _find_match
from mydata_client import build_invoices_doc_xml, MyDataError, _parse_send_response


def test_invoice_totals_mixed_vat():
    items = [{"quantity": 2, "unit_price": 100, "vat_rate": 24}, {"quantity": 1, "unit_price": 50, "vat_rate": 6}]
    t = calculate_invoice_totals(items)
    assert t["subtotal"] == 250
    assert t["vat_amount"] == 51
    assert t["total"] == 301
    assert t["vat_breakdown"] == {"24": 48, "6": 3}


def test_progressive_tax_brackets():
    assert calculate_progressive_tax(10000) == 900
    assert calculate_progressive_tax(20000) == 900 + 2200


def test_payroll_net_is_below_gross():
    p = calculate_payroll(1500, dependents=1)
    assert p["net_salary"] < p["gross_salary"]
    assert p["employer_total_cost"] > p["gross_salary"]


def test_vat_deadline_monthly_and_quarterly():
    assert next_vat_deadline(date(2026, 8, 10), "monthly") == date(2026, 9, 30)
    assert next_vat_deadline(date(2026, 8, 10), "quarterly") == date(2026, 10, 30)
    assert next_efka_deadline(date(2026, 8, 10)) == date(2026, 8, 31)


@pytest.mark.parametrize("raw,expected", [("1.234,56", 1234.56), ("1,234.56", 1234.56), ("-45,00 €", -45.0), ("", 0.0)])
def test_parse_amount(raw, expected):
    assert _parse_amount(raw) == expected


def test_parse_date_formats():
    assert _parse_date("05/08/2026") == "2026-08-05"
    assert _parse_date("2026-08-05") == "2026-08-05"


def test_parse_greek_bank_csv_with_metadata_rows():
    csv = ("Τράπεζα Πειραιώς;Κινήσεις λογαριασμού\n"
           "Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο\n"
           "01/08/2026;ΠΑΠΑΔΟΠΟΥΛΟΣ ΤΠΥ-2026-0001;;620,00;5.000,00\n"
           "02/08/2026;ΔΕΗ;120,00;;4.880,00\n").encode("cp1253")
    rows = _parse_csv(csv)
    assert len(rows) == 2
    assert rows[0]["amount"] == 620.0 and rows[0]["date"] == "2026-08-01"
    assert rows[1]["amount"] == -120.0


@pytest.mark.asyncio
async def test_find_match_scores_amount_date_and_number():
    invoices = [
        {"_id": 1, "number": "ΤΠΥ-2026-0001", "client_name": "Παπαδόπουλος Ιωάννης", "total": 620.0, "date": "2026-07-28"},
        {"_id": 2, "number": "ΤΠΥ-2026-0002", "client_name": "Άλλος", "total": 999.0, "date": "2026-07-28"},
    ]
    tx = {"amount": 620.0, "date": "2026-08-01", "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ τπυ-2026-0001", "reference": ""}
    best, score = await _find_match(tx, invoices)
    assert best["_id"] == 1 and score >= 90
    none, _ = await _find_match({"amount": -5, "date": "2026-08-01", "description": ""}, invoices)
    assert none is None


def test_mydata_xml_requires_afms_and_builds_totals():
    settings = {"afm": "123456789"}
    invoice = {"number": "ΤΠΥ-2026-0007", "date": "2026-08-01", "payment_method": "Μετρητά",
               "line_items": [{"quantity": 1, "unit_price": 100, "vat_rate": 24}]}
    xml = build_invoices_doc_xml(invoice, {"afm": "987654321"}, settings)
    assert "<totalGrossValue>124.00</totalGrossValue>" in xml
    assert "<series>ΤΠΥ-2026</series><aa>0007</aa>" in xml
    assert "<type>3</type>" in xml  # cash
    with pytest.raises(MyDataError):
        build_invoices_doc_xml(invoice, {}, settings)


def test_mydata_parse_response():
    ok = "<ResponseDoc><response><statusCode>Success</statusCode><invoiceMark>400001</invoiceMark><invoiceUid>abc</invoiceUid></response></ResponseDoc>"
    assert _parse_send_response(ok) == {"mark": "400001", "uid": "abc"}
    bad = "<ResponseDoc><response><statusCode>ValidationError</statusCode><errors><error><message>Λάθος ΑΦΜ</message></error></errors></response></ResponseDoc>"
    with pytest.raises(MyDataError, match="Λάθος ΑΦΜ"):
        _parse_send_response(bad)
