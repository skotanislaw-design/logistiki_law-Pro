"""Stripe Payments — Checkout Session για online εξόφληση τιμολογίων."""
import os
import asyncio
import logging
import uuid
import stripe
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId
from pymongo import ReturnDocument
from db import db
from models import PaymentTransaction
from auth import get_current_user
from http_utils import allowed_origins, checked_return_url
from rate_limit import enforce, key_for_token
from routes_client_portal import _validate_token
from tenant import tenant_query, firm_id_for

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or "sk_test_emergent"
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

logger = logging.getLogger(__name__)

# Κάθε επιτυχημένη κλήση του checkout της πύλης δημιουργεί συνεδρία στο Stripe. Ένας πελάτης χρειάζεται μία (ή λίγες,
# αν πληρώνει περισσότερα τιμολόγια)· το όριο κόβει την κατάχρηση χωρίς να ενοχλεί τη χρήση.
PORTAL_CHECKOUT_LIMIT = 10        # συνεδρίες πληρωμής ανά token ανά λεπτό

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


async def _validate_invoice_and_get_amount(invoice_id: str, firm_id: str | None = None,
                                           client_id: str | None = None) -> tuple[dict, float]:
    """Το τιμολόγιο και το ποσό που οφείλεται, ή σφάλμα που εξηγεί γιατί δεν πληρώνεται.

    Με ``client_id`` (πύλη πελάτη) το τιμολόγιο αναζητείται **μέσα στα τιμολόγια αυτού του πελάτη**: ένα τιμολόγιο άλλου
    πελάτη είναι για την πύλη ακριβώς το ίδιο με ένα που δεν υπάρχει (404). Αν ελεγχόταν η ιδιοκτησία μετά την
    επικύρωση, οι απαντήσεις «ήδη εξοφλημένο», «πρόχειρο», «υπάρχει πιστωτικό», «δεν επιτρέπεται» θα μαρτυρούσαν
    σε όποιον έχει έγκυρο σύνδεσμο ποια τιμολόγια των άλλων πελατών του γραφείου υπάρχουν και σε ποια κατάσταση είναι.
    """
    try:
        query = {"_id": ObjectId(invoice_id)}
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id τιμολογίου")
    if firm_id:
        query["firm_id"] = firm_id
    if client_id is not None:
        query["client_id"] = client_id
    inv = await db.invoices.find_one(query)
    if not inv:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if inv.get("status") == "paid":
        raise HTTPException(status_code=400, detail="Το τιμολόγιο είναι ήδη εξοφλημένο")
    if inv.get("status") != "issued":
        # Πρόχειρο ή ακυρωμένο παραστατικό δεν εισπράττεται.
        raise HTTPException(status_code=400, detail="Πληρώνονται μόνο εκδοθέντα, ανεξόφλητα τιμολόγια.")
    if inv.get("credit_note_id"):
        # Το οφειλόμενο δεν συμψηφίζεται με το πιστωτικό (ολικό ή μερικό): το ποσό το ορίζει το γραφείο.
        raise HTTPException(status_code=400, detail="Για το τιμολόγιο υπάρχει πιστωτικό· επικοινωνήστε με το γραφείο για το ποσό που οφείλεται.")
    # Ο πελάτης καταβάλλει το οφειλόμενο: το πληρωτέο (μετά την παρακράτηση που κρατά ο ίδιος),
    # μείον ό,τι έχει ήδη εξοφληθεί από τον πελατολογικό του λογαριασμό. Όχι το ακαθάριστο.
    from invoice_services import outstanding_amount
    settings = await db.settings.find_one({"firm_id": inv.get("firm_id")}) or {}
    total, _withheld = await outstanding_amount(inv.get("firm_id", ""), inv, settings)
    if total <= 0:
        raise HTTPException(status_code=400, detail="Μη έγκυρο ποσό τιμολογίου")
    return inv, total


async def _create_session(inv: dict, amount: float, success_url: str, cancel_url: str) -> dict:
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
        success_url=success_url,
        cancel_url=cancel_url,
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


async def _return_origin(origin_url: str, firm_id: str, *, portal_token: str = "") -> str:
    """Η διεύθυνση της εφαρμογής (scheme://host[:port]) όπου επιστρέφει ο πελάτης μετά την πληρωμή, επικυρωμένη
    (βλ. ``http_utils.checked_return_url``).

    Επιτρέπονται οι διευθύνσεις της εγκατάστασης (``allowed_origins``) και η ``public_url`` των Ρυθμίσεων του γραφείου
    στο οποίο ανήκει το τιμολόγιο — όχι των Ρυθμίσεων άλλου γραφείου. Η πύλη μπορεί να στείλει και τη διεύθυνση της δικής
    της σελίδας (``<origin>/portal/<token>``)· επιστρέφεται πάντα μόνο το origin, και οι διευθύνσεις επιστροφής χτίζονται
    από τον server (``_staff_return_urls``, ``_portal_return_urls``)."""
    settings = await db.settings.find_one({"firm_id": firm_id}) or {}
    origins = allowed_origins()
    if settings.get("public_url"):
        origins = [*origins, settings["public_url"]]
    page = f"/portal/{portal_token}" if portal_token else ""
    url = checked_return_url(origin_url, origins, allowed_paths=("", page) if page else ("",))
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Μη έγκυρη διεύθυνση επιστροφής μετά την πληρωμή. Ανοίξτε την εφαρμογή από τη διεύθυνση του γραφείου.")
    return url[: -len(page)] if page and url.endswith(page) else url


def _staff_return_urls(origin: str, invoice_id: str) -> tuple[str, str]:
    """Το προσωπικό του γραφείου επιστρέφει στη σελίδα αποτελέσματος πληρωμής της εφαρμογής."""
    return (f"{origin}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            f"{origin}/payment/cancel?invoice={invoice_id}")


def _portal_return_urls(origin: str, token: str) -> tuple[str, str]:
    """Ο πελάτης της πύλης επιστρέφει στη **δική του σελίδα** (`/portal/<token>`), η οποία επαληθεύει την πληρωμή από το
    `session_id` ή δείχνει ότι ακυρώθηκε. Παλιότερα η επιστροφή ήταν `/portal/<token>/payment/success`: διαδρομή που δεν
    υπάρχει στο SPA, ενώ η σελίδα της πύλης περίμενε μια παράμετρο (`?paid=1`) που κανείς δεν έστελνε."""
    page = f"{origin}/portal/{token}"
    return page + "?paid=1&session_id={CHECKOUT_SESSION_ID}", page + "?cancelled=1"


def _note_for_trail(request: Request, inv: dict, amount: float) -> None:
    """Τι ζητήθηκε, για το ελεγκτικό ίχνος (δεν καταγράφεται ποτέ σώμα αιτήματος· εδώ μόνο αριθμός και ποσό)."""
    from automation import _fmt
    request.state.audit_details = f"Έναρξη πληρωμής με κάρτα: τιμολόγιο {inv.get('number', '')}, {_fmt(amount)}"


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest, request: Request, user: dict = Depends(get_current_user)):
    firm_id = firm_id_for(user)
    origin = await _return_origin(req.origin_url, firm_id)
    inv, amount = await _validate_invoice_and_get_amount(req.invoice_id, firm_id)
    _note_for_trail(request, inv, amount)
    return await _create_session(inv, amount, *_staff_return_urls(origin, str(inv["_id"])))


@public_router.post("/api/payments/portal-checkout")
async def portal_checkout(req: CheckoutRequest, request: Request):
    """Public endpoint για client portal — απαιτεί έγκυρο portal token.

    Η επικύρωση του token είναι η **κοινή** της πύλης (``_validate_token``): όριο ρυθμού ανά token και IP, λήξη με
    σύγκριση ημερομηνιών, ενημέρωση του ``last_used_at`` και απόδοση της ενέργειας στο ελεγκτικό ίχνος του γραφείου και
    του πελάτη. Εδώ προστίθεται δικό της, αυστηρότερο όριο, γιατί κάθε επιτυχία δημιουργεί συνεδρία στο Stripe.

    Το τιμολόγιο αναζητείται μέσα στα τιμολόγια του πελάτη του συνδέσμου (βλ. ``_validate_invoice_and_get_amount``)."""
    if not req.client_token:
        raise HTTPException(status_code=401, detail="Απαιτείται token πελάτη")
    token = await _validate_token(req.client_token, request)
    await enforce("portal:checkout", key_for_token(req.client_token), PORTAL_CHECKOUT_LIMIT, 60,
                  "Πολλές προσπάθειες πληρωμής.")
    origin = await _return_origin(req.origin_url, token.get("firm_id") or "", portal_token=req.client_token)
    inv, amount = await _validate_invoice_and_get_amount(req.invoice_id, token.get("firm_id"), token["client_id"])
    _note_for_trail(request, inv, amount)
    return await _create_session(inv, amount, *_portal_return_urls(origin, req.client_token))


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
    elif record.get("payment_status") == "paid" and record.get("apply_pending"):
        # Η είσπραξη έχει καταγραφεί αλλά η εφαρμογή στο τιμολόγιο έμεινε στη μέση (π.χ. κατάρρευση): τελείωσέ την.
        await apply_pending_card_payment(session_id)
        record = await db.payment_transactions.find_one({"session_id": session_id}) or record
    return {
        "session_id": record["session_id"],
        "status": record["status"],
        "payment_status": record["payment_status"],
        "invoice_number": record.get("invoice_number", ""),
        "amount": record.get("amount", 0),
    }


APPLY_CLAIM_TTL_SECONDS = 120     # μετά από κατάρρευση, το «κλείδωμα εφαρμογής» λήγει μόνο του
APPLY_MAX_ATTEMPTS = 5            # πόσες φορές επιχειρείται η εφαρμογή πριν ειδοποιηθεί άνθρωπος


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _mark_paid(session_id: str, payment_intent_id: str = None):
    """Idempotent — καλείται από webhook, polling fallback και Autopilot.

    Δύο βήματα, καθένα ασφαλές να επαναληφθεί:

    1. **Καταγραφή** της είσπραξης: η συναλλαγή γίνεται «paid» και σημαίνεται `apply_pending` στην ίδια ατομική
       εγγραφή. Ο δείκτης σβήνει μόνο όταν ολοκληρωθεί η εφαρμογή.
    2. **Εφαρμογή** στο τιμολόγιο (`apply_pending_card_payment`).

    Παλιότερα η εφαρμογή έτρεχε μόνο στην κλήση που έκανε τη μετάβαση σε «paid». Αν η διεργασία έπεφτε ανάμεσα
    στα δύο βήματα, κάθε επόμενη κλήση έβλεπε τη συναλλαγή ήδη «paid» και δεν έκανε τίποτα: χρήματα
    εισπραγμένα, τιμολόγιο ανοιχτό, καμία ειδοποίηση.
    """
    # Το session_id είναι μοναδικό ανά συναλλαγή· το firm_id προκύπτει από την ίδια εγγραφή,
    # οπότε δεν προσθέτει φιλτράρισμα — μόνο ένα περιττό round-trip.
    now = _now()
    await db.payment_transactions.update_one(
        {"session_id": session_id, "payment_status": {"$ne": "paid"}},
        {"$set": {
            "status": "completed",
            "payment_status": "paid",
            "stripe_payment_intent_id": payment_intent_id,
            "paid_at": now,
            "apply_pending": True,
            "updated_at": now,
        }},
    )
    await apply_pending_card_payment(session_id)


async def _release_apply_claim(session_id: str, token: str) -> None:
    try:
        await db.payment_transactions.update_one(
            {"session_id": session_id, "apply_claim": token},
            {"$unset": {"apply_claim": "", "apply_claim_at": ""}})
    except Exception:  # noqa: BLE001 — λήγει μόνο του μετά το TTL
        logger.warning("Δεν αποδεσμεύτηκε το κλείδωμα εφαρμογής πληρωμής κάρτας (συνεδρία %s)", session_id)


async def apply_pending_card_payment(session_id: str) -> bool:
    """Εφαρμόζει στο τιμολόγιο μια πληρωμή κάρτας που έχει καταγραφεί αλλά δεν έχει εφαρμοστεί ακόμη.

    Ασφαλής όσες φορές κι αν κληθεί (webhook που ξαναστέλνεται, polling, Autopilot): την εφαρμογή την κάνει
    μόνο όποιος κερδίσει ένα ατομικό κλείδωμα· αν κάποιος τη διεκδικεί ήδη, οι υπόλοιποι φεύγουν. Αν το κλείδωμα
    μείνει από διεργασία που έπεσε, λήγει μετά από `APPLY_CLAIM_TTL_SECONDS` και την παίρνει ο επόμενος. Σε
    αποτυχία που δεν είναι απλό σφάλμα (ακύρωση αιτήματος, τερματισμός), το κλείδωμα λύνεται αμέσως και η
    εκκρεμότητα μένει. Επιστρέφει True αν αυτή η κλήση ολοκλήρωσε την εφαρμογή.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=APPLY_CLAIM_TTL_SECONDS)).isoformat()
    token = uuid.uuid4().hex
    tx = await db.payment_transactions.find_one_and_update(
        {"session_id": session_id, "apply_pending": True,
         "$or": [{"apply_claim_at": {"$exists": False}}, {"apply_claim_at": None}, {"apply_claim_at": {"$lt": cutoff}}]},
        {"$set": {"apply_claim": token, "apply_claim_at": _now()}, "$inc": {"apply_attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if tx is None:
        return False
    try:
        if int(tx.get("apply_attempts") or 1) > APPLY_MAX_ATTEMPTS:
            result = await _give_up_applying(tx)
        else:
            result = await _apply_card_payment(tx)
    except BaseException:
        await asyncio.shield(_release_apply_claim(session_id, token))
        raise
    await db.payment_transactions.update_one(
        {"session_id": session_id, "apply_claim": token},
        {"$set": {"apply_pending": False, "applied_at": _now(), "apply_result": result},
         "$unset": {"apply_claim": "", "apply_claim_at": ""}})
    return True


async def _give_up_applying(tx: dict) -> str:
    """Η πληρωμή δεν κατάφερε να εφαρμοστεί σε πολλές προσπάθειες: δεν ξαναδοκιμάζεται, ειδοποιείται άνθρωπος."""
    from automation import notify, _fmt
    firm_id = tx.get("firm_id", "legacy-default-firm")
    paid = round(float(tx.get("amount") or 0), 2)
    await notify(
        firm_id, "card_payment_unapplied", "critical", f"Πληρωμή κάρτας {_fmt(paid)} που δεν εφαρμόστηκε",
        f"Η πληρωμή (συνεδρία {tx.get('session_id')}) εισπράχθηκε αλλά η εφαρμογή της στο τιμολόγιο δεν "
        f"ολοκληρώθηκε μετά από {APPLY_MAX_ATTEMPTS} προσπάθειες. Ελέγξτε το τιμολόγιο και συμφωνήστε την "
        "πληρωμή χειροκίνητα.",
        entity="invoice", entity_id=tx.get("invoice_id") or "", requires_action=True,
        dedupe_key=f"card_payment_unapplied:{tx.get('session_id')}")
    return "given_up"


async def _apply_card_payment(tx: dict) -> str:
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

    Η συνάρτηση είναι **επαναλήψιμη**: μια προηγούμενη προσπάθεια μπορεί να έκλεισε ήδη το τιμολόγιο και να
    έπεσε πριν γράψει το αποτέλεσμα. Αυτό αναγνωρίζεται από το `paid_ref` του τιμολογίου (η συνεδρία που το
    εξόφλησε) και δεν θεωρείται πρόβλημα· ένα τιμολόγιο που εξοφλήθηκε από ΑΛΛΗ συνεδρία (διπλοπληρωμή)
    εξακολουθεί να αναφέρεται. Επιστρέφει το αποτέλεσμα: settled / overpaid / underpaid / not_open / failed.
    """
    from invoice_services import mark_paid, outstanding_amount, to_cents
    from automation import notify, _fmt
    firm_id = tx.get("firm_id", "legacy-default-firm")
    invoice_id = tx.get("invoice_id")
    session_id = tx.get("session_id")
    paid = round(float(tx.get("amount") or 0), 2)

    async def alert(type_: str, title: str, message: str) -> None:
        # Η ειδοποίηση ΠΡΕΠΕΙ να καταχωρηθεί. Αν αποτύχει, το σφάλμα φεύγει προς τα πάνω, η εφαρμογή μένει εκκρεμής
        # (`apply_pending`) και ξαναδοκιμάζεται — αλλιώς μια πληρωμή που χρειάζεται άνθρωπο θα έκλεινε χωρίς να το
        # μάθει κανείς. Οι επαναλήψεις είναι φραγμένες από τον μετρητή προσπαθειών (`APPLY_MAX_ATTEMPTS`).
        await notify(firm_id, type_, "critical", title, message, entity="invoice", entity_id=invoice_id,
                     requires_action=True, dedupe_key=f"{type_}:{session_id}")

    def _is_settled_by_this_payment(doc: dict) -> bool:
        return doc.get("status") == "paid" and doc.get("paid_via") == "stripe" and doc.get("paid_ref") == session_id

    try:
        invoice = await db.invoices.find_one({"_id": ObjectId(invoice_id), "firm_id": firm_id}) or {}
        number = invoice.get("number") or "τιμολόγιο"
        settled_here = _is_settled_by_this_payment(invoice)
        # Όλες οι άλλες διαδρομές αρνούνται τιμολόγιο με πιστωτικό (checkout, σύνδεσμος πληρωμής, υπενθύμιση, κατάθεση
        # τράπεζας, πελατολογικός): μια συνεδρία που άνοιξε πριν εκδοθεί το πιστωτικό δεν πρέπει να το «εξοφλήσει».
        credited = invoice.get("status") == "issued" and bool(invoice.get("credit_note_id"))
        if (invoice.get("status") != "issued" or credited) and not settled_here:
            reason = ("για το τιμολόγιο εκδόθηκε πιστωτικό" if credited
                      else f"είχε κατάσταση «{invoice.get('status') or 'άγνωστη'}»")
            await alert(
                "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} για τιμολόγιο που δεν ήταν ανοιχτό",
                f"Το {number}: {reason} όταν ολοκληρώθηκε η πληρωμή "
                f"(συνεδρία {session_id}). Τα χρήματα έχουν εισπραχθεί: ελέγξτε αν χρειάζεται επιστροφή ή σύνδεση "
                "με άλλο παραστατικό.")
            return "not_open"
        settings = await db.settings.find_one({"firm_id": firm_id}) or {}
        due, _withheld = await outstanding_amount(firm_id, invoice, settings)
        difference = round(paid - due, 2)
        if not settled_here:
            if to_cents(paid) < to_cents(due):
                await alert(
                    "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} μικρότερη από το οφειλόμενο",
                    f"Το {number} οφείλει {_fmt(due)}, ο πελάτης πλήρωσε {_fmt(paid)} (συνεδρία {session_id}). "
                    "Το τιμολόγιο ΔΕΝ εξοφλήθηκε και μένει ανοιχτό για το υπόλοιπο· συμφωνήστε την πληρωμή χειροκίνητα "
                    "όταν διευθετηθεί.")
                return "underpaid"
            if not await mark_paid(firm_id, invoice["_id"], paid_via="stripe", ref=session_id):
                fresh = await db.invoices.find_one({"_id": invoice["_id"], "firm_id": firm_id}) or {}
                # Άλλη προσπάθεια της ΙΔΙΑΣ πληρωμής (το κλείδωμα έληξε ενώ εμείς καθυστερούσαμε) πρόλαβε και
                # εξόφλησε το τιμολόγιο με αυτή τη συνεδρία: δεν είναι πρόβλημα — συνεχίζουμε με τον έλεγχο ποσού.
                if not _is_settled_by_this_payment(fresh):
                    await alert(
                        "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} για τιμολόγιο που δεν ήταν ανοιχτό",
                        f"Το {number} είχε κατάσταση «{fresh.get('status') or 'άγνωστη'}» όταν ολοκληρώθηκε η πληρωμή "
                        f"(συνεδρία {session_id}). Τα χρήματα έχουν εισπραχθεί: ελέγξτε αν χρειάζεται επιστροφή ή σύνδεση "
                        "με άλλο παραστατικό.")
                    return "not_open"
        if to_cents(paid) > to_cents(due):
            await alert(
                "card_payment_overpaid", f"Πληρωμή κάρτας {_fmt(paid)} μεγαλύτερη από το οφειλόμενο",
                f"Το {number} εξοφλήθηκε, αλλά το οφειλόμενο ήταν {_fmt(due)} και εισπράχθηκαν {_fmt(paid)} "
                f"(συνεδρία {session_id}). Επιστρέψτε στον πελάτη τη διαφορά {_fmt(difference)} ή πιστώστε τον "
                "πελατολογικό του λογαριασμό.")
            return "overpaid"
        return "settled"
    except Exception as e:  # noqa: BLE001 — η πληρωμή έχει γίνει: το σφάλμα πρέπει να φανεί, όχι να χαθεί
        logger.exception("Αποτυχία εφαρμογής πληρωμής κάρτας (συνεδρία %s)", session_id)
        await alert(
            "card_payment_unapplied", f"Πληρωμή κάρτας {_fmt(paid)} που δεν εφαρμόστηκε",
            f"Η πληρωμή (συνεδρία {session_id}) εισπράχθηκε αλλά η εφαρμογή απέτυχε: {e}. "
            "Ελέγξτε το τιμολόγιο και συμφωνήστε την πληρωμή χειροκίνητα.")
        return "failed"


@public_router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not (STRIPE_WEBHOOK_SECRET or "").strip():
        # Με κενό μυστικό η βιβλιοθήκη του Stripe δέχεται κάθε γεγονός υπογεγραμμένο με κενό κλειδί (HMAC του b""): όποιος
        # ξέρει ένα session_id θα μπορούσε να «εξοφλήσει» τιμολόγιο. Χωρίς μυστικό το webhook δεν λειτουργεί καθόλου·
        # οι εξοφλήσεις εντοπίζονται από το polling (που ρωτά το ίδιο το Stripe με το μυστικό κλειδί του γραφείου).
        raise HTTPException(status_code=503, detail="Webhook payments are not configured")
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
