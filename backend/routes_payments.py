"""Stripe Payments — Checkout Session για online εξόφληση τιμολογίων."""
import os
import logging
import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import PaymentTransaction
from auth import get_current_user
from tenant import tenant_query, firm_id_for

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or "sk_test_emergent"
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])
public_router = APIRouter(tags=["payments-public"])  # για webhook & public status


class CheckoutRequest(BaseModel):
    invoice_id: str
    origin_url: str
    client_token: str = ""  # για client portal — αν υπάρχει token, δεν χρειάζεται auth


def _stripe_ready() -> bool:
    return bool(stripe.api_key and not stripe.api_key.endswith("_emergent"))


@router.get("/config")
async def payment_config(user: dict = Depends(get_current_user)):
    return {
        "enabled": _stripe_ready(),
        "publishable_key": STRIPE_PUBLISHABLE_KEY if _stripe_ready() else "",
        "mode": os.environ.get("STRIPE_MODE", "test"),
    }


async def _validate_invoice_and_get_amount(invoice_id: str, firm_id: str | None = None) -> tuple[dict, float]:
    try:
        query = {"_id": ObjectId(invoice_id)}
        if firm_id:
            query["firm_id"] = firm_id
        inv = await db.invoices.find_one(query)
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id τιμολογίου")
    if not inv:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if inv.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Το τιμολόγιο είναι ήδη εξοφλημένο")
    if inv.get("status") != "issued":
        # Πρόχειρο ή ακυρωμένο παραστατικό δεν εισπράττεται.
        raise HTTPException(status_code=400, detail="Πληρώνονται μόνο εκδοθέντα, ανεξόφλητα τιμολόγια.")
    # Ο πελάτης καταβάλλει το οφειλόμενο: το πληρωτέο (μετά την παρακράτηση που κρατά ο ίδιος),
    # μείον ό,τι έχει ήδη εξοφληθεί από τον πελατολογικό του λογαριασμό. Όχι το ακαθάριστο.
    from invoice_services import outstanding_amount
    settings = await db.settings.find_one({"firm_id": inv.get("firm_id")}) or {}
    total, _withheld = await outstanding_amount(inv.get("firm_id", ""), inv, settings)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Μη έγκυρο ποσό τιμολογίου")
    return inv, total


async def _create_session(inv: dict, amount: float, origin_url: str) -> dict:
    if not _stripe_ready():
        raise HTTPException(status_code=400, detail="Οι πληρωμές δεν είναι ενεργοποιημένες. Ρυθμίστε τα κλειδιά Stripe στις Ρυθμίσεις ή επικοινωνήστε με τη διαχείριση.")
    invoice_id = str(inv["_id"])
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {
                    "name": f"Τιμολόγιο {inv.get('number', '')}",
                    "description": f"Νομικές υπηρεσίες προς {inv.get('client_name', '')}",
                },
                "unit_amount": int(round(amount * 100)),
            },
            "quantity": 1,
        }],
        success_url=f"{origin_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{origin_url}/payment/cancel?invoice={invoice_id}",
        metadata={
            "invoice_id": invoice_id,
            "invoice_number": inv.get("number", ""),
            "client_id": inv.get("client_id", ""),
        },
        payment_intent_data={
            "metadata": {"invoice_id": invoice_id, "invoice_number": inv.get("number", "")},
        },
        billing_address_collection="auto",
    )
    tx = PaymentTransaction(
        session_id=session.id,
        invoice_id=invoice_id,
        invoice_number=inv.get("number", ""),
        client_id=inv.get("client_id", ""),
        client_name=inv.get("client_name", ""),
        amount=amount,
        currency="eur",
        status="initiated",
        payment_status="pending",
        firm_id=inv.get("firm_id", "legacy-default-firm"),
    )
    await db.payment_transactions.insert_one(tx.to_mongo())
    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest, user: dict = Depends(get_current_user)):
    inv, amount = await _validate_invoice_and_get_amount(req.invoice_id, firm_id_for(user))
    return await _create_session(inv, amount, req.origin_url)


@public_router.post("/api/payments/portal-checkout")
async def portal_checkout(req: CheckoutRequest, request: Request):
    """Public endpoint για client portal — απαιτεί έγκυρο portal token."""
    if not req.client_token:
        raise HTTPException(status_code=401, detail="Απαιτείται token πελάτη")
    token = await db.client_portal_tokens.find_one({"token": req.client_token})
    if not token:
        raise HTTPException(status_code=401, detail="Μη έγκυρο ή ληγμένο token")
    if token.get("expires_at", "") < datetime.now(timezone.utc).isoformat():
        raise HTTPException(status_code=401, detail="Το token έχει λήξει")
    inv, amount = await _validate_invoice_and_get_amount(req.invoice_id, token.get("firm_id"))
    if inv.get("client_id") != token["client_id"]:
        raise HTTPException(status_code=403, detail="Δεν επιτρέπεται η πληρωμή τιμολογίου άλλου πελάτη")
    return await _create_session(inv, amount, req.origin_url)


@public_router.get("/api/payments/status/{session_id}")
async def get_payment_status(session_id: str):
    """Public endpoint — επιστρέφει μόνο status πληρωμής."""
    record = await db.payment_transactions.find_one({"session_id": session_id})
    if not record:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε συναλλαγή")
    # Webhook fallback
    if record.get("payment_status") != "paid" and _stripe_ready():
        try:
            s = stripe.checkout.Session.retrieve(session_id)
            if s.payment_status == "paid":
                await _mark_paid(session_id, s.payment_intent if hasattr(s, "payment_intent") else None)
                record = await db.payment_transactions.find_one({"session_id": session_id})
        except stripe.error.StripeError:
            pass
    return {
        "session_id": record["session_id"],
        "status": record["status"],
        "payment_status": record["payment_status"],
        "invoice_number": record.get("invoice_number", ""),
        "amount": record.get("amount", 0),
    }


async def _mark_paid(session_id: str, payment_intent_id: str = None):
    """Idempotent — καλείται από webhook & polling fallback."""
    # Το session_id είναι μοναδικό ανά συναλλαγή· το firm_id προκύπτει από την ίδια εγγραφή,
    # οπότε δεν προσθέτει φιλτράρισμα — μόνο ένα περιττό round-trip.
    result = await db.payment_transactions.update_one(
        {"session_id": session_id, "payment_status": {"$ne": "paid"}},
        {"$set": {
            "status": "completed",
            "payment_status": "paid",
            "stripe_payment_intent_id": payment_intent_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    if result.modified_count > 0:
        tx = await db.payment_transactions.find_one({"session_id": session_id})
        if tx and tx.get("invoice_id"):
            await _apply_card_payment(tx)


async def _apply_card_payment(tx: dict) -> None:
    """Εξοφλεί το τιμολόγιο της πληρωμής — μόνο αν είναι ακόμη ανοιχτό και το ποσό ταιριάζει.

    Η πληρωμή έχει ήδη γίνει όταν φτάνουμε εδώ· το ζήτημα είναι να μην «χαθούν» τα χρήματα
    σιωπηλά. Σε κάθε περίπτωση που η εφαρμογή δεν μπορεί να κάνει το αυτονόητο (να κλείσει το
    τιμολόγιο ακριβώς όσο εισπράχθηκε) ειδοποιεί το γραφείο, με απαίτηση ενέργειας:

    * το τιμολόγιο δεν είναι πια ανοιχτό (ακυρώθηκε, πιστώθηκε, εξοφλήθηκε αλλιώς) — δεν το
      ξαναανοίγουμε, παλιότερα η πληρωμή το έκανε «εξοφλημένο» ακόμη κι ακυρωμένο·
    * η πληρωμή είναι **μικρότερη** από το οφειλόμενο — δεν είναι εξόφληση, το τιμολόγιο μένει ανοιχτό·
    * η πληρωμή είναι **μεγαλύτερη** από το οφειλόμενο (π.χ. ο πελατολογικός λογαριασμός εξόφλησε
      εν μέρει αφού άνοιξε η συνεδρία) — το τιμολόγιο εξοφλείται και το επιπλέον ποσό
      πρέπει να επιστραφεί·
    * οποιοδήποτε σφάλμα κατά την εφαρμογή — δεν καταπίνεται.
    """
    from invoice_services import mark_paid, outstanding_amount, to_cents
    from automation import notify, _fmt
    firm_id = tx.get("firm_id", "legacy-default-firm")
    invoice_id = tx.get("invoice_id")
    session_id = tx.get("session_id")
    paid = round(float(tx.get("amount") or 0), 2)

    async def alert(type_: str, title: str, message: str) -> None:
        try:
            await notify(firm_id, type_, "critical", title, message, entity="invoice", entity_id=invoice_id,
                         requires_action=True, dedupe_key=f"{type_}:{session_id}")
        except Exception:  # noqa: BLE001 — η ειδοποίηση δεν πρέπει να κρύψει το αρχικό πρόβλημα
            logger.exception("Δεν καταχωρήθηκε ειδοποίηση πληρωμής κάρτας (συνεδρία %s)", session_id)

    try:
        invoice = await db.invoices.find_one({"_id": ObjectId(invoice_id), "firm_id": firm_id}) or {}
        number = invoice.get("number") or "τιμολόγιο"
        if invoice.get("status") != "issued":
            await alert(
                "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} για τιμολόγιο που δεν ήταν ανοιχτό",
                f"Το {number} είχε κατάσταση «{invoice.get('status') or 'άγνωστη'}» όταν ολοκληρώθηκε η πληρωμή "
                f"(συνεδρία {session_id}). Τα χρήματα έχουν εισπραχθεί: ελέγξτε αν χρειάζεται επιστροφή ή σύνδεση "
                "με άλλο παραστατικό.")
            return
        settings = await db.settings.find_one({"firm_id": firm_id}) or {}
        due, _withheld = await outstanding_amount(firm_id, invoice, settings)
        difference = round(paid - due, 2)
        if to_cents(paid) < to_cents(due):
            await alert(
                "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} μικρότερη από το οφειλόμενο",
                f"Το {number} οφείλει {_fmt(due)}, ο πελάτης πλήρωσε {_fmt(paid)} (συνεδρία {session_id}). "
                "Το τιμολόγιο ΔΕΝ εξοφλήθηκε και μένει ανοιχτό για το υπόλοιπο· συμφωνήστε την πληρωμή χειροκίνητα "
                "όταν διευθετηθεί.")
            return
        if not await mark_paid(firm_id, invoice["_id"], paid_via="stripe"):
            fresh = await db.invoices.find_one({"_id": invoice["_id"], "firm_id": firm_id}) or {}
            await alert(
                "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} για τιμολόγιο που δεν ήταν ανοιχτό",
                f"Το {number} είχε κατάσταση «{fresh.get('status') or 'άγνωστη'}» όταν ολοκληρώθηκε η πληρωμή "
                f"(συνεδρία {session_id}). Τα χρήματα έχουν εισπραχθεί: ελέγξτε αν χρειάζεται επιστροφή ή σύνδεση "
                "με άλλο παραστατικό.")
            return
        if to_cents(paid) > to_cents(due):
            await alert(
                "card_payment_overpaid", f"Πληρωμή κάρτας {_fmt(paid)} μεγαλύτερη από το οφειλόμενο",
                f"Το {number} εξοφλήθηκε, αλλά το οφειλόμενο ήταν {_fmt(due)} και εισπράχθηκαν {_fmt(paid)} "
                f"(συνεδρία {session_id}). Επιστρέψτε στον πελάτη τη διαφορά {_fmt(difference)} ή πιστώστε τον "
                "πελατολογικό του λογαριασμό.")
    except Exception as e:  # noqa: BLE001 — η πληρωμή έχει γίνει: το σφάλμα πρέπει να φανεί, όχι να χαθεί
        logger.exception("Αποτυχία εφαρμογής πληρωμής κάρτας (συνεδρία %s)", session_id)
        await alert(
            "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} που δεν εφαρμόστηκε",
            f"Η πληρωμή (συνεδρία {session_id}) εισπράχθηκε αλλά η εφαρμογή απέτυχε: {e}. "
            "Ελέγξτε το τιμολόγιο και συμφωνήστε την πληρωμή χειροκίνητα.")


@public_router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")

    obj = event["data"]["object"]
    t = event["type"]
    if t == "checkout.session.completed":
        # Στις καθυστερημένες μεθόδους πληρωμής η συνεδρία ολοκληρώνεται πριν φτάσουν τα χρήματα
        # (payment_status = "unpaid")· τότε περιμένουμε το async_payment_succeeded.
        if obj.get("payment_status", "paid") in ("paid", "no_payment_required"):
            await _mark_paid(obj["id"], obj.get("payment_intent"))
    elif t == "checkout.session.async_payment_succeeded":
        # Όχι προ-σήμανση «paid» εδώ: το _mark_paid εφαρμόζει την πληρωμή μόνο στην πρώτη μετάβαση.
        await _mark_paid(obj["id"], obj.get("payment_intent"))
    elif t == "checkout.session.async_payment_failed":
        await db.payment_transactions.update_one(
            {"session_id": obj["id"]},
            {"$set": {"status": "failed", "payment_status": "failed", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    elif t == "checkout.session.expired":
        await db.payment_transactions.update_one(
            {"session_id": obj["id"]},
            {"$set": {"status": "expired", "payment_status": "expired", "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
    return {"status": "ok"}


@router.get("/transactions")
async def list_transactions(user: dict = Depends(get_current_user)):
    docs = await db.payment_transactions.find(tenant_query(user)).sort("created_at", -1).limit(200).to_list(200)
    return [PaymentTransaction.from_mongo(d) for d in docs]
