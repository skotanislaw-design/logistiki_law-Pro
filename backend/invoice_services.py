"""Reusable invoice workflows shared by the HTTP routes and the Autopilot engine.

Every function here works on raw Mongo documents and never depends on a request,
so the same code path runs whether a human clicked a button or the scheduler
decided it was time.
"""
import os
import logging
from datetime import datetime, timezone, date as _date

from bson import ObjectId

from db import db
from mydata_client import mydata_ready, send_invoice, MyDataError, mydata_mock_enabled, MYDATA_NOT_CONFIGURED_MSG
from email_utils import (
    smtp_ready, send_email_with_pdf, invoice_email_html, invoice_email_text,
    reminder_email_html, reminder_email_text,
)

logger = logging.getLogger(__name__)


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


def safe_pdf_name(invoice: dict) -> str:
    return (invoice.get("number") or "invoice").replace("ΤΠΥ", "TPY") + ".pdf"


# ---------------------------------------------------------------- myDATA ----

async def transmit_to_mydata(firm_id: str, invoice: dict, settings: dict) -> dict:
    """Send one invoice to AADE. Returns the ``$set`` update applied to the invoice.

    * live credentials  → real transmission (synced / failed)
    * ``MYDATA_MOCK=1`` → simulated MARK (explicit demo mode)
    * otherwise         → ``pending`` so Autopilot retries once credentials exist
    """
    import uuid
    now = _now()
    if mydata_ready(settings):
        client = await load_client(firm_id, invoice)
        try:
            result = await send_invoice(settings, invoice, client or {})
            update = {"mydata_status": "synced", "mydata_mark": result["mark"], "mydata_uid": result["uid"],
                      "mydata_error": None, "mydata_synced_at": now}
            inc = None
        except MyDataError as e:
            update = {"mydata_status": "failed", "mydata_error": str(e), "mydata_last_attempt_at": now}
            inc = {"mydata_attempts": 1}
    elif mydata_mock_enabled():
        update = {"mydata_status": "synced", "mydata_mark": f"MOCK-{uuid.uuid4().hex[:16].upper()}",
                  "mydata_uid": f"UID-{uuid.uuid4().hex[:12].upper()}", "mydata_error": None, "mydata_synced_at": now}
        inc = None
    else:
        update = {"mydata_status": "pending", "mydata_error": MYDATA_NOT_CONFIGURED_MSG}
        inc = None
    ops = {"$set": update}
    if inc:
        ops["$inc"] = inc
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"]}), ops)
    return update


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


async def mark_paid(firm_id: str, invoice_id, paid_via: str = "manual") -> bool:
    """Idempotent: returns True only if the invoice transitioned issued → paid."""
    oid = invoice_id if isinstance(invoice_id, ObjectId) else ObjectId(invoice_id)
    result = await db.invoices.update_one(
        firm_query(firm_id, {"_id": oid, "status": "issued"}),
        {"$set": {"status": "paid", "paid_at": _now(), "paid_via": paid_via}},
    )
    return result.modified_count > 0
