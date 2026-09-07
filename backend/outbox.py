"""Outbox για παρενέργειες που δεν επιτρέπεται να χαθούν ούτε να διπλογραφτούν.

Το Autopilot στέλνει email και διαβιβάζει παραστατικά. Χωρίς outbox, ένα crash
ανάμεσα στην «απόφαση να σταλεί» και στην ίδια την αποστολή αφήνει το σύστημα σε
άγνωστη κατάσταση: είτε χάνεται η ενέργεια είτε επαναλαμβάνεται τυφλά.

Ροή μιας εγγραφής:

    pending ──claim()──► in_flight ──ok──► sent
                            │
                            ├──αποτυχία──► pending (με backoff) ή failed (εξαντλήθηκε)
                            └──crash──► μένει in_flight· μετά το `STUCK_AFTER`
                                        γίνεται `needs_review` και ρωτάμε τον χρήστη,
                                        γιατί δεν ξέρουμε αν το email έφυγε.

Η τυφλή επανάληψη ενός in_flight θα μπορούσε να στείλει δεύτερη φορά υπενθύμιση σε
πελάτη· γι' αυτό ζητάμε απόφαση αντί να μαντεύουμε. Το `dedupe_key` εμποδίζει να
μπει η ίδια ενέργεια δύο φορές στην ουρά όσο εκκρεμεί.
"""
from __future__ import annotations

import logging
import socket
import os
from datetime import datetime, timezone, timedelta

from db import db

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
STUCK_AFTER = timedelta(minutes=15)
BACKOFF_MINUTES = (1, 5, 15, 60, 180)

# kind -> async handler(payload) . Οι handlers δηλώνονται από τα modules που τους ανήκουν.
_HANDLERS: dict[str, callable] = {}


def register_handler(kind: str, handler):
    _HANDLERS[kind] = handler


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def enqueue(firm_id: str, kind: str, payload: dict, dedupe_key: str = "") -> dict | None:
    """Βάζει μια ενέργεια στην ουρά. Με dedupe_key, δεν διπλογράφεται όσο εκκρεμεί."""
    if dedupe_key:
        existing = await db.outbox.find_one({
            "firm_id": firm_id, "dedupe_key": dedupe_key,
            "status": {"$in": ["pending", "in_flight", "needs_review"]},
        })
        if existing:
            return None
    doc = {
        "firm_id": firm_id, "kind": kind, "payload": payload, "dedupe_key": dedupe_key,
        "status": "pending", "attempts": 0, "created_at": _iso(_now()),
        "next_attempt_at": _iso(_now()), "error": None, "owner": None,
    }
    result = await db.outbox.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def claim_next(firm_id: str | None = None) -> dict | None:
    """Παίρνει μία pending εγγραφή και τη σημαδεύει in_flight (atomic)."""
    from pymongo import ReturnDocument
    query = {"status": "pending", "next_attempt_at": {"$lte": _iso(_now())}}
    if firm_id:
        query["firm_id"] = firm_id
    return await db.outbox.find_one_and_update(
        query,
        {"$set": {"status": "in_flight", "claimed_at": _iso(_now()), "owner": _owner()}},
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _reschedule(entry: dict, error: str):
    attempts = int(entry.get("attempts", 0)) + 1
    if attempts >= MAX_ATTEMPTS:
        await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
            "status": "failed", "attempts": attempts, "error": error[:500], "failed_at": _iso(_now())}})
        return "failed"
    delay = BACKOFF_MINUTES[min(attempts - 1, len(BACKOFF_MINUTES) - 1)]
    await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
        "status": "pending", "attempts": attempts, "error": error[:500],
        "next_attempt_at": _iso(_now() + timedelta(minutes=delay)), "owner": None}})
    return "retry"


async def process_one(entry: dict) -> str:
    """Εκτελεί μία εγγραφή. Επιστρέφει sent / retry / failed."""
    handler = _HANDLERS.get(entry["kind"])
    if handler is None:
        await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
            "status": "failed", "error": f"Άγνωστος τύπος ενέργειας: {entry['kind']}"}})
        return "failed"
    try:
        result = await handler(entry.get("payload") or {})
    except Exception as e:  # noqa: BLE001 — κάθε αποτυχία handler είναι υποψήφια για retry
        logger.warning("Outbox %s απέτυχε: %s", entry["kind"], e)
        return await _reschedule(entry, str(e))
    await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
        "status": "sent", "sent_at": _iso(_now()), "error": None, "result": result if isinstance(result, dict) else None}})
    return "sent"


async def drain(firm_id: str | None = None, limit: int = 50) -> dict:
    """Εκτελεί ό,τι εκκρεμεί. Καλείται από τον κύκλο του Autopilot."""
    stats = {"sent": 0, "retry": 0, "failed": 0}
    for _ in range(limit):
        entry = await claim_next(firm_id)
        if entry is None:
            break
        stats[await process_one(entry)] += 1
    return stats


async def sweep_stuck() -> dict:
    """Εγγραφές που έμειναν in_flight (crash) → needs_review, γιατί δεν ξέρουμε αν εκτελέστηκαν."""
    cutoff = _iso(_now() - STUCK_AFTER)
    stuck = await db.outbox.find({"status": "in_flight", "claimed_at": {"$lt": cutoff}}).to_list(200)
    for entry in stuck:
        await db.outbox.update_one({"_id": entry["_id"]}, {"$set": {
            "status": "needs_review",
            "error": "Η διεργασία διακόπηκε κατά την εκτέλεση — δεν είναι βέβαιο αν ολοκληρώθηκε.",
        }})
        from automation import notify
        await notify(
            entry["firm_id"], "outbox_stuck", "warning",
            "Μια αυτόματη ενέργεια διακόπηκε στη μέση",
            f"«{describe(entry)}». Το σύστημα δεν ξέρει αν ολοκληρώθηκε, γι' αυτό δεν την επαναλαμβάνει μόνο του.",
            entity="outbox", entity_id=entry["_id"], requires_action=True,
            actions=[{"key": "retry", "label": "Επανάληψη"}, {"key": "discard", "label": "Έγινε ήδη — αγνόησε"}],
            dedupe_key=f"outbox_stuck:{entry['_id']}",
        )
    return {"needs_review": len(stuck)}


def describe(entry: dict) -> str:
    """Ανθρώπινη περιγραφή για το inbox αποφάσεων."""
    payload = entry.get("payload") or {}
    kind = entry.get("kind")
    if kind == "email_invoice":
        return f"Αποστολή τιμολογίου {payload.get('number', '')} στον {payload.get('to', '')}"
    if kind == "email_reminder":
        return f"Υπενθύμιση για το τιμολόγιο {payload.get('number', '')} στον {payload.get('to', '')}"
    if kind == "email_digest":
        return f"Ενημερωτικό email προς {payload.get('to', '')}"
    return f"{kind} {payload}"[:120]


async def resolve_stuck(firm_id: str, entry_id, decision: str) -> str:
    """Απόφαση χρήστη για μια διακοπείσα ενέργεια."""
    from bson import ObjectId
    oid = entry_id if isinstance(entry_id, ObjectId) else ObjectId(entry_id)
    entry = await db.outbox.find_one({"_id": oid, "firm_id": firm_id})
    if not entry:
        raise ValueError("Η ενέργεια δεν βρέθηκε")
    if decision == "retry":
        await db.outbox.update_one({"_id": oid}, {"$set": {
            "status": "pending", "next_attempt_at": _iso(_now()), "owner": None, "error": None}})
        return "requeued"
    await db.outbox.update_one({"_id": oid}, {"$set": {
        "status": "discarded", "resolved_at": _iso(_now())}})
    return "discarded"


async def stats(firm_id: str) -> dict:
    out = {}
    for status in ("pending", "in_flight", "needs_review", "failed", "sent", "discarded"):
        out[status] = await db.outbox.count_documents({"firm_id": firm_id, "status": status})
    return out
