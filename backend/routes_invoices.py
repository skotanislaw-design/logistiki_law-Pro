import tempfile
import os
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from fastapi.responses import Response
from bson import ObjectId
from db import db
from http_utils import content_disposition
from models import Invoice
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import calculate_invoice_totals, VALID_VAT_RATES
from mydata_client import INVOICE_TYPE_LABELS, TYPE_SERVICES_B2B, TYPE_RECEIPT_B2C
from ai_helpers import transcribe_audio, parse_invoice_from_text

router = APIRouter(prefix="/invoices", tags=["invoices"])

# Είδη που μπορεί να ζητήσει ο χρήστης σε πρόχειρο. Το 5.1 (πιστωτικό) δημιουργείται μόνο από
# το /credit-note.
USER_INVOICE_TYPES = (TYPE_SERVICES_B2B, TYPE_RECEIPT_B2C)

# Πεδία που ανήκουν στον server (με την αρχική τιμή τους): ο client δεν τα ορίζει ποτέ, ούτε στη
# δημιουργία ούτε στην τροποποίηση. Τα `status`/`mydata_*`/`issued_at`/`paid_at` έχουν δικούς
# τους κανόνες πιο κάτω.
SERVER_OWNED_DEFAULTS = {
    "credit_note_for": None, "correlated_mark": None, "credit_note_id": None,
    "cancelled_at": None, "cancellation_reason": "", "mydata_cancellation_mark": None,
    "mydata_cancelled_at": None, "mydata_synced_at": None, "mydata_attempts": 0,
    "paid_via": None, "trust_applied": None, "email_sent_at": None, "email_sent_to": None,
    "reminder_sent_at": None, "reminder_count": 0,
}


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
    # Πιστωτικό δεν φτιάχνεται «με το χέρι»: εκδίδεται από το τιμολόγιο που πιστώνει (/credit-note),
    # ώστε να δένεται με το MARK του αρχικού. Άγνωστα είδη δεν περνούν.
    if payload.invoice_type and payload.invoice_type not in USER_INVOICE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Μη έγκυρο είδος παραστατικού. Επιτρέπονται: "
                   + ", ".join(f"{code} ({INVOICE_TYPE_LABELS[code]})" for code in USER_INVOICE_TYPES)
                   + ". Το πιστωτικό εκδίδεται από το αρχικό τιμολόγιο.",
        )
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
    if payload.withholding_amount is not None:
        net = calculate_invoice_totals(payload.line_items)["subtotal"]
        if payload.withholding_amount < 0 or payload.withholding_amount > net:
            raise HTTPException(
                status_code=400,
                detail=f"Η παρακράτηση ({payload.withholding_amount:.2f} €) πρέπει να είναι από 0 έως την καθαρή αξία ({net:.2f} €).",
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
    for field, default in SERVER_OWNED_DEFAULTS.items():
        setattr(payload, field, default)


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
    # Το πρόχειρο δεν έχει αριθμό: ο αριθμός δίνεται στην έκδοση (βλ. invoice_services.issue_draft).
    payload.number = ""
    payload.created_by = user["email"]
    result = await db.invoices.insert_one(payload.to_mongo())
    doc = await db.invoices.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Invoice.from_mongo(doc)


@router.put("/{invoice_id}")
async def update_invoice(invoice_id: str, payload: Invoice, user: dict = Depends(get_current_user)):
    from invoice_services import not_being_issued
    try:
        invoice_oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    existing = await db.invoices.find_one(tenant_query(user, {"_id": invoice_oid}))
    if not existing:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if existing.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα τιμολόγια μπορούν να τροποποιηθούν.")
    is_credit_note = bool(existing.get("credit_note_for"))
    if is_credit_note:
        # Το είδος του πρόχειρου πιστωτικού (5.1) δεν αλλάζει και δεν έρχεται από τον client. Ο έλεγχος
        # ειδών δέχεται μόνο ΤΠΥ/ΑΠΥ· αν ο client έστελνε πίσω το έγγραφο όπως το πήρε (με 5.1), θα
        # απορριπτόταν. Το αγνοούμε εδώ και το επαναφέρουμε από τη βάση αμέσως μετά τον έλεγχο.
        payload.invoice_type = ""
    _validate_invoice_payload(payload)
    if is_credit_note:
        payload.invoice_type = existing.get("invoice_type", "")
    # Η κατάσταση έκδοσης/διαβίβασης δεν έρχεται ποτέ από τον client.
    payload.status = existing.get("status", "draft")
    payload.mydata_status = existing.get("mydata_status", "not_sent")
    payload.mydata_mark = existing.get("mydata_mark")
    payload.mydata_uid = existing.get("mydata_uid")
    payload.paid_at = existing.get("paid_at")
    payload.issued_at = existing.get("issued_at")
    for field, default in SERVER_OWNED_DEFAULTS.items():
        setattr(payload, field, existing.get(field, default))
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
    # Ίδιος κανόνας με τη δημιουργία — ένα πρόχειρο που δεν πέρασε από εκεί δεν πρέπει να
    # φτάνει στην έκδοση με μηδενικό ή αρνητικό σύνολο (και το πιστωτικό με θετικό).
    if is_credit_note:
        if payload.total >= 0:
            raise HTTPException(status_code=400, detail="Το πιστωτικό τιμολόγιο πρέπει να έχει αρνητικό σύνολο.")
    elif payload.total <= 0:
        raise HTTPException(status_code=400, detail="Το σύνολο του τιμολογίου πρέπει να είναι θετικό.")
    payload.number = existing["number"]
    data = payload.to_mongo()
    data.pop("_id", None)
    # Το φίλτρο κατάστασης είναι αναγκαίο: ανάμεσα στην ανάγνωση και στη γραφή το πρόχειρο μπορεί να
    # εκδόθηκε, και η εγγραφή θα άλλαζε τα ποσά ενός ήδη εκδοθέντος παραστατικού.
    result = await db.invoices.update_one(
        tenant_query(user, {"_id": invoice_oid, "status": "draft", **not_being_issued()}), {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=409, detail="Το τιμολόγιο εκδόθηκε ή εκδίδεται αυτή τη στιγμή — δεν τροποποιείται.")
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
    # Αποδέσμευση: αλλιώς το αρχικό τιμολόγιο θα έμενε με «έχει ήδη πιστωτικό» και το γραμμάτιο με
    # «έχει ήδη ενσωματωθεί» για παραστατικό που δεν υπάρχει πια, και δεν θα μπορούσαν να ξαναγίνουν.
    original_id = existing.get("credit_note_for")
    if original_id:
        try:
            await db.invoices.update_one(
                tenant_query(user, {"_id": ObjectId(original_id), "credit_note_id": invoice_id}),
                {"$unset": {"credit_note_id": ""}},
            )
        except Exception:  # noqa: BLE001 — άκυρο id στο παλιό έγγραφο: δεν εμποδίζει τη διαγραφή
            pass
    await db.grammatia.update_many(tenant_query(user, {"invoice_id": invoice_id}), {"$unset": {"invoice_id": ""}})
    return {"message": "Το πρόχειρο τιμολόγιο διαγράφηκε"}


@router.post("/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    from invoice_services import (
        issue_draft, transmit_to_mydata, email_invoice, InvoiceEmailError, InvoiceWorkflowError,
    )
    firm_id = firm_id_for(user)
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο πρόχειρα τιμολόγια μπορούν να εκδοθούν.")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    try:
        doc = await issue_draft(firm_id, doc, settings)
    except InvoiceWorkflowError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await transmit_to_mydata(firm_id, doc, settings)
    # Ξαναδιαβάζουμε μετά τη διαβίβαση: το PDF που θα σταλεί στον πελάτη πρέπει να φέρει το MARK.
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))

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
    safe_name = doc.get("number") or "invoice"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(f"{safe_name}.pdf")},
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
