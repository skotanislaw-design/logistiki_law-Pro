"""Έξοδα: αντιστοίχιση ίδιου παραστατικού από διαφορετικές πηγές και πληρωμή προς προμηθευτές.

Το ίδιο τιμολόγιο προμηθευτή μπορεί να φτάσει τρεις φορές — με email (OCR), από φωτογραφία
και από το myDATA. Αν καταχωρηθεί και τις τρεις, ο ΦΠΑ εισροών δηλώνεται τριπλά. Εδώ ζει η
λογική που τις ενώνει, καθώς και η κατάσταση «προς πληρωμή / πληρωμένο».
"""
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId

from db import db
from invoice_services import firm_query

DEFAULT_DUE_DAYS = 30
AMOUNT_TOLERANCE = 0.01
DATE_TOLERANCE_DAYS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _norm(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def same_vendor(a: str, b: str) -> bool:
    """Ελαστική σύγκριση ονομάτων: «ΔΕΗ Α.Ε.» ~ «ΔΕΗ»."""
    na, nb = _norm(a), _norm(b)
    return bool(na and nb and (na in nb or nb in na))


def payment_defaults(invoice_type: str, doc_date: str) -> dict:
    """Παραστατικό προμηθευτή (τιμολόγιο) → προς πληρωμή· απόδειξη λιανικής/πιστωτικό → πληρωμένο."""
    t = str(invoice_type or "")
    if not t or t.startswith(("5.", "11.")):
        return {"payment_status": "paid"}
    due = (_day(doc_date) or date.today()) + timedelta(days=DEFAULT_DUE_DAYS)
    return {"payment_status": "unpaid", "due_date": due.isoformat()}


async def find_duplicate(firm_id: str, *, amount: float, doc_date: str, vendor: str = "",
                         supplier_afm: str = "", want_mydata: bool | None = None) -> dict | None:
    """Υπάρχον έξοδο που είναι το ίδιο παραστατικό (ποσό + ημερομηνία ± 3 ημέρες + προμηθευτής).

    ``want_mydata``: True → μόνο εγγραφές με MARK, False → μόνο χωρίς MARK.
    """
    target = _day(doc_date)
    if not target or not amount:
        return None
    query: dict = {"amount": {"$gte": amount - AMOUNT_TOLERANCE, "$lte": amount + AMOUNT_TOLERANCE}}
    if want_mydata is True:
        query["mydata_mark"] = {"$nin": [None, ""]}
    elif want_mydata is False:
        query["mydata_mark"] = {"$in": [None, ""]}
    async for exp in db.expenses.find(firm_query(firm_id, query)):
        d = _day(exp.get("date"))
        if not d or abs((d - target).days) > DATE_TOLERANCE_DAYS:
            continue
        if supplier_afm and exp.get("supplier_afm") == supplier_afm:
            return exp
        if same_vendor(exp.get("vendor", ""), vendor):
            return exp
    return None


async def mark_expense_paid(firm_id: str, expense_id, paid_via: str = "manual", close_notifications: bool = True) -> str:
    """Idempotent: ``paid`` | ``already_paid`` | ``not_found``."""
    try:
        oid = expense_id if isinstance(expense_id, ObjectId) else ObjectId(expense_id)
    except Exception:
        return "not_found"
    res = await db.expenses.update_one(
        firm_query(firm_id, {"_id": oid, "payment_status": "unpaid"}),
        {"$set": {"payment_status": "paid", "paid_at": _now(), "paid_via": paid_via}},
    )
    if res.modified_count:
        if close_notifications:
            await db.notifications.update_many(
                firm_query(firm_id, {"entity": "expense", "entity_id": str(oid), "type": {"$in": ["expense_due", "expense_overdue"]},
                                     "resolved_at": None}),
                {"$set": {"resolved_at": _now(), "resolved_by": "autopilot", "resolution": "auto"}})
        return "paid"
    return "already_paid" if await db.expenses.find_one(firm_query(firm_id, {"_id": oid}), {"_id": 1}) else "not_found"
