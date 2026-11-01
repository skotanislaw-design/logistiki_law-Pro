import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from db import db
from auth import get_current_user, require_admin
from tenant import tenant_query
from mydata_client import mydata_ready, send_invoice, validate_credentials, MyDataError, mydata_mock_enabled, MYDATA_NOT_CONFIGURED_MSG

router = APIRouter(prefix="/mydata", tags=["mydata"])

ENV_LABEL = {"sandbox": "Sandbox (δοκιμαστικό)", "production": "Παραγωγή"}


@router.get("/status")
async def mydata_status(user: dict = Depends(get_current_user)):
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    live = mydata_ready(settings)
    env = settings.get("aade_environment", "sandbox")
    pending = await db.invoices.count_documents(tenant_query(user, {"status": {"$in": ["issued", "paid"]}, "mydata_status": {"$in": ["not_sent", "pending"]}}))
    failed = await db.invoices.count_documents(tenant_query(user, {"mydata_status": "failed"}))
    synced = await db.invoices.count_documents(tenant_query(user, {"mydata_status": "synced"}))
    if live:
        note = f"Ζωντανή σύνδεση με το myDATA REST API της ΑΑΔΕ — περιβάλλον: {ENV_LABEL[env]}. Τα παραστατικά διαβιβάζονται πραγματικά."
    else:
        note = ("Λειτουργία προσομοίωσης (MYDATA_MOCK=1): τα παραστατικά ΔΕΝ διαβιβάζονται στην ΑΑΔΕ." if mydata_mock_enabled()
                else "Δεν έχουν καταχωρηθεί credentials ΑΑΔΕ. Τα εκδοθέντα παραστατικά μένουν σε αναμονή και θα διαβιβαστούν αυτόματα από το Autopilot μόλις συμπληρώσετε τα στοιχεία στις Ρυθμίσεις.")
    return {
        "connected": live,
        "mode": "live" if live else ("mock" if mydata_mock_enabled() else "pending"),
        "environment": env,
        "environment_label": ENV_LABEL[env],
        "pending_count": pending,
        "failed_count": failed,
        "synced_count": synced,
        "note": note,
    }


@router.post("/test-connection")
async def test_connection(user: dict = Depends(require_admin)):
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    if not mydata_ready(settings):
        raise HTTPException(status_code=400, detail="Καταχωρήστε και αποθηκεύστε πρώτα AADE User ID & Subscription Key στις Ρυθμίσεις.")
    try:
        await validate_credentials(settings)
    except MyDataError as e:
        raise HTTPException(status_code=400, detail=str(e))
    env = ENV_LABEL[settings.get("aade_environment", "sandbox")]
    return {"message": f"Επιτυχής σύνδεση με το myDATA — περιβάλλον: {env}."}


@router.post("/sync/{invoice_id}")
async def sync_invoice(invoice_id: str, user: dict = Depends(get_current_user)):
    from invoice_services import transmit_to_mydata
    from tenant import firm_id_for
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    doc = await db.invoices.find_one(tenant_query(user, {"_id": oid}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if doc.get("mydata_status") == "synced":
        raise HTTPException(status_code=409, detail="Το παραστατικό έχει ήδη διαβιβαστεί στο myDATA.")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}

    update = await transmit_to_mydata(firm_id_for(user), doc, settings, force=doc.get("mydata_status") == "needs_review")
    if update.get("skipped"):
        raise HTTPException(status_code=409, detail="Η διαβίβαση βρίσκεται ήδη σε εξέλιξη. Δοκιμάστε ξανά σε λίγο.")
    status = update.get("mydata_status")
    if status == "failed":
        raise HTTPException(status_code=400, detail=update.get("mydata_error") or "Η διαβίβαση απέτυχε")
    if status == "pending":
        # Χωρίς διαπιστευτήρια το παραστατικό μένει στην ουρά· το Autopilot το ξαναδοκιμάζει.
        raise HTTPException(status_code=400, detail=MYDATA_NOT_CONFIGURED_MSG)
    await db.notifications.update_many(
        tenant_query(user, {"dedupe_key": f"mydata_needs_review:{oid}", "resolved_at": None}),
        {"$set": {"resolved_at": datetime.now(timezone.utc).isoformat(), "resolved_by": user.get("email", ""), "resolution": "manual"}})
    live = mydata_ready(settings)
    synced_at = update.get("mydata_synced_at") or datetime.now(timezone.utc).isoformat()
    if live:
        env = ENV_LABEL[settings.get("aade_environment", "sandbox")]
        message = f"Το παραστατικό διαβιβάστηκε στο myDATA ({env}) — MARK: {update['mydata_mark']}"
    else:
        message = "Το παραστατικό στάλθηκε (προσομοίωση) στο myDATA"
    return {"message": message, "mark": update["mydata_mark"], "uid": update["mydata_uid"],
            "mode": "live" if live else "mock", "synced_at": synced_at}
