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
    doc = await db.invoices.find_one(tenant_query(user, {"_id": ObjectId(invoice_id)}))
    if not doc:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    settings = await db.settings.find_one(tenant_query(user, {})) or {}

    if mydata_ready(settings):
        client = None
        if doc.get("client_id"):
            try:
                client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(doc["client_id"])}))
            except Exception:
                client = None
        try:
            result = await send_invoice(settings, doc, client or {})
        except MyDataError as e:
            await db.invoices.update_one(
                tenant_query(user, {"_id": ObjectId(invoice_id)}),
                {"$set": {"mydata_status": "failed", "mydata_error": str(e)}, "$inc": {"mydata_attempts": 1}},
            )
            raise HTTPException(status_code=400, detail=str(e))
        await db.invoices.update_one(
            tenant_query(user, {"_id": ObjectId(invoice_id)}),
            {"$set": {"mydata_status": "synced", "mydata_mark": result["mark"], "mydata_uid": result["uid"], "mydata_error": None, "mydata_synced_at": datetime.now(timezone.utc).isoformat()}},
        )
        env = ENV_LABEL[settings.get("aade_environment", "sandbox")]
        return {
            "message": f"Το παραστατικό διαβιβάστηκε στο myDATA ({env}) — MARK: {result['mark']}",
            "mark": result["mark"], "uid": result["uid"], "mode": "live",
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }

    if mydata_mock_enabled():
        mark = f"MOCK-{uuid.uuid4().hex[:16].upper()}"
        uid = f"UID-{uuid.uuid4().hex[:12].upper()}"
        await db.invoices.update_one(
            tenant_query(user, {"_id": ObjectId(invoice_id)}),
            {"$set": {"mydata_status": "synced", "mydata_mark": mark, "mydata_uid": uid, "mydata_error": None}},
        )
        return {
            "message": "Το παραστατικό στάλθηκε (προσομοίωση) στο myDATA",
            "mark": mark, "uid": uid, "mode": "mock",
            "synced_at": datetime.now(timezone.utc).isoformat(),
        }
    # No credentials: keep the invoice in the queue. Autopilot retries automatically once
    # the AADE credentials are saved in Settings.
    await db.invoices.update_one(
        tenant_query(user, {"_id": ObjectId(invoice_id)}),
        {"$set": {"mydata_status": "pending", "mydata_error": MYDATA_NOT_CONFIGURED_MSG}},
    )
    raise HTTPException(status_code=400, detail=MYDATA_NOT_CONFIGURED_MSG)
