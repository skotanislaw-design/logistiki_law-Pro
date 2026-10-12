from fastapi import APIRouter, Depends, HTTPException
from db import db
from models import Settings
from auth import get_current_user, require_admin
from tenant import tenant_query, firm_id_for
from crypto import encrypt_settings_secrets, mask_settings_secrets, merge_masked_secrets

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(user: dict = Depends(get_current_user)):
    doc = await db.settings.find_one(tenant_query(user, {}))
    if not doc:
        default = Settings(firm_id=firm_id_for(user))
        result = await db.settings.insert_one(default.to_mongo())
        doc = await db.settings.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Settings.from_mongo(mask_settings_secrets(doc))


def _validate_firm_afm(afm: str) -> None:
    """Το ΑΦΜ του γραφείου. Λάθος εδώ δεν χαλάει ένα τιμολόγιο — τα απορρίπτει όλα."""
    from tax_utils import afm_error, is_valid_afm

    value = (afm or "").strip()
    if value and not is_valid_afm(value):
        raise HTTPException(status_code=400, detail=afm_error(value, "ΑΦΜ του γραφείου"))


MAX_WITHHOLDING_RATE = 0.5


def _validate_withholding(category=None, rate=None, min_net=None) -> None:
    """Οι παράμετροι παρακράτησης που επηρεάζουν παραστατικά και διαβίβαση στο myDATA.

    * Κατηγορία: διαβιβάζεται αυτούσια ως `taxCategory`· τιμή εκτός λίστας απορρίπτεται από την ΑΑΔΕ μόλις εκδοθεί
      το πρώτο τιμολόγιο με παρακράτηση.
    * Συντελεστής: κλάσμα (0,20 = 20%). Το 20 αντί για 0,20 θα έβγαζε παρακράτηση 20 φορές την καθαρή αξία.
    Ελέγχονται μόνο οι παράμετροι που δίνονται (το PATCH δεν στέλνει όλες).
    """
    from mydata_client import WITHHOLDING_CATEGORIES

    if category is not None and str(category) not in WITHHOLDING_CATEGORIES:
        raise HTTPException(status_code=400, detail=(
            f"Η κατηγορία παρακράτησης «{category}» δεν είναι έγκυρη. Επιτρέπονται οι κατηγορίες 2, 3 ή 10 "
            "(" + "· ".join(f"{k}: {v}" for k, v in WITHHOLDING_CATEGORIES.items()) + ")."))
    if rate is not None and not (0 <= float(rate) <= MAX_WITHHOLDING_RATE):
        raise HTTPException(status_code=400, detail=(
            f"Ο συντελεστής παρακράτησης {rate} δεν είναι έγκυρος: δίνεται ως ποσοστό σε μορφή δεκαδικού "
            f"(π.χ. 0,20 για 20%) και δεν ξεπερνά το {MAX_WITHHOLDING_RATE:g}."))
    if min_net is not None and float(min_net) < 0:
        raise HTTPException(status_code=400, detail="Το όριο καθαρής αξίας για παρακράτηση δεν μπορεί να είναι αρνητικό.")


@router.put("")
async def update_settings(payload: Settings, user: dict = Depends(require_admin)):
    from datetime import datetime, timezone
    _validate_firm_afm(payload.afm)
    _validate_withholding(payload.withholding_category, payload.withholding_rate, payload.withholding_min_net)
    doc = await db.settings.find_one(tenant_query(user, {}))
    data = payload.model_dump(exclude={"id", "firm_id"})
    # Ο client δεν λαμβάνει ποτέ τα μυστικά, άρα στέλνει πίσω μάσκα: κράτα τα αποθηκευμένα.
    data = encrypt_settings_secrets(merge_masked_secrets(data, doc))
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["mydata_connected"] = bool(payload.aade_user_id and data.get("aade_subscription_key"))
    if doc:
        await db.settings.update_one(tenant_query(user, {"_id": doc["_id"]}), {"$set": data})
        doc = await db.settings.find_one(tenant_query(user, {"_id": doc["_id"]}))
    else:
        data["firm_id"] = firm_id_for(user)
        result = await db.settings.insert_one(data)
        doc = await db.settings.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Settings.from_mongo(mask_settings_secrets(doc))


@router.patch("")
async def patch_settings(payload: dict, user: dict = Depends(require_admin)):
    """Partial update — used by the Autopilot page so it never has to resend SMTP/AADE secrets."""
    from datetime import datetime, timezone
    allowed = set(Settings.model_fields) - {"id", "firm_id", "updated_at", "mydata_connected"}
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data:
        raise HTTPException(status_code=400, detail="Δεν δόθηκαν έγκυρα πεδία ρυθμίσεων")
    if "afm" in data:
        _validate_firm_afm(data["afm"])
    _validate_withholding(data.get("withholding_category"), data.get("withholding_rate"), data.get("withholding_min_net"))
    current = await db.settings.find_one(tenant_query(user, {}))
    if not current:
        current = Settings(firm_id=firm_id_for(user)).to_mongo()
        await db.settings.insert_one(current)
    merged = {**current, **merge_masked_secrets(data, current)}
    merged.pop("_id", None)
    validated = Settings(**{k: v for k, v in merged.items() if k in Settings.model_fields})
    data = encrypt_settings_secrets(validated.model_dump(exclude={"id", "firm_id"}))
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["mydata_connected"] = bool(validated.aade_user_id and data.get("aade_subscription_key"))
    await db.settings.update_one(tenant_query(user, {}), {"$set": data})
    doc = await db.settings.find_one(tenant_query(user, {}))
    return Settings.from_mongo(mask_settings_secrets(doc))


@router.post("/test-email")
async def send_test_email(user: dict = Depends(require_admin)):
    from email_utils import smtp_ready, send_email_with_pdf
    import aiosmtplib as _smtp
    settings = await db.settings.find_one(tenant_query(user, {})) or {}
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
