from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional
from bson import ObjectId
from db import db, fetch_capped
from http_utils import content_disposition, disclose_truncation
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from dimensions import inherited_matter
from models import Grammatio, Invoice, InvoiceLineItem
from routes_cases import log_case_event
from tax_utils import athens_today, calculate_invoice_totals, money_limit_error

router = APIRouter(prefix="/grammatia", tags=["grammatia"])

LIST_LIMIT = 500          # τα νεότερα γραμμάτια· αν υπάρχουν κι άλλα, η απάντηση το δηλώνει (X-List-Truncated)

GRAMMATIA_REFERENCE = [
    {"court_type": "Ειρηνοδικείο", "procedure": "Μικροδιαφορές", "amount": 64.0},
    {"court_type": "Ειρηνοδικείο", "procedure": "Τακτική διαδικασία", "amount": 107.0},
    {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "amount": 187.0},
    {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Ασφαλιστικά μέτρα", "amount": 161.0},
    {"court_type": "Πολυμελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "amount": 342.0},
    {"court_type": "Μονομελές Εφετείο", "procedure": "Έφεση", "amount": 288.0},
    {"court_type": "Τριμελές Εφετείο", "procedure": "Έφεση", "amount": 395.0},
    {"court_type": "Άρειος Πάγος", "procedure": "Αναίρεση", "amount": 620.0},
    {"court_type": "Συμβούλιο της Επικρατείας", "procedure": "Αίτηση ακύρωσης", "amount": 620.0},
    {"court_type": "Διοικητικό Πρωτοδικείο", "procedure": "Προσφυγή", "amount": 161.0},
    {"court_type": "Διοικητικό Εφετείο", "procedure": "Έφεση", "amount": 288.0},
    {"court_type": "Πλημμελειοδικείο", "procedure": "Ποινική υπεράσπιση", "amount": 149.0},
    {"court_type": "Εξωδικαστικές ενέργειες", "procedure": "Σύνταξη/κατάθεση δικογράφου", "amount": 107.0},
]


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _next_number(firm_id: str):
    from numbering import next_in_series
    year = athens_today().year
    return await next_in_series(db.grammatia, f"grammatio-{firm_id}-{year}", firm_id, "ΓΡΠ", year)


def _validate_amounts(payload: Grammatio) -> None:
    for label, field, value in (("Η αξία αναφοράς", "Αξία αναφοράς", payload.reference_amount),
                                ("Οι εισφορές", "Εισφορές", payload.contributions_amount),
                                ("Το ΦΠΑ", "ΦΠΑ", payload.vat_amount)):
        if value < 0:
            raise HTTPException(status_code=400, detail=f"{label} δεν μπορεί να είναι αρνητική.")
        problem = money_limit_error(value, field)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    if round(payload.reference_amount + payload.contributions_amount + payload.vat_amount, 2) <= 0:
        raise HTTPException(status_code=400, detail="Το σύνολο του γραμματίου πρέπει να είναι θετικό.")


def _fee_vat_rate(reference: float, vat: float) -> Optional[int]:
    """Ο συντελεστής ΦΠΑ που εξηγεί το ΦΠΑ του γραμματίου επί της αξίας αναφοράς, ή None."""
    if round(vat, 2) == 0:
        return 0
    for rate in (24, 13, 6):
        if abs(round(reference * rate / 100, 2) - round(vat, 2)) <= 0.01:
            return rate
    return None


class IssuePayload(BaseModel):
    portal_ref: str = ""


@router.get("/reference/table")
async def reference_table(user: dict = Depends(get_current_user)):
    return GRAMMATIA_REFERENCE


@router.get("")
async def list_grammatia(response: Response, case_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = {"case_id": case_id} if case_id else {}
    docs, cut = await fetch_capped(db.grammatia.find(tenant_query(user, query)).sort("created_at", -1), LIST_LIMIT)
    disclose_truncation(response, cut, LIST_LIMIT)
    return [Grammatio.from_mongo(d) for d in docs]


@router.post("")
async def create_grammatio(payload: Grammatio, user: dict = Depends(get_current_user)):
    _validate_amounts(payload)
    payload.firm_id = firm_id_for(user)
    payload.number = await _next_number(firm_id_for(user))
    payload.created_by = user["email"]
    # Κατάσταση, έκδοση και σύνδεση με τιμολόγιο ανήκουν στον server.
    payload.status, payload.issued_at, payload.invoice_id = "draft", None, None
    payload.vat_amount = round(payload.vat_amount, 2)
    payload.total = round(payload.reference_amount + payload.contributions_amount + payload.vat_amount, 2)
    result = await db.grammatia.insert_one(payload.to_mongo())
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": result.inserted_id}))
    if payload.case_id:
        await log_case_event(payload.case_id, f"Δημιουργία γραμματίου {payload.number} ({payload.court_type})", user)
    return Grammatio.from_mongo(doc)


@router.put("/{gid}")
async def update_grammatio(gid: str, payload: Grammatio, user: dict = Depends(get_current_user)):
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Δεν επιτρέπεται επεξεργασία εκδοθέντος γραμματίου")
    _validate_amounts(payload)
    data = payload.model_dump(exclude={"id", "number", "created_at", "created_by", "status", "issued_at", "invoice_id", "firm_id"})
    data["total"] = round(payload.reference_amount + payload.contributions_amount + payload.vat_amount, 2)
    # Το φίλτρο κατάστασης είναι αναγκαίο: ανάμεσα στην ανάγνωση και στη γραφή το γραμμάτιο μπορεί να
    # εκδόθηκε (και να πήγε στο portal ή να τιμολογήθηκε), και η γραφή θα άλλαζε τα ποσά ενός εκδοθέντος.
    updated = await db.grammatia.update_one(
        tenant_query(user, {"_id": ObjectId(gid), "status": "draft"}), {"$set": data})
    if updated.matched_count == 0:
        raise HTTPException(status_code=409, detail="Το γραμμάτιο εκδόθηκε στο μεταξύ — δεν τροποποιείται πια.")
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    return Grammatio.from_mongo(doc)


@router.delete("/{gid}")
async def delete_grammatio(gid: str, user: dict = Depends(get_current_user)):
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    # Εκδοθέν γραμμάτιο έχει περάσει από το portal και ενδεχομένως έχει εισπραχθεί/τιμολογηθεί.
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο προσχέδια γραμματίων διαγράφονται· το εκδοθέν παραμένει στο αρχείο.")
    await db.grammatia.delete_one(tenant_query(user, {"_id": ObjectId(gid), "status": "draft"}))
    return {"message": "Το γραμμάτιο διαγράφηκε"}


@router.post("/{gid}/issue")
async def issue_grammatio(gid: str, payload: IssuePayload, user: dict = Depends(get_current_user)):
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if doc.get("status") == "issued":
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη εκδοθεί")
    if doc.get("status") != "draft":
        raise HTTPException(status_code=400, detail="Μόνο προσχέδια γραμματίων μπορούν να εκδοθούν.")
    claimed = await db.grammatia.update_one(
        tenant_query(user, {"_id": ObjectId(gid), "status": "draft"}),
        {"$set": {"status": "issued", "issued_at": _now(), "portal_ref": payload.portal_ref}},
    )
    if claimed.matched_count == 0:      # διπλό κλικ: το άλλο αίτημα πρόλαβε
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη εκδοθεί")
    if doc.get("case_id"):
        await log_case_event(doc["case_id"], f"Έκδοση γραμματίου {doc.get('number')}", user)
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    return Grammatio.from_mongo(doc)


@router.get("/{gid}/pdf")
async def grammatio_pdf(gid: str, user: dict = Depends(get_current_user)):
    from pdf_utils import build_grammatio_pdf
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    pdf_bytes = build_grammatio_pdf(doc, settings)
    safe = doc.get("number") or "grammatio"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(f"{safe}.pdf")},
    )


@router.post("/{gid}/to-invoice")
async def grammatio_to_invoice(gid: str, user: dict = Depends(get_current_user)):
    """Ενσωματώνει εκδοθέν γραμμάτιο σε **πρόχειρο** τιμολόγιο, με το ΦΠΑ του σε ξεχωριστή γραμμή.

    Το σύνολο του τιμολογίου ισούται με το σύνολο του γραμματίου. Η αμοιβή (αξία αναφοράς) πάει
    σε γραμμή με τον συντελεστή που εξηγεί το ΦΠΑ του γραμματίου, ώστε το ΦΠΑ να φτάσει στη
    δήλωση· οι εισφορές πάνε σε δική τους γραμμή χωρίς ΦΠΑ. Πριν, όλο το ποσό (με το ΦΠΑ μέσα)
    γινόταν μία γραμμή 0% και το ΦΠΑ δεν εμφανιζόταν πουθενά.
    """
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": ObjectId(gid)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το γραμμάτιο δεν βρέθηκε")
    if doc.get("status") != "issued":
        raise HTTPException(status_code=400, detail="Ενσωματώνεται μόνο εκδοθέν γραμμάτιο.")
    if not doc.get("client_id"):
        raise HTTPException(status_code=400, detail="Το γραμμάτιο δεν έχει συνδεδεμένο πελάτη")
    if doc.get("invoice_id"):
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη ενσωματωθεί σε τιμολόγιο")

    reference = round(float(doc.get("reference_amount") or 0), 2)
    contributions = round(float(doc.get("contributions_amount") or 0), 2)
    vat = round(float(doc.get("vat_amount") or 0), 2)
    rate = _fee_vat_rate(reference, vat)
    if rate is None or (reference <= 0 and vat > 0):
        raise HTTPException(
            status_code=400,
            detail=(f"Το ΦΠΑ του γραμματίου ({vat:.2f} €) δεν αντιστοιχεί σε συντελεστή 24%, 13% ή 6% επί της "
                    f"αξίας αναφοράς ({reference:.2f} €), οπότε δεν μπορεί να περάσει σωστά σε τιμολόγιο. "
                    "Εκδώστε το τιμολόγιο χειροκίνητα με τις γραμμές και τους συντελεστές που ισχύουν."),
        )
    label = f"Ενσωμάτωση Γραμματίου Προείσπραξης {doc.get('number')} ({doc.get('court_type')})"
    lines = []
    if reference > 0:
        lines.append(InvoiceLineItem(description=f"{label} — αμοιβή", quantity=1, unit_price=reference, vat_rate=rate))
    if contributions > 0:
        lines.append(InvoiceLineItem(description=f"{label} — εισφορές", quantity=1, unit_price=contributions, vat_rate=0))
    if not lines:
        raise HTTPException(status_code=400, detail="Το γραμμάτιο δεν έχει ποσό προς τιμολόγηση.")

    # Το τιμολόγιο ανήκει στην υπόθεση του γραμματίου και στον υπεύθυνο δικηγόρο της — ώστε η κερδοφορία ανά υπόθεση να
    # μη χρειάζεται δεύτερη, χειροκίνητη ταξινόμηση.
    case_id, lawyer_id = await inherited_matter(firm_id_for(user), doc.get("case_id"))
    inv = Invoice(
        firm_id=firm_id_for(user),
        client_id=doc["client_id"],
        client_name=doc.get("client_name", ""),
        date=athens_today().isoformat(),
        line_items=lines,
        case_id=case_id,
        lawyer_id=lawyer_id,
        created_by=user["email"],
        notes=f"Γραμμάτιο: {doc.get('number')} · Υπόθεση: {doc.get('case_title') or '—'}",
    )
    totals = calculate_invoice_totals(inv.line_items)
    expected = round(reference + contributions + vat, 2)
    if abs(totals["total"] - expected) > 0.01:
        raise HTTPException(status_code=400, detail="Το σύνολο του τιμολογίου δεν ταιριάζει με το γραμμάτιο· ελέγξτε τα ποσά.")
    inv.subtotal, inv.vat_amount, inv.total, inv.vat_breakdown = totals["subtotal"], totals["vat_amount"], totals["total"], totals["vat_breakdown"]
    result = await db.invoices.insert_one(inv.to_mongo())   # ο αριθμός δίνεται στην έκδοση
    # Ατομική σύνδεση: αν δύο αιτήματα φτάσουν μαζί, μόνο το πρώτο δένει το γραμμάτιο.
    linked = await db.grammatia.update_one(
        tenant_query(user, {"_id": ObjectId(gid), "$or": [{"invoice_id": {"$exists": False}}, {"invoice_id": None}, {"invoice_id": ""}]}),
        {"$set": {"invoice_id": str(result.inserted_id)}},
    )
    if linked.matched_count == 0:
        await db.invoices.delete_one({"_id": result.inserted_id})
        raise HTTPException(status_code=400, detail="Το γραμμάτιο έχει ήδη ενσωματωθεί σε τιμολόγιο")
    inv_doc = await db.invoices.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Invoice.from_mongo(inv_doc)
