import tempfile
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from fastapi.responses import Response
from bson import ObjectId
from db import db
from models import Invoice
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import calculate_invoice_totals, VALID_VAT_RATES
from ai_helpers import transcribe_audio, parse_invoice_from_text

router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _next_invoice_number(firm_id: str, prefix: str = "ΤΠΥ") -> str:
    from datetime import datetime, timezone
    from pymongo import ReturnDocument
    year = datetime.now(timezone.utc).year
    counter_id = f"invoice-{firm_id}-{year}" if prefix == "ΤΠΥ" else f"invoice-{prefix}-{firm_id}-{year}"
    counter = await db.counters.find_one_and_update(
        {"_id": counter_id},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = counter["seq"]
    # Seed the counter above any pre-existing max if this is the first use for the year
    if seq == 1:
        existing_max = 0
        async for d in db.invoices.find({"firm_id": firm_id, "number": {"$regex": f"^{prefix}-{year}-"}}, {"number": 1}):
            try:
                n = int(d["number"].rsplit("-", 1)[-1])
                if n > existing_max:
                    existing_max = n
            except Exception:
                pass
        if existing_max >= seq:
            counter = await db.counters.find_one_and_update(
                {"_id": counter_id},
                {"$set": {"seq": existing_max + 1}},
                return_document=ReturnDocument.AFTER,
            )
            seq = counter["seq"]
    return f"{prefix}-{year}-{seq:04d}"


@router.get("")
async def list_invoices(user: dict = Depends(get_current_user)):
    docs = await db.invoices.find(tenant_query(user)).sort("created_at", -1).to_list(1000)
    return [Invoice.from_mongo(d) for d in docs]


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    return Invoice.from_mongo(doc)


def _validate_invoice_payload(payload: Invoice) -> None:
    """Επικύρωση στο route, όχι στο μοντέλο.

    Το ίδιο μοντέλο διαβάζει και υπάρχουσες εγγραφές από τη Mongo· αυστηροί
    validators εκεί θα έσπαγαν την ανάγνωση παλιών τιμολογίων που γράφτηκαν πριν
    μπει ο έλεγχος. Εδώ ελέγχουμε μόνο ό,τι φτάνει από τον χρήστη.
    """
    from datetime import date as _date

    if not payload.line_items:
        raise HTTPException(status_code=400, detail="Το τιμολόγιο πρέπει να έχει τουλάχιστον μία γραμμή.")
    try:
        _date.fromisoformat(payload.date)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Μη έγκυρη ημερομηνία — αναμένεται μορφή ΕΕΕΕ-ΜΜ-ΗΗ.")
    for idx, line in enumerate(payload.line_items, start=1):
        if not (line.description or "").strip():
            raise HTTPException(status_code=400, detail=f"Γραμμή {idx}: απαιτείται περιγραφή.")
        if line.quantity <= 0:
            raise HTTPException(status_code=400, detail=f"Γραμμή {idx}: η ποσότητα πρέπει να είναι θετική.")
        if line.vat_rate not in VALID_VAT_RATES:
            allowed = ", ".join(f"{r}%" for r in VALID_VAT_RATES)
            raise HTTPException(
                status_code=400,
                detail=f"Γραμμή {idx}: μη έγκυρος συντελεστής ΦΠΑ {line.vat_rate}%. Επιτρέπονται: {allowed}.",
            )


def _reset_lifecycle_fields(payload: Invoice) -> None:
    """Η κατάσταση έκδοσης και διαβίβασης ανήκει στον server.

    Χωρίς αυτό, ένα POST μπορούσε να δηλώσει status="paid" και mydata_status="synced"
    με πλαστό MARK, δηλαδή παραστατικό που εμφανίζεται διαβιβασμένο στην ΑΑΔΕ χωρίς
    ποτέ να έχει σταλεί. Η κατάσταση αλλάζει μόνο μέσω /issue, /mark-paid, /cancel
    και του Autopilot.
    """
    payload.status = "draft"
    payload.mydata_status = "not_sent"
    payload.mydata_mark = None
    payload.mydata_uid = None
    payload.mydata_error = None
    payload.paid_at = None
    payload.issued_at = None


@router.post("")
async def create_invoice(payload: Invoice, user: dict = Depends(get_current_user)):
    payload.id = None
    _validate_invoice_payload(payload)
    _reset_lifecycle_fields(payload)
    client_doc = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(payload.client_id)}))
    if not client_doc:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    payload.client_name = client_doc["name"]
    payload.firm_id = firm_id_for(user)
    totals = calculate_invoice_totals(payload.line_items)
    payload.subtotal = totals["subtotal"]
    payload.vat_amount = totals["vat_amount"]
    payload.total = totals["total"]
    payload.vat_breakdown = totals["vat_breakdown"]
    if payload.total <= 0:
        raise HTTPException(status_code=400, detail="Το σύνολο του τιμολογίου πρέπει να είναι θετικό.")
    payload.number = await _next_invoice_number(firm_id_for(user))
    payload.created_by = user["email"]
    result = await db.invoices.insert_one(payload.to_mongo())
    doc = await db.invoices.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Invoice.from_mongo(doc)


@router.put("/{invoice_id}")
async def update_invoice(invoice_id: str, payload: Invoice, user: dict = Depends(get_current_user)):
    try:
        invoice_oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    existing = await db.invoices.find_one(tenant_query(user, {"_id": invoice_oid}))
    if not existing:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if existing.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα τιμολόγια μπορούν να τροποποιηθούν.")
    _validate_invoice_payload(payload)
    # Η κατάσταση έκδοσης/διαβίβασης δεν έρχεται ποτέ από τον client.
    payload.status = existing.get("status", "draft")
    payload.mydata_status = existing.get("mydata_status", "not_sent")
    payload.mydata_mark = existing.get("mydata_mark")
    payload.mydata_uid = existing.get("mydata_uid")
    payload.paid_at = existing.get("paid_at")
    payload.issued_at = existing.get("issued_at")
    client_doc = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(payload.client_id)}))
    if not client_doc:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    totals = calculate_invoice_totals(payload.line_items)
    payload.id = invoice_id
    payload.firm_id = firm_id_for(user)
    payload.client_name = client_doc["name"]
    payload.subtotal = totals["subtotal"]
    payload.vat_amount = totals["vat_amount"]
    payload.total = totals["total"]
    payload.vat_breakdown = totals["vat_breakdown"]
    payload.number = existing["number"]
    data = payload.to_mongo()
    data.pop("_id", None)
    await db.invoices.update_one(tenant_query(user, {"_id": invoice_oid}), {"$set": data})
    doc = await db.invoices.find_one(tenant_query(user, {"_id": invoice_oid}))
    return Invoice.from_mongo(doc)


@router.delete("/{invoice_id}")
async def delete_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    try:
        invoice_oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    existing = await db.invoices.find_one(tenant_query(user, {"_id": invoice_oid}))
    if not existing:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    # ΕΛΠ (Ν.4308/2014): εκδοθέν παραστατικό δεν διαγράφεται — ακυρώνεται με πιστωτικό
    # ή αίτημα ακύρωσης στο myDATA, ώστε η σειρά αρίθμησης να μένει αδιάσπαστη.
    if existing.get("status") != "draft":
        raise HTTPException(
            status_code=400,
            detail="Εκδοθέν τιμολόγιο δεν διαγράφεται. Χρησιμοποιήστε ακύρωση ή πιστωτικό τιμολόγιο.",
        )
    await db.invoices.delete_one(tenant_query(user, {"_id": invoice_oid}))
    return {"message": "Το πρόχειρο τιμολόγιο διαγράφηκε"}


@router.post("/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone
    from invoice_services import transmit_to_mydata, email_invoice, InvoiceEmailError
    firm_id = firm_id_for(user)
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα τιμολόγια μπορούν να εκδοθούν.")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"status": "issued", "issued_at": datetime.now(timezone.utc).isoformat()}},
    )
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    await transmit_to_mydata(firm_id, doc, settings)

    # Autopilot: send the PDF to the client right away so the user never has to.
    if settings.get("autopilot_enabled", True) and settings.get("auto_email_on_issue", True):
        try:
            await email_invoice(firm_id, doc, settings)
        except InvoiceEmailError as e:
            # Not fatal — the invoice is issued; Autopilot will retry / raise an inbox item.
            from automation import notify
            await notify(firm_id, "invoice_email_failed", "warning",
                         f"Το τιμολόγιο {doc.get('number')} εκδόθηκε αλλά δεν στάλθηκε email",
                         str(e), entity="invoice", entity_id=invoice_id, requires_action=True,
                         dedupe_key=f"invoice_email_failed:{invoice_id}")
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.get("/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, user: dict = Depends(get_current_user)):
    from pdf_utils import build_invoice_pdf
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    client = None
    if doc.get("client_id"):
        try:
            client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(doc["client_id"])}))
        except Exception:
            client = None
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    pdf_bytes = build_invoice_pdf(doc, client or {}, settings)
    safe_name = (doc.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.post("/{invoice_id}/send-email")
async def send_invoice_by_email(invoice_id: str, user: dict = Depends(get_current_user)):
    from invoice_services import email_invoice, InvoiceEmailError
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    try:
        await email_invoice(firm_id_for(user), doc, await db.settings.find_one(tenant_query(user, {})) or {})
    except InvoiceEmailError as e:
        raise HTTPException(status_code=400, detail=str(e))
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.post("/{invoice_id}/send-reminder")
async def send_invoice_reminder(invoice_id: str, user: dict = Depends(get_current_user)):
    from invoice_services import send_reminder, InvoiceEmailError
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    try:
        await send_reminder(firm_id_for(user), doc, await db.settings.find_one(tenant_query(user, {})) or {})
    except InvoiceEmailError as e:
        raise HTTPException(status_code=400, detail=str(e))
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


class CancelPayload(BaseModel):
    reason: str = ""


class CreditNotePayload(BaseModel):
    reason: str = ""
    amount: Optional[float] = None   # κενό = πλήρες πιστωτικό


@router.post("/{invoice_id}/cancel")
async def cancel_invoice_endpoint(invoice_id: str, payload: CancelPayload, user: dict = Depends(get_current_user)):
    """Ακύρωση τιμολογίου — και στην ΑΑΔΕ αν έχει ήδη διαβιβαστεί."""
    from invoice_services import cancel_in_mydata, InvoiceWorkflowError
    from mydata_client import MyDataError
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    try:
        await cancel_in_mydata(firm_id_for(user), doc, settings)
    except (InvoiceWorkflowError, MyDataError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    if payload.reason:
        await db.invoices.update_one(tenant_query(user, {"_id": ObjectId(invoice_id)}),
                                     {"$set": {"cancellation_reason": payload.reason[:500]}})
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.post("/{invoice_id}/credit-note")
async def create_credit_note(invoice_id: str, payload: CreditNotePayload, user: dict = Depends(get_current_user)):
    """Έκδοση πιστωτικού τιμολογίου συσχετιζόμενου με το αρχικό (myDATA 5.1)."""
    from invoice_services import build_credit_note, InvoiceWorkflowError
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if payload.amount is not None and payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Το ποσό της πίστωσης πρέπει να είναι θετικό.")
    try:
        credit = await build_credit_note(firm_id_for(user), doc, user["email"], payload.reason, payload.amount)
    except InvoiceWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Invoice.from_mongo(credit)


@router.get("/types/list")
async def invoice_types(user: dict = Depends(get_current_user)):
    from mydata_client import INVOICE_TYPE_LABELS
    return [{"code": code, "label": label} for code, label in INVOICE_TYPE_LABELS.items()]


@router.post("/{invoice_id}/mark-paid")
async def mark_invoice_paid(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Το τιμολόγιο είναι ήδη εξοφλημένο.")
    if doc.get("status") != "issued":
        raise HTTPException(status_code=400, detail="Μόνο εκδοθέντα τιμολόγια μπορούν να σημανθούν ως εξοφλημένα")
    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}},
    )
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.post("/{invoice_id}/mark-unpaid")
async def mark_invoice_unpaid(invoice_id: str, user: dict = Depends(get_current_user)):
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("status") != "paid":
        raise HTTPException(status_code=400, detail="Το τιμολόγιο δεν είναι σε κατάσταση εξοφλημένο.")
    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"status": "issued", "paid_at": None}},
    )
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.post("/voice-parse")
async def voice_parse_invoice(audio: UploadFile = File(...), user: dict = Depends(get_current_user)):
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        transcript = await transcribe_audio(tmp_path)
        clients = await db.clients.find(tenant_query(user)).to_list(200)
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
