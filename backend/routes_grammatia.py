from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db
from http_utils import content_disposition
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from models import Grammatio, Invoice, InvoiceLineItem
from routes_cases import log_case_event

router = APIRouter(prefix="/grammatia", tags=["grammatia"])

GRAMMATIA_REFERENCE = [
    {"court_type": "Ειρηνοδικείο", "procedure": "Μικροδιαφορές", "amount": 64.0},
    {"court_type": "Ειρηνοδικείο", "procedure": "Τακτική διαδικασία", "amount": 107.0},
    {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "amount": 187.0},
    {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Ασφαλιστικά μέτρα", "amount": 161.0},
    {"court_type": "Πολυμελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "amount": 342.0},
    {"court_type": "Μονομελές Εφετείο", "procedure": "Έφεση", "amount": 288.0},
    {"court_type": "Τριμελές Εφετείο", "procedure": "Έφεση", "amount": 395.0},
    {"court_type": "Άρειος Πάγος", "procedure": "Αναίρεση", "amount": 620.0},
    {"court_type": "Συμβούλιο της Επικρατείας", "procedure": "Αίτηση ακύρωσης", "amount": 620.0},
    {"court_type": "Διοικητικό Πρωτοδικείο", "procedure": "Προσφυγή", "amount": 161.0},
    {"court_type": "Διοικητικό Εφετείο", "procedure": "Έφεση", "amount": 288.0},
    {"court_type": "Πλημμελειοδικείο", "procedure": "Ποινική υπεράσπιση", "amount": 149.0},
    {"court_type": "Εξωδικαστικές ενέργειες", "procedure": "Σύνταξη/κατάθεση δικογράφου", "amount": 107.0},
]


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _next_number(firm_id: str):
    count = await db.grammatia.count_documents({"firm_id": firm_id})
    return f"ΓΡΠ-{date.today().year}-{count + 1:04d}"


class IssuePayload(BaseModel):
    portal_ref: str = ""


@router.get("/reference/table")
async def reference_table(user: dict = Depends(get_current_user)):
    return GRAMMATIA_REFERENCE


@router.get("")
async def list_grammatia(case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"case_id": case_id} if case_id else {}
    docs = await db.grammatia.find(tenant_query(user, query)).sort("created_at", -1).to_list(500)
    return [Grammatio.from_mongo(d) for d in docs]


@router.post("")
async def create_grammatio(payload: Grammatio, user: dict = Depends(get_current_user)):
    payload.firm_id = firm_id_for(user)
    payload.number = await _next_number(firm_id_for(user))
    payload.created_by = user["email"]
    payload.vat_amount = round(payload.vat_amount, 2)
    payload.total = round(payload.reference_amount + payload.contributions_amount + payload.vat_amount, 2)
    result = await db.grammatia.insert_one(payload.to_mongo())
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": result.inserted_id}))
    if payload.case_id:
        await log_case_event(payload.case_id, f"Δημιουργία γραμματίου {payload.number} ({payload.court_type})", user)
    return Grammatio.from_mongo(doc)


@router.put("/{gid}")
async def update_grammatio(gid: str, payload: Grammatio, user: dict = Depends(get_current_user)):
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if doc.get("status") == "issued":
        raise HTTPException(status_code=400, detail="Δεν επιτρέπεται επεξεργασία εκδοθέντος γραμματίου")
    data = payload.model_dump(exclude={"id", "number", "created_at", "created_by", "status", "issued_at", "invoice_id", "firm_id"})
    data["total"] = round(payload.reference_amount + payload.contributions_amount + payload.vat_amount, 2)
    await db.grammatia.update_one(tenant_query(user, {"_id": ObjectId(gid)}), {"$set": data})
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    return Grammatio.from_mongo(doc)


@router.delete("/{gid}")
async def delete_grammatio(gid: str, user: dict = Depends(get_current_user)):
    result = await db.grammatia.delete_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    return {"message": "Το γραμμάτιο διαγράφηκε"}


@router.post("/{gid}/issue")
async def issue_grammatio(gid: str, payload: IssuePayload, user: dict = Depends(get_current_user)):
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if doc.get("status") == "issued":
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη εκδοθεί")
    await db.grammatia.update_one(
        tenant_query(user, {"_id": ObjectId(gid)}),
        {"$set": {"status": "issued", "issued_at": _now(), "portal_ref": payload.portal_ref}},
    )
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Έκδοση γραμματίου {doc.get('number')}", user)
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    return Grammatio.from_mongo(doc)


@router.get("/{gid}/pdf")
async def grammatio_pdf(gid: str, user: dict = Depends(get_current_user)):
    from pdf_utils import build_grammatio_pdf
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    pdf_bytes = build_grammatio_pdf(doc, settings)
    safe = doc.get("number") or "grammatio"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(f"{safe}.pdf")},
    )


@router.post("/{gid}/to-invoice")
async def grammatio_to_invoice(gid: str, user: dict = Depends(get_current_user)):
    from routes_invoices import _next_invoice_number
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if not doc.get("client_id"):
        raise HTTPException(status_code=400, detail="Το γραμμάτιο δεν έχει συνδεδεμένο πελάτη")
    if doc.get("invoice_id"):
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη ενσωματωθεί σε τιμολόγιο")
    inv = Invoice(
        firm_id=firm_id_for(user),
        client_id=doc["client_id"],
        client_name=doc.get("client_name", ""),
        date=date.today().isoformat(),
        line_items=[InvoiceLineItem(
            description=f"Ενσωμάτωση Γραμματίου Προείσπραξης {doc.get('number')} ({doc.get('court_type')})",
            quantity=1, unit_price=doc.get("total", 0), vat_rate=0,
        )],
        created_by=user["email"],
        notes=f"Γραμμάτιο: {doc.get('number')} · Υπόθεση: {doc.get('case_title') or '—'}",
    )
    from tax_utils import calculate_invoice_totals
    totals = calculate_invoice_totals(inv.line_items)
    inv.subtotal, inv.vat_amount, inv.total, inv.vat_breakdown = totals["subtotal"], totals["vat_amount"], totals["total"], totals["vat_breakdown"]
    inv.number = await _next_invoice_number(firm_id_for(user))
    result = await db.invoices.insert_one(inv.to_mongo())
    await db.grammatia.update_one(tenant_query(user, {"_id": ObjectId(gid)}), {"$set": {"invoice_id": str(result.inserted_id)}})
    inv_doc = await db.invoices.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Invoice.from_mongo(inv_doc)
