from fastapi import APIRouter, Depends, HTTPException
from db import db
from models import Settings
from auth import get_current_user, require_admin

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(user: dict = Depends(get_current_user)):
    doc = await db.settings.find_one({})
    if not doc:
        default = Settings()
        result = await db.settings.insert_one(default.to_mongo())
        doc = await db.settings.find_one({"_id": result.inserted_id})
    return Settings.from_mongo(doc)


@router.put("")
async def update_settings(payload: Settings, user: dict = Depends(require_admin)):
    from datetime import datetime, timezone
    doc = await db.settings.find_one({})
    data = payload.model_dump(exclude={"id"})
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["mydata_connected"] = bool(payload.aade_user_id and payload.aade_subscription_key)
    if doc:
        await db.settings.update_one({"_id": doc["_id"]}, {"$set": data})
        doc = await db.settings.find_one({"_id": doc["_id"]})
    else:
        result = await db.settings.insert_one(data)
        doc = await db.settings.find_one({"_id": result.inserted_id})
    return Settings.from_mongo(doc)


@router.post("/test-email")
async def send_test_email(user: dict = Depends(require_admin)):
    from email_utils import smtp_ready, send_email_with_pdf
    import aiosmtplib as _smtp
    settings = await db.settings.find_one({}) or {}
    if not smtp_ready(settings):
        raise HTTPException(status_code=400, detail="Συμπληρώστε πρώτα email και App Password στις ρυθμίσεις SMTP.")
    to = settings["smtp_user"]
    try:
        await send_email_with_pdf(
            settings, to, "Nomos One — Δοκιμαστικό email",
            "Η αποστολή email από το Nomos One λειτουργεί σωστά.",
            "<p>Η αποστολή email από το <strong>Nomos One</strong> λειτουργεί σωστά. ✔</p>",
        )
    except _smtp.errors.SMTPAuthenticationError:
        raise HTTPException(status_code=400, detail="Αποτυχία σύνδεσης SMTP: λάθος email ή App Password.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία αποστολής: {e}")
    return {"message": f"Το δοκιμαστικό email στάλθηκε στο {to}"}
