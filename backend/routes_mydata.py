import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from db import db
from auth import get_current_user

router = APIRouter(prefix="/mydata", tags=["mydata"])


@router.get("/status")
async def mydata_status(user: dict = Depends(get_current_user)):
    settings = await db.settings.find_one({})
    connected = bool(settings and settings.get("mydata_connected"))
    pending = await db.invoices.count_documents({"mydata_status": {"$in": ["not_sent", "pending", "failed"]}})
    synced = await db.invoices.count_documents({"mydata_status": "synced"})
    return {
        "connected": connected,
        "mode": "mock" if not connected else "mock_connected",
        "pending_count": pending,
        "synced_count": synced,
        "note": "Η σύνδεση με το myDATA/ΑΑΔΕ λειτουργεί σε προσομοίωση (mock) μέχρι να καταχωρηθούν πραγματικά credentials ΑΑΔΕ στις Ρυθμίσεις.",
    }


@router.post("/sync/{invoice_id}")
async def sync_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    mark = f"MOCK-{uuid.uuid4().hex[:16].upper()}"
    uid = f"UID-{uuid.uuid4().hex[:12].upper()}"
    await db.invoices.update_one(
        {"_id": ObjectId(invoice_id)},
        {"$set": {"mydata_status": "synced", "mydata_mark": mark, "mydata_uid": uid}},
    )
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    return {
        "message": "Το παραστατικό στάλθηκε (προσομοίωση) στο myDATA",
        "mark": mark, "uid": uid,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
