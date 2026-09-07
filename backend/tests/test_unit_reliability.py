"""Αξιοπιστία: ουρά αποστολών (outbox), ανάκαμψη από διακοπή, έλεγχος αντιγράφων."""
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import automation
import outbox
from automation import inspect_backups, run_firm
from db import db

FIRM = "firm-rel"
SETTINGS = {"firm_id": FIRM, "company_name": "Γραφείο", "smtp_user": "office@example.gr",
            "smtp_password": "pw", "email": "office@example.gr", "autopilot_enabled": True,
            "auto_email_on_issue": True, "auto_reminders": True, "reminder_after_days": 30,
            "reminder_interval_days": 15, "reminder_max": 3, "digest_frequency": "weekly"}


# ----------------------------------------------------------------- outbox ---

@pytest.mark.asyncio
async def test_enqueue_dedupes_while_pending_and_processes_once():
    calls = []

    async def handler(payload):
        calls.append(payload)
        return {"ok": True}

    outbox.register_handler("test_kind", handler)
    first = await outbox.enqueue(FIRM, "test_kind", {"a": 1}, dedupe_key="k")
    dup = await outbox.enqueue(FIRM, "test_kind", {"a": 1}, dedupe_key="k")
    assert first and dup is None

    stats = await outbox.drain(FIRM)
    assert stats == {"sent": 1, "retry": 0, "failed": 0}
    assert calls == [{"a": 1}]
    entry = await db.outbox.find_one({"_id": first["_id"]})
    assert entry["status"] == "sent" and entry["sent_at"]

    # δεύτερο drain δεν ξαναστέλνει
    assert (await outbox.drain(FIRM))["sent"] == 0
    # μετά την ολοκλήρωση, το ίδιο dedupe_key μπορεί να ξαναμπεί
    assert await outbox.enqueue(FIRM, "test_kind", {"a": 2}, dedupe_key="k")


@pytest.mark.asyncio
async def test_failure_backs_off_then_gives_up_and_asks_for_help():
    async def always_fails(payload):
        raise RuntimeError("SMTP down")

    outbox.register_handler("flaky", always_fails)
    entry = await outbox.enqueue(FIRM, "flaky", {"x": 1})

    stats = await outbox.drain(FIRM)
    assert stats["retry"] == 1
    doc = await db.outbox.find_one({"_id": entry["_id"]})
    assert doc["status"] == "pending" and doc["attempts"] == 1 and "SMTP down" in doc["error"]
    # δεν ξαναδοκιμάζει αμέσως — υπάρχει backoff
    assert (await outbox.drain(FIRM)) == {"sent": 0, "retry": 0, "failed": 0}

    # εξάντληση προσπαθειών
    for _ in range(outbox.MAX_ATTEMPTS):
        await db.outbox.update_one({"_id": entry["_id"]},
                                   {"$set": {"next_attempt_at": datetime.now(timezone.utc).isoformat()}})
        await outbox.drain(FIRM)
    doc = await db.outbox.find_one({"_id": entry["_id"]})
    assert doc["status"] == "failed" and doc["attempts"] == outbox.MAX_ATTEMPTS

    # το Autopilot το ανεβάζει ως απόφαση, μία φορά
    await db.settings.insert_one(dict(SETTINGS))
    r = (await run_firm(FIRM, ["outbox"]))[0]
    assert r["status"] != "error"
    note = await db.notifications.find_one({"type": "outbox_failed"})
    assert note and note["requires_action"]
    assert [a["key"] for a in note["actions"]] == ["retry", "discard"]
    await run_firm(FIRM, ["outbox"])
    assert await db.notifications.count_documents({"type": "outbox_failed"}) == 1


@pytest.mark.asyncio
async def test_crash_mid_flight_is_never_retried_blindly():
    """Μια ενέργεια που κόπηκε στη μέση δεν ξαναστέλνεται μόνη της: ρωτάμε τον χρήστη."""
    await db.settings.insert_one(dict(SETTINGS))
    entry = await outbox.enqueue(FIRM, "email_reminder",
                                 {"firm_id": FIRM, "invoice_id": "x", "number": "ΤΠΥ-1", "to": "c@example.gr"})
    claimed = await outbox.claim_next(FIRM)
    assert claimed["status"] == "in_flight"
    # προσομοίωση crash: το claim έγινε πριν από πολλή ώρα
    await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
        "claimed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})

    assert await outbox.sweep_stuck() == {"needs_review": 1}
    doc = await db.outbox.find_one({"_id": entry["_id"]})
    assert doc["status"] == "needs_review"
    note = await db.notifications.find_one({"type": "outbox_stuck"})
    assert note and note["requires_action"] and "ΤΠΥ-1" in note["message"]

    # ο χρήστης λέει «έγινε ήδη» → δεν ξαναστέλνεται
    from automation import resolve_notification
    assert (await resolve_notification(FIRM, str(note["_id"]), "u@e.gr", "discard"))["resolution"] == "discarded"
    assert (await db.outbox.find_one({"_id": entry["_id"]}))["status"] == "discarded"
    assert (await outbox.drain(FIRM))["sent"] == 0


@pytest.mark.asyncio
async def test_user_can_requeue_a_stuck_action():
    await db.settings.insert_one(dict(SETTINGS))
    entry = await outbox.enqueue(FIRM, "email_digest", {"firm_id": FIRM, "to": "boss@example.gr"})
    await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
        "status": "in_flight", "claimed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}})
    await outbox.sweep_stuck()
    note = await db.notifications.find_one({"type": "outbox_stuck"})

    from automation import resolve_notification
    assert (await resolve_notification(FIRM, str(note["_id"]), "u@e.gr", "retry"))["resolution"] == "requeued"
    assert (await db.outbox.find_one({"_id": entry["_id"]}))["status"] == "pending"


@pytest.mark.asyncio
async def test_jobs_enqueue_instead_of_sending_directly(monkeypatch):
    """Οι εργασίες γράφουν στην ουρά· η αποστολή γίνεται από τον handler."""
    await db.settings.insert_one(dict(SETTINGS))
    client = await db.clients.insert_one({"firm_id": FIRM, "name": "Πελάτης", "email": "c@example.gr"})
    inv = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0001", "client_id": str(client.inserted_id),
                                        "client_name": "Πελάτης", "status": "issued", "total": 124.0,
                                        "date": "2026-09-01", "email_sent_at": None, "line_items": []})
    r = (await run_firm(FIRM, ["send_issued_invoices"]))[0]
    assert r["stats"] == {"queued": 1, "skipped": 0}
    assert await db.outbox.count_documents({"firm_id": FIRM, "kind": "email_invoice", "status": "pending"}) == 1
    # δεύτερος κύκλος δεν διπλογράφει
    assert (await run_firm(FIRM, ["send_issued_invoices"]))[0]["stats"] == {"queued": 0, "skipped": 0}

    sent = []

    async def fake_send(settings, to, subject, text, html, pdf_bytes=None, pdf_filename=""):
        sent.append(to)

    import invoice_services, pdf_utils
    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", lambda *a, **k: b"%PDF")
    assert (await run_firm(FIRM, ["outbox"]))[0]["stats"]["sent"] == 1
    assert sent == ["c@example.gr"]
    assert (await db.invoices.find_one({"_id": inv.inserted_id}))["email_sent_at"]


@pytest.mark.asyncio
async def test_handler_skips_when_state_moved_on(monkeypatch):
    """Idempotency: αν το τιμολόγιο πληρώθηκε στο μεταξύ, η υπενθύμιση δεν φεύγει."""
    await db.settings.insert_one(dict(SETTINGS))
    client = await db.clients.insert_one({"firm_id": FIRM, "name": "Πελάτης", "email": "c@example.gr"})
    inv = await db.invoices.insert_one({"firm_id": FIRM, "number": "ΤΠΥ-2026-0002", "client_id": str(client.inserted_id),
                                        "status": "paid", "total": 124.0, "date": "2026-01-01",
                                        "reminder_count": 0, "line_items": []})
    await outbox.enqueue(FIRM, "email_reminder", {"firm_id": FIRM, "invoice_id": str(inv.inserted_id),
                                                  "number": "ΤΠΥ-2026-0002", "to": "c@example.gr",
                                                  "expected_reminder_count": 0})
    sent = []

    async def fake_send(settings, to, *a, **k):
        sent.append(to)

    import invoice_services
    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    assert (await outbox.drain(FIRM))["sent"] == 1     # η εγγραφή ολοκληρώθηκε…
    assert sent == []                                   # …χωρίς να σταλεί email


# ---------------------------------------------------------------- backups ---

def test_inspect_backups_reports_each_failure_mode(tmp_path):
    import gzip
    missing = inspect_backups(str(tmp_path / "nope"))
    assert missing["ok"] is False and missing["reason"] == "missing_dir"

    assert inspect_backups(str(tmp_path))["reason"] == "no_backups"

    empty = tmp_path / "nomos-1.archive"
    empty.touch()
    assert inspect_backups(str(tmp_path))["reason"] == "empty"

    corrupt = tmp_path / "nomos-2.archive"
    corrupt.write_bytes(b"this is not gzip")
    assert inspect_backups(str(tmp_path))["reason"] == "corrupt"

    good = tmp_path / "nomos-3.archive"
    with gzip.open(good, "wb") as fh:
        fh.write(b"x" * 5000)
    result = inspect_backups(str(tmp_path))
    assert result["ok"] is True and result["file"] == good.name and result["size_mb"] >= 0

    # μπαγιάτικο: 3 ημερών (σε καθαρό φάκελο, ώστε να ελεγχθεί αυτό ακριβώς το αρχείο)
    import os as _os
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    old_archive = stale_dir / "nomos-old.archive"
    with gzip.open(old_archive, "wb") as fh:
        fh.write(b"x" * 5000)
    old = time.time() - 72 * 3600
    _os.utime(old_archive, (old, old))
    stale = inspect_backups(str(stale_dir))
    assert stale["ok"] is False and stale["reason"] == "stale" and stale["age_hours"] > 48


@pytest.mark.asyncio
async def test_backup_job_raises_and_clears_a_decision(tmp_path, monkeypatch):
    import gzip
    await db.settings.insert_one(dict(SETTINGS))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))

    r = (await run_firm(FIRM, ["backup_check"]))[0]
    assert r["stats"]["ok"] is False
    note = await db.notifications.find_one({"dedupe_key": "backup_problem"})
    assert note and note["level"] == "critical"

    with gzip.open(tmp_path / "nomos-ok.archive", "wb") as fh:
        fh.write(b"y" * 4096)
    r = (await run_firm(FIRM, ["backup_check"]))[0]
    assert r["stats"]["ok"] is True
    assert (await db.notifications.find_one({"dedupe_key": "backup_problem"}))["resolved_at"]


def test_worker_module_is_runnable():
    """Ο worker πρέπει να φορτώνει χωρίς να σηκώνει API ή scheduler."""
    import worker
    assert hasattr(worker, "run_forever") and hasattr(worker, "main")
    assert Path(worker.__file__).name == "worker.py"
