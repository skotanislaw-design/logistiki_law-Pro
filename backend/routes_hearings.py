from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db, fetch_all, fetch_capped
from http_utils import disclose_truncation
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import athens_today, valid_iso_date
from models import Hearing
from routes_cases import log_case_event

router = APIRouter(prefix="/hearings", tags=["hearings"])

LIST_LIMIT = 500          # οι πιο πρόσφατες δικάσιμοι (κατά ημερομηνία)· αν υπάρχουν κι άλλες, η απάντηση το δηλώνει
MAX_COURT_NUMBER = 100_000   # ανώτατο νούμερο πινακίου/εκδικαζόμενου (ένα πινάκιο έχει λίγες εκατοντάδες). Πάνω από το όριο ενός
                             # ακέραιου 8 byte η Mongo αρνείται την εγγραφή (OverflowError → 500)· αρνητικό νούμερο δεν έχει νόημα.

STATUS_LABELS = {
    "scheduled": "Προγραμματισμένο",
    "in_progress": "Εκδικάζεται",
    "interrupted": "Διακοπή",
    "completed": "Ολοκληρώθηκε",
    "postponed": "Αναβλήθηκε",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


class LiveUpdatePayload(BaseModel):
    current_number: Optional[int] = Field(default=None, ge=0, le=MAX_COURT_NUMBER)
    # Το νούμερο που έβλεπε ο χρήστης όταν πάτησε «+1». Αν στο μεταξύ άλλαξε (συνεργάτης στο ακροατήριο, άλλη συσκευή), το
    # «+1» δεν εφαρμόζεται: θα έφερνε το νούμερο ΠΙΣΩ ή θα έχανε μια αλλαγή. Χωρίς τιμή (παλιοί πελάτες API) δεν ελέγχεται.
    expected_current: Optional[int] = Field(default=None, ge=0, le=MAX_COURT_NUMBER)
    status: Optional[str] = None
    note: str = ""


@router.get("")
async def list_hearings(response: Response, case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    """Οι δικάσιμοι με αύξουσα σειρά ημερομηνίας.

    Αν είναι περισσότερες από `LIST_LIMIT`, κόβονται οι ΠΑΛΑΙΟΤΕΡΕΣ και η απάντηση το δηλώνει. Πριν, η λίστα ήταν
    `sort(date, 1).to_list(500)`: ένα γραφείο με 501 δικασίμους (λίγα χρόνια δουλειάς) έχανε από τη λίστα τις **πιο
    μελλοντικές** — ακριβώς εκείνες που ο δικηγόρος χρειάζεται να δει."""
    query = {"case_id": case_id} if case_id else {}
    newest_first, cut = await fetch_capped(
        db.hearings.find(tenant_query(user, query)).sort([("date", -1), ("time", -1), ("pinakio_number", -1)]), LIST_LIMIT)
    disclose_truncation(response, cut, LIST_LIMIT)
    return [Hearing.from_mongo(d) for d in reversed(newest_first)]


def _validate_hearing(payload: Hearing) -> None:
    """Η ημερομηνία δικασίμου συγκρίνεται ως κείμενο («Σήμερα» στις ειδοποιήσεις, ταξινόμηση της λίστας)· δεκτή είναι μόνο
    υπαρκτή ημερομηνία της μορφής ΕΕΕΕ-ΜΜ-ΗΗ. Το `20261015` ή το `15/10/2026` δεν θα εμφανίζονταν ποτέ ως «Σήμερα» —
    ένα πινάκιο που ο δικηγόρος δεν θα έβλεπε την ημέρα που εκδικάζεται."""
    if not valid_iso_date(payload.date):
        raise HTTPException(status_code=400, detail="Η ημερομηνία δικασίμου πρέπει να είναι υπαρκτή ημερομηνία της μορφής ΕΕΕΕ-ΜΜ-ΗΗ.")
    for label, value in (("Το νούμερο πινακίου", payload.pinakio_number), ("Το νούμερο που εκδικάζεται", payload.current_number),
                         ("Το όριο ειδοποίησης", payload.notify_threshold)):
        if not 0 <= value <= MAX_COURT_NUMBER:
            raise HTTPException(status_code=400, detail=f"{label} πρέπει να είναι ακέραιος από 0 έως {MAX_COURT_NUMBER}.")


@router.post("")
async def create_hearing(payload: Hearing, user: dict = Depends(get_current_user)):
    _validate_hearing(payload)
    payload.created_by = user["email"]
    payload.firm_id = firm_id_for(user)
    payload.events = [{"event": "Καταχώρηση πινακίου", "at": _now()}]
    result = await db.hearings.insert_one(payload.to_mongo())
    doc = await db.hearings.find_one(tenant_query(user, {"_id": result.inserted_id}))
    if payload.case_id:
        await log_case_event(payload.case_id, f"Καταχώρηση πινακίου Νο {payload.pinakio_number} — {payload.court} ({payload.date})", user)
    return Hearing.from_mongo(doc)


@router.put("/{hid}")
async def update_hearing(hid: str, payload: Hearing, user: dict = Depends(get_current_user)):
    _validate_hearing(payload)
    doc = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το πινάκιο δεν βρέθηκε")
    data = payload.model_dump(exclude={"id", "events", "created_at", "created_by", "current_number", "status", "firm_id"})
    await db.hearings.update_one(tenant_query(user, {"_id": ObjectId(hid)}), {"$set": data})
    doc = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
    return Hearing.from_mongo(doc)


@router.delete("/{hid}")
async def delete_hearing(hid: str, user: dict = Depends(get_current_user)):
    result = await db.hearings.delete_one(tenant_query(user, {"_id": ObjectId(hid)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το πινάκιο δεν βρέθηκε")
    return {"message": "Το πινάκιο διαγράφηκε"}


@router.post("/{hid}/live-update")
async def live_update(hid: str, payload: LiveUpdatePayload, user: dict = Depends(get_current_user)):
    doc = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το πινάκιο δεν βρέθηκε")
    current = doc.get("current_number") or 0
    if payload.expected_current is not None and payload.expected_current != current:
        raise HTTPException(status_code=409, detail=_number_changed(current, payload.expected_current))
    updates = {}
    events = []

    if payload.status and payload.status != doc.get("status"):
        if payload.status not in STATUS_LABELS:
            raise HTTPException(status_code=400, detail="Μη έγκυρη κατάσταση")
        if payload.status == "in_progress":
            label = "Επανέναρξη εκδίκασης" if doc.get("status") == "interrupted" else "Έναρξη εκδίκασης πινακίου"
        elif payload.status == "interrupted":
            label = "Διακοπή συνεδρίασης"
        elif payload.status == "completed":
            label = "Ολοκλήρωση — η υπόθεση εκδικάστηκε"
        elif payload.status == "postponed":
            label = "Αναβολή εκδίκασης"
        else:
            label = STATUS_LABELS[payload.status]
        updates["status"] = payload.status
        events.append({"event": label + (f" — {payload.note}" if payload.note else ""), "at": _now()})

    if payload.current_number is not None and payload.current_number != doc.get("current_number"):
        updates["current_number"] = payload.current_number
        events.append({"event": f"Εκδικάζεται τώρα: Νο {payload.current_number}", "at": _now()})
        remaining = doc["pinakio_number"] - payload.current_number
        if 0 < remaining <= doc.get("notify_threshold", 5):
            events.append({"event": f"⚠ Πλησιάζει το πινάκιό σας — απομένουν {remaining} νούμερα", "at": _now()})
        elif remaining <= 0 and doc.get("status") != "completed":
            events.append({"event": "🔔 ΣΕΙΡΑ ΣΑΣ — εκδικάζεται το πινάκιό σας!", "at": _now()})

    if not updates and payload.note:
        events.append({"event": payload.note, "at": _now()})

    if not updates and not events:
        raise HTTPException(status_code=400, detail="Καμία αλλαγή")

    op = {"$set": updates} if updates else {}
    if events:
        op["$push"] = {"events": {"$each": events}}
    # Ο έλεγχος «δεν άλλαξε στο μεταξύ» μπαίνει και στο φίλτρο της εγγραφής: ανάμεσα στην ανάγνωση και στην εγγραφή μπορεί να
    # παρεμβληθεί άλλο αίτημα (δύο συσκευές που πατούν «+1» την ίδια στιγμή)· τότε η εγγραφή δεν ταιριάζει και αρνούμαστε.
    where = {"_id": ObjectId(hid)}
    if payload.expected_current is not None:
        where["current_number"] = {"$in": [0, None]} if payload.expected_current == 0 else payload.expected_current
    result = await db.hearings.update_one(tenant_query(user, where), op)
    if result.matched_count == 0:
        latest = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
        if not latest:
            raise HTTPException(status_code=404, detail="Το πινάκιο δεν βρέθηκε")
        raise HTTPException(status_code=409, detail=_number_changed(latest.get("current_number") or 0, payload.expected_current))
    doc = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
    return Hearing.from_mongo(doc)


def _number_changed(current: int, seen) -> str:
    return (f"Το νούμερο που εκδικάζεται άλλαξε στο {current} (εσείς βλέπατε το {seen}). "
            "Η ενέργεια δεν εφαρμόστηκε· ελέγξτε το τρέχον νούμερο και δοκιμάστε ξανά.")


@router.get("/alerts/list")
async def alerts(user: dict = Depends(get_current_user)):
    today = athens_today().isoformat()
    # Μόνο όσα μπορούν να δώσουν ειδοποίηση: όσα εκδικάζονται τώρα και όσα προγραμματίστηκαν για ΣΗΜΕΡΑ. Πριν διαβάζονταν
    # «όλα τα μη ολοκληρωμένα» με `to_list(200)` χωρίς σειρά — όμως μια δικάσιμος του παρελθόντος που κανείς δεν σημείωσε
    # «ολοκληρώθηκε» μένει «scheduled» για πάντα· μετά τις πρώτες 200 η σημερινή δεν έφτανε ποτέ στη λίστα.
    due = {"$or": [{"status": {"$in": ["in_progress", "interrupted"]}}, {"status": "scheduled", "date": today}]}
    docs = await fetch_all(db.hearings.find(tenant_query(user, due)))
    out = []
    for d in docs:
        remaining = d.get("pinakio_number", 0) - d.get("current_number", 0)
        threshold = d.get("notify_threshold", 5)
        base = {"id": str(d["_id"]), "case_title": d.get("case_title") or d.get("court"), "court": d.get("court"), "pinakio_number": d.get("pinakio_number")}
        if d.get("status") in ("in_progress", "interrupted") and remaining <= 0:
            out.append({**base, "level": "critical", "message": f"🔔 ΣΕΙΡΑ ΣΑΣ: εκδικάζεται το Νο {d['pinakio_number']} — {d.get('court')}"})
        elif d.get("status") == "in_progress" and 0 < remaining <= threshold:
            out.append({**base, "level": "warning", "message": f"⚠ Πλησιάζει το πινάκιο Νο {d['pinakio_number']} ({d.get('court')}) — απομένουν {remaining} νούμερα"})
        elif d.get("status") == "scheduled" and d.get("date") == today:
            out.append({**base, "level": "info", "message": f"📅 Σήμερα: πινάκιο Νο {d['pinakio_number']} — {d.get('court')} {d.get('time') or ''}".strip()})
    return out
