import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import Expense
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from ai_helpers import ocr_receipt
from storage import upload_receipt_image, get_object

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get("")
async def list_expenses(user: dict = Depends(get_current_user)):
    docs = await db.expenses.find(tenant_query(user)).sort("created_at", -1).to_list(1000)
    return [Expense.from_mongo(d) for d in docs]


@router.get("/payables")
async def payables(user: dict = Depends(get_current_user)):
    from datetime import date
    today = date.today().isoformat()
    docs = await db.expenses.find(tenant_query(user, {"payment_status": "unpaid"})).sort("due_date", 1).to_list(1000)
    items = [Expense.from_mongo(d).model_dump() | {"overdue": bool(d.get("due_date")) and d["due_date"] < today} for d in docs]
    return {
        "items": items,
        "total": round(sum(i["amount"] for i in items), 2),
        "overdue_total": round(sum(i["amount"] for i in items if i["overdue"]), 2),
    }


@router.post("/{expense_id}/mark-paid")
async def mark_paid(expense_id: str, user: dict = Depends(get_current_user)):
    from expense_services import mark_expense_paid
    result = await mark_expense_paid(firm_id_for(user), expense_id, "manual")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    return {"result": result}


@router.post("")
async def create_expense(payload: Expense, user: dict = Depends(get_current_user)):
    payload.id = None
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    result = await db.expenses.insert_one(payload.to_mongo())
    doc = await db.expenses.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Expense.from_mongo(doc)


@router.put("/{expense_id}")
async def update_expense(expense_id: str, payload: Expense, user: dict = Depends(get_current_user)):
    payload.id = expense_id
    data = payload.to_mongo()
    data.pop("_id", None)
    data.pop("firm_id", None)
    result = await db.expenses.update_one(tenant_query(user, {"_id": ObjectId(expense_id)}), {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    doc = await db.expenses.find_one(tenant_query(user, {"_id": ObjectId(expense_id)}))
    return Expense.from_mongo(doc)


@router.delete("/{expense_id}")
async def delete_expense(expense_id: str, user: dict = Depends(get_current_user)):
    result = await db.expenses.delete_one(tenant_query(user, {"_id": ObjectId(expense_id)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    return {"message": "Το έξοδο διαγράφηκε"}


class OcrRequest(BaseModel):
    image_base64: str


@router.post("/ocr-parse")
async def ocr_parse_expense(payload: OcrRequest, user: dict = Depends(get_current_user)):
    try:
        parsed = await ocr_receipt(payload.image_base64)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία ανάλυσης απόδειξης: {e}")
    receipt_path = None
    try:
        image_bytes = base64.b64decode(payload.image_base64)
        receipt_path = upload_receipt_image(image_bytes)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Receipt storage failed: {e}")
    return {"parsed": parsed, "receipt_image_url": receipt_path}


@router.get("/files/{path:path}")
async def get_receipt_file(path: str, user: dict = Depends(get_current_user)):
    try:
        data, content_type = get_object(path)
    except Exception:
        raise HTTPException(status_code=404, detail="Το αρχείο δεν βρέθηκε")
    return Response(content=data, media_type=content_type)
