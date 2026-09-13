"""Pure-function tests: tax calculations, bank CSV parsing and matching, myDATA XML."""
from datetime import date
import pytest

from tax_utils import calculate_invoice_totals, calculate_payroll, next_vat_deadline, next_efka_deadline, calculate_progressive_tax
from routes_bank import _parse_amount, _parse_date, _parse_csv, _find_match
from mydata_client import (
    build_invoices_doc_xml, MyDataError, _parse_send_response, resolve_invoice_type,
    parse_requested_docs, next_mark_from,
)


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


def test_vat_deadline_is_the_next_one_still_pending():
    """Η επόμενη προθεσμία είναι αυτή που ΕΚΚΡΕΜΕΙ, όχι της περιόδου που διανύουμε.

    Στις 10/08 με μηνιαίο ΦΠΑ εκκρεμεί η δήλωση Ιουλίου, με προθεσμία την τελευταία
    εργάσιμη του Αυγούστου. Ο παλιός υπολογισμός επέστρεφε 30/09 και έκρυβε την
    επικείμενη προθεσμία — αυτό το test κωδικοποιούσε το σφάλμα.
    """
    assert next_vat_deadline(date(2026, 8, 10), "monthly") == date(2026, 8, 31)
    assert next_vat_deadline(date(2026, 9, 10), "monthly") == date(2026, 9, 30)
    assert next_vat_deadline(date(2026, 8, 10), "quarterly") == date(2026, 10, 30)
    assert next_efka_deadline(date(2026, 8, 10)) == date(2026, 8, 31)


@pytest.mark.parametrize("today,expected", [
    (date(2026, 10, 15), date(2026, 10, 30)),   # Q3 λήγει 30/09, προθεσμία 31/10 (εργάσιμη 30/10)
    (date(2027, 1, 15), date(2027, 1, 29)),     # Q4/2026, προθεσμία 31/01 (εργάσιμη 29/01)
    (date(2026, 4, 20), date(2026, 4, 30)),     # Q1 λήγει 31/03, προθεσμία 30/04
])
def test_quarterly_vat_deadline_does_not_skip_a_quarter(today, expected):
    """Η τριμηνιαία διαδρομή έκρυβε την προθεσμία έως και τρεις μήνες."""
    assert next_vat_deadline(today, "quarterly") == expected


def test_vat_deadline_for_period_end_is_month_after_period():
    from tax_utils import vat_deadline_for_period_end
    assert vat_deadline_for_period_end(date(2026, 8, 31), "monthly") == date(2026, 9, 30)
    assert vat_deadline_for_period_end(date(2026, 9, 30), "quarterly") == date(2026, 10, 30)


def test_efka_deadline_never_returns_a_past_date():
    """Στις 31/10 η τελευταία εργάσιμη (30/10) έχει παρέλθει — δίνουμε την επόμενη."""
    assert next_efka_deadline(date(2026, 10, 31)) == date(2026, 11, 30)
    assert next_efka_deadline(date(2026, 5, 31)) == date(2026, 6, 30)


def test_withholding_deadline_is_distinct_from_efka():
    from tax_utils import next_withholding_deadline
    d = date(2026, 9, 10)
    assert next_withholding_deadline(d) != next_efka_deadline(d)
    assert next_withholding_deadline(d) == date(2026, 11, 30)


def test_efka_contributions_are_capped():
    """Χωρίς πλαφόν, μικτά 20.000 € έδιναν εισφορά 2.774 € αντί ~1.050 €."""
    from tax_utils import calculate_payroll, EFKA_MONTHLY_CEILING, EMPLOYEE_EFKA_RATE
    p = calculate_payroll(20000.0)
    assert p["employee_efka"] == round(EFKA_MONTHLY_CEILING * EMPLOYEE_EFKA_RATE, 2)
    assert p["employee_efka"] < 20000 * EMPLOYEE_EFKA_RATE


def test_stored_totals_match_the_xml_sent_to_aade():
    """Το παραστατικό του πελάτη και η εγγραφή στην ΑΑΔΕ δεν επιτρέπεται να διαφέρουν."""
    import re
    items = [{"description": "A", "quantity": 1.5, "unit_price": 133.33, "vat_rate": 24},
             {"description": "B", "quantity": 2.5, "unit_price": 88.88, "vat_rate": 13}]
    stored = calculate_invoice_totals(items)
    xml = build_invoices_doc_xml(
        {"number": "ΤΠΥ-2026-0001", "line_items": items, "issue_date": "2026-09-10",
         "payment_method": "Τραπεζική κατάθεση"},
        {"name": "ΑΛΦΑ ΑΕ", "afm": "123456789", "client_type": "company"},
        {"afm": "987654321"})
    xml_net = float(re.search(r"<totalNetValue>([\d.]+)", xml).group(1))
    xml_vat = float(re.search(r"<totalVatAmount>([\d.]+)", xml).group(1))
    xml_gross = float(re.search(r"<totalGrossValue>([\d.]+)", xml).group(1))
    assert (xml_net, xml_vat, xml_gross) == (stored["subtotal"], stored["vat_amount"], stored["total"])


def test_line_vat_amounts_sum_to_the_declared_total():
    """Το myDATA απορρίπτει παραστατικό όπου το άθροισμα γραμμών ≠ σύνολο."""
    import re
    items = [{"description": "x", "quantity": 1, "unit_price": 10.10, "vat_rate": 24}] * 50
    xml = build_invoices_doc_xml(
        {"number": "ΤΠΥ-2026-0002", "line_items": items, "issue_date": "2026-09-10",
         "payment_method": "Τραπεζική κατάθεση"},
        {"name": "ΑΛΦΑ ΑΕ", "afm": "123456789", "client_type": "company"},
        {"afm": "987654321"})
    line_vats = [float(v) for v in re.findall(r"<vatAmount>([\d.]+)</vatAmount>", xml)]
    total_vat = float(re.search(r"<totalVatAmount>([\d.]+)", xml).group(1))
    assert round(sum(line_vats), 2) == total_vat


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


def test_mydata_xml_b2b_type_and_totals():
    settings = {"afm": "123456789"}
    invoice = {"number": "ΤΠΥ-2026-0007", "date": "2026-08-01", "payment_method": "Μετρητά",
               "line_items": [{"quantity": 1, "unit_price": 100, "vat_rate": 24}]}
    xml = build_invoices_doc_xml(invoice, {"afm": "987654321", "client_type": "company"}, settings)
    assert "<invoiceType>2.1</invoiceType>" in xml
    assert "<counterpart><vatNumber>987654321</vatNumber>" in xml
    assert "<totalGrossValue>124.00</totalGrossValue>" in xml
    assert "<totalWithheldAmount>0.00</totalWithheldAmount>" in xml
    assert "<series>ΤΠΥ-2026</series><aa>0007</aa>" in xml
    assert "<type>3</type>" in xml  # cash


def test_mydata_retail_receipt_for_individual_without_afm():
    """Ιδιώτης χωρίς ΑΦΜ → ΑΠΥ λιανικής χωρίς counterpart, αντί για σφάλμα."""
    settings = {"afm": "123456789"}
    invoice = {"number": "ΤΠΥ-2026-0008", "date": "2026-08-01",
               "line_items": [{"quantity": 1, "unit_price": 50, "vat_rate": 24}]}
    xml = build_invoices_doc_xml(invoice, {"name": "Ιδιώτης", "client_type": "individual"}, settings)
    assert "<invoiceType>11.2</invoiceType>" in xml
    assert "<counterpart>" not in xml
    # μια εταιρεία χωρίς ΑΦΜ παραμένει σφάλμα — δεν επιτρέπεται λιανική σε επιχείρηση
    with pytest.raises(MyDataError, match="ΑΦΜ"):
        build_invoices_doc_xml(invoice, {"name": "ΑΕ", "client_type": "company"}, settings)
    with pytest.raises(MyDataError, match="ΑΦΜ της εταιρείας"):
        build_invoices_doc_xml(invoice, {"afm": "987654321"}, {})


def test_mydata_withholding_applied_only_where_due():
    settings = {"afm": "123456789", "withholding_enabled": True, "withholding_min_net": 300}
    company = {"afm": "987654321", "client_type": "company"}
    big = {"number": "ΤΠΥ-2026-0009", "date": "2026-08-01",
           "line_items": [{"quantity": 1, "unit_price": 1000, "vat_rate": 24}]}
    xml = build_invoices_doc_xml(big, company, settings)
    assert "<totalWithheldAmount>200.00</totalWithheldAmount>" in xml
    assert "<taxAmount>200.00</taxAmount>" in xml and "<taxType>1</taxType>" in xml
    # πληρωτέο = 1240 − 200
    assert "<amount>1040.00</amount>" in xml
    # κάτω από το όριο: καμία παρακράτηση
    small = {**big, "line_items": [{"quantity": 1, "unit_price": 100, "vat_rate": 24}]}
    assert "<totalWithheldAmount>0.00</totalWithheldAmount>" in build_invoices_doc_xml(small, company, settings)
    # λιανική: ποτέ παρακράτηση
    retail = build_invoices_doc_xml(big, {"name": "Ιδιώτης"}, settings)
    assert "<totalWithheldAmount>0.00</totalWithheldAmount>" in retail


def test_mydata_credit_note_requires_correlated_mark():
    settings = {"afm": "123456789"}
    company = {"afm": "987654321", "client_type": "company"}
    credit = {"number": "ΠΙΣ-2026-0001", "date": "2026-08-05", "credit_note_for": "abc",
              "line_items": [{"quantity": 1, "unit_price": -100, "vat_rate": 24}]}
    with pytest.raises(MyDataError, match="MARK"):
        build_invoices_doc_xml(credit, company, settings)
    xml = build_invoices_doc_xml({**credit, "correlated_mark": "400001234567890"}, company, settings)
    assert "<invoiceType>5.1</invoiceType>" in xml
    assert "<correlatedInvoices>400001234567890</correlatedInvoices>" in xml
    # Το test απαιτούσε «-100.00»: κωδικοποιούσε το σφάλμα. Το myDATA δέχεται μόνο
    # θετικά ποσά (`AmountType` με `minInclusive="0"`) — το είδος 5.1 φέρει το πρόσημο.
    # Η αποθηκευμένη γραμμή παραμένει -100 · αλλάζει μόνο ό,τι φεύγει προς την ΑΑΔΕ.
    assert "<totalNetValue>100.00</totalNetValue>" in xml


def test_resolve_invoice_type_precedence():
    assert resolve_invoice_type({"invoice_type": "9.3"}, {"client_type": "company"}) == "9.3"
    assert resolve_invoice_type({}, {"client_type": "company"}) == "2.1"
    assert resolve_invoice_type({}, {"afm": "123456789"}) == "2.1"
    assert resolve_invoice_type({}, {"client_type": "individual"}) == "11.2"
    assert resolve_invoice_type({"correlated_mark": "1"}, {"client_type": "company"}) == "5.1"


def test_mydata_parse_response():
    ok = "<ResponseDoc><response><statusCode>Success</statusCode><invoiceMark>400001</invoiceMark><invoiceUid>abc</invoiceUid></response></ResponseDoc>"
    assert _parse_send_response(ok) == {"mark": "400001", "uid": "abc"}
    bad = "<ResponseDoc><response><statusCode>ValidationError</statusCode><errors><error><message>Λάθος ΑΦΜ</message></error></errors></response></ResponseDoc>"
    with pytest.raises(MyDataError, match="Λάθος ΑΦΜ"):
        _parse_send_response(bad)


# ------------------------------------------------- incoming myDATA docs -----

REQUEST_DOCS_XML = """<?xml version="1.0" encoding="utf-8"?>
<RequestedDoc xmlns="http://www.aade.gr/myDATA/invoice/v1.0">
  <invoicesDoc>
    <invoice>
      <mark>400001111111111</mark>
      <uid>UID-A</uid>
      <issuer><vatNumber>111111111</vatNumber><name>ΔΕΗ ΑΕ</name><country>GR</country></issuer>
      <counterpart><vatNumber>123456789</vatNumber><country>GR</country></counterpart>
      <invoiceHeader><series>A</series><aa>55</aa><issueDate>2026-08-10</issueDate><invoiceType>1.1</invoiceType></invoiceHeader>
      <invoiceDetails><lineNumber>1</lineNumber><netValue>100.00</netValue><vatCategory>1</vatCategory><vatAmount>24.00</vatAmount></invoiceDetails>
      <invoiceSummary><totalNetValue>100.00</totalNetValue><totalVatAmount>24.00</totalVatAmount><totalGrossValue>124.00</totalGrossValue></invoiceSummary>
    </invoice>
    <invoice>
      <mark>400002222222222</mark>
      <issuer><vatNumber>123456789</vatNumber><name>Το γραφείο μας</name></issuer>
      <counterpart><vatNumber>987654321</vatNumber></counterpart>
      <invoiceHeader><series>ΤΠΥ-2026</series><aa>1</aa><issueDate>2026-08-11</issueDate><invoiceType>2.1</invoiceType></invoiceHeader>
      <invoiceSummary><totalNetValue>500.00</totalNetValue><totalVatAmount>120.00</totalVatAmount><totalGrossValue>620.00</totalGrossValue></invoiceSummary>
    </invoice>
  </invoicesDoc>
</RequestedDoc>"""


def test_parse_requested_docs_keeps_only_our_expenses():
    docs = parse_requested_docs(REQUEST_DOCS_XML, "123456789")
    assert len(docs) == 1, "το δικό μας εκδοθέν δεν είναι έξοδο"
    d = docs[0]
    assert d["mark"] == "400001111111111"
    assert d["issuer_afm"] == "111111111" and d["issuer_name"] == "ΔΕΗ ΑΕ"
    assert (d["net_value"], d["vat_amount"], d["gross_value"], d["vat_rate"]) == (100.0, 24.0, 124.0, 24)
    assert d["date"] == "2026-08-10" and d["invoice_type"] == "1.1"


def test_next_mark_advances_and_falls_back():
    assert next_mark_from(REQUEST_DOCS_XML) == "400002222222222"
    assert next_mark_from("not xml", fallback="7") == "7"
    assert next_mark_from("<RequestedDoc></RequestedDoc>", fallback="7") == "7"


def test_parse_requested_docs_rejects_garbage():
    with pytest.raises(MyDataError):
        parse_requested_docs("<<<not xml", "123456789")
