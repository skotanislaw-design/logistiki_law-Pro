from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import Expense
from auth import get_current_user
from ai_helpers import ocr_receipt

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get("")
async def list_expenses(user: dict = Depends(get_current_user)):
    docs = await db.expenses.find({}).sort("created_at", -1).to_list(1000)
    return [Expense.from_mongo(d) for d in docs]


@router.post("")
async def create_expense(payload: Expense, user: dict = Depends(get_current_user)):
    payload.id = None
    payload.created_by = user["email"]
    result = await db.expenses.insert_one(payload.to_mongo())
    doc = await db.expenses.find_one({"_id": result.inserted_id})
    return Expense.from_mongo(doc)


@router.put("/{expense_id}")
async def update_expense(expense_id: str, payload: Expense, user: dict = Depends(get_current_user)):
    payload.id = expense_id
    data = payload.to_mongo()
    data.pop("_id", None)
    result = await db.expenses.update_one({"_id": ObjectId(expense_id)}, {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    doc = await db.expenses.find_one({"_id": ObjectId(expense_id)})
    return Expense.from_mongo(doc)


@router.delete("/{expense_id}")
async def delete_expense(expense_id: str, user: dict = Depends(get_current_user)):
    result = await db.expenses.delete_one({"_id": ObjectId(expense_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    return {"message": "Το έξοδο διαγράφηκε"}


class OcrRequest(BaseModel):
    image_base64: str


@router.post("/ocr-parse")
async def ocr_parse_expense(payload: OcrRequest, user: dict = Depends(get_current_user)):
    try:
        parsed = await ocr_receipt(payload.image_base64)
        return {"parsed": parsed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία ανάλυσης απόδειξης: {e}")
