"""Έξοδα: διασταύρωση διπλοτύπων, πληρωμές προμηθευτών, πιστωτικά."""
from datetime import date, timedelta

import pytest

import automation
from automation import run_firm, resolve_notification
from db import db
from expense_services import find_duplicate, mark_expense_paid, payment_defaults, same_vendor
from mydata_client import parse_requested_docs
from tests.test_unit_autopilot import _seed, FIRM


def test_vendor_matching_is_lenient():
    assert same_vendor("ΔΕΗ Α.Ε.", "ΔΕΗ") and not same_vendor("ΔΕΗ", "ΟΤΕ") and not same_vendor("", "ΔΕΗ")


def test_payment_defaults_by_document_type():
    assert payment_defaults("1.1", "2026-08-10") == {"payment_status": "unpaid", "due_date": "2026-09-09"}
    assert payment_defaults("11.1", "2026-08-10") == {"payment_status": "paid"}
    assert payment_defaults("5.1", "2026-08-10") == {"payment_status": "paid"}


CREDIT_XML = """<RequestedDoc><invoicesDoc><invoice>
<issuer><vatNumber>111111111</vatNumber><name>ΔΕΗ</name></issuer>
<counterpart><vatNumber>123456783</vatNumber></counterpart>
<invoiceHeader><issueDate>2026-08-10</issueDate><invoiceType>5.1</invoiceType></invoiceHeader>
<mark>500001</mark>
<invoiceSummary><totalNetValue>100.00</totalNetValue><totalVatAmount>24.00</totalVatAmount><totalGrossValue>124.00</totalGrossValue></invoiceSummary>
</invoice></invoicesDoc></RequestedDoc>"""


def test_supplier_credit_note_is_negative():
    (doc,) = parse_requested_docs(CREDIT_XML, "123456783")
    assert doc["gross_value"] == -124.0 and doc["vat_amount"] == -24.0 and doc["net_value"] == -100.0


@pytest.mark.asyncio
async def test_mydata_merges_into_existing_ocr_expense_instead_of_duplicating(monkeypatch):
    await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456783"})
    await db.expenses.insert_one({"firm_id": FIRM, "vendor": "ΔΕΗ Α.Ε.", "date": "2026-08-11", "amount": 124.0,
                                  "vat_amount": 20.0, "category": "Ρεύμα", "source": "email_ocr"})

    async def fake(settings, mark):
        if mark != "0":
            return ([], mark)
        return ([{"mark": "7", "issuer_afm": "111111111", "issuer_name": "ΔΕΗ", "date": "2026-08-10",
                  "invoice_type": "1.1", "series": "A", "aa": "1", "net_value": 100.0, "vat_amount": 24.0,
                  "gross_value": 124.0, "vat_rate": 24}], "7")

    monkeypatch.setattr(automation, "request_docs", fake)
    r = (await run_firm(FIRM, ["mydata_expenses"]))[0]
    assert r["stats"]["merged"] == 1 and r["stats"]["created"] == 0
    docs = await db.expenses.find({"firm_id": FIRM}).to_list(10)
    assert len(docs) == 1 and docs[0]["mydata_mark"] == "7" and docs[0]["vat_amount"] == 24.0
    assert docs[0]["category"] == "Ρεύμα"


@pytest.mark.asyncio
async def test_new_mydata_invoice_is_payable_with_due_date(monkeypatch):
    await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456783"})

    async def fake(settings, mark):
        if mark != "0":
            return ([], mark)
        return ([{"mark": "9", "issuer_afm": "222222222", "issuer_name": "Προμηθευτής", "date": "2026-08-10",
                  "invoice_type": "1.1", "series": "", "aa": "", "net_value": 10.0, "vat_amount": 2.4,
                  "gross_value": 12.4, "vat_rate": 24}], "9")

    monkeypatch.setattr(automation, "request_docs", fake)
    await run_firm(FIRM, ["mydata_expenses"])
    e = await db.expenses.find_one({"firm_id": FIRM})
    assert e["payment_status"] == "unpaid" and e["due_date"] == "2026-09-09"


@pytest.mark.asyncio
async def test_find_duplicate_respects_tolerances():
    await db.expenses.insert_one({"firm_id": FIRM, "vendor": "ΟΤΕ", "date": "2026-08-10", "amount": 50.0})
    assert await find_duplicate(FIRM, amount=50.0, doc_date="2026-08-12", vendor="ΟΤΕ ΑΕ")
    assert not await find_duplicate(FIRM, amount=50.0, doc_date="2026-08-20", vendor="ΟΤΕ")
    assert not await find_duplicate(FIRM, amount=51.0, doc_date="2026-08-10", vendor="ΟΤΕ")
    assert not await find_duplicate("other-firm", amount=50.0, doc_date="2026-08-10", vendor="ΟΤΕ")


@pytest.mark.asyncio
async def test_payables_job_alerts_once_and_inbox_pay_is_idempotent():
    await _seed({})
    today = date.today()
    late = await db.expenses.insert_one({"firm_id": FIRM, "vendor": "Α", "amount": 10.0, "date": "2026-01-01",
                                         "payment_status": "unpaid", "due_date": (today - timedelta(days=5)).isoformat()})
    await db.expenses.insert_one({"firm_id": FIRM, "vendor": "Β", "amount": 20.0, "date": "2026-01-01",
                                  "payment_status": "unpaid", "due_date": (today + timedelta(days=2)).isoformat()})
    await db.expenses.insert_one({"firm_id": FIRM, "vendor": "Γ", "amount": 30.0, "date": "2026-01-01",
                                  "payment_status": "unpaid", "due_date": (today + timedelta(days=40)).isoformat()})
    first = (await run_firm(FIRM, ["payables"]))[0]["stats"]
    again = (await run_firm(FIRM, ["payables"]))[0]["stats"]
    assert first == {"due_soon": 1, "overdue": 1} and again == {"due_soon": 0, "overdue": 0}

    note = await db.notifications.find_one({"type": "expense_overdue"})
    out = await resolve_notification(FIRM, str(note["_id"]), "u@example.gr", "paid")
    assert out["resolution"] == "paid"
    assert (await db.expenses.find_one({"_id": late.inserted_id}))["payment_status"] == "paid"
    assert await mark_expense_paid(FIRM, late.inserted_id) == "already_paid"
    assert await mark_expense_paid(FIRM, "bad") == "not_found"
