"""Open banking (PSD2): κανονικοποίηση κινήσεων, ροή σύνδεσης, συγχρονισμός."""
from datetime import date, timedelta

import pytest

import automation
import openbanking
from automation import run_firm
from db import db
from openbanking import normalize_transaction, requisition_expiring, openbanking_ready
from tax_utils import athens_today

FIRM = "firm-ob"
SETTINGS = {"firm_id": FIRM, "company_name": "Γραφείο", "autopilot_enabled": True,
            "openbanking_enabled": True, "gocardless_secret_id": "sid",
            "gocardless_secret_key": "skey", "gocardless_institution_name": "Πειραιώς",
            "gocardless_account_ids": ["acc-1"]}


def test_normalize_handles_the_fields_banks_actually_send():
    raw = {
        "transactionId": "TX-1",
        "bookingDate": "2026-08-10",
        "valueDate": "2026-08-11",
        "transactionAmount": {"amount": "620.00", "currency": "EUR"},
        "remittanceInformationUnstructured": "ΤΠΥ-2026-0001",
        "debtorName": "ΠΑΠΑΔΟΠΟΥΛΟΣ",
        "endToEndId": "E2E-9",
    }
    tx = normalize_transaction(raw)
    assert tx["date"] == "2026-08-10" and tx["amount"] == 620.0
    assert "ΤΠΥ-2026-0001" in tx["description"] and "ΠΑΠΑΔΟΠΟΥΛΟΣ" in tx["description"]
    assert tx["reference"] == "E2E-9" and tx["external_id"] == "TX-1"


def test_normalize_falls_back_when_fields_are_missing():
    tx = normalize_transaction({"valueDate": "2026-08-12", "transactionAmount": {"amount": "-45.5"},
                                "remittanceInformationUnstructuredArray": ["ΔΕΗ", "ΛΟΓΑΡΙΑΣΜΟΣ"]})
    assert tx["date"] == "2026-08-12" and tx["amount"] == -45.5
    assert tx["description"] == "ΔΕΗ ΛΟΓΑΡΙΑΣΜΟΣ"
    assert tx["external_id"]              # συνθετικό id για dedup όταν λείπει transactionId

    empty = normalize_transaction({})
    assert empty["date"] == athens_today().isoformat() and empty["amount"] == 0.0
    assert empty["description"] == "Τραπεζική κίνηση"


def test_description_does_not_repeat_the_same_text():
    tx = normalize_transaction({"remittanceInformationUnstructured": "ΔΕΗ", "creditorName": "ΔΕΗ",
                                "transactionAmount": {"amount": "1"}})
    assert tx["description"] == "ΔΕΗ"


def test_readiness_and_consent_expiry():
    assert openbanking_ready(SETTINGS) is True
    assert openbanking_ready({"gocardless_secret_id": "x"}) is False
    today = date(2026, 9, 1)
    assert requisition_expiring({"gocardless_access_valid_until": "2026-09-05"}, today=today) is True
    assert requisition_expiring({"gocardless_access_valid_until": "2026-12-01"}, today=today) is False
    assert requisition_expiring({}, today=today) is False
    assert requisition_expiring({"gocardless_access_valid_until": "όχι ημερομηνία"}, today=today) is False


@pytest.mark.asyncio
async def test_sync_creates_matches_and_dedupes(monkeypatch):
    await db.settings.insert_one(dict(SETTINGS))
    client = await db.clients.insert_one({"firm_id": FIRM, "name": "Παπαδόπουλος", "email": "c@example.gr"})
    inv = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": str(client.inserted_id),
                                        "client_name": "Παπαδόπουλος", "status": "issued", "total": 620.0,
                                        "date": athens_today().isoformat(), "line_items": []})
    calls = []

    async def fake_fetch(settings, account_id, date_from=None):
        calls.append((account_id, date_from))
        return [
            {"date": athens_today().isoformat(), "amount": 620.0,
             "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ ΤΠΥ-2026-0001", "reference": "", "external_id": "TX-1"},
            {"date": athens_today().isoformat(), "amount": -80.0,
             "description": "ΔΕΗ", "reference": "", "external_id": "TX-2"},
        ]

    monkeypatch.setattr(automation, "fetch_transactions", fake_fetch, raising=False)
    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)

    r = (await run_firm(FIRM, ["bank_sync"]))[0]
    assert r["status"] != "error", r.get("error")
    assert r["stats"]["created"] == 2 and r["stats"]["auto_matched"] == 1

    income = await db.bank_transactions.find_one({"firm_id": FIRM, "amount": 620.0})
    assert income["match_status"] == "auto_matched"
    assert income["matched_invoice_id"] == str(inv.inserted_id)
    assert income["external_id"].startswith("psd2:acc-1:")
    assert income["bank"] == "Πειραιώς"
    # η χρέωση δεν συμφωνείται με τιμολόγιο
    outgoing = await db.bank_transactions.find_one({"firm_id": FIRM, "amount": -80.0})
    assert outgoing["match_status"] == "unmatched"

    # δεύτερος κύκλος: τίποτα διπλό, και ζητά κινήσεις με επικάλυψη ημερών
    r2 = (await run_firm(FIRM, ["bank_sync"]))[0]
    assert r2["stats"] == {"created": 0, "already_known": 2, "auto_matched": 0}
    assert len(calls) == 2 and calls[1][1] <= athens_today().isoformat()


@pytest.mark.asyncio
async def test_sync_skips_and_warns_without_link():
    await db.settings.insert_one({**SETTINGS, "gocardless_account_ids": []})
    r = (await run_firm(FIRM, ["bank_sync"]))[0]
    assert r["stats"] == {"skipped": True, "reason": "no_accounts"}
    assert await db.notifications.count_documents({"dedupe_key": "bank_not_linked"}) == 1

    await db.settings.update_one({"firm_id": FIRM}, {"$set": {"openbanking_enabled": False}})
    assert (await run_firm(FIRM, ["bank_sync"]))[0]["stats"] == {"skipped": True, "reason": "not_configured"}


@pytest.mark.asyncio
async def test_sync_warns_before_consent_expires(monkeypatch):
    soon = (athens_today() + timedelta(days=3)).isoformat()
    await db.settings.insert_one({**SETTINGS, "gocardless_access_valid_until": soon})

    async def fake_fetch(settings, account_id, date_from=None):
        return []

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    await run_firm(FIRM, ["bank_sync"])
    note = await db.notifications.find_one({"type": "bank_consent_expiring"})
    assert note and note["requires_action"] and soon in note["message"]


@pytest.mark.asyncio
async def test_provider_failure_surfaces_as_error_and_notification(monkeypatch):
    await db.settings.insert_one(dict(SETTINGS))

    async def boom(settings, account_id, date_from=None):
        raise openbanking.OpenBankingError("Υπέρβαση ορίου κλήσεων προς τον πάροχο.")

    monkeypatch.setattr(openbanking, "fetch_transactions", boom)
    r = (await run_firm(FIRM, ["bank_sync"]))[0]
    assert r["status"] == "error"
    assert await db.notifications.count_documents({"dedupe_key": "bank_sync_failed"}) == 1


def test_secret_key_is_in_the_encrypted_field_list():
    import crypto
    assert "gocardless_secret_key" in crypto.SECRET_SETTINGS_FIELDS
    masked = crypto.mask_settings_secrets(crypto.encrypt_settings_secrets({"gocardless_secret_key": "s3cret"}))
    assert masked["gocardless_secret_key"] == crypto.MASK
