"""Πελατολογικοί λογαριασμοί (Ν.4194/2013 άρθρο 32): χρήματα πελατών που τηρεί το γραφείο.

Δύο αρχές που ισχύουν σε κάθε κίνηση εδώ:

* **Το υπόλοιπο δεν διαβάζεται και ξαναγράφεται.** Παλιότερα κάθε κίνηση διάβαζε το υπόλοιπο,
  το άλλαζε στη μνήμη και το έγραφε πίσω· δύο ταυτόχρονες αναλήψεις έβλεπαν και οι δύο το ίδιο
  υπόλοιπο και περνούσαν και οι δύο, αφήνοντας τον λογαριασμό αρνητικό — χρήματα πελάτη που
  δεν υπήρχαν. Τώρα η αλλαγή γίνεται με ατομική σύγκριση-και-αντικατάσταση (compare-and-swap):
  αν κάποιος πρόλαβε, η κίνηση ξαναϋπολογίζεται πάνω στο νέο υπόλοιπο.
* **Τα χρήματα ενός πελάτη δεν εξοφλούν τιμολόγιο άλλου.** Η εξόφληση τιμολογίου ελέγχει ότι το
  τιμολόγιο ανήκει στον ίδιο πελάτη, είναι ανοιχτό και ότι το ποσό δεν υπερβαίνει το οφειλόμενο.

Η MongoDB (standalone) δεν έχει πολυ-εγγραφικές συναλλαγές· οι κινήσεις που αγγίζουν περισσότερα
από ένα έγγραφα εκτελούνται με σταθερή σειρά (τιμολόγιο → λογαριασμός → εγγραφή στο ledger) και
αναιρούνται αντίστροφα αν κάποιο βήμα αποτύχει.
"""
import logging
import math
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from auth import get_current_user
from db import db
from invoice_services import mark_paid, outstanding_amount, payable_amount
from models import TrustAccount, TrustTransaction
from tax_utils import athens_today
from tenant import tenant_query, firm_id_for

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trust", tags=["trust"])

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000000")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OUT_TYPES = ("withdrawal", "apply_to_invoice", "refund")


class TransactionPayload(BaseModel):
    client_id: str
    type: Literal["deposit", "withdrawal", "apply_to_invoice", "refund"]
    amount: float
    date: str
    description: str = Field("", max_length=500)
    reference: str = Field("", max_length=120)
    invoice_id: Optional[str] = None
    case_id: Optional[str] = None


# ------------------------------------------------------------ επικύρωση ----

def _dec(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _amount(value: float) -> Decimal:
    """Θετικό ποσό με το πολύ δύο δεκαδικά — όπως στο εξερχόμενο/εισερχόμενο της τράπεζας."""
    if not math.isfinite(value):
        raise HTTPException(status_code=400, detail="Μη έγκυρο ποσό.")
    d = Decimal(str(value))
    if d <= 0:
        raise HTTPException(status_code=400, detail="Το ποσό πρέπει να είναι μεγαλύτερο του μηδενός.")
    if d > MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="Το ποσό είναι υπερβολικά μεγάλο — ελέγξτε το.")
    if d != d.quantize(CENT):
        raise HTTPException(status_code=400, detail="Το ποσό πρέπει να έχει το πολύ δύο δεκαδικά ψηφία.")
    return d.quantize(CENT)


def _entry_date(value: str) -> str:
    """ΕΕΕΕ-ΜΜ-ΗΗ, όχι μελλοντική (με ώρα Αθήνας)."""
    text = (value or "").strip()
    parsed = None
    if DATE_RE.match(text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            parsed = None
    if parsed is None:
        raise HTTPException(status_code=400, detail="Η ημερομηνία πρέπει να είναι της μορφής ΕΕΕΕ-ΜΜ-ΗΗ.")
    if parsed > athens_today():
        raise HTTPException(status_code=400, detail="Η ημερομηνία της κίνησης δεν μπορεί να είναι μελλοντική.")
    return parsed.isoformat()


# -------------------------------------------- ατομική αλλαγή (CAS) -------

async def _cas_update(collection, query: dict, compute, attempts: int = 8):
    """Διάβασε → υπολόγισε → γράψε μόνο αν το έγγραφο δεν άλλαξε στο μεταξύ.

    `compute(doc)` επιστρέφει (guard, changes): το `guard` είναι οι τιμές που διαβάστηκαν και
    πρέπει να ισχύουν ακόμη στη στιγμή της εγγραφής. Αν όχι, ξαναδιαβάζουμε. Επιστρέφει το
    νέο έγγραφο, ή None αν δεν υπάρχει. Τα ποσά γράφονται στρογγυλεμένα σε λεπτά, ώστε να
    μη συσσωρεύεται σφάλμα κινητής υποδιαστολής.
    """
    for _ in range(attempts):
        doc = await collection.find_one(query)
        if doc is None:
            return None
        guard, changes = compute(doc)
        updated = await collection.find_one_and_update(
            {**query, **guard}, {"$set": changes}, return_document=ReturnDocument.AFTER)
        if updated is not None:
            return updated
    raise HTTPException(status_code=409, detail="Ο λογαριασμός ενημερώνεται αυτή τη στιγμή από άλλη κίνηση. Δοκιμάστε ξανά.")


def _account_delta(kind: str, amount: Decimal, *, undo: bool = False):
    """Η αλλαγή που προκαλεί μια κίνηση στο (υπόλοιπο, σύνολο καταθέσεων, σύνολο αναλήψεων)."""
    if kind == "deposit":
        d_balance, d_deposits, d_withdrawals = amount, amount, Decimal(0)
    else:
        d_balance, d_deposits, d_withdrawals = -amount, Decimal(0), amount
    if undo:
        d_balance, d_deposits, d_withdrawals = -d_balance, -d_deposits, -d_withdrawals

    def compute(doc: dict):
        balance = _dec(doc.get("balance"))
        new_balance = balance + d_balance
        if new_balance < 0 and not undo:
            raise HTTPException(
                status_code=400,
                detail=f"Ανεπαρκές υπόλοιπο πελατολογικού λογαριασμού. Διαθέσιμο: {balance:.2f} €",
            )
        guard = {k: doc.get(k) for k in ("balance", "total_deposits", "total_withdrawals")}
        return guard, {
            "balance": float(new_balance),
            "total_deposits": float(_dec(doc.get("total_deposits")) + d_deposits),
            "total_withdrawals": float(_dec(doc.get("total_withdrawals")) + d_withdrawals),
        }
    return compute


def _applied_delta(delta: Decimal, payable: Decimal | None = None):
    """Αλλαγή του `trust_applied` του τιμολογίου. Με `payable` ελέγχει ότι δεν ξεπερνά το πληρωτέο."""
    def compute(doc: dict):
        applied = _dec(doc.get("trust_applied"))
        new_applied = applied + delta
        if payable is not None and new_applied > payable:
            raise HTTPException(
                status_code=400,
                detail=f"Το ποσό υπερβαίνει το οφειλόμενο του τιμολογίου ({payable - applied:.2f} €).",
            )
        return {"trust_applied": doc.get("trust_applied")}, {"trust_applied": float(new_applied)}
    return compute


# ------------------------------------------------------------- λογαριασμός ----

async def _load_client(client_id: str, user: dict) -> dict:
    try:
        client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(client_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    return client


async def _ensure_account(client_id: str, user: dict):
    """Επιστρέφει (ObjectId, TrustAccount) του πελάτη, τον δημιουργεί αν λείπει."""
    client = await _load_client(client_id, user)
    query = tenant_query(user, {"client_id": client_id})
    doc = await db.trust_accounts.find_one(query)
    if not doc:
        acc = TrustAccount(client_id=client_id, client_name=client.get("name", ""), firm_id=firm_id_for(user))
        try:
            await db.trust_accounts.insert_one(acc.to_mongo())
        except DuplicateKeyError:
            pass        # δύο πρώτες κινήσεις ταυτόχρονα: το unique index κράτησε τον έναν λογαριασμό
        doc = await db.trust_accounts.find_one(query)
    return doc["_id"], TrustAccount.from_mongo(doc)


async def _client_invoice(invoice_id: str, client_id: str, user: dict) -> dict:
    try:
        invoice = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id τιμολογίου")
    if not invoice:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if invoice.get("client_id") != client_id:
        raise HTTPException(
            status_code=400,
            detail="Το τιμολόγιο ανήκει σε άλλον πελάτη· τα χρήματα ενός πελάτη δεν εξοφλούν τιμολόγιο άλλου.",
        )
    return invoice


async def _ledger_check(user: dict, acc_id, account: TrustAccount) -> dict:
    """Το υπόλοιπο που βγαίνει από τις κινήσεις, δίπλα στο αποθηκευμένο υπόλοιπο."""
    totals: dict[str, Decimal] = {}
    async for row in db.trust_transactions.aggregate([
        {"$match": tenant_query(user, {"account_id": str(acc_id)})},
        {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}},
    ]):
        totals[row["_id"]] = _dec(row["total"])
    ledger = totals.get("deposit", Decimal(0)) - sum((totals.get(t, Decimal(0)) for t in OUT_TYPES), Decimal(0))
    difference = _dec(account.balance) - ledger
    return {"ledger_balance": float(ledger), "difference": float(difference), "consistent": difference == 0}


@router.get("/accounts")
async def list_accounts(user: dict = Depends(get_current_user)):
    docs = await db.trust_accounts.find(tenant_query(user, {})).sort("client_name", 1).to_list(1000)
    return [TrustAccount.from_mongo(d) for d in docs]


@router.get("/accounts/{client_id}")
async def get_account(client_id: str, user: dict = Depends(get_current_user)):
    acc_id, account = await _ensure_account(client_id, user)
    txs = await db.trust_transactions.find(tenant_query(user, {"account_id": str(acc_id)})) \
        .sort([("date", -1), ("created_at", -1)]).to_list(500)
    return {
        "account": account,
        "transactions": [TrustTransaction.from_mongo(t) for t in txs],
        "ledger_check": await _ledger_check(user, acc_id, account),
    }


@router.get("/accounts/{client_id}/open-invoices")
async def open_invoices(client_id: str, user: dict = Depends(get_current_user)):
    """Τα ανοιχτά τιμολόγια του πελάτη με το ποσό που οφείλεται πραγματικά (μετά την παρακράτηση)."""
    await _load_client(client_id, user)
    settings = await db.settings.find_one(tenant_query(user)) or {}
    firm_id = firm_id_for(user)
    rows = []
    async for inv in db.invoices.find(
        tenant_query(user, {"client_id": client_id, "status": "issued", "credit_note_for": None})
    ).sort("date", 1):
        payable, _ = await payable_amount(firm_id, inv, settings)
        due, _ = await outstanding_amount(firm_id, inv, settings)
        if due <= 0:
            continue
        rows.append({
            "id": str(inv["_id"]), "number": inv.get("number", ""), "date": inv.get("date", ""),
            "total": float(inv.get("total") or 0), "payable": payable,
            "trust_applied": float(inv.get("trust_applied") or 0), "outstanding": due,
        })
    return rows


# --------------------------------------------------------------- κινήσεις ----

@router.post("/transactions")
async def create_transaction(payload: TransactionPayload, user: dict = Depends(get_current_user)):
    amount = _amount(payload.amount)
    entry_date = _entry_date(payload.date)
    firm_id = firm_id_for(user)
    invoice_ref = (payload.invoice_id or "").strip() or None
    case_ref = (payload.case_id or "").strip() or None

    acc_id, account = await _ensure_account(payload.client_id, user)

    invoice, payable, invoice_number, label = None, None, None, ""
    if payload.type == "apply_to_invoice" and not invoice_ref:
        raise HTTPException(status_code=400, detail="Για εξόφληση τιμολογίου επιλέξτε το τιμολόγιο.")
    if invoice_ref:
        invoice = await _client_invoice(invoice_ref, payload.client_id, user)
        invoice_number = invoice.get("number") or None
        label = f" {invoice_number}" if invoice_number else ""
        if payload.type == "apply_to_invoice":
            if invoice.get("status") != "issued":
                raise HTTPException(status_code=400, detail="Εξοφλούνται μόνο εκδοθέντα, ανεξόφλητα τιμολόγια.")
            settings = await db.settings.find_one(tenant_query(user)) or {}
            payable_value, _ = await payable_amount(firm_id, invoice, settings)
            payable = _dec(payable_value)
            outstanding = payable - _dec(invoice.get("trust_applied"))
            if amount > outstanding:
                raise HTTPException(
                    status_code=400,
                    detail=f"Το ποσό ({amount:.2f} €) υπερβαίνει το οφειλόμενο του τιμολογίου{label} ({outstanding:.2f} €).",
                )
    if case_ref:
        try:
            case = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_ref)}))
        except Exception:
            case = None
        if not case:
            raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")

    # Σειρά: τιμολόγιο → λογαριασμός → ledger. Αν δεν χωρέσει το ποσό στον λογαριασμό, το
    # τιμολόγιο δεν έχει ακόμη πειραχτεί· αν το ledger αποτύχει, αναιρούνται και τα δύο.
    claimed = moved = False
    try:
        claim = None
        if payload.type == "apply_to_invoice":
            claim = await _cas_update(
                db.invoices, tenant_query(user, {"_id": invoice["_id"], "status": "issued"}),
                _applied_delta(amount, payable))
            if claim is None:
                raise HTTPException(status_code=409, detail="Το τιμολόγιο δεν είναι πλέον ανοιχτό.")
            claimed = True
        updated = await _cas_update(
            db.trust_accounts, tenant_query(user, {"_id": acc_id}), _account_delta(payload.type, amount))
        if updated is None:
            raise HTTPException(status_code=404, detail="Ο λογαριασμός δεν βρέθηκε")
        moved = True
        tx = TrustTransaction(
            account_id=str(acc_id),
            client_id=payload.client_id,
            client_name=account.client_name or "",
            type=payload.type,
            amount=float(amount),
            balance_after=round(float(updated["balance"]), 2),
            date=entry_date,
            description=payload.description,
            reference=payload.reference,
            invoice_id=invoice_ref,
            invoice_number=invoice_number,
            case_id=case_ref,
            created_by=user["email"],
        )
        tx.firm_id = firm_id
        await db.trust_transactions.insert_one(tx.to_mongo())
    except Exception:
        if moved:
            await _cas_update(db.trust_accounts, tenant_query(user, {"_id": acc_id}),
                              _account_delta(payload.type, amount, undo=True))
        if claimed:
            await _cas_update(db.invoices, tenant_query(user, {"_id": invoice["_id"]}),
                              _applied_delta(-amount))
        raise

    message, extra = "Η κίνηση καταχωρήθηκε", {}
    if claim is not None:
        remaining = payable - _dec(claim.get("trust_applied"))
        if remaining <= 0:
            paid = await mark_paid(firm_id, invoice["_id"], paid_via="trust")
            if not paid:
                logger.warning("Τιμολόγιο %s: εξοφλήθηκε πλήρως από πελατολογικό αλλά δεν άλλαξε κατάσταση", invoice_number)
            message = f"Η κίνηση καταχωρήθηκε και το τιμολόγιο{label} εξοφλήθηκε."
        else:
            message = f"Η κίνηση καταχωρήθηκε. Απομένουν {remaining:.2f} € για το τιμολόγιο{label}."
        extra = {"invoice_paid": remaining <= 0, "invoice_outstanding": float(max(remaining, Decimal(0)))}

    final = await db.trust_accounts.find_one(tenant_query(user, {"_id": acc_id}))
    return {
        "message": message,
        "account": TrustAccount.from_mongo(final),
        "transaction": tx,
        **extra,
    }


@router.get("/summary")
async def trust_summary(user: dict = Depends(get_current_user)):
    total = Decimal(0)
    accounts_with_balance = 0
    async for acc in db.trust_accounts.find(tenant_query(user, {})):
        b = _dec(acc.get("balance"))
        total += b
        if b > Decimal("0.005"):
            accounts_with_balance += 1
    return {
        "total_balance": float(total),
        "accounts_with_balance": accounts_with_balance,
    }
