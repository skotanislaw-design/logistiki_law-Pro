"""HTTP surface of the Autopilot engine: status, inbox, manual runs, digest preview."""
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import db
from http_utils import content_disposition
from auth import get_current_user, require_admin
from tenant import tenant_query, firm_id_for
from models import Notification, AutomationRun, VatPeriod
from automation import (
    JOBS, JOB_LABELS, run_autopilot_cycle, run_firm, resolve_notification, firm_snapshot, build_digest,
)
from email_utils import smtp_ready
from mydata_client import mydata_ready
from ai_runtime import ai_ready
from outbox import stats as outbox_stats

router = APIRouter(prefix="/automation", tags=["automation"])


class ResolvePayload(BaseModel):
    decision: str = "done"


class RunPayload(BaseModel):
    jobs: Optional[list[str]] = None


@router.get("/status")
async def automation_status(user: dict = Depends(get_current_user)):
    settings = await db.settings.find_one(tenant_query(user)) or {}
    firm_id = firm_id_for(user)
    open_items = await db.notifications.count_documents(tenant_query(user, {"requires_action": True, "resolved_at": None}))
    unread = await db.notifications.count_documents(tenant_query(user, {"read": False, "resolved_at": None}))
    last_runs = {}
    for job in JOBS:
        doc = await db.automation_runs.find_one({"firm_id": firm_id, "job": job}, sort=[("started_at", -1)])
        last_runs[job] = {
            "label": JOB_LABELS[job],
            "last_status": doc.get("status") if doc else None,
            "last_run_at": doc.get("finished_at") if doc else None,
            "last_stats": doc.get("stats") if doc else {},
            "last_error": doc.get("error") if doc else None,
        }
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    week = {
        "emails_sent": await db.invoices.count_documents(tenant_query(user, {"email_sent_at": {"$gte": since}})),
        "reminders_sent": await db.notifications.count_documents(tenant_query(user, {"type": "reminder_sent", "created_at": {"$gte": since}})),
        "reconciled": await db.bank_transactions.count_documents(tenant_query(user, {"match_status": "auto_confirmed", "confirmed_at": {"$gte": since}})),
        "mydata_synced": await db.invoices.count_documents(tenant_query(user, {"mydata_synced_at": {"$gte": since}})),
        "expenses_from_email": await db.expenses.count_documents(tenant_query(user, {"source": "email_ocr", "created_at": {"$gte": since}})),
        "expenses_from_mydata": await db.expenses.count_documents(tenant_query(user, {"source": "mydata", "created_at": {"$gte": since}})),
        "bank_transactions": await db.bank_transactions.count_documents(tenant_query(user, {"imported_by": "open_banking", "imported_at": {"$gte": since}})),
    }
    return {
        "enabled": settings.get("autopilot_enabled", True),
        "open_items": open_items,
        "unread": unread,
        "capabilities": {
            "email": smtp_ready(settings),
            "mydata": mydata_ready(settings),
            "ai": ai_ready(),
            "imap": bool(settings.get("imap_enabled") and settings.get("imap_user")),
            "mydata_expenses": bool(mydata_ready(settings) and settings.get("mydata_expense_sync", True) and (settings.get("afm") or "").strip()),
            "openbanking": bool(settings.get("openbanking_enabled") and (settings.get("gocardless_account_ids") or [])),
            "stripe": bool(__import__("os").environ.get("STRIPE_SECRET_KEY", "").startswith("sk_")),
        },
        "jobs": last_runs,
        "last_7_days": week,
        "outbox": await outbox_stats(firm_id),
        "last_digest_at": settings.get("last_digest_at"),
        "digest_frequency": settings.get("digest_frequency", "weekly"),
        "cycle_minutes": int(__import__("os").environ.get("AUTOPILOT_INTERVAL_MINUTES", "15")),
    }


@router.get("/notifications")
async def list_notifications(unresolved: bool = True, limit: int = 100, user: dict = Depends(get_current_user)):
    q = {"resolved_at": None} if unresolved else {}
    docs = await db.notifications.find(tenant_query(user, q)).sort("created_at", -1).limit(min(limit, 500)).to_list(500)
    return [Notification.from_mongo(d) for d in docs]


@router.post("/notifications/{notification_id}/resolve")
async def resolve(notification_id: str, payload: ResolvePayload, user: dict = Depends(get_current_user)):
    try:
        return await resolve_notification(firm_id_for(user), notification_id, user["email"], payload.decision)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία: {e}")


@router.post("/notifications/read-all")
async def read_all(user: dict = Depends(get_current_user)):
    r = await db.notifications.update_many(tenant_query(user, {"read": False}), {"$set": {"read": True}})
    return {"marked": r.modified_count}


@router.post("/notifications/dismiss-informational")
async def dismiss_informational(user: dict = Depends(get_current_user)):
    """Clears the «what Autopilot did» feed without touching items that need a decision."""
    now = datetime.now(timezone.utc).isoformat()
    r = await db.notifications.update_many(
        tenant_query(user, {"requires_action": False, "resolved_at": None}),
        {"$set": {"resolved_at": now, "resolved_by": user["email"], "resolution": "dismissed", "read": True}},
    )
    return {"dismissed": r.modified_count}


@router.post("/run")
async def run_now(payload: RunPayload, user: dict = Depends(require_admin)):
    jobs = payload.jobs
    if jobs:
        bad = [j for j in jobs if j not in JOBS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Άγνωστα jobs: {', '.join(bad)}")
    results = await run_firm(firm_id_for(user), jobs, force=True)
    return {"results": results}


@router.get("/runs")
async def list_runs(limit: int = 50, user: dict = Depends(get_current_user)):
    docs = await db.automation_runs.find(tenant_query(user)).sort("started_at", -1).limit(min(limit, 500)).to_list(500)
    out = []
    for d in docs:
        r = AutomationRun.from_mongo(d).model_dump()
        r["label"] = JOB_LABELS.get(r["job"], r["job"])
        out.append(r)
    return out


@router.get("/digest/preview")
async def digest_preview(user: dict = Depends(get_current_user)):
    settings = await db.settings.find_one(tenant_query(user)) or {}
    snapshot = await firm_snapshot(firm_id_for(user), settings)
    subject, text, html = build_digest(snapshot, settings.get("digest_frequency", "weekly"))
    return {"subject": subject, "text": text, "html": html,
            "recipient": settings.get("digest_email") or settings.get("email") or settings.get("smtp_user") or ""}


@router.post("/digest/send-now")
async def digest_send_now(user: dict = Depends(require_admin)):
    results = await run_firm(firm_id_for(user), ["digest"], force=True)
    r = results[0]
    if r.get("status") == "error":
        raise HTTPException(status_code=400, detail=r.get("error"))
    if r.get("stats", {}).get("skipped"):
        raise HTTPException(status_code=400, detail="Δεν στάλθηκε: ρυθμίστε SMTP και email παραλήπτη στις Ρυθμίσεις.")
    return {"message": f"Το ενημερωτικό στάλθηκε στο {r['stats'].get('sent_to')}"}


@router.get("/vat-periods")
async def vat_periods(user: dict = Depends(get_current_user)):
    docs = await db.vat_periods.find(tenant_query(user)).sort("period_end", -1).limit(24).to_list(24)
    return [VatPeriod.from_mongo(d) for d in docs]


async def _vat_return_payload(user: dict, period: str) -> dict:
    from vat_return import build_vat_return
    doc = await db.vat_periods.find_one(tenant_query(user, {"period": period}))
    if not doc:
        raise HTTPException(status_code=404, detail=f"Η περίοδος {period} δεν έχει υπολογιστεί ακόμη.")
    settings = await db.settings.find_one(tenant_query(user)) or {}
    start, end = doc["period_start"], doc["period_end"] + "T23:59:59"
    invoices = await db.invoices.find(tenant_query(user, {
        "status": {"$in": ["issued", "paid"]}, "date": {"$gte": start, "$lte": end}})).to_list(20000)
    expenses = await db.expenses.find(tenant_query(user, {"date": {"$gte": start, "$lte": end}})).to_list(20000)
    return build_vat_return(doc, invoices, expenses, settings)


@router.get("/vat-periods/{period}/return")
async def vat_return(period: str, user: dict = Depends(get_current_user)):
    """Η δήλωση Φ2 της περιόδου, ανά κωδικό εντύπου, έτοιμη για αντιγραφή στο TAXISnet."""
    return await _vat_return_payload(user, period)


@router.get("/vat-periods/{period}/return.pdf")
async def vat_return_pdf(period: str, user: dict = Depends(get_current_user)):
    from fastapi.responses import Response
    from vat_return import build_vat_return_pdf
    data = await _vat_return_payload(user, period)
    pdf = build_vat_return_pdf(data)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(f"FPA-{period}.pdf")})


@router.post("/vat-periods/{period}/submitted")
async def mark_vat_submitted(period: str, user: dict = Depends(get_current_user)):
    r = await db.vat_periods.update_one(tenant_query(user, {"period": period}),
                                        {"$set": {"status": "submitted", "submitted_at": datetime.now(timezone.utc).isoformat()}})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Η περίοδος δεν βρέθηκε")
    return {"message": f"Η περίοδος {period} σημειώθηκε ως υποβληθείσα"}


@router.post("/cycle")
async def run_cycle_all(user: dict = Depends(require_admin)):
    """Operator endpoint: run the full cycle for every firm (what the scheduler does)."""
    return await run_autopilot_cycle(force=True)
