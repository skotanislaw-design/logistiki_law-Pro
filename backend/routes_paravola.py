from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from models import Paravolo
from routes_cases import log_case_event

router = APIRouter(prefix="/paravola", tags=["paravola"])

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
    count = await db.paravola.count_documents({"firm_id": firm_id})
    return f"ΠΡΒ-{date.today().year}-{count + 1:04d}"


class IssuePayload(BaseModel):
    eparavolo_code: str = ""


@router.get("/categories/list")
async def categories(user: dict = Depends(get_current_user)):
    return PARAVOLO_CATEGORIES


@router.get("")
async def list_paravola(case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"case_id": case_id} if case_id else {}
    docs = await db.paravola.find(tenant_query(user, query)).sort("created_at", -1).to_list(500)
    return [Paravolo.from_mongo(d) for d in docs]


@router.post("")
async def create_paravolo(payload: Paravolo, user: dict = Depends(get_current_user)):
    payload.number = await _next_number(firm_id_for(user))
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    result = await db.paravola.insert_one(payload.to_mongo())
    doc = await db.paravola.find_one(tenant_query(user, {"_id": result.inserted_id}))
    if payload.case_id:
        await log_case_event(payload.case_id, f"Δημιουργία παραβόλου {payload.number} ({payload.category})", user)
    return Paravolo.from_mongo(doc)


@router.put("/{pid}")
async def update_paravolo(pid: str, payload: Paravolo, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    data = payload.model_dump(exclude={"id", "number", "created_at", "created_by", "status", "issued_at", "firm_id"})
    await db.paravola.update_one(tenant_query(user, {"_id": ObjectId(pid)}), {"$set": data})
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)


@router.delete("/{pid}")
async def delete_paravolo(pid: str, user: dict = Depends(get_current_user)):
    result = await db.paravola.delete_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    return {"message": "Το παράβολο διαγράφηκε"}


@router.post("/{pid}/issue")
async def issue_paravolo(pid: str, payload: IssuePayload, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    if doc.get("status") in ("issued", "paid"):
        raise HTTPException(status_code=400, detail="Το παράβολο έχει ήδη εκδοθεί")
    await db.paravola.update_one(
        tenant_query(user, {"_id": ObjectId(pid)}),
        {"$set": {"status": "issued", "issued_at": _now(), "eparavolo_code": payload.eparavolo_code}},
    )
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Έκδοση παραβόλου {doc.get('number')}", user)
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)


@router.post("/{pid}/mark-paid")
async def mark_paid(pid: str, user: dict = Depends(get_current_user)):
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το παράβολο δεν βρέθηκε")
    await db.paravola.update_one(tenant_query(user, {"_id": ObjectId(pid)}), {"$set": {"status": "paid"}})
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Εξόφληση παραβόλου {doc.get('number')}", user)
    doc = await db.paravola.find_one(tenant_query(user, {"_id": ObjectId(pid)}))
    return Paravolo.from_mongo(doc)
