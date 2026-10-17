import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from db import db, fetch_capped
from http_utils import disclose_truncation
from models import Expense
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import is_finite_number, money_limit_error, round_half_up, valid_iso_date
from dimensions import MATTER_FIELDS, merge_expense_matter, resolve_expense_matter
from ai_helpers import ocr_receipt
from storage import upload_receipt_image, get_object

router = APIRouter(prefix="/expenses", tags=["expenses"])

LIST_LIMIT = 1000         # τα νεότερα έξοδα· αν υπάρχουν κι άλλα, η απάντηση το δηλώνει (X-List-Truncated). Τα σύνολα (ΦΠΑ, dashboard) δεν περνούν από εδώ.

DUPLICATE_MARK_MSG = "Υπάρχει ήδη έξοδο με αυτό το MARK της ΑΑΔΕ· το ίδιο παραστατικό δεν καταχωρείται δύο φορές."

async def _refuse_duplicate_mark(user: dict, mark, exclude_id: str | None = None) -> None:
    """Το ίδιο MARK δεν καταχωρείται δύο φορές στο ίδιο γραφείο.

    Ο έλεγχος γίνεται εδώ και όχι μόνο από το unique index της βάσης: αν το index δεν μπορέσει να
    δημιουργηθεί (π.χ. υπάρχοντα διπλά στα δεδομένα) η εφαρμογή ξεκινά ούτως ή άλλως, και η
    μοναδικότητα πρέπει να ισχύει. Το index μένει ως δεύτερη γραμμή άμυνας για ταυτόχρονα αιτήματα.
    """
    if not mark:
        return
    query = {"mydata_mark": mark}
    if exclude_id:
        try:
            query["_id"] = {"$ne": ObjectId(exclude_id)}
        except Exception:  # noqa: BLE001 — το άκυρο id απορρίπτεται παρακάτω ως «δεν βρέθηκε»
            pass
    if await db.expenses.find_one(tenant_query(user, query)):
        raise HTTPException(status_code=409, detail=DUPLICATE_MARK_MSG)


def _validate_expense(payload: Expense) -> None:
    """Έλεγχος στη διαδρομή, όχι στο μοντέλο: το μοντέλο διαβάζει και υπάρχουσες εγγραφές.

    * Ημερομηνία ΕΕΕΕ-ΜΜ-ΗΗ και τίποτε άλλο — οι περίοδοι ΦΠΑ και το dashboard τις συγκρίνουν ως κείμενα.
    * Ποσά πεπερασμένα και μέχρι το όριο (δύο `1e308` αθροίζονταν σε `inf` και το dashboard απαντούσε 500).
    * Το ΦΠΑ έχει το πρόσημο του ποσού και δεν το ξεπερνά: αλλιώς η καθαρή δαπάνη βγαίνει αρνητική και το ΦΠΑ εισροών
      φουσκώνει στο Φ2. Το αρνητικό ποσό με αρνητικό ΦΠΑ είναι πιστωτικό προμηθευτή και επιτρέπεται.
    * Το ρητό ποσοστό εκπτωσιμότητας του ΦΠΑ, αν δοθεί, είναι αριθμός από 0 έως 100 (το κενό σημαίνει «κανόνας κατηγορίας»).
    * Ποσό και ΦΠΑ στρογγυλοποιούνται στο λεπτό (μισό προς τα πάνω) πριν αποθηκευτούν: με περισσότερα δεκαδικά το
      «καθαρό κόστος + ΦΠΑ που εκπίπτει = ό,τι πληρώθηκε» έχανε το λεπτό, και τα δύο ποσά της δήλωσης στρογγυλοποιούνταν χωριστά.
    """
    if not valid_iso_date(payload.date):
        raise HTTPException(status_code=400, detail="Μη έγκυρη ημερομηνία — αναμένεται μορφή ΕΕΕΕ-ΜΜ-ΗΗ.")
    for label, value in (("Ποσό", payload.amount), ("ΦΠΑ", payload.vat_amount)):
        problem = money_limit_error(value, label, negative_ok=True)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    if payload.amount * payload.vat_amount < 0 or abs(payload.vat_amount) > abs(payload.amount):
        raise HTTPException(status_code=400, detail="Το ΦΠΑ πρέπει να έχει το ίδιο πρόσημο με το ποσό και να μην το ξεπερνά.")
    pct = payload.vat_deductible_pct
    if pct is not None and not (is_finite_number(pct) and 0 <= pct <= 100):
        raise HTTPException(status_code=400, detail="Το ποσοστό εκπτωσιμότητας του ΦΠΑ πρέπει να είναι αριθμός από 0 έως 100.")
    payload.amount = round_half_up(payload.amount)
    payload.vat_amount = round_half_up(payload.vat_amount)


async def _apply_matter(user: dict, payload: Expense, stored: dict | None = None, sent: set | None = None) -> None:
    """Ελέγχει τις συνδέσεις του εξόδου (υπόθεση, πελάτης, δικηγόρος) μέσα στο γραφείο του χρήστη και γράφει στο `payload`
    τις τελικές τιμές — π.χ. τον πελάτη που προκύπτει από την υπόθεση. Οι κανόνες ζουν στο `dimensions.py`.

    Στην ενημέρωση (`stored`) το αίτημα μπορεί να στείλει μόνο μέρος της ομάδας· ό,τι λείπει συμπληρώνεται από το
    αποθηκευμένο έξοδο (βλ. `merge_expense_matter`) και μόνο οι αναφορές που αλλάζουν ελέγχονται ξανά."""
    values = {field: getattr(payload, field) for field in MATTER_FIELDS}
    if stored is not None:
        values = await merge_expense_matter(user, stored, sent or set(), values)
    resolved = await resolve_expense_matter(user, **values, stored=stored)
    for field, value in resolved.items():
        setattr(payload, field, value)


@router.get("")
async def list_expenses(response: Response, user: dict = Depends(get_current_user)):
    docs, cut = await fetch_capped(db.expenses.find(tenant_query(user)).sort("created_at", -1), LIST_LIMIT)
    disclose_truncation(response, cut, LIST_LIMIT)
    return [Expense.from_mongo(d) for d in docs]


@router.post("")
async def create_expense(payload: Expense, user: dict = Depends(get_current_user)):
    _validate_expense(payload)
    await _apply_matter(user, payload)
    payload.id = None
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    await _refuse_duplicate_mark(user, payload.mydata_mark)
    try:
        result = await db.expenses.insert_one(payload.to_mongo())
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=DUPLICATE_MARK_MSG)
    doc = await db.expenses.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Expense.from_mongo(doc)


@router.put("/{expense_id}")
async def update_expense(expense_id: str, payload: Expense, user: dict = Depends(get_current_user)):
    """Αντικαθιστά τα στοιχεία του εξόδου. Τα πεδία της Φάσης 1 (σε ποιον ανήκει, ποσοστό ΦΠΑ) αλλάζουν **μόνο αν έρθουν στο
    αίτημα**: μια καρτέλα browser ανοιγμένη πριν την ενημέρωση, ή ένας εξωτερικός πελάτης του API, που δεν τα ξέρει, δεν
    πρέπει να σβήνει σιωπηλά την ταξινόμηση που έκανε ο λογιστής. Αν έρθει `scope`, το αίτημα ορίζει ολόκληρη την ομάδα
    (ό,τι λείπει από αυτήν παίρνει την προεπιλογή του)· αν έρθει μόνο κάποιο άλλο μέλος της (π.χ. `lawyer_id` ή
    `rebillable`), αλλάζει μόνο αυτό και τα υπόλοιπα μένουν όπως ήταν. Το ρητό ποσοστό ΦΠΑ `null` το αφαιρεί."""
    _validate_expense(payload)
    sent = set(payload.model_fields_set)          # αντίγραφο: οι εκχωρήσεις παρακάτω προσθέτουν πεδία στο αρχικό σύνολο
    matter_sent = any(f in sent for f in MATTER_FIELDS)
    stored = await db.expenses.find_one(tenant_query(user, {"_id": ObjectId(expense_id)}))
    if not stored:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    if matter_sent:
        await _apply_matter(user, payload, stored, sent)
    payload.id = expense_id
    data = payload.to_mongo()
    data.pop("_id", None)
    data.pop("firm_id", None)
    if not matter_sent:
        for field in MATTER_FIELDS:
            data.pop(field, None)
    update = {"$set": data}
    if "vat_deductible_pct" in sent and payload.vat_deductible_pct is None:
        update["$unset"] = {"vat_deductible_pct": ""}     # `to_mongo` παραλείπει τα None· χωρίς αυτό το κενό δεν θα έσβηνε το ποσοστό
    await _refuse_duplicate_mark(user, payload.mydata_mark, exclude_id=expense_id)
    try:
        result = await db.expenses.update_one(tenant_query(user, {"_id": ObjectId(expense_id)}), update)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=DUPLICATE_MARK_MSG)
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    doc = await db.expenses.find_one(tenant_query(user, {"_id": ObjectId(expense_id)}))
    return Expense.from_mongo(doc)


@router.delete("/{expense_id}")
async def delete_expense(expense_id: str, user: dict = Depends(get_current_user)):
    result = await db.expenses.delete_one(tenant_query(user, {"_id": ObjectId(expense_id)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το έξοδο δεν βρέθηκε")
    return {"message": "Το έξοδο διαγράφηκε"}


class OcrRequest(BaseModel):
    image_base64: str


@router.post("/ocr-parse")
async def ocr_parse_expense(payload: OcrRequest, user: dict = Depends(get_current_user)):
    try:
        parsed = await ocr_receipt(payload.image_base64)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία ανάλυσης απόδειξης: {e}")
    receipt_path = None
    try:
        image_bytes = base64.b64decode(payload.image_base64)
        receipt_path = upload_receipt_image(image_bytes)
    except Exception as e:
        logging.getLogger(__name__).warning(f"Receipt storage failed: {e}")
    return {"parsed": parsed, "receipt_image_url": receipt_path}


@router.get("/files/{path:path}")
async def get_receipt_file(path: str, user: dict = Depends(get_current_user)):
    try:
        data, content_type = get_object(path)
    except Exception:
        raise HTTPException(status_code=404, detail="Το αρχείο δεν βρέθηκε")
    return Response(content=data, media_type=content_type)
