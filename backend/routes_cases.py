from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from datetime import date, datetime, timezone
from bson import ObjectId
from db import db, fetch_capped
from http_utils import disclose_truncation
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from dimensions import canonical_id, resolve_case_client, resolve_case_lawyer
from models import Case
from tax_utils import athens_today

router = APIRouter(prefix="/cases", tags=["cases"])

CASE_TYPES = ["Αστικό", "Ποινικό", "Διοικητικό", "Εμπορικό", "Οικογενειακό", "Εργατικό", "Ακίνητα/Κτηματολόγιο", "Άλλο"]
STATUS_LABELS = {"active": "Σε εξέλιξη", "pending": "Σε αναμονή", "completed": "Ολοκληρώθηκε", "archived": "Αρχειοθετήθηκε"}
CASES_LIST_LIMIT = 500          # οι νεότερες υποθέσεις· αν υπάρχουν κι άλλες, η απάντηση το δηλώνει (X-List-Truncated)
CASE_SUBLIST_LIMIT = 100        # γραμμάτια και παράβολα που δείχνει η σελίδα της υπόθεσης· τα ΣΥΝΟΛΑ καλύπτουν πάντα όλα


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _next_case_number(firm_id: str):
    from numbering import next_in_series
    year = athens_today().year
    return await next_in_series(db.cases, f"case-{firm_id}-{year}", firm_id, "ΥΠΘ", year)


async def log_case_event(case_id: str, event: str, user: dict | None = None):
    try:
        if user is None:
            case = await db.cases.find_one({"_id": ObjectId(case_id)}, {"firm_id": 1})
            user = {"firm_id": (case or {}).get("firm_id")}
        await db.cases.update_one(
            tenant_query(user, {"_id": ObjectId(case_id)}),
            {"$push": {"history": {"event": event, "at": _now()}}},
        )
    except Exception:
        pass


class NotePayload(BaseModel):
    text: str


class StatusPayload(BaseModel):
    status: str


class NextActionPayload(BaseModel):
    next_action: str
    next_action_date: str = ""


@router.get("/types/list")
async def case_types(user: dict = Depends(get_current_user)):
    return CASE_TYPES


@router.get("")
async def list_cases(response: Response, user: dict = Depends(get_current_user)):
    docs, cut = await fetch_capped(db.cases.find(tenant_query(user)).sort("created_at", -1), CASES_LIST_LIMIT)
    disclose_truncation(response, cut, CASES_LIST_LIMIT)
    return [Case.from_mongo(d) for d in docs]


@router.post("")
async def create_case(payload: Case, user: dict = Depends(get_current_user)):
    payload.lawyer_id = await resolve_case_lawyer(user, payload.lawyer_id)      # υπαρκτός δικηγόρος ΤΟΥ γραφείου, ή κενό
    named = "client_name" in payload.model_fields_set
    payload.client_id, client_name = await resolve_case_client(user, payload.client_id)   # υπαρκτός πελάτης ΤΟΥ γραφείου, ή κενό
    if client_name and not named:
        payload.client_name = client_name
    payload.number = await _next_case_number(firm_id_for(user))
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    payload.history = [{"event": "Δημιουργία υπόθεσης", "at": _now()}]
    result = await db.cases.insert_one(payload.to_mongo())
    doc = await db.cases.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Case.from_mongo(doc)


async def _case_total(collection, user: dict, case_id: str, field: str) -> float:
    """Το άθροισμα ενός πεδίου πάνω σε ΟΛΑ τα (μη ακυρωμένα) έγγραφα της υπόθεσης — όχι στα όσα δείχνει η λίστα.

    Πριν, τα «Αμοιβές» και «Έξοδα» της υπόθεσης αθροίζονταν από τα πρώτα 100 γραμμάτια/παράβολα που επιστρέφονταν για
    εμφάνιση: μια υπόθεση με 101 γραμμάτια έδειχνε σύνολο χωρίς το παλαιότερο, δίχως καμία ένδειξη."""
    total = 0.0
    async for doc in collection.find(tenant_query(user, {"case_id": case_id, "status": {"$ne": "cancelled"}}), {field: 1}):
        total += float(doc.get(field) or 0)
    return round(total, 2)


async def _linked_totals(user: dict, case_id: str) -> dict:
    """Τα τιμολόγια και τα έξοδα που έχουν συνδεθεί με την υπόθεση (Φάση 1), χωριστά από τα γραμμάτια/παράβολα.

    * Τιμολόγια: μόνο τα εκδοθέντα και εξοφλημένα (όπως στο dashboard) — ούτε πρόχειρα ούτε ακυρωμένα — με την **καθαρή**
      αξία τους. Τα πιστωτικά έχουν αρνητική αξία και την μειώνουν.
    * Έξοδα: όλα όσα έχουν δηλωθεί ως κόστος της υπόθεσης, με το ΦΠΑ τους (το ακαθάριστο ποσό που πληρώθηκε)."""
    invoices = {"count": 0, "subtotal": 0.0}
    async for doc in db.invoices.find(
            tenant_query(user, {"case_id": case_id, "status": {"$in": ["issued", "paid"]}}), {"subtotal": 1}):
        invoices["count"] += 1
        invoices["subtotal"] += float(doc.get("subtotal") or 0)
    expenses = {"count": 0, "total": 0.0}
    async for doc in db.expenses.find(tenant_query(user, {"case_id": case_id}), {"amount": 1}):
        expenses["count"] += 1
        expenses["total"] += float(doc.get("amount") or 0)
    return {"invoices": {**invoices, "subtotal": round(invoices["subtotal"], 2)},
            "expenses": {**expenses, "total": round(expenses["total"], 2)}}


@router.get("/{case_id}")
async def get_case(case_id: str, response: Response, user: dict = Depends(get_current_user)):
    case_id = canonical_id(case_id)          # τα έγγραφα που συνδέονται με την υπόθεση τη φέρουν σε πεζά hex
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    grammatia, cut_g = await fetch_capped(
        db.grammatia.find(tenant_query(user, {"case_id": case_id})).sort("created_at", -1), CASE_SUBLIST_LIMIT)
    paravola, cut_p = await fetch_capped(
        db.paravola.find(tenant_query(user, {"case_id": case_id})).sort("created_at", -1), CASE_SUBLIST_LIMIT)
    disclose_truncation(response, cut_g or cut_p, CASE_SUBLIST_LIMIT)
    from models import Grammatio, Paravolo
    return {
        "case": Case.from_mongo(doc),
        "grammatia": [Grammatio.from_mongo(g) for g in grammatia],
        "paravola": [Paravolo.from_mongo(p) for p in paravola],
        "fees_total": await _case_total(db.grammatia, user, case_id, "total"),
        "expenses_total": await _case_total(db.paravola, user, case_id, "amount"),
        "linked": await _linked_totals(user, case_id),
    }


@router.put("/{case_id}")
async def update_case(case_id: str, payload: Case, user: dict = Depends(get_current_user)):
    case_id = canonical_id(case_id)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    data = payload.model_dump(exclude={"id", "number", "history", "notes", "created_at", "created_by", "firm_id"})
    sent = set(payload.model_fields_set)
    # Υπεύθυνος δικηγόρος και πελάτης: αλλάζουν μόνο αν έρθουν στο αίτημα (μια καρτέλα ανοιγμένη πριν την ενημέρωση, ή ένας
    # πελάτης του API που αλλάζει μόνο τον τίτλο, δεν τα ξέρει και δεν πρέπει να τα σβήνει — ο πελάτης μάλιστα μεταφέρεται
    # στα έξοδα της υπόθεσης) και ελέγχονται μόνο αν ΑΛΛΑΞΑΝ: μια υπόθεση με δικηγόρο που έφυγε από το γραφείο, ή με πελάτη
    # που διαγράφηκε, πρέπει να μπορεί να επεξεργαστεί χωρίς να εμποδίζεται από παλιά τιμή που δεν άγγιξε ο χρήστης.
    previous_lawyer = doc.get("lawyer_id") or ""
    if "lawyer_id" not in sent:
        data.pop("lawyer_id", None)
    elif canonical_id(payload.lawyer_id) != canonical_id(previous_lawyer):
        data["lawyer_id"] = await resolve_case_lawyer(user, payload.lawyer_id)
    else:
        data["lawyer_id"] = previous_lawyer
    previous_client = doc.get("client_id") or ""
    derived_name = None
    if "client_id" not in sent:
        data.pop("client_id", None)
    elif canonical_id(payload.client_id) != canonical_id(previous_client):
        data["client_id"], derived_name = await resolve_case_client(user, payload.client_id)
    else:
        data["client_id"] = previous_client
    if "client_name" not in sent:           # το όνομα ακολουθεί τον πελάτη: ούτε σβήνεται ούτε μένει το όνομα του προηγούμενου
        if derived_name is None:
            data.pop("client_name", None)
        else:
            data["client_name"] = derived_name
    await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$set": data})
    # Ο πελάτης ενός εξόδου υπόθεσης προκύπτει πάντα από την υπόθεση (βλ. dimensions.py): αν αλλάξει ο πελάτης της
    # υπόθεσης, αλλάζει μαζί και στα έξοδά της — αλλιώς η κερδοφορία ανά πελάτη θα έβλεπε το κόστος στον παλιό.
    new_client = data["client_id"] if "client_id" in data else previous_client
    if new_client != previous_client:
        await db.expenses.update_many(
            tenant_query(user, {"case_id": case_id, "scope": "case"}), {"$set": {"client_id": new_client}})
    await log_case_event(case_id, "Επεξεργασία στοιχείων υπόθεσης", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.delete("/{case_id}")
async def delete_case(case_id: str, user: dict = Depends(get_current_user)):
    case_id = canonical_id(case_id)
    # Έξοδα και τιμολόγια που έχουν συνδεθεί με την υπόθεση δεν μένουν «ορφανά»: η διαγραφή θα έσβηνε από τα στοιχεία
    # κερδοφορίας το πού ανήκει κάθε κόστος και κάθε έσοδο. Η αρχειοθέτηση (status «archived») τα κρατά όλα.
    linked_expenses = await db.expenses.count_documents(tenant_query(user, {"case_id": case_id}))
    linked_invoices = await db.invoices.count_documents(tenant_query(user, {"case_id": case_id}))
    if linked_expenses or linked_invoices:
        raise HTTPException(status_code=409, detail=(
            f"Η υπόθεση έχει συνδεδεμένα {linked_expenses} έξοδα και {linked_invoices} τιμολόγια και δεν διαγράφεται. "
            "Στα τιμολόγια μετρούν και τα πρόχειρα και τα ακυρωμένα, που η σύνοψη της υπόθεσης δεν δείχνει. "
            "Αρχειοθετήστε την, ή αποσυνδέστε πρώτα τα παραστατικά."))
    result = await db.cases.delete_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    return {"message": "Η υπόθεση διαγράφηκε"}


@router.post("/{case_id}/notes")
async def add_note(case_id: str, payload: NotePayload, user: dict = Depends(get_current_user)):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Κενή σημείωση")
    note = {"text": payload.text.strip(), "author": user.get("name") or user["email"], "at": _now()}
    result = await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$push": {"notes": note}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, "Προσθήκη σημείωσης", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.post("/{case_id}/status")
async def change_status(case_id: str, payload: StatusPayload, user: dict = Depends(get_current_user)):
    if payload.status not in STATUS_LABELS:
        raise HTTPException(status_code=400, detail="Μη έγκυρη κατάσταση")
    result = await db.cases.update_one(tenant_query(user, {"_id": ObjectId(case_id)}), {"$set": {"status": payload.status}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, f"Αλλαγή κατάστασης σε «{STATUS_LABELS[payload.status]}»", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)


@router.post("/{case_id}/next-action")
async def set_next_action(case_id: str, payload: NextActionPayload, user: dict = Depends(get_current_user)):
    result = await db.cases.update_one(
        tenant_query(user, {"_id": ObjectId(case_id)}),
        {"$set": {"next_action": payload.next_action, "next_action_date": payload.next_action_date}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    await log_case_event(case_id, f"Ορισμός επόμενης ενέργειας: {payload.next_action}" if payload.next_action else "Καθαρισμός επόμενης ενέργειας", user)
    doc = await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))
    return Case.from_mongo(doc)
