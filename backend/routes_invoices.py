import uuid
import tempfile
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from bson import ObjectId
from db import db
from models import Invoice
from auth import get_current_user
from tax_utils import calculate_invoice_totals
from ai_helpers import transcribe_audio, parse_invoice_from_text

router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _next_invoice_number() -> str:
    count = await db.invoices.count_documents({})
    return f"ΤΠΥ-{2026}-{count + 1:04d}"


@router.get("")
async def list_invoices(user: dict = Depends(get_current_user)):
    docs = await db.invoices.find({}).sort("created_at", -1).to_list(1000)
    return [Invoice.from_mongo(d) for d in docs]


@router.post("")
async def create_invoice(payload: Invoice, user: dict = Depends(get_current_user)):
    payload.id = None
    client_doc = await db.clients.find_one({"_id": ObjectId(payload.client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    payload.client_name = client_doc["name"]
    totals = calculate_invoice_totals(payload.line_items)
    payload.subtotal = totals["subtotal"]
    payload.vat_amount = totals["vat_amount"]
    payload.total = totals["total"]
    payload.vat_breakdown = totals["vat_breakdown"]
    payload.number = await _next_invoice_number()
    payload.created_by = user["email"]
    result = await db.invoices.insert_one(payload.to_mongo())
    doc = await db.invoices.find_one({"_id": result.inserted_id})
    return Invoice.from_mongo(doc)


@router.put("/{invoice_id}")
async def update_invoice(invoice_id: str, payload: Invoice, user: dict = Depends(get_current_user)):
    existing = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    client_doc = await db.clients.find_one({"_id": ObjectId(payload.client_id)})
    if not client_doc:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    totals = calculate_invoice_totals(payload.line_items)
    payload.id = invoice_id
    payload.client_name = client_doc["name"]
    payload.subtotal = totals["subtotal"]
    payload.vat_amount = totals["vat_amount"]
    payload.total = totals["total"]
    payload.vat_breakdown = totals["vat_breakdown"]
    payload.number = existing["number"]
    data = payload.to_mongo()
    data.pop("_id", None)
    await db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": data})
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    return Invoice.from_mongo(doc)


@router.delete("/{invoice_id}")
async def delete_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    result = await db.invoices.delete_one({"_id": ObjectId(invoice_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    return {"message": "Το τιμολόγιο διαγράφηκε"}


@router.post("/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    mark = f"MOCK-{uuid.uuid4().hex[:16].upper()}"
    uid = f"UID-{uuid.uuid4().hex[:12].upper()}"
    await db.invoices.update_one(
        {"_id": ObjectId(invoice_id)},
        {"$set": {
            "status": "issued",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "mydata_status": "synced",
            "mydata_mark": mark,
            "mydata_uid": uid,
        }},
    )
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    return Invoice.from_mongo(doc)


@router.get("/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, user: dict = Depends(get_current_user)):
    from pdf_utils import build_invoice_pdf
    doc = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    client = None
    if doc.get("client_id"):
        try:
            client = await db.clients.find_one({"_id": ObjectId(doc["client_id"])})
        except Exception:
            client = None
    settings = await db.settings.find_one({}) or {}
    pdf_bytes = build_invoice_pdf(doc, client or {}, settings)
    safe_name = (doc.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.post("/voice-parse")
async def voice_parse_invoice(audio: UploadFile = File(...), user: dict = Depends(get_current_user)):
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        transcript = await transcribe_audio(tmp_path)
        clients = await db.clients.find({}).to_list(200)
        client_names = [c["name"] for c in clients]
        parsed = await parse_invoice_from_text(transcript, client_names)
        matched_client = None
        for c in clients:
            if c["name"].lower() in parsed.get("client_name", "").lower() or parsed.get("client_name", "").lower() in c["name"].lower():
                matched_client = {"id": str(c["_id"]), "name": c["name"]}
                break
        return {"transcript": transcript, "parsed": parsed, "matched_client": matched_client}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία επεξεργασίας φωνητικής εντολής: {e}")
    finally:
        os.unlink(tmp_path)
