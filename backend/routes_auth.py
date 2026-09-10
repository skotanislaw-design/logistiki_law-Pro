import os
import logging
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from db import db
from models import User, UserPublic
from auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    set_auth_cookies, get_current_user, check_lockout, record_failed_attempt, clear_attempts,
    get_jwt_secret, JWT_ALGORITHM, create_mfa_token, decode_mfa_token,
)
from crypto import encrypt, decrypt
import mfa as mfa_lib
from tenant import LEGACY_FIRM_ID, new_firm_id, firm_id_for, tenant_query
import jwt

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: str = "lawyer"


def to_public(user: dict) -> dict:
    return {"id": str(user["_id"]) if "_id" in user else user.get("id"), "email": user["email"],
            "name": user["name"], "role": user["role"], "created_at": user.get("created_at", ""),
            "firm_id": firm_id_for(user), "mfa_enabled": bool(user.get("mfa_enabled"))}


MIN_PASSWORD_LENGTH = 10


def _validate_password(password: str) -> None:
    """Ελάχιστη πολιτική κωδικού. Πριν από αυτό, το «a» ήταν αποδεκτός κωδικός."""
    pw = password or ""
    if len(pw) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Ο κωδικός πρέπει να έχει τουλάχιστον {MIN_PASSWORD_LENGTH} χαρακτήρες.",
        )
    if pw.isdigit() or pw.isalpha():
        raise HTTPException(
            status_code=400,
            detail="Ο κωδικός πρέπει να συνδυάζει γράμματα με αριθμούς ή σύμβολα.",
        )


@router.post("/register")
async def register(payload: RegisterRequest, response: Response):
    email = payload.email.lower()
    _validate_password(payload.password)
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Υπάρχει ήδη χρήστης με αυτό το email")
    # Public registration must never grant elevated privileges.  A new
    # registration starts a separate firm; invitations can provision members.
    user = User(email=email, password_hash=hash_password(payload.password), name=payload.name, role="lawyer", firm_id=new_firm_id())
    result = await db.users.insert_one(user.to_mongo())
    user_id = str(result.inserted_id)
    access = create_access_token(user_id, email)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    doc = await db.users.find_one({"_id": result.inserted_id})
    return to_public(doc)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    email = payload.email.lower()
    # Ώστε το ελεγκτικό ίχνος να αποδίδει και τις ΑΠΟΤΥΧΗΜΕΝΕΣ απόπειρες σε email — αυτό
    # ακριβώς ψάχνει ένας έλεγχος ασφαλείας.
    request.state.audit_email = email
    identifier = f"{request.client.host}:{email}"
    await check_lockout(identifier)
    user = await db.users.find_one({"email": email})
    if user:
        # Η απόπειρα χρεώνεται στο γραφείο του λογαριασμού-στόχου: μια αποτυχημένη
        # σύνδεση πρέπει να είναι ορατή σε αυτόν που έχει λόγο να τη δει.
        request.state.audit_firm_id = user.get("firm_id")
    if not user or not verify_password(payload.password, user["password_hash"]):
        await record_failed_attempt(identifier)
        raise HTTPException(status_code=401, detail="Λάθος email ή κωδικός πρόσβασης")
    await clear_attempts(identifier)
    user_id = str(user["_id"])
    if user.get("mfa_enabled"):
        # Σωστός κωδικός δεν αρκεί: καμία συνεδρία μέχρι τον δεύτερο παράγοντα.
        return {"mfa_required": True, "mfa_token": create_mfa_token(user_id),
                "message": "Καταχωρήστε τον κωδικό μιας χρήσης από την εφαρμογή ταυτοποίησης."}
    access = create_access_token(user_id, email)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    return to_public(user)


@router.post("/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"message": "Αποσυνδεθήκατε επιτυχώς"}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return to_public(user)


@router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="Δεν βρέθηκε refresh token")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Μη έγκυρος τύπος token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Μη έγκυρο ή ληγμένο token")
    user = await db.users.find_one({"_id": __import__("bson").ObjectId(payload["sub"])})
    if not user:
        raise HTTPException(status_code=401, detail="Ο χρήστης δεν βρέθηκε")
    from auth import token_predates_password_change
    if token_predates_password_change(payload, user):
        raise HTTPException(status_code=401, detail="Ο κωδικός άλλαξε. Συνδεθείτε ξανά.")
    access = create_access_token(str(user["_id"]), user["email"])
    response.set_cookie(key="access_token", value=access, httponly=True, secure=True, samesite="none", max_age=1800, path="/")
    return {"message": "ok"}


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordRequest):
    email = payload.email.lower()
    user = await db.users.find_one({"email": email})
    if user:
        token = secrets.token_urlsafe(32)
        await db.password_reset_tokens.insert_one({
            "token": token, "email": email,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)),
            "used": False,
        })
        link = f"{(os.environ.get('PUBLIC_URL') or '').rstrip('/')}/reset-password?token={token}"
        # Send through the firm's own SMTP when configured; otherwise log for the operator.
        try:
            from email_utils import smtp_ready, send_email_with_pdf
            settings = await db.settings.find_one(tenant_query(user, {})) or {}
            if smtp_ready(settings):
                await send_email_with_pdf(
                    settings, email, "Nomos One — Επαναφορά κωδικού",
                    f"Για να ορίσετε νέο κωδικό ανοίξτε τον σύνδεσμο (ισχύει 1 ώρα):\n{link}",
                    f"<p>Για να ορίσετε νέο κωδικό πατήστε <a href=\"{link}\">εδώ</a> (ισχύει 1 ώρα).</p>",
                )
            else:
                logging.getLogger(__name__).warning("[PASSWORD RESET LINK] %s για %s (SMTP δεν έχει ρυθμιστεί)", link, email)
        except Exception as e:
            logging.getLogger(__name__).error("Password reset email failed for %s: %s", email, e)
    return {"message": "Αν υπάρχει λογαριασμός με αυτό το email, θα λάβετε οδηγίες επαναφοράς."}


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest):
    rec = await db.password_reset_tokens.find_one({"token": payload.token, "used": False})
    if not rec:
        raise HTTPException(status_code=400, detail="Μη έγκυρο token επαναφοράς")
    expires_at = rec["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Το token έχει λήξει")
    _validate_password(payload.new_password)
    # Το password_changed_at ακυρώνει κάθε token που είχε εκδοθεί νωρίτερα: η επαναφορά
    # κωδικού διώχνει και όποιον είχε ήδη ενεργή συνεδρία.
    await db.users.update_one(
        {"email": rec["email"]},
        {"$set": {"password_hash": hash_password(payload.new_password),
                  "password_changed_at": datetime.now(timezone.utc).isoformat()}},
    )
    await db.password_reset_tokens.update_one({"token": payload.token}, {"$set": {"used": True}})
    return {"message": "Ο κωδικός πρόσβασης άλλαξε επιτυχώς"}


@router.get("/users")
async def list_users(user: dict = Depends(get_current_user)):
    users = await db.users.find(tenant_query(user)).to_list(200)
    return [to_public(u) for u in users]


# ---------------------------------------------------------------- 2FA -------

class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str


class MfaSetupRequest(BaseModel):
    password: str


class MfaEnableRequest(BaseModel):
    code: str


class MfaDisableRequest(BaseModel):
    password: str
    code: str = ""


async def _user_by_id(user_id: str) -> dict:
    from bson import ObjectId
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Ο χρήστης δεν βρέθηκε")
    return user


async def _check_second_factor(user: dict, code: str) -> dict:
    """Δέχεται κωδικό TOTP ή εφεδρικό κωδικό. Επιστρέφει το $set update για τον χρήστη."""
    secret = decrypt(user.get("mfa_secret") or "")
    last = user.get("mfa_last_counter")
    ok, counter = mfa_lib.verify_totp(secret, code, used_counter=last)
    if ok:
        return {"mfa_last_counter": counter}
    used, remaining = mfa_lib.consume_backup_code(code, user.get("mfa_backup_codes") or [])
    if used:
        return {"mfa_backup_codes": remaining}
    # Σωστός κωδικός που έχει ήδη χρησιμοποιηθεί: κάθε OTP ισχύει μία φορά (RFC 6238 §5.2).
    # Χωρίς διακριτό μήνυμα ο χρήστης νομίζει ότι η εφαρμογή του βγάζει λάθος κωδικούς.
    if last is not None and mfa_lib.verify_totp(secret, code)[0]:
        raise HTTPException(status_code=401,
                            detail="Ο κωδικός έχει ήδη χρησιμοποιηθεί. Περιμένετε τον επόμενο (ανανεώνεται κάθε 30″).")
    raise HTTPException(status_code=401, detail="Λανθασμένος κωδικός ταυτοποίησης")


@router.post("/2fa/verify")
async def verify_2fa(payload: MfaVerifyRequest, request: Request, response: Response):
    """Δεύτερο βήμα σύνδεσης: ανταλλάσσει το mfa_token με κανονική συνεδρία."""
    user_id = decode_mfa_token(payload.mfa_token)
    identifier = f"mfa:{request.client.host}:{user_id}"
    await check_lockout(identifier)
    user = await _user_by_id(user_id)
    if not user.get("mfa_enabled"):
        raise HTTPException(status_code=400, detail="Δεν είναι ενεργή η ταυτοποίηση δύο παραγόντων")
    try:
        update = await _check_second_factor(user, payload.code)
    except HTTPException:
        await record_failed_attempt(identifier)
        raise
    await clear_attempts(identifier)
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    set_auth_cookies(response, create_access_token(user_id, user["email"]), create_refresh_token(user_id))
    return to_public(user)


@router.get("/2fa/status")
async def status_2fa(user: dict = Depends(get_current_user)):
    full = await _user_by_id(user["_id"])
    return {
        "enabled": bool(full.get("mfa_enabled")),
        "pending_setup": bool(full.get("mfa_pending_secret")),
        "backup_codes_remaining": len(full.get("mfa_backup_codes") or []),
        "enabled_at": full.get("mfa_enabled_at"),
    }


@router.post("/2fa/setup")
async def setup_2fa(payload: MfaSetupRequest, user: dict = Depends(get_current_user)):
    """Παράγει νέο μυστικό (εκκρεμές μέχρι την επιβεβαίωση) και το otpauth URI."""
    full = await _user_by_id(user["_id"])
    if not verify_password(payload.password, full["password_hash"]):
        raise HTTPException(status_code=401, detail="Λανθασμένος κωδικός πρόσβασης")
    if full.get("mfa_enabled"):
        raise HTTPException(status_code=400, detail="Η ταυτοποίηση δύο παραγόντων είναι ήδη ενεργή. Απενεργοποιήστε την πρώτα.")
    secret = mfa_lib.generate_secret()
    await db.users.update_one({"_id": full["_id"]}, {"$set": {"mfa_pending_secret": encrypt(secret)}})
    return {
        "secret": secret,
        "secret_formatted": mfa_lib.format_secret(secret),
        "otpauth_uri": mfa_lib.provisioning_uri(secret, full["email"]),
        "message": "Σαρώστε το QR ή καταχωρήστε το κλειδί, και επιβεβαιώστε με τον 6ψήφιο κωδικό.",
    }


@router.post("/2fa/enable")
async def enable_2fa(payload: MfaEnableRequest, user: dict = Depends(get_current_user)):
    """Επιβεβαιώνει το εκκρεμές μυστικό και επιστρέφει τους εφεδρικούς κωδικούς **μία φορά**."""
    full = await _user_by_id(user["_id"])
    pending = decrypt(full.get("mfa_pending_secret") or "")
    if not pending:
        raise HTTPException(status_code=400, detail="Ξεκινήστε πρώτα τη ρύθμιση (2fa/setup).")
    ok, counter = mfa_lib.verify_totp(pending, payload.code)
    if not ok:
        raise HTTPException(status_code=401, detail="Λανθασμένος κωδικός — ελέγξτε την ώρα της συσκευής σας.")
    codes = mfa_lib.generate_backup_codes()
    await db.users.update_one({"_id": full["_id"]}, {"$set": {
        "mfa_enabled": True,
        "mfa_secret": encrypt(pending),
        "mfa_pending_secret": "",
        "mfa_backup_codes": mfa_lib.hash_backup_codes(codes),
        "mfa_last_counter": counter,
        "mfa_enabled_at": datetime.now(timezone.utc).isoformat(),
    }})
    return {
        "enabled": True,
        "backup_codes": codes,
        "message": "Αποθηκεύστε τους εφεδρικούς κωδικούς σε ασφαλές σημείο — δεν θα εμφανιστούν ξανά.",
    }


@router.post("/2fa/disable")
async def disable_2fa(payload: MfaDisableRequest, user: dict = Depends(get_current_user)):
    full = await _user_by_id(user["_id"])
    if not verify_password(payload.password, full["password_hash"]):
        raise HTTPException(status_code=401, detail="Λανθασμένος κωδικός πρόσβασης")
    if not full.get("mfa_enabled"):
        raise HTTPException(status_code=400, detail="Η ταυτοποίηση δύο παραγόντων δεν είναι ενεργή")
    await _check_second_factor(full, payload.code)
    await db.users.update_one({"_id": full["_id"]}, {"$set": {
        "mfa_enabled": False, "mfa_secret": "", "mfa_pending_secret": "",
        "mfa_backup_codes": [], "mfa_last_counter": None, "mfa_enabled_at": None,
    }})
    return {"enabled": False, "message": "Η ταυτοποίηση δύο παραγόντων απενεργοποιήθηκε."}


@router.post("/2fa/backup-codes")
async def regenerate_backup_codes(payload: MfaSetupRequest, user: dict = Depends(get_current_user)):
    """Νέο σετ εφεδρικών κωδικών· τα παλιά ακυρώνονται αμέσως."""
    full = await _user_by_id(user["_id"])
    if not verify_password(payload.password, full["password_hash"]):
        raise HTTPException(status_code=401, detail="Λανθασμένος κωδικός πρόσβασης")
    if not full.get("mfa_enabled"):
        raise HTTPException(status_code=400, detail="Η ταυτοποίηση δύο παραγόντων δεν είναι ενεργή")
    codes = mfa_lib.generate_backup_codes()
    await db.users.update_one({"_id": full["_id"]}, {"$set": {"mfa_backup_codes": mfa_lib.hash_backup_codes(codes)}})
    return {"backup_codes": codes, "message": "Οι προηγούμενοι εφεδρικοί κωδικοί ακυρώθηκαν."}
