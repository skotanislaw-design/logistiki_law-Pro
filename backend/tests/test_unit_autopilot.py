"""Autopilot engine tests against an in-memory database — no SMTP, no AADE, no network."""
from datetime import date, datetime, timedelta, timezone

import pytest

import automation
import invoice_services
from automation import (
    reminder_due, mydata_retry_due, closed_vat_periods, digest_due, build_digest,
    notify, resolve_notification, run_firm, acquire_lock, release_lock, MYDATA_MAX_ATTEMPTS,
)
from db import db

FIRM = "firm-test"
BASE_SETTINGS = {
    "firm_id": FIRM, "company_name": "Δοκιμαστικό Γραφείο", "smtp_user": "office@example.gr", "smtp_password": "x",
    "email": "office@example.gr", "vat_period": "monthly", "autopilot_enabled": True,
    "auto_email_on_issue": True, "auto_reminders": True, "reminder_after_days": 30, "reminder_interval_days": 15,
    "reminder_max": 3, "auto_mydata_retry": True, "auto_reconcile_min_confidence": 90, "stale_draft_days": 7,
    "deadline_alert_days": [7, 1], "digest_frequency": "weekly", "digest_hour": 7,
}


# ----------------------------------------------------------------- policies --

def test_reminder_policy():
    today = date(2026, 9, 4)
    s = BASE_SETTINGS
    fresh = {"status": "issued", "date": "2026-08-20", "reminder_count": 0}
    assert not reminder_due(fresh, s, today)
    old = {"status": "issued", "date": "2026-07-01", "reminder_count": 0}
    assert reminder_due(old, s, today)
    recently_reminded = {**old, "reminder_count": 1, "reminder_sent_at": "2026-08-30T10:00:00+00:00"}
    assert not reminder_due(recently_reminded, s, today)
    due_again = {**old, "reminder_count": 1, "reminder_sent_at": "2026-08-01T10:00:00+00:00"}
    assert reminder_due(due_again, s, today)
    exhausted = {**old, "reminder_count": 3, "reminder_sent_at": "2026-08-01T10:00:00+00:00"}
    assert not reminder_due(exhausted, s, today)
    assert not reminder_due({**old, "status": "paid"}, s, today)


def test_mydata_backoff():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    assert mydata_retry_due({"status": "issued", "mydata_status": "pending", "mydata_attempts": 0}, now)
    recent = {"status": "issued", "mydata_status": "failed", "mydata_attempts": 2,
              "mydata_last_attempt_at": (now - timedelta(minutes=20)).isoformat()}
    assert not mydata_retry_due(recent, now)  # needs 30'
    assert mydata_retry_due({**recent, "mydata_last_attempt_at": (now - timedelta(minutes=31)).isoformat()}, now)
    assert not mydata_retry_due({**recent, "mydata_attempts": MYDATA_MAX_ATTEMPTS}, now)
    assert not mydata_retry_due({"status": "draft", "mydata_status": "not_sent"}, now)


def test_closed_vat_periods():
    monthly = closed_vat_periods(date(2026, 9, 4), "monthly", lookback=2)
    assert [p["period"] for p in monthly] == ["2026-07", "2026-08"]
    assert monthly[-1]["start"] == date(2026, 8, 1) and monthly[-1]["end"] == date(2026, 8, 31)
    assert monthly[-1]["due"] == date(2026, 9, 30)
    quarterly = closed_vat_periods(date(2026, 1, 15), "quarterly", lookback=2)
    assert [p["period"] for p in quarterly] == ["2025-Q3", "2025-Q4"]
    assert quarterly[-1]["end"] == date(2025, 12, 31)


def test_digest_schedule():
    monday_9 = datetime(2026, 9, 7, 6, 0, tzinfo=timezone.utc)   # 09:00 Athens
    assert digest_due({"digest_frequency": "weekly", "digest_hour": 7}, None, monday_9)
    assert not digest_due({"digest_frequency": "weekly", "digest_hour": 7}, monday_9.isoformat(), monday_9 + timedelta(hours=1))
    tuesday = monday_9 + timedelta(days=1)
    assert not digest_due({"digest_frequency": "weekly", "digest_hour": 7}, None, tuesday)
    assert digest_due({"digest_frequency": "daily", "digest_hour": 7}, monday_9.isoformat(), tuesday)
    assert not digest_due({"digest_frequency": "daily", "digest_hour": 7}, None, datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc))
    assert not digest_due({"digest_frequency": "off"}, None, monday_9)


def test_build_digest_mentions_decisions_and_unpaid():
    snap = {
        "today": date(2026, 9, 4), "company": "Γραφείο", "income_net": 1000, "expense_net": 200, "output_vat": 240,
        "input_vat": 48, "vat_due": 192, "unpaid": [{"number": "ΤΠΥ-2026-0001", "client_name": "Πελάτης", "total": 124, "date": "2026-07-01", "reminder_count": 1}],
        "unpaid_total": 124, "deadlines": [{"due_date": "2026-09-30", "title": "Υποβολή & Πληρωμή ΦΠΑ", "description": "", "category": "vat"}],
        "activity": [{"title": "Στάλθηκε υπενθύμιση"}], "open_items": [{"title": "Ταιριάζει η κατάθεση;", "message": "620 €"}],
        "mydata_pending": 2, "vat_periods": [{"period": "2026-08", "vat_due": 192, "due_date": "2026-09-30"}], "public_url": "https://nomos.example",
    }
    subject, text, html = build_digest(snap, "weekly")
    assert "Εβδομαδιαία" in subject
    assert "ΧΡΕΙΑΖΟΝΤΑΙ ΑΠΟΦΑΣΗ (1)" in text and "Ταιριάζει η κατάθεση;" in text
    assert "ΤΠΥ-2026-0001" in text and "myDATA: 2" in text
    assert "https://nomos.example/autopilot" in html and "<strong>192,00 €</strong>" in html


# ------------------------------------------------------------------ engine ---

async def _seed(settings_extra=None):
    settings = {**BASE_SETTINGS, **(settings_extra or {})}
    await db.settings.insert_one(dict(settings))
    client = await db.clients.insert_one({"firm_id": FIRM, "name": "Παπαδόπουλος", "email": "client@example.gr", "afm": "123456789"})
    return settings, str(client.inserted_id)


@pytest.mark.asyncio
async def test_notify_dedupes_unresolved_and_resolve_reconcile(monkeypatch):
    settings, client_id = await _seed()
    inv = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": client_id, "client_name": "Παπαδόπουλος",
                                        "status": "issued", "total": 124.0, "date": "2026-08-01"})
    tx = await db.bank_transactions.insert_one({"firm_id": FIRM, "amount": 124.0, "date": "2026-08-03", "description": "x",
                                                "match_status": "auto_matched", "match_confidence": 70, "matched_invoice_id": str(inv.inserted_id)})
    first = await notify(FIRM, "reconcile_review", "warning", "t", entity="bank_tx", entity_id=tx.inserted_id, requires_action=True, dedupe_key="k1")
    dup = await notify(FIRM, "reconcile_review", "warning", "t", entity="bank_tx", entity_id=tx.inserted_id, requires_action=True, dedupe_key="k1")
    assert first and dup is None
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 1

    out = await resolve_notification(FIRM, str(first["_id"]), "user@example.gr", "confirm")
    assert out["resolution"] == "confirmed"
    assert (await db.invoices.find_one({"_id": inv.inserted_id}))["status"] == "paid"
    assert (await db.bank_transactions.find_one({"_id": tx.inserted_id}))["match_status"] == "manual_matched"
    assert (await db.notifications.find_one({"_id": first["_id"]}))["resolved_by"] == "user@example.gr"
    # resolved → same key may be raised again
    assert await notify(FIRM, "reconcile_review", "warning", "t", dedupe_key="k1", requires_action=True)


@pytest.mark.asyncio
async def test_full_cycle_sends_emails_reminders_and_reconciles(monkeypatch):
    settings, client_id = await _seed()
    sent = []

    async def fake_send(settings, to, subject, text, html, pdf_bytes=None, pdf_filename=""):
        sent.append({"to": to, "subject": subject, "pdf": bool(pdf_bytes)})

    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(automation, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(invoice_services, "build_payment_link", lambda *a, **k: _async_value(""))
    import pdf_utils
    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", lambda *a, **k: b"%PDF-fake")

    today = date.today()
    old = (today - timedelta(days=45)).isoformat()
    issued_unsent = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": client_id, "client_name": "Παπαδόπουλος",
                                                  "status": "issued", "total": 124.0, "subtotal": 100, "vat_amount": 24, "date": today.isoformat(),
                                                  "email_sent_at": None, "reminder_count": 0, "mydata_status": "pending", "line_items": []})
    overdue = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0002", "client_id": client_id, "client_name": "Παπαδόπουλος",
                                            "status": "issued", "total": 620.0, "subtotal": 500, "vat_amount": 120, "date": old,
                                            "email_sent_at": "2026-01-01T00:00:00+00:00", "reminder_count": 0, "mydata_status": "synced", "line_items": []})
    stale_draft = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0003", "client_id": client_id, "client_name": "Παπαδόπουλος",
                                                "status": "draft", "total": 50.0, "date": old, "created_at": "2026-01-01T00:00:00+00:00", "line_items": []})
    hi = await db.bank_transactions.insert_one({"firm_id": FIRM, "amount": 620.0, "date": today.isoformat(), "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ",
                                                "match_status": "auto_matched", "match_confidence": 95, "matched_invoice_id": str(overdue.inserted_id),
                                                "matched_invoice_number": "ΤΠΥ-2026-0002"})
    lo = await db.bank_transactions.insert_one({"firm_id": FIRM, "amount": 124.0, "date": today.isoformat(), "description": "?",
                                                "match_status": "auto_matched", "match_confidence": 60, "matched_invoice_id": str(issued_unsent.inserted_id),
                                                "matched_invoice_number": "ΤΠΥ-2026-0001"})

    results = await run_firm(FIRM, ["send_issued_invoices", "reconcile", "reminders", "mydata_retry",
                                    "stale_drafts", "vat_periods", "deadlines", "outbox"])
    by_job = {r["job"]: r for r in results}
    assert all(r["status"] != "error" for r in results), by_job

    # 1. the unsent invoice was queued and then actually sent with its PDF by the outbox
    assert by_job["send_issued_invoices"]["stats"] == {"queued": 1, "skipped": 0}
    assert by_job["outbox"]["stats"]["sent"] == 1
    assert (await db.invoices.find_one({"_id": issued_unsent.inserted_id}))["email_sent_to"] == "client@example.gr"
    # 2. high-confidence match auto-confirmed → invoice paid, low-confidence → decision in inbox
    assert by_job["reconcile"]["stats"] == {"confirmed": 1, "sent_for_review": 1}
    assert (await db.invoices.find_one({"_id": overdue.inserted_id}))["status"] == "paid"
    assert (await db.bank_transactions.find_one({"_id": hi.inserted_id}))["match_status"] == "auto_confirmed"
    review = await db.notifications.find_one({"type": "reconcile_review", "entity_id": str(lo.inserted_id)})
    assert review and review["requires_action"] and [a["key"] for a in review["actions"]] == ["confirm", "unmatch", "ignore"]
    # 3. reminders: the overdue one is now paid, the fresh one isn't due → nothing queued
    assert by_job["reminders"]["stats"]["queued"] == 0
    # 4. no AADE credentials → one inbox item, invoice stays pending
    assert by_job["mydata_retry"]["stats"]["skipped"] and by_job["mydata_retry"]["stats"]["pending"] == 1
    assert await db.notifications.count_documents({"dedupe_key": "mydata_not_configured"}) == 1
    # 5. stale draft flagged once
    assert by_job["stale_drafts"]["stats"] == {"flagged": 1}
    assert await db.notifications.count_documents({"dedupe_key": f"stale_draft:{stale_draft.inserted_id}"}) == 1
    # 6. previous months got a VAT period
    assert by_job["vat_periods"]["stats"]["computed"] == 3
    assert await db.vat_periods.count_documents({"firm_id": FIRM}) == 3
    # every job produced an audit row
    assert await db.automation_runs.count_documents({"firm_id": FIRM}) == 8

    # run again: idempotent — nothing re-sent, nothing duplicated
    results2 = await run_firm(FIRM, ["send_issued_invoices", "reconcile", "stale_drafts", "vat_periods", "outbox"])
    stats2 = {r["job"]: r["stats"] for r in results2}
    assert stats2["send_issued_invoices"] == {"queued": 0, "skipped": 0}
    assert stats2["reconcile"] == {"confirmed": 0, "sent_for_review": 0}
    assert stats2["stale_drafts"] == {"flagged": 0} and stats2["vat_periods"] == {"computed": 0}
    assert stats2["outbox"]["sent"] == 0
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_reminder_is_sent_when_due_and_counted(monkeypatch):
    settings, client_id = await _seed()
    sent = []

    async def fake_send(settings, to, subject, text, html, pdf_bytes=None, pdf_filename=""):
        sent.append(subject)

    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(invoice_services, "build_payment_link", lambda *a, **k: _async_value("https://pay.example/x"))
    import pdf_utils
    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", lambda *a, **k: b"%PDF-fake")
    old = (date.today() - timedelta(days=40)).isoformat()
    inv = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0009", "client_id": client_id, "client_name": "Παπαδόπουλος",
                                        "status": "issued", "total": 100.0, "date": old, "reminder_count": 0, "line_items": []})
    queued = (await run_firm(FIRM, ["reminders"]))[0]
    assert queued["stats"]["queued"] == 1
    assert (await run_firm(FIRM, ["outbox"]))[0]["stats"]["sent"] == 1
    assert "Υπενθύμιση εξόφλησης" in sent[0]
    doc = await db.invoices.find_one({"_id": inv.inserted_id})
    assert doc["reminder_count"] == 1 and doc["reminder_sent_at"]
    # immediately again → interval not elapsed
    assert (await run_firm(FIRM, ["reminders"]))[0]["stats"]["queued"] == 0


@pytest.mark.asyncio
async def test_mydata_retry_transmits_when_credentials_exist(monkeypatch):
    settings, client_id = await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "111111111"})
    calls = []

    async def fake_send_invoice(settings, invoice, client):
        calls.append(invoice["number"])
        if invoice["number"].endswith("2"):
            from mydata_client import MyDataError
            raise MyDataError("Λάθος ΑΦΜ αντισυμβαλλομένου")
        return {"mark": "400123", "uid": "uid-1"}

    monkeypatch.setattr(invoice_services, "send_invoice", fake_send_invoice)
    ok = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": client_id, "status": "issued", "mydata_status": "pending", "mydata_attempts": 0, "line_items": []})
    bad = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0002", "client_id": client_id, "status": "issued", "mydata_status": "failed",
                                        "mydata_attempts": MYDATA_MAX_ATTEMPTS - 1, "mydata_last_attempt_at": "2020-01-01T00:00:00+00:00", "line_items": []})
    r = (await run_firm(FIRM, ["mydata_retry"]))[0]
    assert r["stats"] == {"synced": 1, "failed": 1}
    assert (await db.invoices.find_one({"_id": ok.inserted_id}))["mydata_mark"] == "400123"
    bad_doc = await db.invoices.find_one({"_id": bad.inserted_id})
    assert bad_doc["mydata_attempts"] == MYDATA_MAX_ATTEMPTS and "ΑΦΜ" in bad_doc["mydata_error"]
    crit = await db.notifications.find_one({"type": "mydata_failed"})
    assert crit and crit["level"] == "critical" and crit["requires_action"]
    assert sorted(calls) == ["ΤΠΥ-2026-0001", "ΤΠΥ-2026-0002"]


@pytest.mark.asyncio
async def test_digest_job_sends_and_records(monkeypatch):
    settings, _ = await _seed({"digest_email": "boss@example.gr"})
    sent = []

    async def fake_send(settings, to, subject, text, html, pdf_bytes=None, pdf_filename=""):
        sent.append((to, subject))

    monkeypatch.setattr(automation, "send_email_with_pdf", fake_send)
    r = (await run_firm(FIRM, ["digest"], force=True))[0]
    assert r["stats"]["queued_to"] == "boss@example.gr"
    assert (await run_firm(FIRM, ["outbox"]))[0]["stats"]["sent"] == 1
    assert sent[0][0] == "boss@example.gr"
    assert (await db.settings.find_one({"firm_id": FIRM}))["last_digest_at"]


@pytest.mark.asyncio
async def test_autopilot_disabled_skips_everything():
    await _seed({"autopilot_enabled": False})
    results = await run_firm(FIRM)
    assert results == [{"job": "*", "status": "skipped", "stats": {"reason": "autopilot_disabled"}}]


@pytest.mark.asyncio
async def test_lock_is_exclusive_until_released():
    assert await acquire_lock("cycle-test", ttl_seconds=60)
    assert not await acquire_lock("cycle-test", ttl_seconds=60)
    await release_lock("cycle-test")
    assert await acquire_lock("cycle-test", ttl_seconds=60)


async def _async_value(v):
    return v


# ------------------------------------------------ myDATA expense inbound ----

@pytest.mark.asyncio
async def test_mydata_expense_sync_creates_expenses_once(monkeypatch):
    settings, _ = await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456789"})
    calls = []

    async def fake_request_docs(settings, mark):
        calls.append(mark)
        if mark == "0":
            return ([
                {"mark": "400001", "uid": "A", "issuer_afm": "111111111", "issuer_name": "ΔΕΗ ΑΕ",
                 "date": "2026-08-10", "invoice_type": "1.1", "series": "A", "aa": "55",
                 "net_value": 100.0, "vat_amount": 24.0, "gross_value": 124.0, "vat_rate": 24},
                {"mark": "400002", "uid": "B", "issuer_afm": "222222222", "issuer_name": "",
                 "date": "2026-08-12", "invoice_type": "2.1", "series": "B", "aa": "9",
                 "net_value": 200.0, "vat_amount": 48.0, "gross_value": 248.0, "vat_rate": 24},
            ], "400002")
        return ([], mark)

    monkeypatch.setattr(automation, "request_docs", fake_request_docs)
    r = (await run_firm(FIRM, ["mydata_expenses"]))[0]
    assert r["stats"]["created"] == 2 and r["stats"]["last_mark"] == "400002"

    expenses = await db.expenses.find({"firm_id": FIRM}).to_list(10)
    assert {e["mydata_mark"] for e in expenses} == {"400001", "400002"}
    dei = next(e for e in expenses if e["mydata_mark"] == "400001")
    assert dei["vendor"] == "ΔΕΗ ΑΕ" and dei["amount"] == 124.0 and dei["vat_amount"] == 24.0
    assert dei["source"] == "mydata" and dei["review_status"] == "needs_review"
    anonymous = next(e for e in expenses if e["mydata_mark"] == "400002")
    assert "222222222" in anonymous["vendor"]  # χωρίς επωνυμία, πέφτουμε στο ΑΦΜ
    # ο δείκτης προχώρησε ώστε η επόμενη κλήση να μην ξανακατεβάσει τα ίδια
    assert (await db.settings.find_one({"firm_id": FIRM}))["mydata_expense_last_mark"] == "400002"

    # δεύτερος κύκλος: συνεχίζει από το τελευταίο MARK, τίποτα διπλό
    r2 = (await run_firm(FIRM, ["mydata_expenses"]))[0]
    assert r2["stats"]["created"] == 0
    assert calls == ["0", "400002"]
    assert await db.expenses.count_documents({"firm_id": FIRM}) == 2


@pytest.mark.asyncio
async def test_mydata_expense_sync_skips_without_credentials_or_afm(monkeypatch):
    await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": ""})
    r = (await run_firm(FIRM, ["mydata_expenses"]))[0]
    assert r["stats"] == {"skipped": True, "reason": "afm"}
    assert await db.notifications.count_documents({"dedupe_key": "mydata_afm_missing"}) == 1


@pytest.mark.asyncio
async def test_mydata_expense_sync_reports_aade_failure(monkeypatch):
    from mydata_client import MyDataError as _E
    await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456789"})

    async def boom(settings, mark):
        raise _E("Υπέρβαση ορίου κλήσεων προς την ΑΑΔΕ.")

    monkeypatch.setattr(automation, "request_docs", boom)
    r = (await run_firm(FIRM, ["mydata_expenses"]))[0]
    assert r["status"] == "error" and "ΑΑΔΕ" in r["error"]
    note = await db.notifications.find_one({"dedupe_key": "mydata_expense_sync_failed"})
    assert note and note["requires_action"] is False


# ----------------------------------------------- cancellation / credit ------

@pytest.mark.asyncio
async def test_cancel_invoice_calls_aade_and_blocks_paid(monkeypatch):
    import invoice_services as svc
    settings, client_id = await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456789"})
    cancelled = []

    async def fake_cancel(settings, mark):
        cancelled.append(mark)
        return {"cancellation_mark": "500999"}

    monkeypatch.setattr(svc, "cancel_invoice", fake_cancel)
    synced = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": client_id,
                                           "status": "issued", "mydata_status": "synced", "mydata_mark": "400001", "line_items": []})
    doc = await db.invoices.find_one({"_id": synced.inserted_id})
    await svc.cancel_in_mydata(FIRM, doc, settings)
    updated = await db.invoices.find_one({"_id": synced.inserted_id})
    assert updated["status"] == "cancelled" and updated["mydata_status"] == "cancelled"
    assert updated["mydata_cancellation_mark"] == "500999" and cancelled == ["400001"]

    paid = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0002", "client_id": client_id,
                                         "status": "paid", "mydata_status": "synced", "mydata_mark": "400002", "line_items": []})
    with pytest.raises(svc.InvoiceWorkflowError, match="πιστωτικό"):
        await svc.cancel_in_mydata(FIRM, await db.invoices.find_one({"_id": paid.inserted_id}), settings)


@pytest.mark.asyncio
async def test_credit_note_mirrors_original_and_links_both_ways():
    import invoice_services as svc
    settings, client_id = await _seed({"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456789"})
    original = await db.invoices.insert_one({
        "firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": client_id, "client_name": "Παπαδόπουλος",
        "status": "paid", "mydata_status": "synced", "mydata_mark": "400001", "total": 124.0,
        "line_items": [{"description": "Συμβουλευτική", "quantity": 1, "unit_price": 100, "vat_rate": 24}]})
    doc = await db.invoices.find_one({"_id": original.inserted_id})

    credit = await svc.build_credit_note(FIRM, doc, "user@example.gr", reason="Λάθος χρέωση")
    assert credit["number"].startswith("ΠΙΣ-")
    assert credit["invoice_type"] == "5.1" and credit["correlated_mark"] == "400001"
    assert credit["credit_note_for"] == str(original.inserted_id)
    assert credit["total"] == -124.0 and credit["line_items"][0]["unit_price"] == -100
    assert (await db.invoices.find_one({"_id": original.inserted_id}))["credit_note_id"] == str(credit["_id"])

    # δεύτερο πιστωτικό για το ίδιο τιμολόγιο απορρίπτεται
    with pytest.raises(svc.InvoiceWorkflowError, match="ήδη"):
        await svc.build_credit_note(FIRM, await db.invoices.find_one({"_id": original.inserted_id}), "user@example.gr")


@pytest.mark.asyncio
async def test_credit_note_partial_amount_and_requires_mark():
    import invoice_services as svc
    settings, client_id = await _seed()
    not_sent = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0003", "client_id": client_id,
                                             "status": "issued", "mydata_status": "pending", "total": 124.0,
                                             "line_items": [{"description": "x", "quantity": 1, "unit_price": 100, "vat_rate": 24}]})
    with pytest.raises(svc.InvoiceWorkflowError, match="MARK"):
        await svc.build_credit_note(FIRM, await db.invoices.find_one({"_id": not_sent.inserted_id}), "u@e.gr")

    synced = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0004", "client_id": client_id,
                                           "status": "issued", "mydata_status": "synced", "mydata_mark": "400009", "total": 124.0,
                                           "line_items": [{"description": "x", "quantity": 1, "unit_price": 100, "vat_rate": 24}]})
    credit = await svc.build_credit_note(FIRM, await db.invoices.find_one({"_id": synced.inserted_id}), "u@e.gr", amount=40)
    assert credit["subtotal"] == -40.0 and credit["total"] == -49.6


@pytest.mark.asyncio
async def test_payable_amount_subtracts_withholding():
    import invoice_services as svc
    settings, client_id = await _seed({"withholding_enabled": True, "withholding_min_net": 300})
    company = await db.clients.insert_one({"firm_id": FIRM, "name": "ΑΕ", "afm": "987654321", "client_type": "company"})
    inv = {"_id": "x", "firm_id": FIRM, "client_id": str(company.inserted_id), "subtotal": 1000.0, "total": 1240.0, "line_items": []}
    payable, withheld = await svc.payable_amount(FIRM, inv, settings)
    assert (payable, withheld) == (1040.0, 200.0)
    plain, none_withheld = await svc.payable_amount(FIRM, inv, {**settings, "withholding_enabled": False})
    assert (plain, none_withheld) == (1240.0, 0.0)


def test_autopilot_covers_firms_that_never_opened_the_dashboard():
    """Ο Autopilot άντλησε γραφεία από τη settings, που γράφεται στο πρώτο άνοιγμα του
    Dashboard: γραφεία δημιουργημένα μέσω API/import έμεναν εκτός χωρίς καμία ένδειξη."""
    import asyncio
    from fastapi.testclient import TestClient
    import server
    from db import db
    from automation import all_firm_ids, run_autopilot_cycle

    with TestClient(server.app, base_url="https://testserver") as c:
        firm = c.post("/api/auth/register",
                      json={"email": "viaapi@example.gr", "password": "Str0ngPass!",
                            "name": "Μέσω API"}).json()["firm_id"]

        loop = asyncio.get_event_loop_policy().new_event_loop()
        # Καμία κλήση στο /api/settings: το γραφείο δεν έχει ακόμη έγγραφο ρυθμίσεων.
        assert loop.run_until_complete(db.settings.find_one({"firm_id": firm})) is None
        assert firm in loop.run_until_complete(all_firm_ids())

        loop.run_until_complete(run_autopilot_cycle())
        assert loop.run_until_complete(db.automation_runs.count_documents({"firm_id": firm})) > 0
