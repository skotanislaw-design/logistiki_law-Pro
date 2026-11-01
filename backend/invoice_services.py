"""Reusable invoice workflows shared by the HTTP routes and the Autopilot engine.

Every function here works on raw Mongo documents and never depends on a request,
so the same code path runs whether a human clicked a button or the scheduler
decided it was time.
"""
import os
import logging
from datetime import datetime, timedelta, timezone, date as _date

from bson import ObjectId

from db import db
from mydata_client import (
    mydata_ready, send_invoice, cancel_invoice, MyDataError, mydata_mock_enabled,
    MYDATA_NOT_CONFIGURED_MSG, resolve_invoice_type, withholding_for,
)
from email_utils import (
    smtp_ready, send_email_with_pdf, invoice_email_html, invoice_email_text,
    reminder_email_html, reminder_email_text,
)

logger = logging.getLogger(__name__)


async def _event(firm_id: str, invoice_id, action: str, details: str = "") -> None:
    """Γεγονός στο χρονολόγιο του τιμολογίου (audit_logs, εκτελεστής «system»)."""
    from audit import record
    await record(action=action, firm_id=firm_id, entity="invoices", entity_id=str(invoice_id),
                 actor_type="system", details=details)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def firm_query(firm_id: str, extra: dict | None = None) -> dict:
    return {**(extra or {}), "firm_id": firm_id}


async def load_client(firm_id: str, invoice: dict) -> dict | None:
    if not invoice.get("client_id"):
        return None
    try:
        return await db.clients.find_one(firm_query(firm_id, {"_id": ObjectId(invoice["client_id"])}))
    except Exception:
        return None


async def payable_amount(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> tuple[float, float]:
    """(πληρωτέο, παρακρατούμενος φόρος). Ο πελάτης-επιχείρηση πληρώνει μείον την παρακράτηση."""
    total = float(invoice.get("total", 0))
    if not settings.get("withholding_enabled", False) and invoice.get("withholding_amount") is None:
        return round(total, 2), 0.0
    client = client if client is not None else await load_client(firm_id, invoice)
    invoice_type = resolve_invoice_type(invoice, client or {})
    withheld = withholding_for(invoice, settings, invoice_type, float(invoice.get("subtotal", 0)))
    return round(total - withheld, 2), withheld


def safe_pdf_name(invoice: dict) -> str:
    from http_utils import ascii_filename
    return ascii_filename(invoice.get("number") or "invoice") + ".pdf"


# ---------------------------------------------------------------- myDATA ----

MYDATA_CLAIM_TTL_MIN = 15
_SENDABLE = ("not_sent", "pending", "failed")


async def transmit_to_mydata(firm_id: str, invoice: dict, settings: dict, force: bool = False) -> dict:
    """Send one invoice to AADE. Returns the ``$set`` update applied to the invoice.

    * live credentials  → real transmission (synced / failed)
    * ``MYDATA_MOCK=1`` → simulated MARK (explicit demo mode)
    * otherwise         → ``pending`` so Autopilot retries once credentials exist

    Ένα παραστατικό διαβιβάζεται το πολύ μία φορά τη φορά: πριν την αποστολή γίνεται
    atomic claim, ώστε ταυτόχρονες κλήσεις (χειροκίνητο κουμπί + Autopilot) να μην
    στείλουν διπλό παραστατικό στην ΑΑΔΕ. Αν το σύστημα πέσει μετά την αποστολή και
    πριν την εγγραφή του MARK, το claim μένει «ορφανό» και το `sweep_stale_mydata_claims`
    το στέλνει για έλεγχο — δεν ξαναστέλνεται τυφλά. ``force`` επιτρέπεται μόνο για
    παραστατικά σε κατάσταση ``needs_review`` και μόνο από ρητή ενέργεια χρήστη.
    """
    import uuid
    now = _now()
    allowed = list(_SENDABLE) + (["needs_review"] if force else [])
    token = uuid.uuid4().hex
    claimed = await db.invoices.update_one(
        firm_query(firm_id, {"_id": invoice["_id"], "mydata_claim_token": {"$in": [None, ""]},
                             "$or": [{"mydata_status": {"$in": allowed}}, {"mydata_status": {"$exists": False}}]}),
        {"$set": {"mydata_claim_token": token, "mydata_claim_at": now}},
    )
    if not getattr(claimed, "modified_count", 0):
        current = await db.invoices.find_one(firm_query(firm_id, {"_id": invoice["_id"]})) or {}
        state = current.get("mydata_status") or "not_sent"
        return {"mydata_status": state, "skipped": True,
                "in_progress": bool(current.get("mydata_claim_token"))}

    release = {"mydata_claim_token": None, "mydata_claim_at": None}
    inc = None
    if mydata_ready(settings):
        client = await load_client(firm_id, invoice)
        try:
            result = await send_invoice(settings, invoice, client or {})
            update = {"mydata_status": "synced", "mydata_mark": result["mark"], "mydata_uid": result["uid"],
                      "mydata_error": None, "mydata_synced_at": now}
        except MyDataError as e:
            update = {"mydata_status": "failed", "mydata_error": str(e), "mydata_last_attempt_at": now}
            inc = {"mydata_attempts": 1}
    elif mydata_mock_enabled():
        update = {"mydata_status": "synced", "mydata_mark": f"MOCK-{uuid.uuid4().hex[:16].upper()}",
                  "mydata_uid": f"UID-{uuid.uuid4().hex[:12].upper()}", "mydata_error": None, "mydata_synced_at": now}
    else:
        update = {"mydata_status": "pending", "mydata_error": MYDATA_NOT_CONFIGURED_MSG}
    ops = {"$set": {**update, **release}}
    if inc:
        ops["$inc"] = inc
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"], "mydata_claim_token": token}), ops)
    if update["mydata_status"] != "pending":
        detail = update.get("mydata_mark") or update.get("mydata_error") or ""
        await _event(firm_id, invoice["_id"], f"invoice:mydata_{update['mydata_status']}", str(detail))
    return update


async def sweep_stale_mydata_claims(firm_id: str, now: datetime | None = None) -> list:
    """Παραστατικά με claim που δεν ολοκληρώθηκε (κρασάρισμα μεταξύ αποστολής και εγγραφής).

    Άγνωστο αν η ΑΑΔΕ το παρέλαβε· γι' αυτό περνούν σε ``needs_review`` και όχι σε αυτόματη
    επανάληψη, που θα μπορούσε να δημιουργήσει διπλό παραστατικό.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=MYDATA_CLAIM_TTL_MIN)).isoformat()
    stuck = []
    async for inv in db.invoices.find(firm_query(firm_id, {"mydata_claim_token": {"$nin": [None, ""]},
                                                           "mydata_claim_at": {"$lt": cutoff}})):
        res = await db.invoices.update_one(
            firm_query(firm_id, {"_id": inv["_id"], "mydata_claim_token": inv["mydata_claim_token"]}),
            {"$set": {"mydata_status": "needs_review", "mydata_claim_token": None, "mydata_claim_at": None,
                      "mydata_error": "Η διαβίβαση διακόπηκε πριν επιβεβαιωθεί. Ελέγξτε στην ΑΑΔΕ αν το παραστατικό έχει καταχωρηθεί."}},
        )
        if getattr(res, "modified_count", 0):
            stuck.append(inv)
            await _event(firm_id, inv["_id"], "invoice:mydata_needs_review", "Διακοπή πριν την επιβεβαίωση")
    return stuck


# ------------------------------------------------------------------ email ----

class InvoiceEmailError(Exception):
    pass


async def email_invoice(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> dict:
    """Send the invoice PDF to the client. Raises InvoiceEmailError with a Greek message."""
    from pdf_utils import build_invoice_pdf
    client = client or await load_client(firm_id, invoice)
    if not client or not client.get("email"):
        raise InvoiceEmailError("Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    if not smtp_ready(settings):
        raise InvoiceEmailError("Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")
    pdf_bytes = build_invoice_pdf(invoice, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Τιμολόγιο {invoice.get('number')} — {company}"
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            invoice_email_text(invoice, settings), invoice_email_html(invoice, settings),
            pdf_bytes=pdf_bytes, pdf_filename=safe_pdf_name(invoice),
        )
    except Exception as e:  # SMTPAuthenticationError, network, …
        raise InvoiceEmailError(f"Αποτυχία αποστολής email: {e}")
    update = {"email_sent_at": _now(), "email_sent_to": client["email"]}
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"]}), {"$set": update})
    return update


async def build_payment_link(invoice: dict, settings: dict) -> str:
    """Create a Stripe Checkout link for the invoice, or '' when Stripe is not configured."""
    try:
        import stripe as _stripe
        api_key = os.environ.get("STRIPE_SECRET_KEY") or ""
        if not api_key or api_key.endswith("_emergent"):
            return ""
        _stripe.api_key = api_key
        total = float(invoice.get("total", 0))
        if settings.get("withholding_enabled", False) or invoice.get("withholding_amount") is not None:
            total, _ = await payable_amount(invoice.get("firm_id", ""), invoice, settings)
        if total <= 0:
            return ""
        origin = settings.get("public_url") or os.environ.get("PUBLIC_URL") or os.environ.get("PREVIEW_ENDPOINT", "") or "https://example.com"
        invoice_id = str(invoice["_id"])
        session = _stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {
                        "name": f"Τιμολόγιο {invoice.get('number', '')}",
                        "description": f"Νομικές υπηρεσίες προς {invoice.get('client_name', '')}",
                    },
                    "unit_amount": int(round(total * 100)),
                },
                "quantity": 1,
            }],
            success_url=origin + "/payment/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=origin + "/payment/cancel",
            metadata={"invoice_id": invoice_id, "invoice_number": invoice.get("number", "")},
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 60 * 60 * 24 * 7,
        )
        from models import PaymentTransaction
        tx = PaymentTransaction(
            firm_id=invoice.get("firm_id", "legacy-default-firm"),
            session_id=session.id, invoice_id=invoice_id, invoice_number=invoice.get("number", ""),
            client_id=invoice.get("client_id", ""), client_name=invoice.get("client_name", ""),
            amount=total, currency="eur",
        )
        await db.payment_transactions.insert_one(tx.to_mongo())
        return session.url
    except Exception as e:
        logger.warning("Stripe payment link failed: %s", e)
        return ""


def days_since_invoice(invoice: dict, today: _date | None = None) -> int:
    today = today or _date.today()
    try:
        issued_date = _date.fromisoformat((invoice.get("date") or "")[:10])
        return max(0, (today - issued_date).days)
    except Exception:
        return 0


async def send_reminder(firm_id: str, invoice: dict, settings: dict, client: dict | None = None,
                        with_payment_link: bool = True) -> dict:
    """Send a payment reminder for an issued, unpaid invoice."""
    from pdf_utils import build_invoice_pdf
    if invoice.get("status") == "paid":
        raise InvoiceEmailError("Το τιμολόγιο είναι ήδη εξοφλημένο — δεν χρειάζεται υπενθύμιση.")
    if invoice.get("status") != "issued":
        raise InvoiceEmailError("Μόνο εκδοθέντα τιμολόγια μπορούν να λάβουν υπενθύμιση. Εκδώστε πρώτα το τιμολόγιο.")
    client = client or await load_client(firm_id, invoice)
    if not client or not client.get("email"):
        raise InvoiceEmailError("Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    if not smtp_ready(settings):
        raise InvoiceEmailError("Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")

    days_overdue = days_since_invoice(invoice)
    reminder_count = int(invoice.get("reminder_count") or 0)
    payment_link = await build_payment_link(invoice, settings) if with_payment_link else ""
    pdf_bytes = build_invoice_pdf(invoice, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Υπενθύμιση εξόφλησης — Τιμολόγιο {invoice.get('number')} — {company}"
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            reminder_email_text(invoice, settings, days_overdue, reminder_count, payment_link),
            reminder_email_html(invoice, settings, days_overdue, reminder_count, payment_link),
            pdf_bytes=pdf_bytes, pdf_filename=safe_pdf_name(invoice),
        )
    except Exception as e:
        raise InvoiceEmailError(f"Αποτυχία αποστολής υπενθύμισης: {e}")
    await db.invoices.update_one(
        firm_query(firm_id, {"_id": invoice["_id"]}),
        {"$set": {"reminder_sent_at": _now(), "email_sent_to": client["email"]}, "$inc": {"reminder_count": 1}},
    )
    return {"reminder_sent_at": _now(), "payment_link": payment_link}


async def apply_payment(firm_id: str, invoice_id, paid_via: str = "manual") -> str:
    """Idempotent εξόφληση. Επιστρέφει ``paid`` | ``already_paid`` | ``not_payable`` | ``not_found``.

    ``not_payable`` = πρόχειρο ή ακυρωμένο τιμολόγιο: ένα ποσό που εισπράχθηκε δεν πρέπει
    ποτέ να «εξοφλεί» σιωπηλά παραστατικό που δεν ισχύει — χρειάζεται ανθρώπινη απόφαση.
    """
    try:
        oid = invoice_id if isinstance(invoice_id, ObjectId) else ObjectId(invoice_id)
    except Exception:
        return "not_found"
    result = await db.invoices.update_one(
        firm_query(firm_id, {"_id": oid, "status": "issued"}),
        {"$set": {"status": "paid", "paid_at": _now(), "paid_via": paid_via}},
    )
    if result.modified_count > 0:
        await _event(firm_id, oid, "invoice:paid", f"via {paid_via}")
        await db.notifications.update_many(
            firm_query(firm_id, {"dedupe_key": f"payment_at_risk:{oid}", "resolved_at": None}),
            {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
        return "paid"
    inv = await db.invoices.find_one(firm_query(firm_id, {"_id": oid}))
    if not inv:
        return "not_found"
    return "already_paid" if inv.get("status") == "paid" else "not_payable"


async def mark_paid(firm_id: str, invoice_id, paid_via: str = "manual") -> bool:
    """Idempotent: returns True only if the invoice transitioned issued → paid."""
    return await apply_payment(firm_id, invoice_id, paid_via) == "paid"


# ------------------------------------------------- cancellation / credit ----

class InvoiceWorkflowError(Exception):
    """Μη επιτρεπτή μετάβαση κατάστασης τιμολογίου (μήνυμα προς τον χρήστη)."""


async def cancel_in_mydata(firm_id: str, invoice: dict, settings: dict) -> dict:
    """Ακυρώνει το παραστατικό στην ΑΑΔΕ και σημειώνει το τιμολόγιο ως ακυρωμένο.

    Η ακύρωση επιτρέπεται μόνο για διαβιβασμένα (synced) και μη εξοφλημένα
    παραστατικά· για εξοφλημένα ο σωστός δρόμος είναι πιστωτικό τιμολόγιο.
    """
    if invoice.get("status") == "paid":
        raise InvoiceWorkflowError(
            "Το τιμολόγιο είναι εξοφλημένο — δεν ακυρώνεται. Εκδώστε πιστωτικό τιμολόγιο."
        )
    if invoice.get("status") == "cancelled":
        raise InvoiceWorkflowError("Το τιμολόγιο είναι ήδη ακυρωμένο.")

    update = {"status": "cancelled", "cancelled_at": _now()}
    if invoice.get("mydata_status") == "synced" and invoice.get("mydata_mark"):
        if not mydata_ready(settings):
            raise InvoiceWorkflowError(
                "Το παραστατικό έχει διαβιβαστεί στο myDATA αλλά λείπουν τα credentials ΑΑΔΕ "
                "για την ακύρωση. Συμπληρώστε τα στις Ρυθμίσεις."
            )
        result = await cancel_invoice(settings, invoice["mydata_mark"])
        update.update({
            "mydata_status": "cancelled",
            "mydata_cancellation_mark": result["cancellation_mark"],
            "mydata_cancelled_at": _now(),
        })
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"]}), {"$set": update})
    return update


async def build_credit_note(firm_id: str, invoice: dict, user_email: str,
                            reason: str = "", amount: float | None = None) -> dict:
    """Δημιουργεί πιστωτικό τιμολόγιο συσχετιζόμενο με το αρχικό.

    Πλήρες πιστωτικό από προεπιλογή· με ``amount`` γίνεται μερικό (η καθαρή αξία
    κατανέμεται στο πρώτο συντελεστή ΦΠΑ του αρχικού).
    """
    from models import Invoice, InvoiceLineItem
    from tax_utils import calculate_invoice_totals
    from routes_invoices import _next_invoice_number

    if invoice.get("mydata_status") != "synced" or not invoice.get("mydata_mark"):
        raise InvoiceWorkflowError(
            "Το πιστωτικό απαιτεί διαβιβασμένο αρχικό παραστατικό (MARK). "
            "Περιμένετε να ολοκληρωθεί η διαβίβαση στο myDATA."
        )
    if invoice.get("credit_note_id"):
        raise InvoiceWorkflowError("Έχει ήδη εκδοθεί πιστωτικό για αυτό το τιμολόγιο.")

    # Τα ποσά αποθηκεύονται **αρνητικά** σκόπιμα: έτσι το πιστωτικό μειώνει έσοδα, ΦΠΑ
    # και τζίρο σε κάθε άθροισμα, χωρίς καμία συγκεντρωτική να χρειάζεται να το εξαιρέσει.
    # Προς την ΑΑΔΕ διαβιβάζονται θετικά — βλ. CREDIT_TYPES στο mydata_client.
    original_lines = invoice.get("line_items") or []
    if amount is None:
        lines = [
            InvoiceLineItem(
                description=f"Πίστωση: {ln.get('description', '')}"[:200],
                quantity=float(ln.get("quantity", 1)),
                unit_price=-abs(float(ln.get("unit_price", 0))),
                vat_rate=int(ln.get("vat_rate", 24)),
            )
            for ln in original_lines
        ]
    else:
        rate = int(original_lines[0].get("vat_rate", 24)) if original_lines else 24
        lines = [InvoiceLineItem(description=f"Μερική πίστωση τιμολογίου {invoice.get('number')}",
                                 quantity=1, unit_price=-abs(float(amount)), vat_rate=rate)]
    if not lines:
        raise InvoiceWorkflowError("Το αρχικό τιμολόγιο δεν έχει γραμμές για πίστωση.")

    credit = Invoice(
        firm_id=firm_id,
        client_id=invoice.get("client_id", ""),
        client_name=invoice.get("client_name", ""),
        date=_date.today().isoformat(),
        line_items=lines,
        invoice_type="5.1",
        credit_note_for=str(invoice["_id"]),
        correlated_mark=invoice.get("mydata_mark"),
        notes=reason or f"Πιστωτικό για το {invoice.get('number')}",
        payment_method=invoice.get("payment_method", ""),
        created_by=user_email,
    )
    totals = calculate_invoice_totals(credit.line_items)
    credit.subtotal, credit.vat_amount, credit.total = totals["subtotal"], totals["vat_amount"], totals["total"]
    credit.vat_breakdown = totals["vat_breakdown"]
    credit.number = await _next_invoice_number(firm_id, prefix="ΠΙΣ")
    result = await db.invoices.insert_one(credit.to_mongo())
    await db.invoices.update_one(
        firm_query(firm_id, {"_id": invoice["_id"]}),
        {"$set": {"credit_note_id": str(result.inserted_id)}},
    )
    return await db.invoices.find_one(firm_query(firm_id, {"_id": result.inserted_id}))
