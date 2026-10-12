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
from pymongo.errors import DuplicateKeyError
from db import db
from models import BankTransaction
from auth import get_current_user, require_admin
from pydantic import BaseModel
from tenant import tenant_query, firm_id_for
from invoice_services import (
    AMOUNT_TOLERANCE, apply_bank_payment, firm_query, load_client, outstanding_amount,
)

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


MATCH_THRESHOLD = 60          # κάτω από αυτό δεν προτείνεται τιμολόγιο
AMBIGUITY_MARGIN = 10         # άλλο τιμολόγιο με σκορ τόσο κοντά ⇒ η πρόταση δεν είναι μονοσήμαντη
UNSURE_CONFIDENCE = 75        # ανώτατη «βεβαιότητα» πρότασης με διαφορά ποσού ή με δεύτερη εκδοχή


def _due(inv: dict) -> float:
    """Το ποσό που αναμένεται να εισπραχθεί: πληρωτέο μείον ό,τι εξοφλήθηκε από πελατολογικό.

    Ο πελάτης-επιχείρηση κρατά την παρακράτηση φόρου και καταθέτει λιγότερα από το σύνολο του
    τιμολογίου· αν η σύγκριση γινόταν με το σύνολο, οι εισπράξεις τους δεν θα ταίριαζαν ποτέ
    (ή θα ταίριαζαν σε λάθος τιμολόγιο). Το `_due` το υπολογίζει το `build_match_pool`.
    """
    if inv.get("_due") is not None:
        return float(inv["_due"])
    return float(inv.get("total") or 0)


def match_details(tx: dict, invoices: list[dict]) -> dict:
    """Το τιμολόγιο που πιθανότατα εξοφλεί η κατάθεση και πόσο σίγουρη είναι η πρόταση.

    Επιστρέφει {"invoice", "confidence" 0-100, "exact" (ακριβές ποσό), "ambiguous"}.

    * Μόνο ακριβές ποσό (±1 λεπτό) μπορεί να εξοφληθεί αυτόματα· διαφορά κάτω από 1 € δίνει
      πρόταση για χειροκίνητο έλεγχο, με «βεβαιότητα» ως 75.
    * Αν υπάρχει και δεύτερο τιμολόγιο εξίσου πιθανό — ή άλλο τιμολόγιο του ίδιου πελάτη με το
      ίδιο ποσό, χωρίς να αναγράφεται ο αριθμός — η πρόταση είναι `ambiguous`: ο πελάτης μπορεί
      να πληρώνει οποιοδήποτε από τα δύο, και η εξόφληση λάθος τιμολογίου μένει απαρατήρητη.
    """
    nothing = {"invoice": None, "confidence": 0, "exact": False, "ambiguous": False}
    if tx["amount"] <= 0:
        return nothing          # δεν γίνεται matching για χρεώσεις — μόνο για εισπράξεις

    tx_desc = (tx.get("description", "") + " " + tx.get("reference", "")).lower()
    candidates = []
    for inv in invoices:
        difference = abs(_due(inv) - tx["amount"])
        if difference < AMOUNT_TOLERANCE:
            score, exact = 50, True
        elif difference < 1:
            score, exact = 30, False
        else:
            continue            # χωρίς amount match, δεν το θεωρώ ίδιο

        # Ημερομηνία (±60 ημέρες)
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

        # Όνομα πελάτη στην αιτιολογία
        cname = (inv.get("client_name", "") or "").lower()
        if cname:
            first_word = cname.split()[0] if cname.split() else ""
            if first_word and len(first_word) > 3 and first_word in tx_desc:
                score += 15
            ratio = SequenceMatcher(None, cname, tx_desc).ratio()
            if ratio > 0.4:
                score += int(ratio * 10)

        # Αριθμός τιμολογίου στην αιτιολογία
        inv_num = (inv.get("number", "") or "").lower()
        numbered = bool(inv_num and inv_num in tx_desc)
        if numbered:
            score += 20

        candidates.append({"inv": inv, "score": min(100, score), "exact": exact, "numbered": numbered})

    if not candidates:
        return nothing
    best = max(candidates, key=lambda c: c["score"])        # στην ισοβαθμία, το πρώτο της λίστας
    if best["score"] < MATCH_THRESHOLD:
        return {**nothing, "confidence": best["score"]}

    rivals = [c for c in candidates if c is not best]
    # Όταν η αιτιολογία κατονομάζει τον αριθμό του μόνο ενός τιμολογίου, αυτό κερδίζει.
    close = [r for r in rivals
             if r["score"] >= best["score"] - AMBIGUITY_MARGIN and not (best["numbered"] and not r["numbered"])]
    client_id = best["inv"].get("client_id")
    same_client_same_amount = bool(
        client_id and best["exact"] and not best["numbered"]
        and any(r["exact"] and r["inv"].get("client_id") == client_id for r in rivals)
    )
    ambiguous = bool(close) or same_client_same_amount
    confidence = best["score"]
    if ambiguous or not best["exact"]:
        confidence = min(confidence, UNSURE_CONFIDENCE)
    return {"invoice": best["inv"], "confidence": confidence, "exact": best["exact"], "ambiguous": ambiguous}


async def _find_match(tx: dict, invoices: list[dict]) -> tuple[dict, int]:
    """Επιστρέφει (best_invoice_or_None, confidence 0-100)."""
    found = match_details(tx, invoices)
    return found["invoice"], found["confidence"]


async def build_match_pool(firm_id: str, settings: dict) -> list[dict]:
    """Τα ανοιχτά τιμολόγια με το ποσό που οφείλεται (`_due`), για το matching.

    Εξαιρούνται όσα έχουν ήδη συνδεθεί με άλλη κίνηση: μία κατάθεση ↔ ένα τιμολόγιο, ώστε δύο
    ίδιες καταθέσεις να μην «εξοφλούν» το ίδιο τιμολόγιο δύο φορές.
    """
    taken = set()
    async for d in db.bank_transactions.find(
        firm_query(firm_id, {"matched_invoice_id": {"$nin": [None, ""]},
                             "match_status": {"$in": ["auto_matched", "auto_confirmed", "manual_matched"]}}),
        {"matched_invoice_id": 1},
    ):
        taken.add(d["matched_invoice_id"])
    pool, clients = [], {}
    async for inv in db.invoices.find(firm_query(firm_id, {"status": "issued"})).limit(2000):
        if str(inv["_id"]) in taken or float(inv.get("total") or 0) <= 0:
            continue                              # ήδη δεσμευμένο, ή πιστωτικό (αρνητικό σύνολο)
        client_id = inv.get("client_id")
        if client_id not in clients:
            clients[client_id] = await load_client(firm_id, inv) or {}
        due, _withheld = await outstanding_amount(firm_id, inv, settings, clients[client_id])
        pool.append({**inv, "_due": due})
    return pool


def _statement_key(rec: dict) -> str:
    """Κλειδί αναγνώρισης γραμμής extrait. Δεν ταυτίζει γραμμές με διαφορετικό reference ή υπόλοιπο."""
    key = f"{rec['date']}|{rec['amount']:.2f}|{rec['description'][:80]}"
    if rec.get("reference"):
        key += f"|{rec['reference']}"
    if rec.get("balance") is not None:
        key += f"|{rec['balance']:.2f}"
    return key


def _legacy_statement_key(rec: dict) -> str:
    """Το κλειδί των εισαγωγών πριν τον γύρο 4 — αναγνωρίζεται ώστε να μη διπλογράφονται παλιές κινήσεις."""
    return f"{rec['date']}|{rec['amount']:.2f}|{rec['description'][:80]}"


@router.post("/import")
async def import_bank_statement(bank: str = "Άγνωστη", file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Κενό αρχείο")

    parsed = _parse_csv(content)
    if not parsed:
        raise HTTPException(status_code=400, detail="Δεν βρέθηκαν εγκυρες κινήσεις.")

    firm_id = firm_id_for(user)
    settings = await db.settings.find_one(tenant_query(user)) or {}
    pool = await build_match_pool(firm_id, settings)

    created, skipped, auto_matched, needs_review = 0, 0, 0, 0
    seen: dict[str, int] = {}
    for rec in parsed:
        # Δύο πανομοιότυπες γραμμές στο ίδιο αρχείο (δύο καταθέσεις €100 την ίδια μέρα με την ίδια
        # αιτιολογία) είναι δύο κινήσεις· παλιότερα η δεύτερη χανόταν ως «διπλότυπο».
        key = _statement_key(rec)
        seen[key] = seen.get(key, 0) + 1
        ordinal = seen[key]
        if ordinal == 1:
            external_id = key
            known = {"external_id": {"$in": [key, _legacy_statement_key(rec)]}}
        else:
            external_id = f"{key}#{ordinal}"
            known = {"external_id": external_id}
        if await db.bank_transactions.find_one(tenant_query(user, known)):
            skipped += 1
            continue

        tx = BankTransaction(
            firm_id=firm_id,
            date=rec["date"],
            description=rec["description"][:500],
            reference=rec["reference"],
            amount=rec["amount"],
            balance_after=rec.get("balance"),
            bank=bank,
            external_id=external_id,
            imported_by=user["email"],
        )

        found = match_details(rec, pool)
        match = found["invoice"]
        if match is not None:
            tx.matched_invoice_id = str(match["_id"])
            tx.matched_invoice_number = match.get("number", "")
            tx.match_confidence = found["confidence"]
            tx.match_status = "auto_matched"
            tx.match_ambiguous = found["ambiguous"]

        try:
            await db.bank_transactions.insert_one(tx.to_mongo())
        except DuplicateKeyError:
            skipped += 1                # ταυτόχρονη εισαγωγή του ίδιου αρχείου
            continue
        created += 1
        if match is not None:
            auto_matched += 1
            needs_review += 1 if (found["ambiguous"] or not found["exact"]) else 0
            pool = [i for i in pool if i is not match]      # το τιμολόγιο δεσμεύεται από αυτή την κίνηση

    message = f"Εισήχθησαν {created} κινήσεις. Αυτόματα συνδέθηκαν με τιμολόγια: {auto_matched}."
    if needs_review:
        message += f" Από αυτές, {needs_review} χρειάζονται έλεγχο (διαφορά ποσού ή περισσότερα από ένα πιθανά τιμολόγια)."
    return {
        "message": message,
        "created": created,
        "skipped": skipped,
        "auto_matched": auto_matched,
        "needs_review": needs_review,
    }


@router.get("/transactions")
async def list_transactions(status: str = "all", user: dict = Depends(get_current_user)):
    query = {}
    if status != "all":
        query["match_status"] = status
    docs = await db.bank_transactions.find(tenant_query(user, query)).sort("date", -1).limit(500).to_list(500)
    return [BankTransaction.from_mongo(d) for d in docs]


CONFIRMED_STATES = ("auto_confirmed", "manual_matched")
REFUSAL_STATUS = {"invoice_missing": 404, "not_a_deposit": 400}


@router.post("/reconcile/{tx_id}")
async def reconcile(tx_id: str, invoice_id: str = "", ignore: bool = False, accept_difference: bool = False,
                    user: dict = Depends(get_current_user)):
    """Χειροκίνητη συμφωνία κατάθεσης με τιμολόγιο.

    Το ποσό πρέπει να ισούται με το οφειλόμενο. Αν η διαφορά είναι αποδεκτή (π.χ. προμήθεια της
    τράπεζας) ο χρήστης το δηλώνει ρητά με `accept_difference=true` και η διαφορά καταγράφεται.
    """
    try:
        tx = await db.bank_transactions.find_one(tenant_query(user, {"_id": ObjectId(tx_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")
    if not tx:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")

    if ignore:
        if tx.get("match_status") in CONFIRMED_STATES:
            number = tx.get("matched_invoice_number")
            raise HTTPException(
                status_code=409,
                detail=f"Η κίνηση έχει ήδη συμφωνηθεί με το τιμολόγιο{' ' + number if number else ''} "
                       "και δεν μπορεί να αγνοηθεί.")
        await db.bank_transactions.update_one(
            tenant_query(user, {"_id": ObjectId(tx_id)}),
            {"$set": {"match_status": "ignored", "matched_invoice_id": None, "matched_invoice_number": None,
                      "match_ambiguous": False}},
        )
        return {"message": "Η κίνηση σημειώθηκε ως αγνοητέα"}

    if not invoice_id:
        raise HTTPException(status_code=400, detail="Δώστε invoice_id")

    settings = await db.settings.find_one(tenant_query(user)) or {}
    result = await apply_bank_payment(
        firm_id_for(user), tx, invoice_id, settings=settings, paid_via="bank_manual",
        status_on_success="manual_matched", accept_difference=accept_difference)
    if result["status"] == "refused":
        detail = result["message"]
        if result["reason"] == "amount_mismatch":
            detail += " Αν η διαφορά είναι αποδεκτή (π.χ. προμήθεια τράπεζας), επιβεβαιώστε ρητά με accept_difference=true."
        raise HTTPException(status_code=REFUSAL_STATUS.get(result["reason"], 409), detail=detail)

    message = ("Συνδέθηκε και το τιμολόγιο σημειώθηκε ως εξοφλημένο." if result["status"] == "paid"
               else "Η κατάθεση συνδέθηκε με το τιμολόγιο, που ήταν ήδη σημειωμένο ως εξοφλημένο.")
    if result["difference"]:
        message += f" Καταγράφηκε διαφορά {result['difference']:+.2f} €."
    return {"message": message, "status": result["status"], "difference": result["difference"]}


@router.post("/confirm-auto-matches")
async def confirm_auto_matches(user: dict = Depends(get_current_user)):
    """Επιβεβαιώνει τις προτάσεις που είναι ασφαλές να επιβεβαιωθούν μαζικά.

    Μαζικά περνούν μόνο οι μονοσήμαντες προτάσεις με ακριβές ποσό για ανοιχτό τιμολόγιο. Όσες έχουν
    διαφορά ποσού, δεύτερη πιθανή εκδοχή ή τιμολόγιο που δεν είναι πια ανοιχτό μένουν ως έχουν και
    αναφέρονται στην απάντηση: χρειάζονται ατομική απόφαση.
    """
    firm_id = firm_id_for(user)
    settings = await db.settings.find_one(tenant_query(user)) or {}
    confirmed, skipped = 0, []
    async for tx in db.bank_transactions.find(tenant_query(user, {"match_status": "auto_matched"})):
        if not tx.get("matched_invoice_id"):
            continue
        if tx.get("match_ambiguous"):
            skipped.append({"id": str(tx["_id"]), "reason": "ambiguous",
                            "message": "Ταιριάζουν περισσότερα από ένα τιμολόγια — επιλέξτε ατομικά."})
            continue
        result = await apply_bank_payment(
            firm_id, tx, tx["matched_invoice_id"], settings=settings, paid_via="bank_manual",
            status_on_success="manual_matched")
        if result["status"] == "refused":
            skipped.append({"id": str(tx["_id"]), "reason": result["reason"], "message": result["message"]})
        else:
            confirmed += 1
    message = f"Επιβεβαιώθηκαν {confirmed} συνδέσεις. Τα αντίστοιχα τιμολόγια εξοφλήθηκαν."
    if skipped:
        message += (f" {len(skipped)} χρειάζονται ατομικό έλεγχο (διαφορά ποσού, περισσότερα από ένα πιθανά "
                    "τιμολόγια ή τιμολόγιο που έχει ήδη εξοφληθεί).")
    return {"message": message, "confirmed": confirmed, "skipped": len(skipped), "skipped_items": skipped}


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
