from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import athens_today
from models import Hearing
from routes_cases import log_case_event

router = APIRouter(prefix="/hearings", tags=["hearings"])

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
    current_number: Optional[int] = None
    status: Optional[str] = None
    note: str = ""


@router.get("")
async def list_hearings(case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"case_id": case_id} if case_id else {}
    docs = await db.hearings.find(tenant_query(user, query)).sort("date", 1).to_list(500)
    return [Hearing.from_mongo(d) for d in docs]


@router.post("")
async def create_hearing(payload: Hearing, user: dict = Depends(get_current_user)):
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
    await db.hearings.update_one(tenant_query(user, {"_id": ObjectId(hid)}), op)
    doc = await db.hearings.find_one(tenant_query(user, {"_id": ObjectId(hid)}))
    return Hearing.from_mongo(doc)


@router.get("/alerts/list")
async def alerts(user: dict = Depends(get_current_user)):
    today = athens_today().isoformat()
    docs = await db.hearings.find(tenant_query(user, {"status": {"$nin": ["completed", "postponed"]}})).to_list(200)
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
