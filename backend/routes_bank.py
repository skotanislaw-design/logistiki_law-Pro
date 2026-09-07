"""Bank Statement Import + Auto-Reconciliation.
Δέχεται CSV από όλες τις μεγάλες ελληνικές τράπεζες (Πειραιώς, Alpha, Eurobank, ΕΤΕ, Attica).
Auto-matches κάθε κίνηση με ανοιχτό τιμολόγιο βάσει (ποσό + ημερομηνία + name/reference).
"""
import csv
import io
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from bson import ObjectId
from db import db
from models import BankTransaction
from auth import get_current_user, require_admin
from pydantic import BaseModel
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/bank", tags=["bank"])


def _parse_amount(s: str) -> float:
    if s is None:
        return 0.0
    s = str(s).strip().replace("€", "").replace(" ", "")
    if not s or s == "-":
        return 0.0
    # Ελληνικό format: 1.234,56 / Αγγλικό: 1,234.56
    if "," in s and "." in s:
        # Το τελευταίο decimal είναι είτε , είτε .
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # ISO-like fallback
    m = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1).replace("/", "-"), "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return ""


BANK_COLUMN_MAP = {
    # Universal keys → possible header names in bank CSVs
    "date": ["ημερομηνία", "date", "ημ/νία", "ημερομηνια", "ημ.συναλ.", "ημ. συναλλαγής", "posting date", "value date"],
    "description": ["αιτιολογία", "description", "περιγραφή", "λεπτομέρειες", "narrative", "λόγος"],
    "reference": ["αριθμός", "reference", "αναφορά", "αρ.συναλ", "transaction id"],
    "amount": ["ποσό", "amount", "χρέωση/πίστωση", "κίνηση", "συνολο"],
    "credit": ["πίστωση", "credit", "εισπραξη"],
    "debit": ["χρέωση", "debit", "πληρωμη"],
    "balance": ["υπόλοιπο", "balance"],
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-zα-ωάέήίόύώϊϋΐΰ0-9]", "", (s or "").lower())


def _match_column(header: str) -> str:
    n = _norm(header)
    for key, candidates in BANK_COLUMN_MAP.items():
        for c in candidates:
            if _norm(c) == n or _norm(c) in n:
                return key
    return ""


def _parse_csv(content: bytes) -> list[dict]:
    """Επιστρέφει list από dicts με keys: date, description, reference, amount, balance."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1253", "iso-8859-7", "windows-1252"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(status_code=400, detail="Αποτυχία αποκωδικοποίησης CSV — αποθηκεύστε το ως UTF-8.")

    # Detect delimiter
    delim = ","
    for cand in [";", ",", "\t", "|"]:
        if text[:2000].count(cand) > text[:2000].count(delim):
            delim = cand

    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="Το αρχείο δεν έχει επαρκή δεδομένα.")

    # Find header row (μπορεί να μην είναι το 1ο — μερικές τράπεζες βάζουν metadata πάνω)
    header_idx = 0
    for i, row in enumerate(rows[:10]):
        matches = sum(1 for c in row if _match_column(c))
        if matches >= 2:
            header_idx = i
            break

    header = rows[header_idx]
    col_map = {i: _match_column(h) for i, h in enumerate(header)}
    if not any(col_map.values()):
        raise HTTPException(status_code=400, detail="Δεν αναγνωρίστηκαν στήλες. Ελέγξτε ότι το αρχείο περιέχει: Ημερομηνία, Αιτιολογία, Ποσό.")

    result = []
    for row in rows[header_idx + 1:]:
        rec = {"date": "", "description": "", "reference": "", "amount": 0.0, "balance": None}
        credit_val = None
        debit_val = None
        for i, v in enumerate(row):
            key = col_map.get(i, "")
            if key == "date":
                rec["date"] = _parse_date(v)
            elif key == "description":
                rec["description"] = (rec["description"] + " " + str(v)).strip()
            elif key == "reference":
                rec["reference"] = str(v).strip()
            elif key == "amount":
                rec["amount"] = _parse_amount(v)
            elif key == "credit":
                credit_val = _parse_amount(v)
            elif key == "debit":
                debit_val = _parse_amount(v)
            elif key == "balance":
                rec["balance"] = _parse_amount(v)
        if credit_val is not None or debit_val is not None:
            rec["amount"] = (credit_val or 0) - (debit_val or 0)
        if rec["date"] and (rec["amount"] != 0 or rec["description"]):
            result.append(rec)
    return result


async def _find_match(tx: dict, invoices: list[dict]) -> tuple[dict, int]:
    """Επιστρέφει (best_invoice_or_None, confidence 0-100)."""
    if tx["amount"] <= 0:
        return None, 0  # Δεν κάνει matching για χρεώσεις — μόνο για εισπράξεις
    best = None
    best_score = 0

    tx_desc = (tx.get("description", "") + " " + tx.get("reference", "")).lower()

    for inv in invoices:
        score = 0
        # 1. Amount match (±0.01)
        if abs(inv.get("total", 0) - tx["amount"]) < 0.01:
            score += 50
        elif abs(inv.get("total", 0) - tx["amount"]) < 1:
            score += 30
        else:
            continue  # χωρίς amount match, δεν το θεωρώ ίδιο

        # 2. Date proximity (±60 days)
        try:
            inv_date = datetime.fromisoformat(inv.get("date", "")[:10]).date()
            tx_date = datetime.fromisoformat(tx["date"]).date()
            days_diff = abs((tx_date - inv_date).days)
            if days_diff <= 7:
                score += 30
            elif days_diff <= 30:
                score += 20
            elif days_diff <= 60:
                score += 10
        except Exception:
            pass

        # 3. Client name in description
        cname = (inv.get("client_name", "") or "").lower()
        if cname:
            first_word = cname.split()[0] if cname.split() else ""
            if first_word and len(first_word) > 3 and first_word in tx_desc:
                score += 15
            ratio = SequenceMatcher(None, cname, tx_desc).ratio()
            if ratio > 0.4:
                score += int(ratio * 10)

        # 4. Invoice number in description
        inv_num = (inv.get("number", "") or "").lower()
        if inv_num and inv_num in tx_desc:
            score += 20

        score = min(100, score)
        if score > best_score:
            best = inv
            best_score = score

    if best_score >= 60:  # threshold για auto-match
        return best, best_score
    return None, best_score


@router.post("/import")
async def import_bank_statement(bank: str = "Άγνωστη", file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Κενό αρχείο")

    parsed = _parse_csv(content)
    if not parsed:
        raise HTTPException(status_code=400, detail="Δεν βρέθηκαν εγκυρες κινήσεις.")

    unpaid_invoices = await db.invoices.find(tenant_query(user, {"status": "issued"})).to_list(2000)

    created, skipped, auto_matched = 0, 0, 0
    for rec in parsed:
        # Dedup με βάση (date, amount, description)
        dedup_key = f"{rec['date']}|{rec['amount']:.2f}|{rec['description'][:80]}"
        existing = await db.bank_transactions.find_one(tenant_query(user, {"external_id": dedup_key}))
        if existing:
            skipped += 1
            continue

        tx = BankTransaction(
            firm_id=firm_id_for(user),
            date=rec["date"],
            description=rec["description"][:500],
            reference=rec["reference"],
            amount=rec["amount"],
            balance_after=rec.get("balance"),
            bank=bank,
            external_id=dedup_key,
            imported_by=user["email"],
        )

        # Auto-match
        match, confidence = await _find_match(rec, unpaid_invoices)
        if match:
            tx.matched_invoice_id = str(match["_id"])
            tx.matched_invoice_number = match.get("number", "")
            tx.match_confidence = confidence
            tx.match_status = "auto_matched"
            auto_matched += 1

        await db.bank_transactions.insert_one(tx.to_mongo())
        created += 1

    return {
        "message": f"Εισήχθησαν {created} κινήσεις. Αυτόματα συνδέθηκαν με τιμολόγια: {auto_matched}.",
        "created": created,
        "skipped": skipped,
        "auto_matched": auto_matched,
    }


@router.get("/transactions")
async def list_transactions(status: str = "all", user: dict = Depends(get_current_user)):
    query = {}
    if status != "all":
        query["match_status"] = status
    docs = await db.bank_transactions.find(tenant_query(user, query)).sort("date", -1).limit(500).to_list(500)
    return [BankTransaction.from_mongo(d) for d in docs]


@router.post("/reconcile/{tx_id}")
async def reconcile(tx_id: str, invoice_id: str = "", ignore: bool = False, user: dict = Depends(get_current_user)):
    try:
        tx = await db.bank_transactions.find_one(tenant_query(user, {"_id": ObjectId(tx_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")
    if not tx:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")

    if ignore:
        await db.bank_transactions.update_one(
            tenant_query(user, {"_id": ObjectId(tx_id)}),
            {"$set": {"match_status": "ignored", "matched_invoice_id": None, "matched_invoice_number": None}},
        )
        return {"message": "Η κίνηση σημειώθηκε ως αγνοητέα"}

    if not invoice_id:
        raise HTTPException(status_code=400, detail="Δώστε invoice_id")

    try:
        inv = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if not inv:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")

    await db.bank_transactions.update_one(
        tenant_query(user, {"_id": ObjectId(tx_id)}),
        {"$set": {
            "matched_invoice_id": invoice_id,
            "matched_invoice_number": inv.get("number", ""),
            "match_status": "manual_matched",
            "match_confidence": 100,
        }},
    )
    # Auto-mark το τιμολόγιο ως εξοφλημένο αν είναι σε issued
    from invoice_services import mark_paid
    await mark_paid(firm_id_for(user), invoice_id, paid_via="bank_manual")
    return {"message": "Συνδέθηκε και το τιμολόγιο σημειώθηκε ως εξοφλημένο."}


@router.post("/confirm-auto-matches")
async def confirm_auto_matches(user: dict = Depends(get_current_user)):
    """Επιβεβαιώνει όλα τα auto_matched — σημαίνει τα τιμολόγια ως paid."""
    now = datetime.now(timezone.utc).isoformat()
    confirmed = 0
    async for tx in db.bank_transactions.find(tenant_query(user, {"match_status": "auto_matched"})):
        if tx.get("matched_invoice_id"):
            try:
                result = await db.invoices.update_one(
                    tenant_query(user, {"_id": ObjectId(tx["matched_invoice_id"]), "status": "issued"}),
                    {"$set": {"status": "paid", "paid_at": now}},
                )
                if result.modified_count > 0:
                    confirmed += 1
                await db.bank_transactions.update_one(
                    tenant_query(user, {"_id": tx["_id"]}),
                    {"$set": {"match_status": "manual_matched", "match_confidence": 100}},
                )
            except Exception:
                continue
    return {"message": f"Επιβεβαιώθηκαν {confirmed} συνδέσεις. Τα αντίστοιχα τιμολόγια εξοφλήθηκαν.", "confirmed": confirmed}


# ------------------------------------------------- open banking (PSD2) -----

class LinkRequest(BaseModel):
    institution_id: str
    redirect_url: str = ""


@router.get("/openbanking/status")
async def openbanking_status(user: dict = Depends(get_current_user)):
    from openbanking import openbanking_ready, requisition_expiring
    settings = await db.settings.find_one(tenant_query(user)) or {}
    return {
        "configured": openbanking_ready(settings),
        "enabled": bool(settings.get("openbanking_enabled")),
        "institution": settings.get("gocardless_institution_name", ""),
        "accounts": settings.get("gocardless_account_ids") or [],
        "valid_until": settings.get("gocardless_access_valid_until", ""),
        "expiring_soon": requisition_expiring(settings),
        "last_sync": settings.get("bank_sync_last_date", ""),
    }


@router.get("/openbanking/institutions")
async def openbanking_institutions(country: str = "GR", user: dict = Depends(require_admin)):
    from openbanking import list_institutions, OpenBankingError
    settings = await db.settings.find_one(tenant_query(user)) or {}
    try:
        return await list_institutions(settings, country)
    except OpenBankingError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/openbanking/link")
async def openbanking_link(payload: LinkRequest, user: dict = Depends(require_admin)):
    """Ξεκινά τη σύνδεση: ο χρήστης ανοίγει το link και εγκρίνει στην τράπεζά του."""
    import os
    from openbanking import create_requisition, OpenBankingError
    settings = await db.settings.find_one(tenant_query(user)) or {}
    redirect = payload.redirect_url or (settings.get("public_url") or os.environ.get("PUBLIC_URL", "")).rstrip("/") + "/settings"
    if not redirect.startswith("http"):
        raise HTTPException(status_code=400, detail="Ορίστε PUBLIC_URL ώστε η τράπεζα να μπορεί να σας επιστρέψει στην εφαρμογή.")
    try:
        result = await create_requisition(settings, payload.institution_id, redirect)
    except OpenBankingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.settings.update_one(tenant_query(user), {"$set": {
        "gocardless_requisition_id": result["requisition_id"],
        "gocardless_institution_id": payload.institution_id,
    }})
    return result


@router.post("/openbanking/complete")
async def openbanking_complete(user: dict = Depends(require_admin)):
    """Μετά την έγκριση: καταγράφει τους λογαριασμούς που παραχώρησε ο χρήστης."""
    from datetime import date as _d, timedelta as _td
    from openbanking import get_requisition, account_details, OpenBankingError
    settings = await db.settings.find_one(tenant_query(user)) or {}
    requisition_id = settings.get("gocardless_requisition_id")
    if not requisition_id:
        raise HTTPException(status_code=400, detail="Δεν έχει ξεκινήσει σύνδεση με τράπεζα.")
    try:
        requisition = await get_requisition(settings, requisition_id)
    except OpenBankingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    accounts = requisition.get("accounts") or []
    if not accounts:
        raise HTTPException(status_code=400,
                            detail=f"Η σύνδεση δεν έχει ολοκληρωθεί (κατάσταση: {requisition.get('status')}). "
                                   "Ολοκληρώστε την έγκριση στη σελίδα της τράπεζας και δοκιμάστε ξανά.")
    described = []
    for account_id in accounts:
        try:
            described.append({"id": account_id, **await account_details(settings, account_id)})
        except OpenBankingError:
            described.append({"id": account_id, "iban": "", "name": ""})
    await db.settings.update_one(tenant_query(user), {"$set": {
        "gocardless_account_ids": accounts,
        "openbanking_enabled": True,
        "gocardless_access_valid_until": (_d.today() + _td(days=90)).isoformat(),
    }})
    return {"accounts": described, "message": f"Συνδέθηκαν {len(accounts)} λογαριασμοί. "
                                              "Οι κινήσεις θα κατεβαίνουν αυτόματα."}


@router.post("/openbanking/disconnect")
async def openbanking_disconnect(user: dict = Depends(require_admin)):
    await db.settings.update_one(tenant_query(user), {"$set": {
        "openbanking_enabled": False, "gocardless_account_ids": [],
        "gocardless_requisition_id": "", "gocardless_access_valid_until": "",
    }})
    return {"message": "Η σύνδεση με την τράπεζα διακόπηκε. Οι ήδη κατεβασμένες κινήσεις παραμένουν."}


@router.get("/summary")
async def summary(user: dict = Depends(get_current_user)):
    total = await db.bank_transactions.count_documents(tenant_query(user))
    unmatched = await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "unmatched"}))
    auto = await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "auto_matched"}))
    manual = await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "manual_matched"}))
    auto_confirmed = await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "auto_confirmed"}))
    ignored = await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "ignored"}))
    return {"total": total, "unmatched": unmatched, "auto_matched": auto, "manual_matched": manual,
            "auto_confirmed": auto_confirmed, "ignored": ignored}
