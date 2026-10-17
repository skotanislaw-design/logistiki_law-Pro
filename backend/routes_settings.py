from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError
from db import db
from models import Settings
from auth import get_current_user, require_admin
from tenant import tenant_query, firm_id_for
from crypto import encrypt_settings_secrets, mask_settings_secrets, merge_masked_secrets
from vat_rules import RulesError, normalize_rules

router = APIRouter(prefix="/settings", tags=["settings"])


async def _settings_doc(user: dict) -> dict:
    """Το έγγραφο ρυθμίσεων του γραφείου· δημιουργείται την πρώτη φορά.

    Ένα έγγραφο ανά γραφείο, και το εγγυάται μοναδικό ευρετήριο στο `firm_id` (βλ. `ensure_settings_index`): δύο
    ταυτόχρονα πρώτα αιτήματα δεν φτιάχνουν δύο έγγραφα — το δεύτερο παίρνει αυτό που δημιούργησε το πρώτο. (Με
    δύο έγγραφα η εφαρμογή διάβαζε το ένα και έγραφε στο άλλο, και μια επαναφορά αντιγράφου δεν φαινόταν ποτέ.)
    """
    doc = await db.settings.find_one(tenant_query(user, {}))
    if doc:
        return doc
    try:
        await db.settings.insert_one(Settings(firm_id=firm_id_for(user)).to_mongo())
    except DuplicateKeyError:
        pass                                 # το δημιούργησε άλλο αίτημα την ίδια στιγμή
    return await db.settings.find_one(tenant_query(user, {}))


@router.get("")
async def get_settings(user: dict = Depends(get_current_user)):
    return Settings.from_mongo(mask_settings_secrets(await _settings_doc(user)))


def _validate_firm_afm(afm) -> None:
    """Το ΑΦΜ του γραφείου. Λάθος εδώ δεν χαλάει ένα τιμολόγιο — τα απορρίπτει όλα."""
    from tax_utils import afm_error, is_valid_afm

    if afm is not None and not isinstance(afm, str):
        # Το PATCH δέχεται ελεύθερο JSON: ένας αριθμός ή μια λίστα εδώ έσκαγε με AttributeError (500).
        raise HTTPException(status_code=400, detail="Το ΑΦΜ του γραφείου πρέπει να είναι κείμενο.")
    value = (afm or "").strip()
    if value and not is_valid_afm(value):
        raise HTTPException(status_code=400, detail=afm_error(value, "ΑΦΜ του γραφείου"))


MAX_WITHHOLDING_RATE = 0.5


def _as_number(value, label: str) -> float:
    """Ο αριθμός ενός πεδίου ρύθμισης, ή 400. Το PATCH δέχεται ελεύθερο JSON: «abc», μια λίστα ή ένα `bool` εδώ
    έσκαγαν με ValueError/TypeError (500), ενώ το `NaN` περνούσε από κάθε σύγκριση ορίων ως «έγκυρο»."""
    from tax_utils import is_finite_number

    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            value = None
    if not is_finite_number(value):
        raise HTTPException(status_code=400, detail=f"{label}: αναμένεται έγκυρος αριθμός.")
    return float(value)


def _validate_withholding(category=None, rate=None, min_net=None) -> None:
    """Οι παράμετροι παρακράτησης που επηρεάζουν παραστατικά και διαβίβαση στο myDATA.

    * Κατηγορία: διαβιβάζεται αυτούσια ως `taxCategory`· τιμή εκτός λίστας απορρίπτεται από την ΑΑΔΕ μόλις εκδοθεί
      το πρώτο τιμολόγιο με παρακράτηση.
    * Συντελεστής: κλάσμα (0,20 = 20%). Το 20 αντί για 0,20 θα έβγαζε παρακράτηση 20 φορές την καθαρή αξία.
    * Όριο καθαρής αξίας: όχι αρνητικό και όχι NaN (με NaN η παρακράτηση θα εφαρμοζόταν σε κάθε τιμολόγιο).
    Ελέγχονται μόνο οι παράμετροι που δίνονται (το PATCH δεν στέλνει όλες).
    """
    from mydata_client import WITHHOLDING_CATEGORIES

    if category is not None and str(category) not in WITHHOLDING_CATEGORIES:
        raise HTTPException(status_code=400, detail=(
            f"Η κατηγορία παρακράτησης «{category}» δεν είναι έγκυρη. Επιτρέπονται οι κατηγορίες 2, 3 ή 10 "
            "(" + "· ".join(f"{k}: {v}" for k, v in WITHHOLDING_CATEGORIES.items()) + ")."))
    if rate is not None:
        number = _as_number(rate, "Ο συντελεστής παρακράτησης")
        if not (0 <= number <= MAX_WITHHOLDING_RATE):
            raise HTTPException(status_code=400, detail=(
                f"Ο συντελεστής παρακράτησης {rate} δεν είναι έγκυρος: δίνεται ως ποσοστό σε μορφή δεκαδικού "
                f"(π.χ. 0,20 για 20%) και δεν ξεπερνά το {MAX_WITHHOLDING_RATE:g}."))
    if min_net is not None and _as_number(min_net, "Το όριο καθαρής αξίας για παρακράτηση") < 0:
        raise HTTPException(status_code=400, detail="Το όριο καθαρής αξίας για παρακράτηση δεν μπορεί να είναι αρνητικό.")


MAX_RULES_CONFIRMED_BY = 120
VAT_RULE_FIELDS = ("vat_deductibility_rules", "vat_rules_confirmed_by")


def _check_vat_rules(data: dict, stored: dict) -> None:
    """Ελέγχει και ομαλοποιεί, μέσα στο `data` που θα γραφτεί, τον πίνακα εκπτωσιμότητας ΦΠΑ και το όνομα όποιου τον επιβεβαίωσε.

    Ο πίνακας ορίζει πόσο ΦΠΑ εκπίπτει στο Φ2, άρα αποθηκεύεται μόνο όπως ελέγχθηκε (βλ. vat_rules.normalize_rules).

    Η επιβεβαίωση του λογιστή δίνεται για **έναν συγκεκριμένο πίνακα**. Όταν ο πίνακας αλλάζει, ισχύει μόνο νέα επιβεβαίωση,
    δηλαδή **διαφορετικό** όνομα/ημερομηνία από το αποθηκευμένο· αν το αίτημα δεν φέρει όνομα, ή φέρει το ίδιο, η παλιά
    ανακαλείται — το «επιβεβαιωμένος από τον λογιστή» δεν πρέπει να μένει πάνω σε αριθμούς που δεν είδε. (Ένα αίτημα που
    στέλνει ολόκληρη τη φόρμα — μια παλιά καρτέλα ή ένας πελάτης του API που ξαναστέλνει ό,τι διάβασε — φέρνει μαζί και το
    αποθηκευμένο όνομα· το ίδιο όνομα με αλλαγμένο πίνακα σημαίνει «δεν το ξαναδήλωσε κανείς». Η οθόνη Ρυθμίσεων στέλνει
    πίνακα και όνομα μόνο όταν τα άλλαξε ο χρήστης — βλ. frontend/src/lib/vatRules.js. Για αυτό το όνομα γράφεται με
    ημερομηνία: «Χριστόφορος, 20/10/2026».)"""
    if "vat_deductibility_rules" in data:
        try:
            data["vat_deductibility_rules"] = normalize_rules(data["vat_deductibility_rules"])
        except RulesError as e:
            raise HTTPException(status_code=400, detail=str(e))
    rules_changed = ("vat_deductibility_rules" in data
                     and data["vat_deductibility_rules"] != (stored.get("vat_deductibility_rules") or {}))
    if "vat_rules_confirmed_by" in data:
        name = data["vat_rules_confirmed_by"]
        if name is None:
            name = ""
        if not isinstance(name, str) or len(name.strip()) > MAX_RULES_CONFIRMED_BY:
            raise HTTPException(status_code=400, detail=(
                f"Το όνομα όποιου επιβεβαίωσε τον πίνακα ΦΠΑ πρέπει να είναι κείμενο έως {MAX_RULES_CONFIRMED_BY} χαρακτήρες."))
        name = name.strip()
        if rules_changed and name == (stored.get("vat_rules_confirmed_by") or "").strip():
            name = ""
        data["vat_rules_confirmed_by"] = name
    elif rules_changed:
        data["vat_rules_confirmed_by"] = ""


def _percent(value) -> str:
    return "—" if value is None else f"{value:g}%"


def _vat_rules_note(stored: dict, data: dict) -> str:
    """Τι άλλαξε στον πίνακα εκπτωσιμότητας ΦΠΑ και στην επιβεβαίωσή του, για το ελεγκτικό ίχνος (κενό αν δεν άλλαξε τίποτα).

    Το ίχνος κρατούσε μόνο τη διαδρομή του αιτήματος· ένα ποσοστό όμως αλλάζει το ΦΠΑ που δηλώνεται, και ο έλεγχος θέλει
    να ξέρει από τι σε τι. Γράφονται μόνο οι κατηγορίες που άλλαξαν."""
    before = stored.get("vat_deductibility_rules") or {}
    after = data.get("vat_deductibility_rules", before)
    parts = [f"{category} {_percent(before.get(category))} → {_percent(after.get(category))}"
             for category in sorted(set(before) | set(after)) if before.get(category) != after.get(category)]
    note = "Πίνακας εκπτωσιμότητας ΦΠΑ: " + ", ".join(parts) if parts else ""
    name_before = (stored.get("vat_rules_confirmed_by") or "").strip()
    name_after = data.get("vat_rules_confirmed_by", name_before)
    if name_before != name_after:
        change = (f"ανακλήθηκε η επιβεβαίωση λογιστή «{name_before}»" if name_before and not name_after
                  else f"επιβεβαίωση λογιστή «{name_after}»")
        note = f"{note}· {change}" if note else change
    return note


@router.put("")
async def update_settings(payload: Settings, request: Request, user: dict = Depends(require_admin)):
    from datetime import datetime, timezone
    _validate_firm_afm(payload.afm)
    payload.afm = (payload.afm or "").strip()       # αποθηκεύεται όπως ελέγχθηκε
    _validate_withholding(payload.withholding_category, payload.withholding_rate, payload.withholding_min_net)
    doc = await _settings_doc(user)
    data = payload.model_dump(exclude={"id", "firm_id"})
    # Ο πίνακας ΦΠΑ και η επιβεβαίωσή του αλλάζουν μόνο αν έρθουν στο αίτημα: μια καρτέλα ανοιγμένη πριν την ενημέρωση,
    # που αποθηκεύει π.χ. τα στοιχεία SMTP, δεν πρέπει να σβήνει τον πίνακα που υπέγραψε ο λογιστής.
    for field in VAT_RULE_FIELDS:
        if field not in payload.model_fields_set:
            data.pop(field, None)
    _check_vat_rules(data, doc)
    request.state.audit_details = _vat_rules_note(doc, data)
    # Ο client δεν λαμβάνει ποτέ τα μυστικά, άρα στέλνει πίσω μάσκα: κράτα τα αποθηκευμένα.
    data = encrypt_settings_secrets(merge_masked_secrets(data, doc))
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["mydata_connected"] = bool(payload.aade_user_id and data.get("aade_subscription_key"))
    await db.settings.update_one(tenant_query(user, {"_id": doc["_id"]}), {"$set": data})
    doc = await db.settings.find_one(tenant_query(user, {"_id": doc["_id"]}))
    return Settings.from_mongo(mask_settings_secrets(doc))


@router.patch("")
async def patch_settings(payload: dict, request: Request, user: dict = Depends(require_admin)):
    """Partial update — used by the Autopilot page so it never has to resend SMTP/AADE secrets."""
    from datetime import datetime, timezone
    allowed = set(Settings.model_fields) - {"id", "firm_id", "updated_at", "mydata_connected"}
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data:
        raise HTTPException(status_code=400, detail="Δεν δόθηκαν έγκυρα πεδία ρυθμίσεων")
    if "afm" in data:
        _validate_firm_afm(data["afm"])
        data["afm"] = (data["afm"] or "").strip()   # αποθηκεύεται όπως ελέγχθηκε
    _validate_withholding(data.get("withholding_category"), data.get("withholding_rate"), data.get("withholding_min_net"))
    current = await _settings_doc(user)
    _check_vat_rules(data, current)
    request.state.audit_details = _vat_rules_note(current, data)
    merged = {**current, **merge_masked_secrets(data, current)}
    merged.pop("_id", None)
    try:
        validated = Settings(**{k: v for k, v in merged.items() if k in Settings.model_fields})
    except ValidationError as e:
        # Λάθος τύπος σε ελεύθερο JSON (π.χ. `autopilot_enabled: "όχι"`): απάντηση 400 με το πεδίο που φταίει, όχι 500.
        fields = ", ".join(sorted({".".join(str(part) for part in err["loc"]) for err in e.errors()}))
        raise HTTPException(status_code=400, detail=f"Μη έγκυρη τιμή στο πεδίο: {fields}.")
    data = encrypt_settings_secrets(validated.model_dump(exclude={"id", "firm_id"}))
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["mydata_connected"] = bool(validated.aade_user_id and data.get("aade_subscription_key"))
    await db.settings.update_one(tenant_query(user, {"_id": current["_id"]}), {"$set": data})
    doc = await db.settings.find_one(tenant_query(user, {"_id": current["_id"]}))
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
