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
from mydata_client import (
    mydata_ready, send_invoice, cancel_invoice, MyDataError, mydata_mock_enabled,
    MYDATA_NOT_CONFIGURED_MSG, resolve_invoice_type, withholding_for,
    TYPE_SERVICES_B2B, TYPE_RECEIPT_B2C, TYPE_CREDIT_NOTE,
)
from tax_utils import athens_today
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


async def payable_amount(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> tuple[float, float]:
    """(πληρωτέο, παρακρατούμενος φόρος). Ο πελάτης-επιχείρηση πληρώνει μείον την παρακράτηση."""
    total = float(invoice.get("total", 0))
    if not settings.get("withholding_enabled", False) and invoice.get("withholding_amount") is None:
        return round(total, 2), 0.0
    client = client if client is not None else await load_client(firm_id, invoice)
    invoice_type = resolve_invoice_type(invoice, client or {})
    withheld = withholding_for(invoice, settings, invoice_type, float(invoice.get("subtotal", 0)))
    return round(total - withheld, 2), withheld


async def outstanding_amount(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> tuple[float, float]:
    """(οφειλόμενο, παρακράτηση): το πληρωτέο μείον ό,τι έχει ήδη εξοφληθεί από πελατολογικό λογαριασμό.

    Είναι το ποσό που πρέπει να φτάσει από τράπεζα ή κάρτα για να εξοφληθεί το τιμολόγιο.
    """
    payable, withheld = await payable_amount(firm_id, invoice, settings, client)
    applied = round(float(invoice.get("trust_applied") or 0), 2)
    return round(max(payable - applied, 0.0), 2), withheld


async def effective_invoice(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> dict:
    """Το παραστατικό με την παρακράτηση που ισχύει, έτοιμο για PDF και email.

    Τα εκδοθέντα έχουν την παρακράτηση παγωμένη (`withholding_amount`). Για παλαιότερα, που
    εκδόθηκαν πριν αποθηκεύεται, την υπολογίζουμε όπως θα την υπολόγιζε η διαβίβαση.
    """
    if invoice.get("withholding_amount") is not None:
        return invoice
    _payable, withheld = await payable_amount(firm_id, invoice, settings, client)
    return {**invoice, "withholding_amount": withheld}


def safe_pdf_name(invoice: dict) -> str:
    """Όνομα συνημμένου χωρίς ελληνικούς χαρακτήρες (πολλοί mail clients τους παραμορφώνουν)."""
    name = invoice.get("number") or "invoice"
    for greek, latin in (("ΤΠΥ", "TPY"), ("ΑΠΥ", "APY"), ("ΠΙΣ", "PIS")):
        name = name.replace(greek, latin)
    return name + ".pdf"


# ----------------------------------------------------------- αρίθμηση -----
#
# Κάθε είδος παραστατικού έχει δική του σειρά και δική του αρίθμηση ανά έτος. Ο αριθμός
# δίνεται **μόνο όταν εκδίδεται** το παραστατικό: ένα πρόχειρο δεν είναι φορολογικό
# παραστατικό, και αν έπαιρνε αριθμό στη δημιουργία, κάθε διαγραφή πρόχειρου θα άφηνε
# κενό στη σειρά των εκδοθέντων — ακριβώς αυτό που η αδιάσπαστη αρίθμηση απαγορεύει.

SERIES_PREFIX = {
    TYPE_SERVICES_B2B: "ΤΠΥ",
    TYPE_RECEIPT_B2C: "ΑΠΥ",
    TYPE_CREDIT_NOTE: "ΠΙΣ",
}
DEFAULT_SERIES_PREFIX = "ΤΠΥ"


def series_prefix(invoice_type: str) -> str:
    return SERIES_PREFIX.get(invoice_type, DEFAULT_SERIES_PREFIX)


def _counter_id(firm_id: str, prefix: str, year: int) -> str:
    # Η σειρά ΤΠΥ κρατά το αρχικό όνομα μετρητή ώστε οι υπάρχουσες εγκαταστάσεις να συνεχίσουν
    # από εκεί που έμειναν.
    return f"invoice-{firm_id}-{year}" if prefix == DEFAULT_SERIES_PREFIX else f"invoice-{prefix}-{firm_id}-{year}"


async def next_invoice_number(firm_id: str, prefix: str = DEFAULT_SERIES_PREFIX, year: int | None = None) -> str:
    from numbering import next_in_series
    year = year or athens_today().year
    return await next_in_series(db.invoices, _counter_id(firm_id, prefix, year), firm_id, prefix, year)


async def release_invoice_number(firm_id: str, number: str) -> None:
    """Επιστρέφει αριθμό που δεν χρησιμοποιήθηκε — μόνο αν δεν έχει δοθεί κάποιος μεταγενέστερος."""
    try:
        prefix, year, seq = number.rsplit("-", 2)
        await db.counters.update_one({"_id": _counter_id(firm_id, prefix, int(year)), "seq": int(seq)},
                                     {"$inc": {"seq": -1}})
    except Exception:  # noqa: BLE001 — best effort, ένα κενό είναι προτιμότερο από μια εξαίρεση
        logger.warning("Δεν επιστράφηκε ο αριθμός %s", number)


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
    invoice = await effective_invoice(firm_id, invoice, settings, client)
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
    invoice = await effective_invoice(firm_id, invoice, settings, client)
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


# ------------------------------------------------ εξόφληση από τράπεζα ----

AMOUNT_TOLERANCE = 0.01   # διαφορά μικρότερη από 1 λεπτό θεωρείται ίδιο ποσό


def _eur(v) -> str:
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class SettlementRefused(Exception):
    """Η εξόφληση τιμολογίου από κατάθεση δεν επιτρέπεται (το μήνυμα απευθύνεται στον χρήστη)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


async def _claimed_by_other_deposit(firm_id: str, invoice_id: str, tx_id) -> bool:
    """Έχει ήδη εξοφληθεί αυτό το τιμολόγιο από **άλλη** τραπεζική κίνηση;"""
    other = await db.bank_transactions.find_one(firm_query(firm_id, {
        "matched_invoice_id": invoice_id,
        "match_status": {"$in": ["auto_confirmed", "manual_matched"]},
        "_id": {"$ne": tx_id},
    }))
    return other is not None


async def apply_bank_payment(firm_id: str, tx: dict, invoice_id, *, settings: dict, paid_via: str,
                             status_on_success: str, accept_difference: bool = False,
                             dry_run: bool = False) -> dict:
    """Συνδέει μια κατάθεση με ένα τιμολόγιο και το εξοφλεί — με όλους τους ελέγχους, σε ένα σημείο.

    Την καλούν η αυτόματη συμφωνία, η μαζική και η χειροκίνητη επιβεβαίωση, και το inbox. Κανόνες:

    * η κίνηση πρέπει να είναι είσπραξη και να μην έχει ήδη συμφωνηθεί (μία κατάθεση ↔ ένα τιμολόγιο)·
    * το τιμολόγιο πρέπει να είναι ανοιχτό· αν είναι ήδη εξοφλημένο, η κατάθεση συνδέεται μόνο όταν
      το είχε σημειώσει ο χρήστης με το χέρι και καμία άλλη κίνηση δεν το έχει εξοφλήσει — αλλιώς
      πρόκειται για διπλοπληρωμή ή για πληρωμή από άλλο κανάλι (Stripe, πελατολογικός) και ένας
      άνθρωπος πρέπει να αποφασίσει·
    * το ποσό πρέπει να ισούται με το οφειλόμενο. Διαφορά δέχεται μόνο ρητή απόφαση ανθρώπου
      (`accept_difference`) και καταγράφεται στην κίνηση.

    Επιστρέφει {"status": "paid" | "linked" | "refused", "reason", "message", ...}. Με `dry_run`
    κάνει μόνο τους ελέγχους και επιστρέφει "ok" χωρίς να αλλάξει τίποτα.
    """
    def refuse(reason: str, message: str, **extra) -> dict:
        return {"status": "refused", "reason": reason, "message": message, **extra}

    amount = round(float(tx.get("amount") or 0), 2)
    if amount <= 0:
        return refuse("not_a_deposit", "Η κίνηση δεν είναι είσπραξη (το ποσό δεν είναι θετικό).")
    if tx.get("match_status") in ("auto_confirmed", "manual_matched"):
        return refuse("tx_settled", f"Η κίνηση έχει ήδη συμφωνηθεί με το τιμολόγιο {tx.get('matched_invoice_number') or ''}.".strip())

    try:
        oid = ObjectId(str(invoice_id))
    except Exception:
        return refuse("invoice_missing", "Το τιμολόγιο δεν βρέθηκε.")
    invoice = await db.invoices.find_one(firm_query(firm_id, {"_id": oid}))
    if not invoice:
        return refuse("invoice_missing", "Το τιμολόγιο δεν βρέθηκε.")
    number = invoice.get("number") or "(χωρίς αριθμό)"
    status = invoice.get("status")
    if status not in ("issued", "paid"):
        return refuse("invoice_not_open", f"Το τιμολόγιο {number} δεν είναι ανοιχτό (κατάσταση: {status}) και δεν μπορεί να εξοφληθεί.")

    def already_paid_message() -> str:
        return (f"Το τιμολόγιο {number} έχει ήδη εξοφληθεί. Η κατάθεση {_eur(amount)} μπορεί να είναι διπλοπληρωμή "
                "ή πληρωμή που αφορά άλλο παραστατικό — ελέγξτε την πριν τη συνδέσετε.")

    key = str(oid)
    if status == "paid" and (invoice.get("paid_via") in ("stripe", "trust")
                             or await _claimed_by_other_deposit(firm_id, key, tx["_id"])):
        return refuse("already_paid", already_paid_message())

    due, _withheld = await outstanding_amount(firm_id, invoice, settings)
    difference = round(amount - due, 2)
    exact = abs(difference) < AMOUNT_TOLERANCE
    if not exact and not accept_difference:
        return refuse(
            "amount_mismatch",
            f"Η κατάθεση ({_eur(amount)}) δεν ταιριάζει με το οφειλόμενο του {number} ({_eur(due)}): "
            f"διαφορά {'+' if difference > 0 else '−'}{_eur(abs(difference))}.",
            due=due, difference=difference)

    if dry_run:
        return {"status": "ok", "reason": "", "message": "", "due": due,
                "difference": 0.0 if exact else difference, "invoice_number": invoice.get("number", "")}

    outcome = "linked"
    if status == "issued":
        if await mark_paid(firm_id, oid, paid_via=paid_via):
            outcome = "paid"
        else:
            # Άλλη διεργασία πρόλαβε ανάμεσα στον έλεγχο και την εξόφληση: ξαναελέγχουμε την τρέχουσα εικόνα.
            fresh = await db.invoices.find_one(firm_query(firm_id, {"_id": oid})) or {}
            if (fresh.get("status") != "paid" or fresh.get("paid_via") in ("stripe", "trust")
                    or await _claimed_by_other_deposit(firm_id, key, tx["_id"])):
                return refuse("already_paid", already_paid_message())

    update = {"$set": {
        "matched_invoice_id": key, "matched_invoice_number": invoice.get("number", ""),
        "match_status": status_on_success, "match_ambiguous": False, "confirmed_at": _now(),
        **({"match_confidence": 100} if status_on_success == "manual_matched" else {}),
        **({} if exact else {"amount_difference": difference}),
    }}
    if exact:
        update["$unset"] = {"amount_difference": ""}
    await db.bank_transactions.update_one(firm_query(firm_id, {"_id": tx["_id"]}), update)
    return {"status": outcome, "reason": "", "message": "", "due": due, "difference": 0.0 if exact else difference,
            "invoice_number": invoice.get("number", "")}


# ------------------------------------------------- cancellation / credit ----

class InvoiceWorkflowError(Exception):
    """Μη επιτρεπτή μετάβαση κατάστασης τιμολογίου (μήνυμα προς τον χρήστη)."""


def _series_of(number: str) -> tuple[str, int] | None:
    """('ΤΠΥ', 2026) από το «ΤΠΥ-2026-0007»· None αν ο αριθμός δεν έχει αυτή τη μορφή."""
    try:
        prefix, year, _seq = number.rsplit("-", 2)
        return prefix, int(year)
    except (ValueError, AttributeError):
        return None


async def _previous_in_series(firm_id: str, prefix: str, year: int):
    """(αριθμός, ημερομηνία, κείμενο αριθμού) του τελευταίου **εκδοθέντος** της σειράς, ή None.

    Τα πρόχειρα με παλιό αριθμό δεν μετρούν: δεν είναι εκδοθέντα παραστατικά.
    """
    best = None
    query = firm_query(firm_id, {"number": {"$regex": f"^{prefix}-{year}-"},
                                 "status": {"$in": ["issued", "paid", "cancelled"]}})
    async for d in db.invoices.find(query, {"number": 1, "date": 1}):
        try:
            n = int(d["number"].rsplit("-", 1)[-1])
        except (ValueError, KeyError):
            continue
        if best is None or n > best[0]:
            best = (n, (d.get("date") or "")[:10], d["number"])
    return best


async def issue_draft(firm_id: str, doc: dict, settings: dict) -> dict:
    """Μετατρέπει ένα πρόχειρο σε εκδοθέν παραστατικό και επιστρέφει το ενημερωμένο έγγραφο.

    Εδώ και μόνο εδώ το παραστατικό παίρνει:

    * **αριθμό** από τη σειρά του είδους του (ΤΠΥ / ΑΠΥ / ΠΙΣ) — βλ. σχόλιο στα SERIES_PREFIX·
    * **είδος** (`invoice_type`), παγωμένο ώστε μια μεταγενέστερη αλλαγή στην καρτέλα του
      πελάτη να μη μεταβάλλει το είδος ενός ήδη εκδοθέντος παραστατικού·
    * **παρακράτηση** (`withholding_amount`), παγωμένη με τις ρυθμίσεις της στιγμής εκείνης,
      ώστε PDF, XML προς ΑΑΔΕ, πληρωμή και αντιστοίχιση τράπεζας να βλέπουν το ίδιο ποσό.

    Ελέγχει επίσης την ημερομηνία: όχι μελλοντική, και όχι προγενέστερη του τελευταίου
    εκδοθέντος της ίδιας σειράς — αλλιώς η αρίθμηση παύει να είναι χρονολογική.
    """
    from pymongo import ReturnDocument

    if doc.get("status") != "draft":
        raise InvoiceWorkflowError("Μόνο πρόχειρα τιμολόγια μπορούν να εκδοθούν.")
    client = await load_client(firm_id, doc)
    invoice_type = resolve_invoice_type(doc, client or {})

    try:
        inv_date = _date.fromisoformat((doc.get("date") or "")[:10])
    except ValueError:
        raise InvoiceWorkflowError("Μη έγκυρη ημερομηνία παραστατικού — διορθώστε την πριν την έκδοση.")
    today = athens_today()
    if inv_date > today:
        raise InvoiceWorkflowError(
            f"Η ημερομηνία του παραστατικού ({inv_date:%d/%m/%Y}) είναι μελλοντική. Το παραστατικό φέρει "
            "την ημερομηνία έκδοσής του· διορθώστε την ή εκδώστε το όταν έρθει η ημέρα."
        )

    # Πρόχειρο από παλαιότερη έκδοση της εφαρμογής: κρατά τον αριθμό που ήδη έχει.
    number = (doc.get("number") or "").strip()
    series = _series_of(number) if number else (series_prefix(invoice_type), inv_date.year)
    if series:
        previous = await _previous_in_series(firm_id, *series)
        if previous:
            try:
                prev_date = _date.fromisoformat(previous[1])
            except ValueError:
                prev_date = None
            # Μια μελλοντική ημερομηνία σε παλιό παραστατικό δεν πρέπει να μπλοκάρει για πάντα τη σειρά.
            if prev_date and inv_date < min(prev_date, today):
                raise InvoiceWorkflowError(
                    f"Η ημερομηνία {inv_date:%d/%m/%Y} είναι προγενέστερη του τελευταίου εκδοθέντος της σειράς "
                    f"({previous[2]} — {prev_date:%d/%m/%Y}). Η αρίθμηση πρέπει να ακολουθεί τη χρονολογική "
                    f"σειρά· αλλάξτε την ημερομηνία του πρόχειρου σε {min(prev_date, today):%d/%m/%Y} ή αργότερα."
                )

    allocated = False
    if not number:
        number = await next_invoice_number(firm_id, series[0], series[1])
        allocated = True

    withheld = withholding_for(doc, settings, invoice_type, float(doc.get("subtotal") or 0))
    claimed = await db.invoices.find_one_and_update(
        firm_query(firm_id, {"_id": doc["_id"], "status": "draft"}),
        {"$set": {"status": "issued", "issued_at": _now(), "number": number,
                  "invoice_type": invoice_type, "withholding_amount": withheld}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:          # άλλο αίτημα πρόλαβε την έκδοση (π.χ. διπλό κλικ)
        if allocated:
            await release_invoice_number(firm_id, number)
        raise InvoiceWorkflowError("Το τιμολόγιο έχει ήδη εκδοθεί.")
    return claimed


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

    if invoice.get("mydata_status") != "synced" or not invoice.get("mydata_mark"):
        raise InvoiceWorkflowError(
            "Το πιστωτικό απαιτεί διαβιβασμένο αρχικό παραστατικό (MARK). "
            "Περιμένετε να ολοκληρωθεί η διαβίβαση στο myDATA."
        )
    if invoice.get("credit_note_id"):
        raise InvoiceWorkflowError("Έχει ήδη εκδοθεί πιστωτικό για αυτό το τιμολόγιο.")
    if invoice.get("credit_note_for") or invoice.get("invoice_type") == TYPE_CREDIT_NOTE:
        raise InvoiceWorkflowError("Πιστωτικό τιμολόγιο δεν πιστώνεται· εκδώστε νέο τιμολόγιο αν χρειάζεται.")
    if invoice.get("status") == "cancelled":
        raise InvoiceWorkflowError("Το τιμολόγιο είναι ακυρωμένο — δεν χρειάζεται πιστωτικό.")

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
        # Δεν πιστώνεται περισσότερο από όσο τιμολογήθηκε (καθαρή αξία).
        original_net = round(abs(calculate_invoice_totals(original_lines)["subtotal"]), 2)
        if round(abs(float(amount)), 2) > original_net:
            raise InvoiceWorkflowError(
                f"Το ποσό της πίστωσης ({abs(float(amount)):.2f} €) υπερβαίνει την καθαρή αξία του "
                f"αρχικού τιμολογίου ({original_net:.2f} €)."
            )
        rate = int(original_lines[0].get("vat_rate", 24)) if original_lines else 24
        lines = [InvoiceLineItem(description=f"Μερική πίστωση τιμολογίου {invoice.get('number')}",
                                 quantity=1, unit_price=-abs(float(amount)), vat_rate=rate)]
    if not lines:
        raise InvoiceWorkflowError("Το αρχικό τιμολόγιο δεν έχει γραμμές για πίστωση.")

    credit = Invoice(
        firm_id=firm_id,
        client_id=invoice.get("client_id", ""),
        client_name=invoice.get("client_name", ""),
        date=athens_today().isoformat(),
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
    # Χωρίς αριθμό μέχρι την έκδοση (σειρά ΠΙΣ) — ένα πρόχειρο πιστωτικό που θα διαγραφεί
    # δεν πρέπει να αφήνει κενό στη σειρά.
    result = await db.invoices.insert_one(credit.to_mongo())
    await db.invoices.update_one(
        firm_query(firm_id, {"_id": invoice["_id"]}),
        {"$set": {"credit_note_id": str(result.inserted_id)}},
    )
    return await db.invoices.find_one(firm_query(firm_id, {"_id": result.inserted_id}))
