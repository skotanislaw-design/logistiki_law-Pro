"""Ανάγνωση του ελεγκτικού ίχνους — μόνο για διαχειριστή.

Ένα ίχνος που κανείς δεν μπορεί να διαβάσει είναι το ίδιο άχρηστο με ένα ίχνος που δεν
γράφεται: σε έλεγχο κατά Ν.4557/2018 ή σε αίτημα υποκειμένου κατά ΓΚΠΔ πρέπει να μπορεί
κανείς να απαντήσει «ποιος άνοιξε αυτόν τον φάκελο και πότε», μέσα σε λεπτά.

Δεν υπάρχουν endpoints εγγραφής, ενημέρωσης ή διαγραφής. Είναι σκόπιμο: βλ. audit.py.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from db import db
from auth import require_admin
from tenant import tenant_query

router = APIRouter(prefix="/audit", tags=["audit"])


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc


@router.get("")
async def list_audit_log(
    user: dict = Depends(require_admin),
    entity: Optional[str] = Query(None, description="π.χ. invoices, kyc, clients"),
    entity_id: Optional[str] = Query(None),
    user_email: Optional[str] = Query(None),
    actor_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO ημερομηνία, συμπεριλαμβανομένη"),
    date_to: Optional[str] = Query(None, description="ISO ημερομηνία, συμπεριλαμβανομένη"),
    limit: int = Query(100, ge=1, le=1000),
    skip: int = Query(0, ge=0),
):
    """Το ίχνος του γραφείου, νεότερα πρώτα. Πάντα περιορισμένο στον μισθωτή του χρήστη."""
    q: dict = {}
    if entity:
        q["entity"] = entity
    if entity_id:
        q["entity_id"] = entity_id
    if user_email:
        q["user_email"] = user_email
    if actor_type:
        q["actor_type"] = actor_type
    if action:
        q["action"] = {"$regex": action, "$options": "i"}
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["$gte"] = date_from
        if date_to:
            # Η ημερομηνία λήξης είναι συμπεριλαμβανομένη: ο χρήστης γράφει «έως 10/09»
            # και περιμένει να δει και ό,τι έγινε στις 10/09 το απόγευμα.
            rng["$lte"] = date_to + "T23:59:59.999999+00:00" if len(date_to) == 10 else date_to
        q["created_at"] = rng

    scoped = tenant_query(user, q)
    total = await db.audit_logs.count_documents(scoped)
    docs = await db.audit_logs.find(scoped).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)
    return {"total": total, "skip": skip, "limit": limit, "items": [_serialize(d) for d in docs]}


@router.get("/entity/{entity}/{entity_id}")
async def entity_history(entity: str, entity_id: str, user: dict = Depends(require_admin),
                         limit: int = Query(200, ge=1, le=1000)):
    """Ό,τι έγινε σε μία συγκεκριμένη οντότητα — η ερώτηση που κάνει ένας ελεγκτής."""
    docs = await db.audit_logs.find(
        tenant_query(user, {"entity": entity, "entity_id": entity_id})
    ).sort("created_at", 1).limit(limit).to_list(limit)
    return [_serialize(d) for d in docs]


@router.get("/summary")
async def audit_summary(user: dict = Depends(require_admin), days: int = Query(30, ge=1, le=365)):
    """Σύνοψη δραστηριότητας — για να φαίνεται με μια ματιά αν κάτι είναι ασυνήθιστο."""
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    scoped = tenant_query(user, {"created_at": {"$gte": since}})

    by_user: dict[str, int] = {}
    by_entity: dict[str, int] = {}
    failed_logins = 0
    async for d in db.audit_logs.find(scoped, {"user_email": 1, "entity": 1, "path": 1, "status_code": 1}):
        email = d.get("user_email") or "—"
        by_user[email] = by_user.get(email, 0) + 1
        ent = d.get("entity") or "—"
        by_entity[ent] = by_entity.get(ent, 0) + 1
        if (d.get("path") or "").startswith("/api/auth/login") and (d.get("status_code") or 0) >= 400:
            failed_logins += 1

    total = await db.audit_logs.count_documents(scoped)
    return {
        "days": days,
        "total": total,
        "failed_logins": failed_logins,
        "by_user": dict(sorted(by_user.items(), key=lambda kv: -kv[1])[:20]),
        "by_entity": dict(sorted(by_entity.items(), key=lambda kv: -kv[1])[:20]),
    }
