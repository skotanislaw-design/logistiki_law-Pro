from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from datetime import date, datetime, timezone
from bson import ObjectId
from db import db, fetch_capped
from http_utils import disclose_truncation
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from models import Case
from tax_utils import athens_today

router = APIRouter(prefix="/cases", tags=["cases"])

CASE_TYPES = ["Αστικό", "Ποινικό", "Διοικητικό", "Εμπορικό", "Οικογενειακό", "Εργατικό", "Ακίνητα/Κτηματολόγιο", "Άλλο"]
STATUS_LABELS = {"active": "Σε εξέλιξη", "pending": "Σε αναμονή", "completed": "Ολοκληρώθηκε", "archived": "Αρχειοθετήθηκε"}
CASES_LIST_LIMIT = 500          # οι νεότερες υποθέσεις· αν υπάρχουν κι άλλες, η απάντηση το δηλώνει (X-List-Truncated)
CASE_SUBLIST_LIMIT = 100        # γραμμάτια και παράβολα που δείχνει η σελίδα της υπόθεσης· τα ΣΥΝΟΛΑ καλύπτουν πάντα όλα


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _next_case_number(firm_id: str):
    from numbering import next_in_series
    year = athens_today().year
    return await next_in_series(db.cases, f"case-{firm_id}-{year}", firm_id, "ΥΠΘ", year)


async def log_case_event(case_id: str, event: str, user: dict | None = None):
    try:
        if user is None:
            case = await db.cases.find_one({"_id": ObjectId(case_id)}, {"firm_id": 1})
            user = {"firm_id": (case or {}).get("firm_id")}
        await db.cases.update_one(
            tenant_query(user, {"_id": ObjectId(case_id)}),
            {"$push": {"history": {"event": event, "at": _now()}}},
        )
    except Exception:
        pass


class NotePayload(BaseModel):
    text: str


class StatusPayload(BaseModel):
    status: str


class NextActionPayload(BaseModel):
    next_action: str
    next_action_date: str = ""


@router.get("/types/list")
async def case_types(user: dict = Depends(get_current_user)):
    return CASE_TYPES


@router.get("")
async def list_cases(response: Response, user: dict = Depends(get_current_user)):
    docs, cut = await fetch_capped(db.cases.find(tenant_query(user)).sort("created_at", -1), CASES_LIST_LIMIT)
    disclose_truncation(response, cut, CASES_LIST_LIMIT)
    return [Case.from_mongo(d) for d in docs]


@router.post("")
async def create_case(payload: Case, user: dict = Depends(get_current_user)):
    payload.number = await _next_case_number(firm_id_for(user))
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    payload.history = [{"event": "Δημιουργία υπόθεσης", "at": _now()}]
    result = await db.cases.insert_one(payload.to_mongo())
    doc = await db.cases.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Case.from_mongo(doc)


async def _case_total(collection, user: dict, case_id: str, field: str) -> float:
    """Το άθροισμα ενός πεδίου πάνω σε ΟΛΑ τα (μη ακυρωμένα) έγγραφα της υπόθεσης — όχι στα όσα δείχνει η λίστα.

    Πριν, τα «Αμοιβές» και «Έξοδα» της υπόθεσης αθροίζονταν από τα πρώτα 100 γραμμάτια/παράβολα που επιστρέφονταν για
    εμφάνιση: μια υπόθεση με 101 γραμμάτια έδειχνε σύνολο χωρίς το παλαιότερο, δίχως καμία ένδειξη."""
    total = 0.0
    async for doc in collection.find(tenant_query(user, {"case_id": case_id, "status": {"$ne": "cancelled"}}), {field: 1}):
        total += float(doc.get(field) or 0)
    return round(total, 2)


@router.get("/{case_id}")
async def get_case(case_id: str, response: Response, user: dict = Depends(get_current_user)):
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    grammatia, cut_g = await fetch_capped(
        db.grammatia.find(tenant_query(user, {"case_id": case_id})).sort("created_at", -1), CASE_SUBLIST_LIMIT)
    paravola, cut_p = await fetch_capped(
        db.paravola.find(tenant_query(user, {"case_id": case_id})).sort("created_at", -1), CASE_SUBLIST_LIMIT)
    disclose_truncation(response, cut_g or cut_p, CASE_SUBLIST_LIMIT)
    from models import Grammatio, Paravolo
    return {
        "case": Case.from_mongo(doc),
        "grammatia": [Grammatio.from_mongo(g) for g in grammatia],
        "paravola": [Paravolo.from_mongo(p) for p in paravola],
        "fees_total": await _case_total(db.grammatia, user, case_id, "total"),
        "expenses_total": await _case_total(db.paravola, user, case_id, "amount"),
    }


@router.put("/{case_id}")
async def update_case(case_id: str, payload: Case, user: dict = Depends(get_current_user)):
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    data = payload.model_dump(exclude={"id", "number", "history", "notes", "created_at", "created_by", "firm_id"})
    await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$set": data})
    await log_case_event(case_id, "Επεξεργασία στοιχείων υπόθεσης", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.delete("/{case_id}")
async def delete_case(case_id: str, user: dict = Depends(get_current_user)):
    result = await db.cases.delete_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    return {"message": "Η υπόθεση διαγράφηκε"}


@router.post("/{case_id}/notes")
async def add_note(case_id: str, payload: NotePayload, user: dict = Depends(get_current_user)):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Κενή σημείωση")
    note = {"text": payload.text.strip(), "author": user.get("name") or user["email"], "at": _now()}
    result = await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$push": {"notes": note}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, "Προσθήκη σημείωσης", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.post("/{case_id}/status")
async def change_status(case_id: str, payload: StatusPayload, user: dict = Depends(get_current_user)):
    if payload.status not in STATUS_LABELS:
        raise HTTPException(status_code=400, detail="Μη έγκυρη κατάσταση")
    result = await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$set": {"status": payload.status}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, f"Αλλαγή κατάστασης σε «{STATUS_LABELS[payload.status]}»", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.post("/{case_id}/next-action")
async def set_next_action(case_id: str, payload: NextActionPayload, user: dict = Depends(get_current_user)):
    result = await db.cases.update_one(
        tenant_query(user, {"_id": ObjectId(case_id)}),
        {"$set": {"next_action": payload.next_action, "next_action_date": payload.next_action_date}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, f"Ορισμός επόμενης ενέργειας: {payload.next_action}" if payload.next_action else "Καθαρισμός επόμενης ενέργειας", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)
