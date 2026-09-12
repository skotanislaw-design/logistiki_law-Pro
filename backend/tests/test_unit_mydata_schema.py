"""Επικύρωση του XML προς την ΑΑΔΕ κατά το **επίσημο** σχήμα myDATA v2.0.1.

Ο κώδικας δεν είχε δοκιμαστεί ποτέ σε πραγματικό περιβάλλον ΑΑΔΕ. Αυτό δεν
υποκαθιστά μια πραγματική κλήση, αλλά πιάνει ό,τι απορρίπτεται στο επίπεδο σχήματος —
που είναι και η πλειονότητα των απορρίψεων.

Τα XSD στο ``fixtures/mydata/`` είναι τα επίσημα αρχεία της ΑΑΔΕ (έκδοση 2.0.1,
Μάρτιος 2026). Το namespace παρέμεινε ``v1.0`` σε όλες τις εκδόσεις.

Τρία σφάλματα εντοπίστηκαν έτσι, και τα τρία θα απορρίπτονταν από την ΑΑΔΕ:
  * η ακαθάριστη αξία δεν αφαιρούσε την παρακράτηση (ValidationError 203)
  * η ΑΠΥ λιανικής δήλωνε χαρακτηρισμό χονδρικής (μη επιτρεπτός συνδυασμός)
  * **κάθε** πιστωτικό διαβιβαζόταν με αρνητικά ποσά (`AmountType` με `minInclusive="0"`)

Το τρίτο διέφυγε την πρώτη φορά επειδή το test έχτιζε πιστωτικό στο χέρι, με θετικά
ποσά. Ένα παραστατικό φτιαγμένο από το test αποδεικνύει μόνο ότι το test είναι έγκυρο:
το ``test_credit_note_built_by_the_app_is_accepted`` περνά πλέον από την πραγματική
διαδρομή έκδοσης, γι' αυτό και βρήκε αυτό που το προηγούμενο δεν μπορούσε να δει.
"""
import asyncio
import pathlib

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from lxml import etree

from mydata_client import MyDataError, build_invoices_doc_xml

XSD = pathlib.Path(__file__).parent / "fixtures" / "mydata" / "InvoicesDoc-v2.0.1.xsd"
NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"

FIRM = {"afm": "123456789"}
FIRM_WITH_WITHHOLDING = {"afm": "123456789", "withholding_enabled": True}
COMPANY = {"name": "ΑΛΦΑ ΑΕ", "afm": "987654321", "client_type": "company"}
INDIVIDUAL = {"name": "Ιωάννης Παπαδόπουλος", "client_type": "individual"}


@pytest.fixture(scope="module")
def schema():
    return etree.XMLSchema(etree.parse(str(XSD)))


def _line(price, rate=24, qty=1):
    return {"description": "Νομικές υπηρεσίες", "quantity": qty, "unit_price": price, "vat_rate": rate}


def _doc(invoice, client, settings=FIRM):
    return etree.fromstring(build_invoices_doc_xml(invoice, client, settings).encode("utf-8"))


def _text(doc, tag):
    return doc.findtext(f".//{NS}{tag}")


def _assert_valid(schema, doc):
    if not schema.validate(doc):
        pytest.fail("Το XML δεν είναι έγκυρο κατά το επίσημο σχήμα:\n" +
                    "\n".join(f"  γρ.{e.line}: {e.message}" for e in schema.error_log))


# --------------------------------------------------- εγκυρότητα ανά μορφή ---

@pytest.mark.parametrize("label,invoice,client,settings", [
    ("B2B 24%", {"number": "ΤΠΥ-2026-0001", "date": "2026-09-10",
                 "line_items": [_line(1000.0)]}, COMPANY, FIRM),
    ("B2B 0% με απαλλαγή", {"number": "ΤΠΥ-2026-0002", "date": "2026-09-10",
                            "line_items": [_line(1000.0, 0)]}, COMPANY, FIRM),
    ("B2B πολλαπλοί συντελεστές", {"number": "ΤΠΥ-2026-0003", "date": "2026-09-10",
                                   "line_items": [_line(1000.0, 24), _line(500.0, 13),
                                                  _line(200.0, 6), _line(100.0, 0)]}, COMPANY, FIRM),
    ("B2B με παρακράτηση", {"number": "ΤΠΥ-2026-0004", "date": "2026-09-10",
                            "line_items": [_line(5000.0)]}, COMPANY, FIRM_WITH_WITHHOLDING),
    ("Πιστωτικό 5.1", {"number": "ΠΙΣ-2026-0001", "date": "2026-09-10",
                       "correlated_mark": "400001234567890",
                       "line_items": [_line(1000.0)]}, COMPANY, FIRM),
    ("ΑΠΥ λιανικής 11.2", {"number": "ΑΠΥ-2026-0001", "date": "2026-09-10",
                           "line_items": [_line(200.0)]}, INDIVIDUAL, FIRM),
    ("Κλασματικές ποσότητες", {"number": "ΤΠΥ-2026-0005", "date": "2026-09-10",
                               "line_items": [_line(133.33, 24, 1.5), _line(88.88, 13, 2.5)]},
     COMPANY, FIRM),
])
def test_generated_xml_validates_against_official_schema(schema, label, invoice, client, settings):
    _assert_valid(schema, _doc(invoice, client, settings))


# ------------------------------------------------------- ακαθάριστη αξία ---

def test_gross_value_matches_the_official_reference_sample(schema):
    """Οι ακριβείς αριθμοί του επίσημου δείγματος: 4900 + 1176 − 980 = 5096.

    Ο κώδικας δήλωνε 6076 (χωρίς αφαίρεση της παρακράτησης). Η ΑΑΔΕ το απορρίπτει με
    ValidationError 203: «Η Ακαθάριστη Αξία δεν ταιριάζει με το άθροισμα της καθαρής
    αξίας συν τους φόρους».
    """
    doc = _doc({"number": "ΤΠΥ-2026-0004", "date": "2026-09-10", "line_items": [_line(4900.0)]},
               COMPANY, FIRM_WITH_WITHHOLDING)
    _assert_valid(schema, doc)
    assert _text(doc, "totalNetValue") == "4900.00"
    assert _text(doc, "totalVatAmount") == "1176.00"
    assert _text(doc, "totalWithheldAmount") == "980.00"
    assert _text(doc, "totalGrossValue") == "5096.00"


def test_payment_amount_equals_gross_value(schema):
    """Ο οδηγός ERP απαιτεί το ποσό πληρωμής να ισούται με το totalGrossValue."""
    for settings in (FIRM, FIRM_WITH_WITHHOLDING):
        doc = _doc({"number": "ΤΠΥ-2026-0006", "date": "2026-09-10",
                    "line_items": [_line(2000.0)]}, COMPANY, settings)
        amount = doc.findtext(f".//{NS}paymentMethodDetails/{NS}amount")
        assert amount == _text(doc, "totalGrossValue")


def test_gross_identity_holds_without_withholding():
    """Χωρίς παρακράτηση, η ακαθάριστη αξία παραμένει καθαρή + ΦΠΑ."""
    doc = _doc({"number": "ΤΠΥ-2026-0007", "date": "2026-09-10",
                "line_items": [_line(1000.0, 24), _line(500.0, 13)]}, COMPANY, FIRM)
    net, vat, gross = (float(_text(doc, t)) for t in
                       ("totalNetValue", "totalVatAmount", "totalGrossValue"))
    assert round(net + vat, 2) == gross
    assert _text(doc, "totalWithheldAmount") == "0.00"


# ------------------------------------------------ χαρακτηρισμοί εσόδων ---

def test_retail_receipt_uses_the_retail_classification(schema):
    """Το E3_561_001 (χονδρικές) **δεν** είναι επιτρεπτό για ΑΠΥ λιανικής 11.2.

    Πηγή: επίσημο `syndiasmoi_xaraktirismwn_v2.0.1.xlsx`, φύλλο «11.2» — επιτρεπτά
    E3_561_003, E3_561_005, E3_561_006, E3_561_007, E3_563. Ο κώδικας έστελνε
    E3_561_001 σε κάθε τύπο παραστατικού, δηλαδή σε κάθε ΑΠΥ προς ιδιώτη.
    """
    doc = _doc({"number": "ΑΠΥ-2026-0001", "date": "2026-09-10",
                "line_items": [_line(200.0)]}, INDIVIDUAL, FIRM)
    _assert_valid(schema, doc)
    assert _text(doc, "invoiceType") == "11.2"
    types = {e.text for e in doc.iter("{https://www.aade.gr/myDATA/incomeClassificaton/v1.0}classificationType")}
    assert types == {"E3_561_003"}


def test_b2b_invoice_keeps_the_wholesale_classification():
    doc = _doc({"number": "ΤΠΥ-2026-0008", "date": "2026-09-10",
                "line_items": [_line(1000.0)]}, COMPANY, FIRM)
    assert _text(doc, "invoiceType") == "2.1"
    types = {e.text for e in doc.iter("{https://www.aade.gr/myDATA/incomeClassificaton/v1.0}classificationType")}
    assert types == {"E3_561_001"}


# ------------------------------------------------------------ λοιπά ---

def test_zero_vat_line_declares_an_exemption_reason(schema):
    """Ο οδηγός ERP (παρατήρηση 3): το vatExemptionCategory είναι υποχρεωτικό όποτε
    ο συντελεστής είναι 0%."""
    doc = _doc({"number": "ΤΠΥ-2026-0002", "date": "2026-09-10",
                "line_items": [_line(1000.0, 0)]}, COMPANY, FIRM)
    _assert_valid(schema, doc)
    assert _text(doc, "vatCategory") == "7"
    assert _text(doc, "vatExemptionCategory")


def test_withholding_declared_via_taxes_totals(schema):
    """Η παρακράτηση δηλώνεται στο `taxesTotals`, που ο οδηγός ρητά επιτρέπει ως
    εναλλακτική των φόρων ανά γραμμή (§5.6, παρατήρηση 2)."""
    doc = _doc({"number": "ΤΠΥ-2026-0004", "date": "2026-09-10",
                "line_items": [_line(5000.0)]}, COMPANY, FIRM_WITH_WITHHOLDING)
    _assert_valid(schema, doc)
    assert _text(doc, "taxType") == "1"
    # Κατηγορία 3 = «Αμοιβές Συμβουλών Διοίκησης — 20%». Η παλιά προεπιλογή «2»
    # ήταν «Δικαιώματα», που δεν αφορά αμοιβές υπηρεσιών.
    assert _text(doc, "taxCategory") == "3"
    assert _text(doc, "taxAmount") == "1000.00"


def test_withholding_category_is_configurable():
    """Το γραφείο μπορεί να ορίσει άλλη κατηγορία — π.χ. 10 (προκαταβλητέος
    δικηγόρων 15%) — χωρίς αλλαγή κώδικα."""
    doc = _doc({"number": "ΤΠΥ-2026-0004", "date": "2026-09-10", "line_items": [_line(5000.0)]},
               COMPANY, {**FIRM_WITH_WITHHOLDING, "withholding_category": "10",
                         "withholding_rate": 0.15})
    assert _text(doc, "taxCategory") == "10"
    assert _text(doc, "taxAmount") == "750.00"
    assert _text(doc, "totalGrossValue") == "5450.00"   # 5000 + 1200 − 750


def test_credit_note_carries_the_correlated_mark(schema):
    doc = _doc({"number": "ΠΙΣ-2026-0001", "date": "2026-09-10",
                "correlated_mark": "400001234567890", "line_items": [_line(1000.0)]}, COMPANY, FIRM)
    _assert_valid(schema, doc)
    assert _text(doc, "invoiceType") == "5.1"
    assert _text(doc, "correlatedInvoices") == "400001234567890"


def test_endpoints_match_the_official_documentation():
    """Οι διευθύνσεις δοκιμών και παραγωγής διαφέρουν δομικά — η δοκιμαστική **δεν**
    έχει το τμήμα `/myDATA`. Οδηγός ERP v2.0.1 §4.2.1."""
    from mydata_client import _urls

    assert _urls({"aade_environment": "production"})["send"] == \
        "https://mydatapi.aade.gr/myDATA/SendInvoices"
    assert _urls({"aade_environment": "sandbox"})["send"] == \
        "https://mydataapidev.aade.gr/SendInvoices"


# ---------------------------------------------------------------------------
# Πιστωτικά: μέσα από την πραγματική διαδρομή έκδοσης, όχι από χειροποίητο fixture.
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _issued_invoice(app_client, price=1000.0):
    """Εκδοθέν τιμολόγιο με MARK — προϋπόθεση για να επιτραπεί πιστωτικό."""
    from db import db

    app_client.post("/api/auth/register",
                    json={"email": "d@example.gr", "password": "Str0ngPass!", "name": "Δ"})
    cid = app_client.post("/api/clients", json={
        "name": "ΑΛΦΑ ΑΕ", "afm": "987654321", "client_type": "company"}).json()["id"]
    iid = app_client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [_line(price)]}).json()["id"]
    app_client.post(f"/api/invoices/{iid}/issue")
    _run(db.invoices.update_one({"_id": ObjectId(iid)}, {"$set": {
        "mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    return cid, iid


@pytest.mark.parametrize("payload,expected_net", [
    ({"reason": "Ακύρωση"}, "1000.00"),
    ({"reason": "Μερική έκπτωση", "amount": 300}, "300.00"),
])
def test_credit_note_built_by_the_app_is_accepted(app_client, schema, payload, expected_net):
    """Το πιστωτικό που παράγει η εφαρμογή, όπως ακριβώς το αποθηκεύει.

    Η εφαρμογή αποθηκεύει αρνητικά ποσά — σκόπιμα, ώστε το πιστωτικό να μειώνει έσοδα
    και ΦΠΑ σε κάθε άθροισμα. Το myDATA όμως δέχεται μόνο θετικά: το είδος 5.1 φέρει το
    πρόσημο. Πριν τη διόρθωση απορρίπτονταν και τα δύο, με οκτώ παραβιάσεις σχήματος.
    """
    from db import db

    cid, iid = _issued_invoice(app_client)
    r = app_client.post(f"/api/invoices/{iid}/credit-note", json=payload)
    assert r.status_code == 200, r.text

    credit = _run(db.invoices.find_one({"_id": ObjectId(r.json()["id"])}))
    client_doc = _run(db.clients.find_one({"_id": ObjectId(cid)}))
    assert credit["total"] < 0, "η αποθήκευση οφείλει να παραμείνει αρνητική"

    doc = _doc(credit, client_doc)
    assert schema.validate(doc), [e.message for e in schema.error_log]
    assert _text(doc, "invoiceType") == "5.1"
    assert _text(doc, "netValue") == expected_net
    assert _text(doc, "correlatedInvoices") == "400001111111111"
    assert not any(v.text.startswith("-") for v in doc.iter() if v.text and v.text.strip())


def test_negative_line_outside_a_credit_note_is_refused():
    """Σιωπηλή διόρθωση θα έκρυβε το πραγματικό πρόβλημα: λείπει πιστωτικό ή η γραμμή
    είναι λάθος. Και τα δύο θέλουν άνθρωπο."""
    invoice = {"number": "ΤΠΥ-2026-0099", "date": "2026-09-10", "line_items": [_line(-100.0)]}
    with pytest.raises(MyDataError, match="πιστωτικ"):
        build_invoices_doc_xml(invoice, COMPANY, FIRM)
