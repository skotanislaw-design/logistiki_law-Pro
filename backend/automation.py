"""Autopilot — the zero-touch accounting engine.

Runs on the server on a schedule (see ``server.py``) and does the work a user
would otherwise do by hand, per firm (tenant):

* send freshly issued invoices to the client
* send payment reminders on a policy (after N days, every M days, max K times)
* retry myDATA transmissions that are pending or failed (with backoff)
* confirm high-confidence bank reconciliations, route the rest to the inbox
* compute the VAT return of every closed period
* raise tax-deadline alerts (VAT / EFKA) a few days ahead
* flag forgotten draft invoices
* pull receipts that were emailed to the office mailbox, OCR them into expenses
* email one daily/weekly digest with everything that happened and every decision
  that still needs a human

Every job is idempotent and safe to re-run; every side effect is recorded in
``automation_runs`` and, when a person needs to decide something, an entry is
written to ``notifications`` (the Autopilot inbox).  Only exceptions reach the user.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import socket
from datetime import datetime, timezone, timedelta, date as _date
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from db import db
from crypto import decrypt
from models import Notification, AutomationRun, VatPeriod, Expense
from tax_utils import athens_today, generate_tax_calendar, vat_deadline_for_period_end
from email_utils import smtp_ready, send_email_with_pdf
from mydata_client import mydata_ready, request_docs, MyDataError
from invoice_services import (
    email_invoice, send_reminder, transmit_to_mydata, mark_paid, load_client, InvoiceEmailError, firm_query,
    apply_bank_payment, SettlementRefused, close_tx_notifications, to_cents, numbering_report,
    invoice_balances, open_total,
)
import outbox

logger = logging.getLogger("autopilot")
ATHENS = ZoneInfo("Europe/Athens")

JOBS = [
    "send_issued_invoices", "reminders", "mydata_retry", "mydata_expenses", "reconcile",
    "deadlines", "stale_drafts", "vat_periods", "inbox_receipts", "digest",
    "bank_sync", "outbox", "backup_check", "numbering_check", "card_payments",
]
JOB_LABELS = {
    "send_issued_invoices": "Αποστολή εκδοθέντων τιμολογίων",
    "reminders": "Υπενθυμίσεις ανείσπρακτων",
    "mydata_retry": "Διαβίβαση myDATA (επανάληψη)",
    "mydata_expenses": "Λήψη εξόδων από myDATA",
    "reconcile": "Συμφωνία τραπεζικών κινήσεων",
    "deadlines": "Ειδοποιήσεις προθεσμιών ΦΠΑ/ΕΦΚΑ",
    "stale_drafts": "Ξεχασμένα πρόχειρα τιμολόγια",
    "vat_periods": "Κλείσιμο περιόδου ΦΠΑ",
    "inbox_receipts": "Παραστατικά από email (OCR)",
    "digest": "Ενημερωτικό email (digest)",
    "bank_sync": "Λήψη τραπεζικών κινήσεων (open banking)",
    "outbox": "Ουρά αποστολών (outbox)",
    "backup_check": "Έλεγχος αντιγράφων ασφαλείας",
    "numbering_check": "Έλεγχος αρίθμησης τιμολογίων",
    "card_payments": "Ολοκλήρωση πληρωμών κάρτας",
}
MYDATA_MAX_ATTEMPTS = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt(v) -> str:
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------- outbox handlers --

async def _load_invoice_and_settings(payload: dict):
    from bson import ObjectId
    firm_id = payload["firm_id"]
    settings = await db.settings.find_one({"firm_id": firm_id}) or {}
    invoice = await db.invoices.find_one(firm_query(firm_id, {"_id": ObjectId(payload["invoice_id"])}))
    if not invoice:
        raise ValueError("Το τιμολόγιο δεν υπάρχει πλέον")
    return firm_id, invoice, settings


async def _handle_email_invoice(payload: dict) -> dict:
    firm_id, invoice, settings = await _load_invoice_and_settings(payload)
    if invoice.get("email_sent_at"):
        return {"skipped": "already_sent"}      # idempotency: κάποιος πρόλαβε
    await email_invoice(firm_id, invoice, settings)
    return {"sent_to": invoice.get("email_sent_to")}


async def _handle_email_reminder(payload: dict) -> dict:
    firm_id, invoice, settings = await _load_invoice_and_settings(payload)
    if invoice.get("status") != "issued":
        return {"skipped": "no_longer_unpaid"}
    if int(invoice.get("reminder_count") or 0) != int(payload.get("expected_reminder_count", -1)):
        return {"skipped": "already_reminded"}  # στάλθηκε στο μεταξύ
    await send_reminder(firm_id, invoice, settings)
    return {"reminder_count": int(invoice.get("reminder_count") or 0) + 1}


async def _handle_email_digest(payload: dict) -> dict:
    firm_id = payload["firm_id"]
    settings = await db.settings.find_one({"firm_id": firm_id}) or {}
    if not smtp_ready(settings):
        raise ValueError("Δεν έχει ρυθμιστεί SMTP")
    snapshot = await firm_snapshot(firm_id, settings)
    subject, text, html = build_digest(snapshot, settings.get("digest_frequency", "weekly"))
    await send_email_with_pdf(settings, payload["to"], subject, text, html)
    await db.settings.update_one(firm_query(firm_id), {"$set": {"last_digest_at": _now()}})
    return {"sent_to": payload["to"]}


outbox.register_handler("email_invoice", _handle_email_invoice)
outbox.register_handler("email_reminder", _handle_email_reminder)
outbox.register_handler("email_digest", _handle_email_digest)


# ------------------------------------------------------------ notifications --

async def notify(firm_id: str, type_: str, level: str, title: str, message: str = "", *,
                 entity: str = "", entity_id: str = "", requires_action: bool = False,
                 actions: list | None = None, dedupe_key: str = "") -> dict | None:
    """Write an inbox entry. With ``dedupe_key`` an unresolved duplicate is not re-created."""
    if dedupe_key:
        existing = await db.notifications.find_one(firm_query(firm_id, {"dedupe_key": dedupe_key, "resolved_at": None}))
        if existing:
            return None
    n = Notification(
        firm_id=firm_id, type=type_, level=level, title=title, message=message, entity=entity,
        entity_id=str(entity_id or ""), requires_action=requires_action, actions=actions or [], dedupe_key=dedupe_key,
    )
    doc = n.to_mongo()
    result = await db.notifications.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def resolve_notification(firm_id: str, notification_id: str, user_email: str, decision: str = "done") -> dict:
    """Apply the user's decision for an inbox item and close it."""
    n = await db.notifications.find_one(firm_query(firm_id, {"_id": ObjectId(notification_id)}))
    if not n:
        raise ValueError("Η ειδοποίηση δεν βρέθηκε")
    outcome = decision
    if n.get("type") in ("reconcile_review", "reconcile_conflict") and n.get("entity") == "bank_tx":
        tx = await db.bank_transactions.find_one(firm_query(firm_id, {"_id": ObjectId(n["entity_id"])}))
        if tx and tx.get("match_status") in ("auto_confirmed", "manual_matched"):
            outcome = "already_settled"          # στο μεταξύ συμφωνήθηκε αλλιώς· η ειδοποίηση είναι παλιά
        elif tx:
            if decision == "confirm" and not tx.get("matched_invoice_id"):
                # Η κίνηση δεν έχει πια προτεινόμενο τιμολόγιο (αποδεσμεύτηκε, αγνοήθηκε): η κάρτα είναι παλιά
                # και το «Ναι, εξοφλήθηκε» δεν θα έκανε τίποτα — δεν το παρουσιάζουμε ως επιβεβαίωση.
                outcome = "stale"
            elif decision == "confirm":
                # Το «Ναι, εξοφλήθηκε» είναι ρητή απόφαση ανθρώπου: δέχεται και διαφορά ποσού (που
                # αναγράφεται στην ειδοποίηση). Δεν δέχεται τιμολόγιο που δεν είναι πια ανοιχτό, ούτε
                # οφειλόμενο διαφορετικό από αυτό που είδε ο άνθρωπος (π.χ. εξόφληση από πελατολογικό
                # αφού δημιουργήθηκε η κάρτα) — η απόφασή του αφορούσε άλλο ποσό.
                settings = await db.settings.find_one(firm_query(firm_id)) or {}
                check = await apply_bank_payment(
                    firm_id, tx, tx["matched_invoice_id"], settings=settings, paid_via="bank_manual",
                    status_on_success="manual_matched", accept_difference=True, dry_run=True)
                if check["status"] == "refused":
                    raise SettlementRefused(check["reason"], check["message"])
                seen_due = tx.get("review_due")
                if seen_due is None and abs(check["difference"]) >= 1:
                    # Κάρτα που δημιουργήθηκε πριν καταγράφεται το οφειλόμενο (ή χωρίς αυτό): δεν ξέρουμε
                    # ποιο ποσό είδε ο άνθρωπος. Μικρές διαφορές (< 1 €) περνούν όπως πάντα· μεγάλες όχι.
                    raise SettlementRefused(
                        "due_unverified",
                        f"Η κατάθεση ({_fmt(tx.get('amount'))}) διαφέρει από το τρέχον οφειλόμενο του τιμολογίου "
                        f"({_fmt(check['due'])}) κατά {_fmt(abs(check['difference']))}, και η ειδοποίηση δεν έχει "
                        "καταγεγραμμένο το ποσό που είχατε δει — δεν εξοφλήθηκε τίποτα. Επιλέξτε «Όχι, άλλο "
                        "τιμολόγιο» ή «Αγνόησε την κίνηση» και ελέγξτε την κατάθεση με τον λογιστή.")
                if seen_due is not None and to_cents(check["due"]) != to_cents(seen_due):
                    raise SettlementRefused(
                        "due_changed",
                        f"Το οφειλόμενο του τιμολογίου άλλαξε από {_fmt(seen_due)} σε {_fmt(check['due'])} από τότε που "
                        "δημιουργήθηκε η ειδοποίηση — δεν εξοφλήθηκε τίποτα. Ελέγξτε την κατάθεση: μπορεί να πρόκειται "
                        "για διπλοπληρωμή ή για πληρωμή άλλου τιμολογίου. Επιλέξτε «Όχι, άλλο τιμολόγιο» ή «Αγνόησε την "
                        "κίνηση» και αποφασίστε με τον λογιστή τι θα γίνει με το ποσό.")
                result = await apply_bank_payment(
                    firm_id, tx, tx["matched_invoice_id"], settings=settings, paid_via="bank_manual",
                    status_on_success="manual_matched", accept_difference=True)
                if result["status"] == "refused":
                    raise SettlementRefused(result["reason"], result["message"])
                outcome = "confirmed"
            elif decision == "ignore":
                await db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {"match_status": "ignored", "matched_invoice_id": None, "matched_invoice_number": None, "match_ambiguous": False}})
                outcome = "ignored"
            elif decision == "unmatch":
                await db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {"match_status": "unmatched", "matched_invoice_id": None, "matched_invoice_number": None, "match_confidence": 0, "match_ambiguous": False}})
                outcome = "unmatched"
    elif n.get("entity") == "outbox" and n.get("type") in ("outbox_stuck", "outbox_failed"):
        outcome = await outbox.resolve_stuck(firm_id, n["entity_id"], decision)
    elif n.get("type") == "expense_review" and n.get("entity") == "expense" and decision == "confirm":
        await db.expenses.update_one(firm_query(firm_id, {"_id": ObjectId(n["entity_id"])}), {"$set": {"review_status": "ok"}})
        outcome = "confirmed"
    elif n.get("type") == "expense_review" and n.get("entity") == "expense" and decision == "delete":
        await db.expenses.delete_one(firm_query(firm_id, {"_id": ObjectId(n["entity_id"])}))
        outcome = "deleted"
    await db.notifications.update_one(
        {"_id": n["_id"]},
        {"$set": {"resolved_at": _now(), "resolved_by": user_email, "resolution": outcome, "read": True}},
    )
    return {"resolution": outcome}


# ----------------------------------------------------------------- locking --

async def acquire_lock(name: str, ttl_seconds: int = 600) -> bool:
    """Cluster-wide lease so only one uvicorn worker / replica runs a cycle at a time."""
    now = datetime.now(timezone.utc)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
    try:
        await db.automation_locks.update_one(
            {"_id": name, "$or": [{"expires_at": {"$lt": now.isoformat()}}, {"expires_at": None}]},
            {"$set": {"owner": owner, "expires_at": expires, "acquired_at": now.isoformat()}},
            upsert=True,
        )
        rec = await db.automation_locks.find_one({"_id": name})
        return bool(rec and rec.get("owner") == owner and rec.get("expires_at") == expires)
    except Exception:
        # DuplicateKeyError = someone else holds it and it hasn't expired
        return False


async def release_lock(name: str):
    await db.automation_locks.update_one({"_id": name}, {"$set": {"expires_at": None}})


# --------------------------------------------------------------- run log ----

async def record_run(firm_id: str, job: str, func, *args, **kwargs) -> dict:
    started = datetime.now(timezone.utc)
    run = AutomationRun(firm_id=firm_id, job=job, started_at=started.isoformat())
    try:
        stats = await func(*args, **kwargs)
        run.stats = stats or {}
        run.status = "skipped" if (stats or {}).get("skipped") else "ok"
    except Exception as e:  # never let one job kill the cycle
        logger.exception("Autopilot job %s failed for %s", job, firm_id)
        run.status = "error"
        run.error = str(e)[:500]
    finished = datetime.now(timezone.utc)
    run.finished_at = finished.isoformat()
    run.duration_ms = int((finished - started).total_seconds() * 1000)
    await db.automation_runs.insert_one(run.to_mongo())
    return run.model_dump()


# ------------------------------------------------------------- policies -----
# Pure functions: easy to unit-test, no I/O.

def reminder_due(invoice: dict, settings: dict, today: _date | None = None) -> bool:
    if invoice.get("status") != "issued":
        return False
    if invoice.get("credit_note_id"):
        return False        # υπάρχει πιστωτικό: το ποσό που οφείλεται δεν είναι πια το ποσό του τιμολογίου
    today = today or athens_today()
    try:
        issued = _date.fromisoformat((invoice.get("date") or "")[:10])
    except ValueError:
        return False
    count = int(invoice.get("reminder_count") or 0)
    if count >= int(settings.get("reminder_max", 3)):
        return False
    age = (today - issued).days
    if count == 0:
        return age >= int(settings.get("reminder_after_days", 30))
    last = invoice.get("reminder_sent_at")
    if not last:
        return age >= int(settings.get("reminder_after_days", 30))
    try:
        last_d = datetime.fromisoformat(last).date()
    except ValueError:
        return True
    return (today - last_d).days >= int(settings.get("reminder_interval_days", 15))


def mydata_retry_due(invoice: dict, now: datetime | None = None) -> bool:
    """Exponential backoff: 15 min, 30, 60, 120, 240, 480 … capped by MYDATA_MAX_ATTEMPTS."""
    if invoice.get("status") not in ("issued", "paid"):
        return False
    if invoice.get("mydata_status") not in ("not_sent", "pending", "failed"):
        return False
    attempts = int(invoice.get("mydata_attempts") or 0)
    if attempts >= MYDATA_MAX_ATTEMPTS:
        return False
    if attempts == 0 or not invoice.get("mydata_last_attempt_at"):
        return True
    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(invoice["mydata_last_attempt_at"])
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(minutes=15 * (2 ** (attempts - 1)))


def closed_vat_periods(today: _date, vat_period: str = "monthly", lookback: int = 3) -> list[dict]:
    """The last ``lookback`` periods that have already ended, newest last."""
    periods = []
    if vat_period == "quarterly":
        q = (today.month - 1) // 3  # 0-based current quarter
        year = today.year
        for _ in range(lookback):
            q -= 1
            if q < 0:
                q = 3
                year -= 1
            start_m = q * 3 + 1
            end_m = start_m + 2
            start = _date(year, start_m, 1)
            end = _date(year, end_m, 1) + timedelta(days=32)
            end = end.replace(day=1) - timedelta(days=1)
            periods.append({"period": f"{year}-Q{q + 1}", "start": start, "end": end})
    else:
        y, m = today.year, today.month
        for _ in range(lookback):
            m -= 1
            if m < 1:
                m = 12
                y -= 1
            start = _date(y, m, 1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            periods.append({"period": f"{y}-{m:02d}", "start": start, "end": end})
    for p in periods:
        p["due"] = vat_deadline_for_period_end(p["end"], vat_period)
    return list(reversed(periods))


def digest_due(settings: dict, last_sent_iso: str | None, now_utc: datetime | None = None) -> bool:
    freq = settings.get("digest_frequency", "weekly")
    if freq == "off":
        return False
    now_local = (now_utc or datetime.now(timezone.utc)).astimezone(ATHENS)
    if now_local.hour < int(settings.get("digest_hour", 7)):
        return False
    last_local = None
    if last_sent_iso:
        try:
            last_local = datetime.fromisoformat(last_sent_iso).astimezone(ATHENS)
        except ValueError:
            last_local = None
    if freq == "daily":
        return last_local is None or last_local.date() < now_local.date()
    # weekly: Mondays, once
    if now_local.weekday() != 0:
        return False
    return last_local is None or (now_local.date() - last_local.date()).days >= 6


# ----------------------------------------------------------------- jobs -----

async def job_send_issued_invoices(firm_id: str, settings: dict) -> dict:
    if not settings.get("auto_email_on_issue", True):
        return {"skipped": True}
    if not smtp_ready(settings):
        return {"skipped": True, "reason": "smtp"}
    queued = skipped = 0
    cursor = db.invoices.find(firm_query(firm_id, {"status": "issued", "email_sent_at": None}))
    async for inv in cursor:
        client = await load_client(firm_id, inv)
        if not client or not client.get("email"):
            await notify(firm_id, "invoice_email_failed", "warning",
                         f"Το τιμολόγιο {inv.get('number')} δεν μπορεί να σταλεί", "Ο πελάτης δεν έχει email.",
                         entity="invoice", entity_id=inv["_id"], requires_action=True,
                         dedupe_key=f"invoice_email_failed:{inv['_id']}")
            skipped += 1
            continue
        entry = await outbox.enqueue(
            firm_id, "email_invoice",
            {"firm_id": firm_id, "invoice_id": str(inv["_id"]), "number": inv.get("number", ""), "to": client["email"]},
            dedupe_key=f"email_invoice:{inv['_id']}")
        queued += 1 if entry else 0
    return {"queued": queued, "skipped": skipped}


async def job_reminders(firm_id: str, settings: dict) -> dict:
    if not settings.get("auto_reminders", True):
        return {"skipped": True}
    if not smtp_ready(settings):
        return {"skipped": True, "reason": "smtp"}
    queued = skipped = 0
    today = athens_today()
    async for inv in db.invoices.find(firm_query(firm_id, {"status": "issued"})):
        if not reminder_due(inv, settings, today):
            skipped += 1
            continue
        client = await load_client(firm_id, inv)
        if not client or not client.get("email"):
            await notify(firm_id, "reminder_failed", "warning", f"Δεν στάλθηκε υπενθύμιση για {inv.get('number')}",
                         "Ο πελάτης δεν έχει καταχωρημένο email.",
                         entity="invoice", entity_id=inv["_id"], requires_action=True,
                         dedupe_key=f"reminder_failed:{inv['_id']}")
            continue
        count = int(inv.get("reminder_count") or 0)
        entry = await outbox.enqueue(
            firm_id, "email_reminder",
            {"firm_id": firm_id, "invoice_id": str(inv["_id"]), "number": inv.get("number", ""),
             "to": client["email"], "expected_reminder_count": count},
            dedupe_key=f"email_reminder:{inv['_id']}:{count}")
        queued += 1 if entry else 0
    return {"queued": queued, "not_due": skipped}


async def job_mydata_retry(firm_id: str, settings: dict) -> dict:
    if not settings.get("auto_mydata_retry", True):
        return {"skipped": True}
    if not mydata_ready(settings):
        pending = await db.invoices.count_documents(firm_query(firm_id, {"status": {"$in": ["issued", "paid"]}, "mydata_status": {"$in": ["not_sent", "pending", "failed"]}}))
        if pending:
            await notify(firm_id, "mydata_not_configured", "warning",
                         f"{pending} παραστατικά περιμένουν διαβίβαση στο myDATA",
                         "Συμπληρώστε User ID & Subscription Key ΑΑΔΕ στις Ρυθμίσεις. Το Autopilot θα τα διαβιβάσει αυτόματα.",
                         entity="settings", requires_action=True, dedupe_key="mydata_not_configured")
        return {"skipped": True, "reason": "credentials", "pending": pending}
    synced = failed = 0
    now = datetime.now(timezone.utc)
    async for inv in db.invoices.find(firm_query(firm_id, {"status": {"$in": ["issued", "paid"]}, "mydata_status": {"$in": ["not_sent", "pending", "failed"]}})):
        if not mydata_retry_due(inv, now):
            continue
        update = await transmit_to_mydata(firm_id, inv, settings)
        if update.get("mydata_status") == "synced":
            synced += 1
            await db.notifications.update_many(firm_query(firm_id, {"dedupe_key": f"mydata_failed:{inv['_id']}", "resolved_at": None}),
                                               {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
        else:
            failed += 1
            attempts = int(inv.get("mydata_attempts") or 0) + 1
            if attempts >= MYDATA_MAX_ATTEMPTS:
                await notify(firm_id, "mydata_failed", "critical",
                             f"Το {inv.get('number')} απορρίπτεται από την ΑΑΔΕ",
                             f"{update.get('mydata_error')} — Μετά από {attempts} προσπάθειες χρειάζεται διόρθωση (ΑΦΜ πελάτη; στοιχεία εταιρείας;).",
                             entity="invoice", entity_id=inv["_id"], requires_action=True,
                             dedupe_key=f"mydata_failed:{inv['_id']}")
    if synced:
        await db.notifications.update_many(firm_query(firm_id, {"dedupe_key": "mydata_not_configured", "resolved_at": None}),
                                           {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
    return {"synced": synced, "failed": failed}


async def job_reconcile(firm_id: str, settings: dict) -> dict:
    """Εξοφλεί αυτόματα μόνο ό,τι είναι ασφαλές· όλα τα υπόλοιπα πάνε στο inbox με την αιτία τους.

    Αυτόματη εξόφληση απαιτεί **και** βεβαιότητα ≥ όριο **και** μονοσήμαντη πρόταση **και** ακριβές
    ποσό για ανοιχτό τιμολόγιο (βλ. `apply_bank_payment`). Η βεβαιότητα μόνη της δεν αρκεί:
    ένα σκορ 90+ μπορεί να προκύπτει από όνομα και ημερομηνία ενώ το ποσό διαφέρει.
    """
    threshold = int(settings.get("auto_reconcile_min_confidence", 90) or 0)
    confirmed = review = 0
    async for tx in db.bank_transactions.find(firm_query(firm_id, {"match_status": "auto_matched"})):
        inv_id = tx.get("matched_invoice_id")
        if not inv_id:
            continue
        # Χωρίς σήμανση (παλιά εγγραφή) η πρόταση θεωρείται αμφίβολη — βλ. flag_unchecked_bank_suggestions.
        ambiguous = bool(tx.get("match_ambiguous", True))
        confident = bool(threshold) and int(tx.get("match_confidence") or 0) >= threshold and not ambiguous
        result = await apply_bank_payment(
            firm_id, tx, inv_id, settings=settings, paid_via="bank_auto",
            status_on_success="auto_confirmed", dry_run=not confident)

        if confident and result["status"] in ("paid", "linked"):
            confirmed += 1
            # Αν η κίνηση είχε πάει νωρίτερα για έλεγχο (π.χ. χαμηλότερη βεβαιότητα, άλλο οφειλόμενο, αλλαγή
            # ορίου), η κάρτα έμενε ανοιχτή με «Ναι, εξοφλήθηκε» πάνω σε κίνηση που έχει πια εξοφληθεί.
            await close_tx_notifications(firm_id, tx["_id"], "already_settled", "autopilot")
            await notify(firm_id, "reconciled", "success", f"Εξοφλήθηκε το {tx.get('matched_invoice_number')}",
                         f"Κατάθεση {_fmt(tx.get('amount'))} στις {tx.get('date')} — «{(tx.get('description') or '')[:60]}» (βεβαιότητα {tx.get('match_confidence')}%)"
                         + ("" if result["status"] == "paid" else " — το τιμολόγιο ήταν ήδη εξοφλημένο"),
                         entity="bank_tx", entity_id=tx["_id"])
            continue

        if result["status"] == "refused" and result["reason"] != "amount_mismatch":
            # Το τιμολόγιο δεν είναι πια ανοιχτό ή το έχει εξοφλήσει άλλη κίνηση/κανάλι: η κατάθεση
            # δεν μπορεί να του αποδοθεί (πιθανή διπλοπληρωμή ή επιστροφή). Αποδεσμεύεται και
            # ένας άνθρωπος αποφασίζει — δεν «συμφωνείται» σιωπηλά.
            await db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {
                "match_status": "unmatched", "matched_invoice_id": None, "matched_invoice_number": None,
                "match_confidence": 0, "match_ambiguous": False}})
            # Η προηγούμενη κάρτα ελέγχου (αν υπήρχε) αφορούσε σύνδεση που δεν υπάρχει πια.
            await close_tx_notifications(firm_id, tx["_id"], "superseded", "autopilot")
            review += 1
            await notify(firm_id, "reconcile_conflict", "warning",
                         f"Η κατάθεση {_fmt(tx.get('amount'))} δεν συνδέθηκε με το {tx.get('matched_invoice_number')}",
                         f"{tx.get('date')} — «{(tx.get('description') or '')[:80]}». {result['message']}",
                         entity="bank_tx", entity_id=tx["_id"], requires_action=True,
                         actions=[{"key": "ignore", "label": "Αγνόησε την κίνηση"}, {"key": "done", "label": "Το χειρίστηκα"}],
                         dedupe_key=f"reconcile_conflict:{tx['_id']}")
            continue

        if not tx.get("review_notified"):
            if ambiguous:
                why = "Ταιριάζουν και άλλα τιμολόγια με το ίδιο ποσό — επιλέξτε ποιο εξοφλείται."
            elif result["status"] == "refused":          # amount_mismatch
                why = result["message"]
            else:
                why = f"Η βεβαιότητα ({tx.get('match_confidence')}%) είναι κάτω από το όριο αυτόματης εξόφλησης ({threshold}%)."
            review += 1
            await db.bank_transactions.update_one({"_id": tx["_id"]}, {"$set": {
                "review_notified": True, "review_due": result.get("due")}})
            await notify(firm_id, "reconcile_review", "warning",
                         f"Ταιριάζει η κατάθεση {_fmt(tx.get('amount'))} με το {tx.get('matched_invoice_number')};",
                         f"{tx.get('date')} — «{(tx.get('description') or '')[:80]}» (βεβαιότητα {tx.get('match_confidence')}%). {why}",
                         entity="bank_tx", entity_id=tx["_id"], requires_action=True,
                         actions=[{"key": "confirm", "label": "Ναι, εξοφλήθηκε"}, {"key": "unmatch", "label": "Όχι, άλλο τιμολόγιο"}, {"key": "ignore", "label": "Αγνόησε την κίνηση"}],
                         dedupe_key=f"reconcile_review:{tx['_id']}")
    return {"confirmed": confirmed, "sent_for_review": review}


async def job_deadlines(firm_id: str, settings: dict) -> dict:
    today = athens_today()
    alert_days = settings.get("deadline_alert_days") or [7, 1]
    raised = 0
    for item in generate_tax_calendar(today, settings.get("vat_period", "monthly")):
        due = _date.fromisoformat(item["due_date"])
        left = (due - today).days
        if left in alert_days:
            extra = ""
            if item["category"] == "vat":
                vp = await db.vat_periods.find_one(firm_query(firm_id, {"due_date": item["due_date"]}))
                if vp:
                    extra = f" Εκτίμηση ΦΠΑ περιόδου {vp['period']}: {_fmt(vp['vat_due'])}."
            level = "critical" if left <= 1 else "warning"
            created = await notify(firm_id, "deadline", level,
                                   f"{item['title']} σε {left} ημέρ{'α' if left == 1 else 'ες'} ({due.strftime('%d/%m/%Y')})",
                                   item["description"] + extra, entity="deadline", entity_id=item["category"],
                                   dedupe_key=f"deadline:{item['category']}:{item['due_date']}:{left}")
            raised += 1 if created else 0
    return {"raised": raised}


async def job_stale_drafts(firm_id: str, settings: dict) -> dict:
    days = int(settings.get("stale_draft_days", 7) or 0)
    if days <= 0:
        return {"skipped": True}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    flagged = 0
    async for inv in db.invoices.find(firm_query(firm_id, {"status": "draft", "created_at": {"$lt": cutoff}})):
        created = await notify(firm_id, "stale_draft", "info",
                               f"Πρόχειρο τιμολόγιο{(' ' + inv['number']) if inv.get('number') else ''} περιμένει έκδοση",
                               f"{inv.get('client_name')} — {_fmt(inv.get('total'))}, δημιουργήθηκε {inv.get('created_at', '')[:10]}",
                               entity="invoice", entity_id=inv["_id"], requires_action=True,
                               dedupe_key=f"stale_draft:{inv['_id']}")
        flagged += 1 if created else 0
    return {"flagged": flagged}


async def compute_vat_period(firm_id: str, period: dict) -> VatPeriod:
    start, end = period["start"].isoformat(), period["end"].isoformat()
    invoices = await db.invoices.find(firm_query(firm_id, {"status": {"$in": ["issued", "paid"]}, "date": {"$gte": start, "$lte": end + "T23:59:59"}})).to_list(10000)
    expenses = await db.expenses.find(firm_query(firm_id, {"date": {"$gte": start, "$lte": end + "T23:59:59"}})).to_list(10000)
    output_vat = round(sum(i.get("vat_amount", 0) for i in invoices), 2)
    input_vat = round(sum(e.get("vat_amount", 0) for e in expenses), 2)
    breakdown: dict = {}
    for i in invoices:
        for rate, amt in (i.get("vat_breakdown") or {}).items():
            breakdown[rate] = round(breakdown.get(rate, 0) + amt, 2)
    return VatPeriod(
        firm_id=firm_id, period=period["period"], period_start=start, period_end=end, due_date=period["due"].isoformat(),
        output_vat=output_vat, input_vat=input_vat, vat_due=round(output_vat - input_vat, 2),
        income_net=round(sum(i.get("subtotal", 0) for i in invoices), 2),
        expense_net=round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in expenses), 2),
        invoice_count=len(invoices), expense_count=len(expenses), vat_breakdown=breakdown,
    )


async def job_vat_periods(firm_id: str, settings: dict) -> dict:
    computed = 0
    for period in closed_vat_periods(athens_today(), settings.get("vat_period", "monthly")):
        existing = await db.vat_periods.find_one(firm_query(firm_id, {"period": period["period"]}))
        vp = await compute_vat_period(firm_id, period)
        if existing:
            if existing.get("status") == "submitted":
                continue
            await db.vat_periods.update_one({"_id": existing["_id"]}, {"$set": {k: v for k, v in vp.to_mongo().items() if k != "status"}})
            continue
        await db.vat_periods.insert_one(vp.to_mongo())
        computed += 1
        await notify(firm_id, "vat_period", "info" if vp.vat_due <= 0 else "warning",
                     f"Έκλεισε η περίοδος ΦΠΑ {vp.period}: {'πληρωτέο ' + _fmt(vp.vat_due) if vp.vat_due > 0 else 'πιστωτικό ' + _fmt(abs(vp.vat_due))}",
                     f"Εκροές {_fmt(vp.output_vat)} − Εισροές {_fmt(vp.input_vat)} από {vp.invoice_count} τιμολόγια & {vp.expense_count} έξοδα. Προθεσμία υποβολής: {period['due'].strftime('%d/%m/%Y')}.",
                     entity="vat_period", entity_id=vp.period, dedupe_key=f"vat_period:{vp.period}")
    return {"computed": computed}


# ------------------------------------------------------- inbox receipts ----

def _fetch_unseen_attachments(settings: dict, limit: int = 20) -> list[dict]:
    """Blocking IMAP fetch (run in a thread). Returns [{message_id, subject, sender, filename, content_type, data}]."""
    import imaplib
    import email
    from email.header import decode_header
    out = []
    conn = imaplib.IMAP4_SSL(settings.get("imap_host") or "imap.gmail.com", int(settings.get("imap_port") or 993))
    try:
        conn.login(settings["imap_user"], decrypt(settings["imap_password"]))
        conn.select(settings.get("imap_folder") or "INBOX")
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return out
        ids = data[0].split()[-limit:]
        for mid in ids:
            status, msg_data = conn.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = ""
            for part, enc in decode_header(msg.get("Subject", "")):
                subject += part.decode(enc or "utf-8", "ignore") if isinstance(part, bytes) else part
            found = False
            for part in msg.walk():
                ctype = part.get_content_type()
                fname = part.get_filename() or ""
                if ctype in ("image/jpeg", "image/png", "application/pdf") or fname.lower().endswith((".jpg", ".jpeg", ".png", ".pdf")):
                    payload = part.get_payload(decode=True)
                    if payload:
                        found = True
                        out.append({"message_id": msg.get("Message-ID") or f"imap-{mid.decode()}", "subject": subject,
                                    "sender": msg.get("From", ""), "filename": fname or "attachment",
                                    "content_type": ctype, "data": payload})
            if found:
                conn.store(mid, "+FLAGS", "\\Seen")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return out


def _pdf_first_page_png(data: bytes) -> bytes | None:
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=data, filetype="pdf")
        pix = doc[0].get_pixmap(dpi=150)
        return pix.tobytes("png")
    except Exception:
        return None


async def job_inbox_receipts(firm_id: str, settings: dict) -> dict:
    if not settings.get("imap_enabled") or not settings.get("imap_user") or not settings.get("imap_password"):
        return {"skipped": True}
    from storage import upload_receipt_image
    from ai_runtime import ai_ready
    attachments = await asyncio.to_thread(_fetch_unseen_attachments, settings)
    created = review = 0
    for att in attachments:
        key = f"{att['message_id']}::{att['filename']}"
        if await db.inbox_processed.find_one(firm_query(firm_id, {"key": key})):
            continue
        receipt_path = None
        try:
            receipt_path = upload_receipt_image(att["data"], att["content_type"])
        except Exception as e:
            logger.warning("receipt storage failed: %s", e)
        parsed = None
        if ai_ready():
            try:
                from ai_helpers import ocr_receipt
                img = att["data"] if att["content_type"] != "application/pdf" else _pdf_first_page_png(att["data"])
                if img:
                    parsed = await ocr_receipt(base64.b64encode(img).decode())
            except Exception as e:
                logger.warning("OCR failed for %s: %s", att["filename"], e)
        needs_review = parsed is None or not parsed.get("amount")
        exp = Expense(
            firm_id=firm_id,
            vendor=(parsed or {}).get("vendor") or att["sender"][:80] or "Άγνωστος προμηθευτής",
            date=(parsed or {}).get("date") or athens_today().isoformat(),
            amount=float((parsed or {}).get("amount") or 0),
            vat_amount=float((parsed or {}).get("vat_amount") or 0),
            vat_rate=int((parsed or {}).get("vat_rate") or 24),
            category=(parsed or {}).get("category") or "Λοιπά",
            description=(parsed or {}).get("description") or f"Από email: {att['subject']}"[:200],
            receipt_image_url=receipt_path, source="email_ocr", created_by="autopilot",
            review_status="needs_review" if needs_review else "ok",
        )
        result = await db.expenses.insert_one(exp.to_mongo())
        await db.inbox_processed.insert_one({"firm_id": firm_id, "key": key, "expense_id": str(result.inserted_id), "processed_at": _now()})
        created += 1
        if needs_review:
            review += 1
            await notify(firm_id, "expense_review", "warning", "Νέο παραστατικό από email χρειάζεται έλεγχο",
                         f"«{att['subject']}» από {att['sender']} — {'το OCR δεν βρήκε ποσό' if parsed else 'το OCR δεν είναι διαθέσιμο'}. Συμπληρώστε ποσό/ΦΠΑ.",
                         entity="expense", entity_id=result.inserted_id, requires_action=True,
                         actions=[{"key": "confirm", "label": "Το έλεγξα"}, {"key": "delete", "label": "Διαγραφή"}],
                         dedupe_key=f"expense_review:{result.inserted_id}")
        else:
            await notify(firm_id, "expense_created", "success", f"Καταχωρήθηκε έξοδο {_fmt(exp.amount)} — {exp.vendor}",
                         f"Από email «{att['subject']}» · κατηγορία {exp.category} · ΦΠΑ {exp.vat_rate}%",
                         entity="expense", entity_id=result.inserted_id)
    return {"created": created, "needs_review": review}


async def job_mydata_expenses(firm_id: str, settings: dict) -> dict:
    """Κατεβάζει τα παραστατικά που διαβίβασαν οι προμηθευτές μας και τα κάνει έξοδα.

    Είναι ο ακριβέστερος τρόπος καταχώρησης εξόδων: τα ποσά προέρχονται από την ΑΑΔΕ,
    όχι από OCR. Κάθε MARK καταχωρείται μία φορά (dedupe στο `mydata_mark`).
    """
    if not settings.get("mydata_expense_sync", True):
        return {"skipped": True}
    if not mydata_ready(settings):
        return {"skipped": True, "reason": "credentials"}
    if not (settings.get("afm") or "").strip():
        await notify(firm_id, "mydata_afm_missing", "warning",
                     "Συμπληρώστε το ΑΦΜ της εταιρείας",
                     "Χωρίς ΑΦΜ το Autopilot δεν μπορεί να ξεχωρίσει ποια παραστατικά της ΑΑΔΕ είναι δικά σας έξοδα.",
                     entity="settings", requires_action=True, dedupe_key="mydata_afm_missing")
        return {"skipped": True, "reason": "afm"}

    last_mark = str(settings.get("mydata_expense_last_mark") or "0")
    try:
        docs, next_mark = await request_docs(settings, last_mark)
    except MyDataError as e:
        await notify(firm_id, "mydata_expense_sync_failed", "warning",
                     "Αποτυχία λήψης εξόδων από το myDATA", str(e),
                     entity="settings", requires_action=False, dedupe_key="mydata_expense_sync_failed")
        raise

    created = skipped = 0
    suppliers = {c.get("afm"): c.get("name") for c in await db.clients.find(firm_query(firm_id)).to_list(2000) if c.get("afm")}
    for doc in docs:
        if await db.expenses.find_one(firm_query(firm_id, {"mydata_mark": doc["mark"]})):
            skipped += 1
            continue
        vendor = doc.get("issuer_name") or suppliers.get(doc.get("issuer_afm")) or f"ΑΦΜ {doc.get('issuer_afm') or '—'}"
        exp = Expense(
            firm_id=firm_id,
            vendor=vendor[:120],
            date=doc["date"],
            amount=doc["gross_value"],
            vat_amount=doc["vat_amount"],
            vat_rate=doc["vat_rate"],
            category="Λοιπά",
            description=f"myDATA {doc.get('invoice_type') or ''} {doc.get('series') or ''}{('-' + doc['aa']) if doc.get('aa') else ''}".strip(),
            source="mydata",
            created_by="autopilot",
            mydata_mark=doc["mark"],
            supplier_afm=doc.get("issuer_afm", ""),
            mydata_invoice_type=doc.get("invoice_type", ""),
            # Τα ποσά είναι επίσημα· μόνο η κατηγορία χρειάζεται ματιά για τη φορολογική έκπτωση.
            review_status="needs_review",
        )
        try:
            result = await db.expenses.insert_one(exp.to_mongo())
        except DuplicateKeyError:
            # Ταυτόχρονος κύκλος (API/worker) πρόλαβε το ίδιο MARK· το unique index το κρατά μοναδικό.
            skipped += 1
            continue
        created += 1
        await notify(firm_id, "expense_created", "success",
                     f"Νέο έξοδο από myDATA: {_fmt(exp.amount)} — {exp.vendor}",
                     f"MARK {doc['mark']} · {doc['date']} · ΦΠΑ {_fmt(exp.vat_amount)}. Ελέγξτε την κατηγορία δαπάνης.",
                     entity="expense", entity_id=result.inserted_id)

    if next_mark != last_mark:
        await db.settings.update_one(firm_query(firm_id), {"$set": {"mydata_expense_last_mark": next_mark}})
    if created:
        await db.notifications.update_many(
            firm_query(firm_id, {"dedupe_key": "mydata_expense_sync_failed", "resolved_at": None}),
            {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
    return {"created": created, "already_known": skipped, "last_mark": next_mark}


# ---------------------------------------------------------------- digest ----

async def firm_snapshot(firm_id: str, settings: dict, since: datetime | None = None) -> dict:
    today = athens_today()
    month_start = today.replace(day=1).isoformat()
    invoices = await db.invoices.find(firm_query(firm_id, {"status": {"$in": ["issued", "paid"]}})).to_list(5000)
    expenses = await db.expenses.find(firm_query(firm_id, {"date": {"$gte": month_start}})).to_list(5000)
    month_inv = [i for i in invoices if (i.get("date") or "") >= month_start]
    issued = [i for i in invoices if i.get("status") == "issued"]
    # Ανείσπρακτα = ό,τι απομένει να εισπραχθεί (μετά την παρακράτηση και τα εξοφλημένα από πελατολογικό), όχι το μεικτό σύνολο.
    balances = await invoice_balances(firm_id, issued, settings)
    unpaid = sorted([{**i, "open_amount": balances[str(i["_id"])][1]} for i in issued], key=lambda i: i.get("date", ""))
    output_vat = round(sum(i.get("vat_amount", 0) for i in month_inv), 2)
    input_vat = round(sum(e.get("vat_amount", 0) for e in expenses), 2)
    since = since or datetime.now(timezone.utc) - timedelta(days=7 if settings.get("digest_frequency") == "weekly" else 1)
    since_iso = since.isoformat()
    activity = await db.notifications.find(firm_query(firm_id, {"created_at": {"$gte": since_iso}, "requires_action": False})).sort("created_at", -1).to_list(200)
    open_items = await db.notifications.find(firm_query(firm_id, {"requires_action": True, "resolved_at": None})).sort("created_at", -1).to_list(200)
    mydata_pending = await db.invoices.count_documents(firm_query(firm_id, {"status": {"$in": ["issued", "paid"]}, "mydata_status": {"$in": ["not_sent", "pending", "failed"]}}))
    vat_periods = await db.vat_periods.find(firm_query(firm_id, {"status": "computed"})).sort("period_end", -1).to_list(3)
    return {
        "today": today, "company": settings.get("company_name") or "Nomos One",
        "income_net": round(sum(i.get("subtotal", 0) for i in month_inv), 2),
        "expense_net": round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in expenses), 2),
        "output_vat": output_vat, "input_vat": input_vat, "vat_due": round(output_vat - input_vat, 2),
        "unpaid": unpaid, "unpaid_total": open_total(issued, balances),
        "deadlines": generate_tax_calendar(today, settings.get("vat_period", "monthly")),
        "activity": activity, "open_items": open_items, "mydata_pending": mydata_pending,
        "vat_periods": vat_periods, "public_url": settings.get("public_url") or os.environ.get("PUBLIC_URL", ""),
    }


def _owed(invoice: dict):
    """Το ποσό που εμφανίζεται δίπλα σε ανεξόφλητο τιμολόγιο: ό,τι απομένει να εισπραχθεί (`open_amount` από το
    `firm_snapshot`)· για στιγμιότυπο παλιότερης μορφής, χωρίς αυτό το πεδίο, το σύνολο του τιμολογίου."""
    amount = invoice.get("open_amount")
    return invoice.get("total") if amount is None else amount


def build_digest(snapshot: dict, frequency: str = "weekly") -> tuple[str, str, str]:
    """Return (subject, text, html) for the digest email — pure, testable."""
    s = snapshot
    label = "Εβδομαδιαία" if frequency == "weekly" else "Ημερήσια"
    subject = f"{label} ενημέρωση Autopilot — {s['company']} — {s['today'].strftime('%d/%m/%Y')}"
    lines = [f"{label} ενημέρωση — {s['company']} — {s['today'].strftime('%d/%m/%Y')}", ""]
    lines += ["ΤΡΕΧΩΝ ΜΗΝΑΣ",
              f"  Καθαρά έσοδα: {_fmt(s['income_net'])}", f"  Καθαρά έξοδα: {_fmt(s['expense_net'])}",
              f"  ΦΠΑ εκτίμηση: {_fmt(s['vat_due'])} (εκροές {_fmt(s['output_vat'])} − εισροές {_fmt(s['input_vat'])})",
              f"  Ανείσπρακτα: {len(s['unpaid'])} τιμολόγια / {_fmt(s['unpaid_total'])}", ""]
    if s["open_items"]:
        lines += [f"ΧΡΕΙΑΖΟΝΤΑΙ ΑΠΟΦΑΣΗ ({len(s['open_items'])})"]
        lines += [f"  • {n['title']}" + (f" — {n['message']}" if n.get("message") else "") for n in s["open_items"][:15]]
        lines.append("")
    else:
        lines += ["ΧΡΕΙΑΖΟΝΤΑΙ ΑΠΟΦΑΣΗ: τίποτα — όλα τρέχουν αυτόματα. ✔", ""]
    lines += ["ΠΡΟΘΕΣΜΙΕΣ"] + [f"  • {d['due_date'][8:10]}/{d['due_date'][5:7]} — {d['title']}" for d in s["deadlines"]] + [""]
    if s["vat_periods"]:
        lines += ["ΠΕΡΙΟΔΟΙ ΦΠΑ ΠΡΟΣ ΥΠΟΒΟΛΗ"] + [f"  • {v['period']}: {_fmt(v['vat_due'])} έως {v['due_date'][8:10]}/{v['due_date'][5:7]}" for v in s["vat_periods"]] + [""]
    if s["mydata_pending"]:
        lines += [f"myDATA: {s['mydata_pending']} παραστατικά σε αναμονή διαβίβασης.", ""]
    if s["activity"]:
        lines += [f"ΤΙ ΕΚΑΝΕ ΤΟ AUTOPILOT ({len(s['activity'])})"] + [f"  • {n['title']}" for n in s["activity"][:20]] + [""]
    if s["unpaid"]:
        lines += ["ΑΝΕΙΣΠΡΑΚΤΑ (παλαιότερα πρώτα)"] + [f"  • {i.get('number')} {i.get('client_name')} {_fmt(_owed(i))} ({i.get('date', '')[:10]}, υπενθ. {i.get('reminder_count', 0)})" for i in s["unpaid"][:15]] + [""]
    if s["public_url"]:
        lines += [f"Άνοιγμα Autopilot: {s['public_url']}/autopilot"]
    text = "\n".join(lines)

    def esc(t):
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def section(title, items, empty=""):
        if not items:
            return f"<h3 style='margin:18px 0 6px;font-size:13px;letter-spacing:2px;color:#8a6d1c'>{esc(title)}</h3><p style='margin:0;color:#556'>{esc(empty)}</p>" if empty else ""
        lis = "".join(f"<li style='margin:3px 0'>{it}</li>" for it in items)
        return f"<h3 style='margin:18px 0 6px;font-size:13px;letter-spacing:2px;color:#8a6d1c'>{esc(title)}</h3><ul style='margin:0;padding-left:18px'>{lis}</ul>"

    kpi = "".join(
        f"<td style='padding:10px 14px;border:1px solid #e6e2d6;text-align:center'><div style='font-size:10px;letter-spacing:2px;color:#777'>{k}</div><div style='font-family:monospace;font-size:16px'>{v}</div></td>"
        for k, v in [("ΕΣΟΔΑ", _fmt(s["income_net"])), ("ΕΞΟΔΑ", _fmt(s["expense_net"])), ("ΦΠΑ", _fmt(s["vat_due"])), ("ΑΝΕΙΣΠΡΑΚΤΑ", _fmt(s["unpaid_total"]))]
    )
    html = f"""<html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:620px;">
      <div style="background:#0a0f1e;padding:18px 24px;border-bottom:3px solid #c9a227;">
        <span style="color:#c9a227;font-size:18px;font-weight:bold;letter-spacing:2px;">NOMOS ONE · AUTOPILOT</span><br/>
        <span style="color:#8a93a8;font-size:11px;">{esc(label)} ενημέρωση — {esc(s['company'])} — {s['today'].strftime('%d/%m/%Y')}</span>
      </div>
      <div style="padding:20px 24px;">
        <table style="border-collapse:collapse;width:100%"><tr>{kpi}</tr></table>
        {section(f"Χρειάζονται απόφαση ({len(s['open_items'])})", [f"<strong>{esc(n['title'])}</strong>" + (f"<br/><span style='color:#556'>{esc(n['message'])}</span>" if n.get('message') else '') for n in s['open_items'][:15]], empty="Τίποτα — όλα τρέχουν αυτόματα ✔")}
        {section("Προθεσμίες", [f"{d['due_date'][8:10]}/{d['due_date'][5:7]} — {esc(d['title'])}" for d in s['deadlines']])}
        {section("Περίοδοι ΦΠΑ προς υποβολή", [f"{esc(v['period'])}: <strong>{_fmt(v['vat_due'])}</strong> έως {v['due_date'][8:10]}/{v['due_date'][5:7]}" for v in s['vat_periods']])}
        {section(f"Τι έκανε το Autopilot ({len(s['activity'])})", [esc(n['title']) for n in s['activity'][:20]])}
        {section("Ανείσπρακτα", [f"{esc(i.get('number'))} · {esc(i.get('client_name'))} · <strong>{_fmt(_owed(i))}</strong> ({i.get('date', '')[:10]}, υπενθ. {i.get('reminder_count', 0)})" for i in s['unpaid'][:15]])}
        {"<p style='margin-top:20px'><a href='" + esc(s['public_url']) + "/autopilot' style='background:#c9a227;color:#0a0f1e;padding:10px 20px;text-decoration:none;font-weight:600;border-radius:6px'>Άνοιγμα Autopilot</a></p>" if s['public_url'] else ''}
      </div>
      <div style="padding:12px 24px;border-top:1px solid #e0ddd3;color:#8a93a8;font-size:11px;">Αυτόματο μήνυμα από το Nomos One Autopilot. Δεν αποτελεί λογιστική συμβουλή.</div>
    </body></html>"""
    return subject, text, html


async def job_digest(firm_id: str, settings: dict, force: bool = False) -> dict:
    if not force and not digest_due(settings, settings.get("last_digest_at")):
        return {"skipped": True, "reason": "not_due"}
    if not smtp_ready(settings):
        return {"skipped": True, "reason": "smtp"}
    to = settings.get("digest_email") or settings.get("email") or settings.get("smtp_user")
    if not to:
        return {"skipped": True, "reason": "no_recipient"}
    entry = await outbox.enqueue(firm_id, "email_digest", {"firm_id": firm_id, "to": to},
                                 dedupe_key=f"email_digest:{athens_today().isoformat()}")
    return {"queued_to": to} if entry else {"skipped": True, "reason": "already_queued"}


async def job_bank_sync(firm_id: str, settings: dict) -> dict:
    """Κατεβάζει τις κινήσεις μέσω PSD2 και τις περνά από το ίδιο auto-matching με τα CSV."""
    from openbanking import (
        openbanking_ready, fetch_transactions, requisition_expiring, OpenBankingError,
    )
    from routes_bank import match_details, build_match_pool, reserves_invoice
    from models import BankTransaction

    if not settings.get("openbanking_enabled") or not openbanking_ready(settings):
        return {"skipped": True, "reason": "not_configured"}
    account_ids = settings.get("gocardless_account_ids") or []
    if not account_ids:
        await notify(firm_id, "bank_not_linked", "warning", "Δεν έχει συνδεθεί τραπεζικός λογαριασμός",
                     "Ολοκληρώστε τη σύνδεση με την τράπεζά σας από τις Ρυθμίσεις για αυτόματη συμφωνία εισπράξεων.",
                     entity="settings", requires_action=True, dedupe_key="bank_not_linked")
        return {"skipped": True, "reason": "no_accounts"}

    if requisition_expiring(settings):
        await notify(firm_id, "bank_consent_expiring", "warning", "Λήγει η σύνδεση με την τράπεζα",
                     f"Η συγκατάθεση PSD2 λήγει στις {settings.get('gocardless_access_valid_until')}. "
                     "Ανανεώστε τη σύνδεση από τις Ρυθμίσεις για να μη σταματήσουν οι αυτόματες εισπράξεις.",
                     entity="settings", requires_action=True,
                     dedupe_key=f"bank_consent:{settings.get('gocardless_access_valid_until')}")

    # Επικάλυψη ημερών: οι τράπεζες αργούν να οριστικοποιήσουν κινήσεις.
    last = settings.get("bank_sync_last_date")
    date_from = (_date.fromisoformat(last) - timedelta(days=5)).isoformat() if last else \
        (athens_today() - timedelta(days=90)).isoformat()

    pool = await build_match_pool(firm_id, settings)
    created = skipped = matched = 0
    for account_id in account_ids:
        try:
            transactions = await fetch_transactions(settings, account_id, date_from)
        except OpenBankingError as e:
            await notify(firm_id, "bank_sync_failed", "warning", "Αποτυχία λήψης τραπεζικών κινήσεων", str(e),
                         entity="settings", dedupe_key="bank_sync_failed")
            raise
        for tx in transactions:
            external_id = f"psd2:{account_id}:{tx['external_id']}"
            if await db.bank_transactions.find_one(firm_query(firm_id, {"external_id": external_id})):
                skipped += 1
                continue
            record = BankTransaction(
                firm_id=firm_id, date=tx["date"], description=tx["description"][:500],
                reference=tx["reference"], amount=tx["amount"], bank=settings.get("gocardless_institution_name", ""),
                account=account_id, external_id=external_id, imported_by="open_banking",
            )
            found = match_details(tx, pool)
            match = found["invoice"]
            if match is not None:
                record.matched_invoice_id = str(match["_id"])
                record.matched_invoice_number = match.get("number", "")
                record.match_confidence = found["confidence"]
                record.match_ambiguous = found["ambiguous"]
                record.match_status = "auto_matched"
            try:
                await db.bank_transactions.insert_one(record.to_mongo())
            except DuplicateKeyError:
                skipped += 1                # άλλη διεργασία πρόλαβε να καταχωρήσει την ίδια κίνηση
                continue
            created += 1
            if match is not None:
                matched += 1
                if reserves_invoice(found["confidence"], found["ambiguous"]):
                    pool = [i for i in pool if i is not match]      # ισχυρή πρόταση: δεσμεύεται από αυτή την κίνηση

    await db.settings.update_one(firm_query(firm_id), {"$set": {"bank_sync_last_date": athens_today().isoformat()}})
    if created:
        await db.notifications.update_many(
            firm_query(firm_id, {"dedupe_key": "bank_sync_failed", "resolved_at": None}),
            {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
    return {"created": created, "already_known": skipped, "auto_matched": matched}


async def job_outbox(firm_id: str, settings: dict) -> dict:
    """Εκτελεί την ουρά αποστολών και εντοπίζει ό,τι κόλλησε από διακοπή."""
    stats = await outbox.drain(firm_id)
    stats.update(await outbox.sweep_stuck())
    for entry in await db.outbox.find(firm_query(firm_id, {"status": "failed", "notified": {"$ne": True}})).to_list(50):
        await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {"notified": True}})
        await notify(firm_id, "outbox_failed", "warning", "Μια αυτόματη αποστολή απέτυχε οριστικά",
                     f"«{outbox.describe(entry)}» — {entry.get('error') or 'άγνωστο σφάλμα'}. "
                     f"Μετά από {entry.get('attempts')} προσπάθειες χρειάζεται έλεγχος (SMTP; διεύθυνση πελάτη;).",
                     entity="outbox", entity_id=entry["_id"], requires_action=True,
                     actions=[{"key": "retry", "label": "Επανάληψη"}, {"key": "discard", "label": "Αγνόησε"}],
                     dedupe_key=f"outbox_failed:{entry['_id']}")
    return stats


BACKUP_MAX_AGE_HOURS = 48


def inspect_backups(directory: str, now: datetime | None = None) -> dict:
    """Ελέγχει το τελευταίο αντίγραφο: υπάρχει, δεν είναι κενό, δεν είναι μπαγιάτικο, ανοίγει.

    Ένα backup που δεν έχει επαληθευτεί δεν είναι backup. Ο πλήρης έλεγχος επαναφοράς
    χρειάζεται mongorestore· εδώ επαληθεύεται ό,τι μπορεί ο server μόνος του.
    """
    import gzip
    from pathlib import Path
    now = now or datetime.now(timezone.utc)
    path = Path(directory)
    if not path.is_dir():
        return {"ok": False, "reason": "missing_dir", "message": f"Ο φάκελος αντιγράφων {directory} δεν υπάρχει."}
    archives = sorted(path.glob("*.archive"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        return {"ok": False, "reason": "no_backups", "message": f"Δεν βρέθηκε κανένα αντίγραφο στο {directory}."}
    latest = archives[0]
    stat = latest.stat()
    age_hours = (now.timestamp() - stat.st_mtime) / 3600
    if stat.st_size == 0:
        return {"ok": False, "reason": "empty", "message": f"Το τελευταίο αντίγραφο ({latest.name}) είναι κενό.",
                "file": latest.name, "age_hours": round(age_hours, 1)}
    try:
        with gzip.open(latest, "rb") as fh:
            fh.read(4096)
    except Exception as e:
        return {"ok": False, "reason": "corrupt", "message": f"Το αντίγραφο {latest.name} δεν διαβάζεται: {e}",
                "file": latest.name, "age_hours": round(age_hours, 1)}
    if age_hours > BACKUP_MAX_AGE_HOURS:
        return {"ok": False, "reason": "stale", "file": latest.name, "age_hours": round(age_hours, 1),
                "size_mb": round(stat.st_size / 1e6, 2), "count": len(archives),
                "message": f"Το τελευταίο αντίγραφο είναι {age_hours:.0f} ωρών ({latest.name})."}
    return {"ok": True, "file": latest.name, "age_hours": round(age_hours, 1),
            "size_mb": round(stat.st_size / 1e6, 2), "count": len(archives)}


async def job_backup_check(firm_id: str, settings: dict) -> dict:
    """Ένα αντίγραφο που κανείς δεν ελέγχει είναι ψευδής ασφάλεια."""
    directory = os.environ.get("BACKUP_DIR", "/app/backups")
    result = await asyncio.to_thread(inspect_backups, directory)
    if result["ok"]:
        await db.notifications.update_many(
            firm_query(firm_id, {"dedupe_key": "backup_problem", "resolved_at": None}),
            {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
    else:
        await notify(firm_id, "backup_problem", "critical", "Πρόβλημα με τα αντίγραφα ασφαλείας",
                     result["message"] + " Ελέγξτε την υπηρεσία backup του docker-compose.",
                     entity="backup", requires_action=True, dedupe_key="backup_problem")
    return result


# ----------------------------------------------------------- αρίθμηση -------

def _numbering_digest(items) -> str:
    import hashlib
    return hashlib.sha1("|".join(str(i) for i in items).encode("utf-8")).hexdigest()[:10]


def _dmy(iso: str) -> str:
    try:
        return _date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def numbering_findings(report: dict) -> dict:
    """{dedupe_key: (επίπεδο, τίτλος, μήνυμα)} για ό,τι δείχνει ο έλεγχος αρίθμησης.

    Το κλειδί περιέχει σύνοψη των στοιχείων του προβλήματος: ίδιο πρόβλημα → ίδιο κλειδί → καμία νέα κάρτα·
    αλλαγή στα στοιχεία (νέο κενό, νέο διπλότυπο) → νέο κλειδί → νέα κάρτα.
    """
    found: dict = {}
    if not report["unique_index"]:
        found["numbering:index"] = (
            "warning", "Η βάση δεν εμποδίζει διπλούς αριθμούς τιμολογίων",
            "Δεν υπάρχει το unique index των αριθμών παραστατικών (uniq_firm_invoice_number). Συνήθως δημιουργείται "
            "στην εκκίνηση· αν υπάρχουν ήδη διπλοί αριθμοί, ή η MongoDB είναι παλαιότερη της 6.0, δεν μπορεί να "
            "δημιουργηθεί. Η έκδοση ελέγχει τους αριθμούς και από τον κώδικα, χωρίς το index όμως λείπει η δεύτερη "
            "γραμμή άμυνας. Ελέγξτε τα logs της εκκίνησης.")
    for s in report["series"]:
        name = s["series"]
        if s["duplicates"]:
            numbers = sorted(d["number"] for d in s["duplicates"])
            found[f"numbering:dup:{name}:{_numbering_digest(numbers)}"] = (
                "critical", f"Διπλός αριθμός παραστατικού στη σειρά {name}",
                f"Οι αριθμοί {', '.join(numbers)} υπάρχουν σε περισσότερα από ένα παραστατικά. Δύο παραστατικά με τον "
                "ίδιο αριθμό δεν επιτρέπεται να υπάρχουν στην ίδια σειρά· ελέγξτε τα με τον λογιστή πριν από κάθε "
                "υποβολή ή δήλωση.")
        if s["gaps_total"]:
            shown = ", ".join(f"{n:04d}" for n in s["gaps"][:10])
            more = " και άλλοι" if s["gaps_total"] > 10 else ""
            found[f"numbering:gaps:{name}:{_numbering_digest(s['gaps'] + [s['gaps_total']])}"] = (
                "warning", f"Κενό στην αρίθμηση της σειράς {name}",
                f"Λείπουν οι αριθμοί {shown}{more} (σύνολο {s['gaps_total']}). Αν παραστατικό διαγράφηκε ή η έκδοσή "
                "του δεν ολοκληρώθηκε, η σειρά δεν είναι συνεχής· ο λογιστής αποφασίζει πώς θα καλυφθεί το κενό.")
        if s["date_order"]:
            lines = "; ".join(f"{o['number']} ({_dmy(o['date'])}) μετά το {o['previous_number']} ({_dmy(o['previous_date'])})"
                              for o in s["date_order"][:5])
            found[f"numbering:order:{name}:{_numbering_digest([o['number'] for o in s['date_order']])}"] = (
                "warning", f"Αρίθμηση εκτός χρονολογικής σειράς στη σειρά {name}",
                f"Η ημερομηνία δεν ακολουθεί τον αριθμό: {lines}. Η αρίθμηση πρέπει να είναι και συνεχής και χρονολογική.")
        counter = s["counter"]
        if counter["state"] == "behind":
            found[f"numbering:counter:{name}:{counter['seq']}:{s['last']}"] = (
                "warning", f"Ο μετρητής της σειράς {name} είναι πίσω από τον τελευταίο αριθμό",
                f"Ο μετρητής δείχνει {counter['seq']} ενώ ο τελευταίος αριθμός που υπάρχει είναι {s['last']} (συνήθως "
                "μετά από επαναφορά αντιγράφου). Η επόμενη έκδοση τον διορθώνει μόνη της· ελέγξτε παράλληλα ότι δεν "
                "υπάρχουν διπλοί αριθμοί.")
    if report["unparseable"]:
        numbers = [u["number"] for u in report["unparseable"]]
        found[f"numbering:format:{_numbering_digest(sorted(numbers))}"] = (
            "warning", "Παραστατικά με αριθμό εκτός της μορφής της σειράς",
            f"Οι αριθμοί {', '.join(numbers[:10])} δεν έχουν τη μορφή ΠΡΟΘΕΜΑ-ΕΤΟΣ-ΑΥΞΩΝ και δεν ελέγχονται ως προς "
            "τη συνέχεια της σειράς. Ελέγξτε αν προέρχονται από εισαγωγή ή παλαιότερο σύστημα.")
    if report["problems"]["unnumbered"]:
        found[f"numbering:unnumbered:{report['problems']['unnumbered']}"] = (
            "critical", "Εκδοθέντα παραστατικά χωρίς αριθμό",
            f"{report['problems']['unnumbered']} εκδοθέντα/εξοφλημένα/ακυρωμένα παραστατικά δεν έχουν αριθμό. "
            "Ένα εκδοθέν παραστατικό πρέπει να φέρει αριθμό σειράς· ελέγξτε τα με τον λογιστή.")
    return found


async def job_numbering_check(firm_id: str, settings: dict) -> dict:
    """Ελέγχει τη σειρά των εκδοθέντων και ανοίγει κάρτα **μόνο όταν αλλάξει κάτι**.

    Η έκδοση ελέγχει τη σειρά τη στιγμή που εκδίδει. Ένα κενό ή ένα διπλότυπο που προκύπτει αργότερα (επαναφορά
    αντιγράφου, χειροκίνητη επέμβαση στη βάση, παλιά έκδοση της εφαρμογής) θα έμενε αθέατο μέχρι τον έλεγχο της
    εφορίας. Οι κάρτες κλείνουν μόνες τους όταν το πρόβλημα διορθωθεί.
    """
    report = await numbering_report(firm_id)
    wanted = numbering_findings(report)
    resolved = 0
    async for card in db.notifications.find(firm_query(firm_id, {
            "type": "numbering", "dedupe_key": {"$regex": "^numbering:"}, "resolved_at": None})):
        if card.get("dedupe_key") not in wanted:
            await db.notifications.update_one(
                {"_id": card["_id"]},
                {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto", "read": True}})
            resolved += 1
    raised = 0
    # Ό,τι ένας άνθρωπος έχει ήδη κλείσει δεν ξανανοίγει: το κλειδί περιέχει σύνοψη των στοιχείων, άρα ένα ΝΕΟ πρόβλημα
    # έχει νέο κλειδί και νέα κάρτα. Ένα κενό σε εκδοθείσα σειρά δεν διορθώνεται ποτέ από μόνο του — χωρίς αυτό η κάρτα
    # θα επέστρεφε κάθε κύκλο (AUTOPILOT_INTERVAL_MINUTES) αφού την έκλεισε ο λογιστής.
    acknowledged = {c["dedupe_key"] async for c in db.notifications.find(firm_query(firm_id, {
        "type": "numbering", "dedupe_key": {"$in": list(wanted)}, "resolved_at": {"$ne": None},
        "resolved_by": {"$nin": ["autopilot", None]}}))}
    for key, (level, title, message) in wanted.items():
        if key in acknowledged:
            continue
        created = await notify(firm_id, "numbering", level, title, message, entity="invoice_series",
                               entity_id=key.split(":")[2] if key.count(":") >= 2 else "", requires_action=True,
                               dedupe_key=key)
        raised += 1 if created else 0
    return {"ok": not wanted, "unique_index": report["unique_index"], "problems": report["problems"],
            "series": len(report["series"]), "raised": raised, "resolved": resolved}


# --------------------------------------------------------- πληρωμές κάρτας ----

CARD_APPLY_GRACE_MINUTES = 5       # η ζωντανή κλήση (webhook) ίσως δουλεύει ακόμη· δεν της παίρνουμε τη δουλειά


async def job_card_payments(firm_id: str, settings: dict) -> dict:
    """Ολοκληρώνει πληρωμές κάρτας που καταγράφηκαν ως εισπραγμένες αλλά δεν εφαρμόστηκαν στο τιμολόγιο.

    Συμβαίνει όταν η διεργασία πέφτει ανάμεσα στην καταγραφή και την εφαρμογή. Το Stripe ξαναστέλνει το webhook,
    αλλά όχι για πάντα — και η σελίδα επιτυχίας του πελάτη δεν είναι εγγύηση. Η εφαρμογή είναι ατομική και
    επαναλήψιμη (βλ. routes_payments.apply_pending_card_payment): ό,τι ολοκληρώθηκε ήδη δεν ξαναγίνεται.
    """
    from routes_payments import apply_pending_card_payment
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=CARD_APPLY_GRACE_MINUTES)).isoformat()
    pending = applied = 0
    async for tx in db.payment_transactions.find(
            firm_query(firm_id, {"apply_pending": True, "paid_at": {"$lt": cutoff}})):
        pending += 1
        if await apply_pending_card_payment(tx["session_id"]):
            applied += 1
    return {"pending": pending, "applied": applied}


# ---------------------------------------------------------------- cycle -----

JOB_FUNCS = {
    "send_issued_invoices": job_send_issued_invoices,
    "reminders": job_reminders,
    "mydata_retry": job_mydata_retry,
    "mydata_expenses": job_mydata_expenses,
    "reconcile": job_reconcile,
    "deadlines": job_deadlines,
    "stale_drafts": job_stale_drafts,
    "vat_periods": job_vat_periods,
    "inbox_receipts": job_inbox_receipts,
    "digest": job_digest,
    "bank_sync": job_bank_sync,
    "outbox": job_outbox,
    "backup_check": job_backup_check,
    "numbering_check": job_numbering_check,
    "card_payments": job_card_payments,
}


async def run_firm(firm_id: str, jobs: list[str] | None = None, force: bool = False) -> list[dict]:
    settings = await db.settings.find_one({"firm_id": firm_id}) or {"firm_id": firm_id}
    if not force and not settings.get("autopilot_enabled", True):
        return [{"job": "*", "status": "skipped", "stats": {"reason": "autopilot_disabled"}}]
    results = []
    for job in jobs or JOBS:
        func = JOB_FUNCS[job]
        if job == "digest":
            results.append(await record_run(firm_id, job, func, firm_id, settings, force))
        else:
            results.append(await record_run(firm_id, job, func, firm_id, settings))
    return results


async def all_firm_ids() -> list[str]:
    """Κάθε γραφείο που έχει χρήστη — όχι μόνο όσα έχουν αποθηκευμένες ρυθμίσεις.

    Το έγγραφο settings δημιουργείται στο πρώτο ``GET /api/settings``, δηλαδή στο πρώτο
    άνοιγμα του Dashboard. Γραφεία που δημιουργήθηκαν μέσω API, import ή seed και δεν
    ανοίχτηκαν ποτέ στο UI έμεναν εκτός Autopilot χωρίς καμία ένδειξη πουθενά: κανένα
    τιμολόγιο δεν στελνόταν, καμία υπενθύμιση, κανένα κλείσιμο ΦΠΑ.
    """
    ids = set(await db.settings.distinct("firm_id"))
    ids.update(await db.users.distinct("firm_id"))
    return sorted(i for i in ids if i)


async def run_autopilot_cycle(jobs: list[str] | None = None, only_firm: str | None = None, force: bool = False) -> dict:
    """Entry point for the scheduler and the «Εκτέλεση τώρα» button."""
    if not await acquire_lock("autopilot_cycle", ttl_seconds=900):
        return {"skipped": True, "reason": "another worker is running the cycle"}
    summary = {"firms": 0, "runs": 0, "errors": 0, "started_at": _now()}
    try:
        firm_ids = [only_firm] if only_firm else await all_firm_ids()
        for firm_id in firm_ids:
            if not firm_id:
                continue
            summary["firms"] += 1
            for r in await run_firm(firm_id, jobs, force):
                summary["runs"] += 1
                if r.get("status") == "error":
                    summary["errors"] += 1
        # housekeeping
        cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        await db.automation_runs.delete_many({"started_at": {"$lt": cutoff}})
        await db.notifications.delete_many({"requires_action": False, "read": True, "created_at": {"$lt": cutoff}})
    finally:
        await release_lock("autopilot_cycle")
    summary["finished_at"] = _now()
    logger.info("Autopilot cycle: %s", summary)
    return summary
