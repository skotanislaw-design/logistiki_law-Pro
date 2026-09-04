import uuid
import tempfile
import os
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from bson import ObjectId
from db import db
from models import Invoice
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import calculate_invoice_totals
from ai_helpers import transcribe_audio, parse_invoice_from_text

router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _next_invoice_number(firm_id: str) -> str:
    from datetime import datetime, timezone
    from pymongo import ReturnDocument
    year = datetime.now(timezone.utc).year
    counter = await db.counters.find_one_and_update(
        {"_id": f"invoice-{firm_id}-{year}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = counter["seq"]
    # Seed the counter above any pre-existing max if this is the first use for the year
    if seq == 1:
        existing_max = 0
        async for d in db.invoices.find({"firm_id": firm_id, "number": {"$regex": f"^ΤΠΥ-{year}-"}}, {"number": 1}):
            try:
                n = int(d["number"].rsplit("-", 1)[-1])
                if n > existing_max:
                    existing_max = n
            except Exception:
                pass
        if existing_max >= seq:
            counter = await db.counters.find_one_and_update(
                {"_id": f"invoice-{firm_id}-{year}"},
                {"$set": {"seq": existing_max + 1}},
                return_document=ReturnDocument.AFTER,
            )
            seq = counter["seq"]
    return f"ΤΠΥ-{year}-{seq:04d}"


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


@router.post("")
async def create_invoice(payload: Invoice, user: dict = Depends(get_current_user)):
    payload.id = None
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
    result = await db.invoices.delete_one(tenant_query(user, {"_id": invoice_oid}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    return {"message": "Το τιμολόγιο διαγράφηκε"}


@router.post("/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone
    from mydata_client import mydata_ready, send_invoice, MyDataError
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    settings = await db.settings.find_one({}) or {}
    update = {"status": "issued", "issued_at": datetime.now(timezone.utc).isoformat()}

    if mydata_ready(settings):
        client = None
        if doc.get("client_id"):
            try:
                client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(doc["client_id"])}))
            except Exception:
                client = None
        try:
            result = await send_invoice(settings, doc, client or {})
            update.update({"mydata_status": "synced", "mydata_mark": result["mark"], "mydata_uid": result["uid"], "mydata_error": None})
        except MyDataError as e:
            update.update({"mydata_status": "failed", "mydata_error": str(e)})
    else:
        update.update({
            "mydata_status": "synced",
            "mydata_mark": f"MOCK-{uuid.uuid4().hex[:16].upper()}",
            "mydata_uid": f"UID-{uuid.uuid4().hex[:12].upper()}",
        })

    await db.invoices.update_one(tenant_query(user, {"_id": ObjectId(invoice_id)}), {"$set": update})
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
    settings = await db.settings.find_one({}) or {}
    pdf_bytes = build_invoice_pdf(doc, client or {}, settings)
    safe_name = (doc.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.post("/{invoice_id}/send-email")
async def send_invoice_by_email(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone
    from pdf_utils import build_invoice_pdf
    from email_utils import smtp_ready, send_email_with_pdf, invoice_email_html, invoice_email_text
    import aiosmtplib as _smtp

    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    client = None
    if doc.get("client_id"):
        try:
            client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(doc["client_id"])}))
        except Exception:
            client = None
    if not client or not client.get("email"):
        raise HTTPException(status_code=400, detail="Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    settings = await db.settings.find_one({}) or {}
    if not smtp_ready(settings):
        raise HTTPException(status_code=400, detail="Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")

    pdf_bytes = build_invoice_pdf(doc, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Τιμολόγιο {doc.get('number')} — {company}"
    safe_name = (doc.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            invoice_email_text(doc, settings), invoice_email_html(doc, settings),
            pdf_bytes=pdf_bytes, pdf_filename=f"{safe_name}.pdf",
        )
    except _smtp.errors.SMTPAuthenticationError:
        raise HTTPException(status_code=400, detail="Αποτυχία σύνδεσης SMTP: λάθος email ή App Password. Ελέγξτε τις Ρυθμίσεις.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία αποστολής email: {e}")

    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"email_sent_at": datetime.now(timezone.utc).isoformat(), "email_sent_to": client["email"]}},
    )
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


@router.post("/{invoice_id}/send-reminder")
async def send_invoice_reminder(invoice_id: str, user: dict = Depends(get_current_user)):
    from datetime import datetime, timezone, date as _date
    from pdf_utils import build_invoice_pdf
    from email_utils import smtp_ready, send_email_with_pdf, reminder_email_html, reminder_email_text
    import aiosmtplib as _smtp

    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Το τιμολόγιο είναι ήδη εξοφλημένο — δεν χρειάζεται υπενθύμιση.")
    if doc.get("status") != "issued":
        raise HTTPException(status_code=400, detail="Μόνο εκδοθέντα τιμολόγια μπορούν να λάβουν υπενθύμιση. Εκδώστε πρώτα το τιμολόγιο.")

    client = None
    if doc.get("client_id"):
        try:
            client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(doc["client_id"])}))
        except Exception:
            client = None
    if not client or not client.get("email"):
        raise HTTPException(status_code=400, detail="Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    settings = await db.settings.find_one({}) or {}
    if not smtp_ready(settings):
        raise HTTPException(status_code=400, detail="Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")

    days_overdue = 0
    try:
        issued_date = _date.fromisoformat((doc.get("date") or "")[:10])
        days_overdue = max(0, (_date.today() - issued_date).days)
    except Exception:
        days_overdue = 0

    reminder_count = int(doc.get("reminder_count") or 0)

    # Δημιουργία payment link αν είναι διαθέσιμο Stripe
    payment_link = ""
    try:
        import os
        import stripe as _stripe
        _stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or "sk_test_emergent"
        if _stripe.api_key and not _stripe.api_key.endswith("_emergent"):
            total = float(doc.get("total", 0))
            if total > 0:
                origin = settings.get("public_url") or os.environ.get("PREVIEW_ENDPOINT", "")
                session = _stripe.checkout.Session.create(
                    mode="payment",
                    line_items=[{
                        "price_data": {
                            "currency": "eur",
                            "product_data": {
                                "name": f"Τιμολόγιο {doc.get('number', '')}",
                                "description": f"Νομικές υπηρεσίες προς {doc.get('client_name', '')}",
                            },
                            "unit_amount": int(round(total * 100)),
                        },
                        "quantity": 1,
                    }],
                    success_url=(origin or "https://example.com") + "/payment/success?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=(origin or "https://example.com") + "/payment/cancel",
                    metadata={"invoice_id": invoice_id, "invoice_number": doc.get("number", "")},
                    expires_at=int((datetime.now(timezone.utc).timestamp())) + 60 * 60 * 24 * 7,  # 7 ημέρες
                )
                payment_link = session.url
                from models import PaymentTransaction
                tx = PaymentTransaction(
                    session_id=session.id,
                    invoice_id=invoice_id,
                    invoice_number=doc.get("number", ""),
                    client_id=doc.get("client_id", ""),
                    client_name=doc.get("client_name", ""),
                    amount=total,
                    currency="eur",
                )
                await db.payment_transactions.insert_one(tx.to_mongo())
    except Exception:
        payment_link = ""

    pdf_bytes = build_invoice_pdf(doc, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Υπενθύμιση εξόφλησης — Τιμολόγιο {doc.get('number')} — {company}"
    safe_name = (doc.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            reminder_email_text(doc, settings, days_overdue, reminder_count, payment_link),
            reminder_email_html(doc, settings, days_overdue, reminder_count, payment_link),
            pdf_bytes=pdf_bytes, pdf_filename=f"{safe_name}.pdf",
        )
    except _smtp.errors.SMTPAuthenticationError:
        raise HTTPException(status_code=400, detail="Αποτυχία σύνδεσης SMTP: λάθος email ή App Password. Ελέγξτε τις Ρυθμίσεις.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία αποστολής υπενθύμισης: {e}")

    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"reminder_sent_at": datetime.now(timezone.utc).isoformat(), "email_sent_to": client["email"]},
         "$inc": {"reminder_count": 1}},
    )
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    return Invoice.from_mongo(doc)


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
