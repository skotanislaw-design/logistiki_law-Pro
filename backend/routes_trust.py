from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from pydantic import BaseModel
from typing import Optional, Literal
from db import db
from models import TrustAccount, TrustTransaction
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/trust", tags=["trust"])


class TransactionPayload(BaseModel):
    client_id: str
    type: Literal["deposit", "withdrawal", "apply_to_invoice", "refund"]
    amount: float
    date: str
    description: str = ""
    reference: str = ""
    invoice_id: Optional[str] = None
    case_id: Optional[str] = None


async def _ensure_account(client_id: str, user: dict):
    """Επιστρέφει (ObjectId, TrustAccount) του πελάτη, τον δημιουργεί αν λείπει."""
    try:
        client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(client_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    doc = await db.trust_accounts.find_one(tenant_query(user, {"client_id": client_id}))
    if not doc:
        acc = TrustAccount(client_id=client_id, client_name=client.get("name", ""), firm_id=firm_id_for(user))
        result = await db.trust_accounts.insert_one(acc.to_mongo())
        doc = await db.trust_accounts.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return doc["_id"], TrustAccount.from_mongo(doc)


@router.get("/accounts")
async def list_accounts(user: dict = Depends(get_current_user)):
    docs = await db.trust_accounts.find(tenant_query(user, {})).sort("client_name", 1).to_list(1000)
    return [TrustAccount.from_mongo(d) for d in docs]


@router.get("/accounts/{client_id}")
async def get_account(client_id: str, user: dict = Depends(get_current_user)):
    acc_id, account = await _ensure_account(client_id, user)
    txs = await db.trust_transactions.find(tenant_query(user, {"account_id": str(acc_id)})).sort("date", -1).to_list(500)
    return {
        "account": account,
        "transactions": [TrustTransaction.from_mongo(t) for t in txs],
    }


@router.post("/transactions")
async def create_transaction(payload: TransactionPayload, user: dict = Depends(get_current_user)):
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Το ποσό πρέπει να είναι μεγαλύτερο του μηδενός.")
    acc_id, account = await _ensure_account(payload.client_id, user)
    account_id_str = str(acc_id)
    balance = float(account.balance or 0)
    deposits = float(account.total_deposits or 0)
    withdrawals = float(account.total_withdrawals or 0)

    if payload.type == "deposit":
        balance += payload.amount
        deposits += payload.amount
    else:
        if payload.amount > balance:
            raise HTTPException(
                status_code=400,
                detail=f"Ανεπαρκές υπόλοιπο πελατολογικού λογαριασμού. Διαθέσιμο: {balance:.2f} €",
            )
        balance -= payload.amount
        withdrawals += payload.amount

    invoice_number = None
    if payload.invoice_id:
        try:
            inv = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(payload.invoice_id)}))
            invoice_number = inv.get("number") if inv else None
        except Exception:
            invoice_number = None

    tx = TrustTransaction(
        account_id=account_id_str,
        client_id=payload.client_id,
        client_name=account.client_name or "",
        type=payload.type,
        amount=payload.amount,
        balance_after=round(balance, 2),
        date=payload.date,
        description=payload.description,
        reference=payload.reference,
        invoice_id=payload.invoice_id,
        invoice_number=invoice_number,
        case_id=payload.case_id,
        created_by=user["email"],
    )
    tx.firm_id = firm_id_for(user)
    await db.trust_transactions.insert_one(tx.to_mongo())
    await db.trust_accounts.update_one(
        tenant_query(user, {"_id": acc_id}),
        {"$set": {
            "balance": round(balance, 2),
            "total_deposits": round(deposits, 2),
            "total_withdrawals": round(withdrawals, 2),
        }},
    )

    if payload.type == "apply_to_invoice" and payload.invoice_id:
        try:
            inv = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(payload.invoice_id)}))
            if inv and inv.get("status") == "issued" and payload.amount >= float(inv.get("total", 0)):
                await db.invoices.update_one(
                    tenant_query(user, {"_id": ObjectId(payload.invoice_id)}),
                    {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}},
                )
        except Exception:
            pass

    updated = await db.trust_accounts.find_one(tenant_query(user, {"_id": acc_id}))
    return {
        "message": "Η κίνηση καταχωρήθηκε",
        "account": TrustAccount.from_mongo(updated),
        "transaction": tx,
    }


@router.get("/summary")
async def trust_summary(user: dict = Depends(get_current_user)):
    total = 0.0
    accounts_with_balance = 0
    async for acc in db.trust_accounts.find(tenant_query(user, {})):
        b = float(acc.get("balance", 0))
        total += b
        if b > 0.005:
            accounts_with_balance += 1
    return {
        "total_balance": round(total, 2),
        "accounts_with_balance": accounts_with_balance,
    }
