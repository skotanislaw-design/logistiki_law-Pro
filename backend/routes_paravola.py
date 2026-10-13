from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db, fetch_capped
from http_utils import disclose_truncation
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from models import Paravolo
from tax_utils import athens_today, money_limit_error
from routes_cases import log_case_event

router = APIRouter(prefix="/paravola", tags=["paravola"])

LIST_LIMIT = 500          # τα νεότερα παράβολα· αν υπάρχουν κι άλλα, η απάντηση το δηλώνει (X-List-Truncated)

PARAVOLO_CATEGORIES = [
    "Ένδικα βοηθήματα/μέσα (πολιτικά δικαστήρια)",
    "Ένδικα βοηθήματα/μέσα (διοικητικά δικαστήρια)",
    "Ποινική διαδικασία (μήνυση/έφεση)",
    "Διαταγή πληρωμής",
    "Κτηματολόγιο / Υποθηκοφυλακείο",
    "Πιστοποιητικά / Άδειες",
    "Δικαστικό ένσημο",
    "Λοιπά παράβολα",
]


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _next_number(firm_id: str):
    from numbering import next_in_series
    year = athens_today().year
    return await next_in_series(db.paravola, f"paravolo-{firm_id}-{year}", firm_id, "ΠΡΒ", year)


class IssuePayload(BaseModel):
    eparavolo_code: str = ""


def _validate_amount(payload: Paravolo) -> None:
    """Το ποσό παραβόλου: πεπερασμένο, όχι αρνητικό, μέχρι το όριο. Έλεγχος στη διαδρομή (το μοντέλο διαβάζει και παλιές εγγραφές)."""
    problem = money_limit_error(payload.amount, "Ποσό παραβόλου")
    if problem:
        raise HTTPException(status_code=400, detail=problem)


@router.get("/categories/list")
async def categories(user: dict = Depends(get_current_user)):
    return PARAVOLO_CATEGORIES


@router.get("")
async def list_paravola(response: Response, case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"case_id": case_id} if case_id else {}
    docs, cut = await fetch_capped(db.paravola.find(tenant_query(user, query)).sort("created_at", -1), LIST_LIMIT)
    disclose_truncation(response, cut, LIST_LIMIT)
    return [Paravolo.from_mongo(d) for d in docs]


@router.post("")
async def create_paravolo(payload: Paravolo, user: dict = Depends(get_current_user)):
    _validate_amount(payload)
    payload.number = await _next_number(firm_id_for(user))
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    payload.status, payload.issued_at = "draft", None       # η κατάσταση ανήκει στον server
    result = await db.paravola.insert_one(payload.to_mongo())
    doc = await db.paravola.find_one(tenant_query(user, {"_id": result.inserted_id}))
    if payload.case_id:
        await log_case_event(payload.case_id, f"Δημιουργία παραβόλου {payload.number} ({payload.category})", user)
    return Paravolo.from_mongo(doc)


@router.put("/{pid}")
async def update_paravolo(pid: str, payload: Paravolo, user: dict = Depends(get_current_user)):
    _validate_amount(payload)
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    # Εκδοθέν ή εξοφλημένο παράβολο είναι καταγραφή πραγματικής συναλλαγής με το Δημόσιο — όπως στη
    # διαγραφή, τροποποιείται μόνο το πρόχειρο (αλλιώς αλλάζει το ποσό και σβήνει ο κωδικός e-παραβόλου).
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα παράβολα τροποποιούνται· το εκδοθέν ή εξοφλημένο παραμένει στο αρχείο.")
    data = payload.model_dump(exclude={"id", "number", "created_at", "created_by", "status", "issued_at", "firm_id"})
    result = await db.paravola.update_one(tenant_query(user, {"_id": ObjectId(pid), "status": "draft"}), {"$set": data})
    if result.matched_count == 0:       # εκδόθηκε στο μεταξύ
        raise HTTPException(status_code=400, detail="Το παράβολο δεν είναι πια πρόχειρο — δεν τροποποιείται.")
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)


@router.delete("/{pid}")
async def delete_paravolo(pid: str, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    # Εκδοθέν ή εξοφλημένο παράβολο είναι καταγραφή πραγματικής συναλλαγής με το Δημόσιο.
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα παράβολα διαγράφονται· το εκδοθέν ή εξοφλημένο παραμένει στο αρχείο.")
    await db.paravola.delete_one(tenant_query(user, {"_id": ObjectId(pid), "status": "draft"}))
    return {"message": "Το παράβολο διαγράφηκε"}


@router.post("/{pid}/issue")
async def issue_paravolo(pid: str, payload: IssuePayload, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    if doc.get("status") in ("issued", "paid"):
        raise HTTPException(status_code=400, detail="Το παράβολο έχει ήδη εκδοθεί")
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Εκδίδεται μόνο πρόχειρο παράβολο.")
    # Ατομική μετάβαση πρόχειρο → εκδοθέν: δύο ταυτόχρονες εκδόσεις δεν γράφουν η μία πάνω στην άλλη.
    result = await db.paravola.update_one(
        tenant_query(user, {"_id": ObjectId(pid), "status": "draft"}),
        {"$set": {"status": "issued", "issued_at": _now(), "eparavolo_code": payload.eparavolo_code}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=400, detail="Το παράβολο έχει ήδη εκδοθεί")
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Έκδοση παραβόλου {doc.get('number')}", user)
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)


@router.post("/{pid}/mark-paid")
async def mark_paid(pid: str, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    if doc.get("status") != "issued":
        raise HTTPException(status_code=400, detail="Εξοφλείται μόνο εκδοθέν παράβολο (και όχι ήδη εξοφλημένο).")
    await db.paravola.update_one(tenant_query(user, {"_id": ObjectId(pid)}), {"$set": {"status": "paid"}})
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Εξόφληση παραβόλου {doc.get('number')}", user)
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)
