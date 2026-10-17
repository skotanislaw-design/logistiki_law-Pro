import os
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException, Depends
from db import db
from bson import ObjectId
from tenant import LEGACY_FIRM_ID, firm_id_for

JWT_ALGORITHM = "HS256"


def get_jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id, "email": email,
        "iat": now, "exp": now + timedelta(minutes=30),
        "type": "access",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_mfa_token(user_id: str) -> str:
    """Βραχύβιο token που πιστοποιεί «σωστός κωδικός, εκκρεμεί ο 2ος παράγοντας».
    Δεν δίνει καμία πρόσβαση σε δεδομένα — μόνο στο endpoint επαλήθευσης."""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "type": "mfa",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_mfa_token(token: str) -> str:
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Η σύνδεση έληξε. Ξεκινήστε ξανά.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Μη έγκυρο token επαλήθευσης")
    if payload.get("type") != "mfa":
        raise HTTPException(status_code=401, detail="Μη έγκυρος τύπος token")
    return payload["sub"]


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now, "exp": now + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)


def token_predates_password_change(payload: dict, user: dict) -> bool:
    """Το token εκδόθηκε πριν την τελευταία αλλαγή κωδικού;

    Χωρίς αυτόν τον έλεγχο, η επαναφορά κωδικού δεν έδιωχνε κανέναν: το refresh
    token επτά ημερών του επιτιθέμενου συνέχιζε να δουλεύει κανονικά.

    Tokens εκδοθέντα πριν από αυτή την αναβάθμιση δεν φέρουν ``iat``· τους δίνεται
    χάρη ώστε η αναβάθμιση να μην αποσυνδέσει όλους τους χρήστες.
    """
    changed = user.get("password_changed_at")
    issued_at = payload.get("iat")
    if not changed or issued_at is None:
        return False
    try:
        changed_dt = datetime.fromisoformat(changed)
    except (TypeError, ValueError):
        return False
    if changed_dt.tzinfo is None:
        changed_dt = changed_dt.replace(tzinfo=timezone.utc)
    return float(issued_at) < changed_dt.timestamp()


ACCESS_COOKIE_MAX_AGE = 1800            # 30′, όσο και το access token
REFRESH_COOKIE_MAX_AGE = 604800         # 7 ημέρες, όσο και το refresh token
COOKIE_SAMESITE_VALUES = ("lax", "strict", "none")


def cookie_samesite() -> str:
    """Η τιμή `SameSite` των cookies σύνδεσης (μεταβλητή `COOKIE_SAMESITE`, προεπιλογή `lax`).

    Με `none` ο browser τα στέλνει και σε αιτήματα που ξεκινούν από ΑΛΛΟΝ ιστότοπο — αυτό ήταν η αιτία του CSRF (βλ.
    `csrf_guard.py`). Η εγκατάσταση του docker-compose είναι ίδιας προέλευσης, οπότε το `lax` δεν κοστίζει τίποτα. Το `none`
    χρειάζεται μόνο όταν η εφαρμογή (frontend) και το API βρίσκονται σε διαφορετικά sites (π.χ. app.firma.gr και api.otherfirma.gr)·
    τότε ορίστε `COOKIE_SAMESITE=none` ΚΑΙ βάλτε την προέλευση της εφαρμογής στο `CORS_ORIGINS`. Άγνωστη τιμή = `lax`."""
    value = (os.environ.get("COOKIE_SAMESITE") or "lax").strip().lower()
    return value if value in COOKIE_SAMESITE_VALUES else "lax"


def set_access_cookie(response, access_token: str):
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=True,
                        samesite=cookie_samesite(), max_age=ACCESS_COOKIE_MAX_AGE, path="/")


def set_auth_cookies(response, access_token: str, refresh_token: str):
    set_access_cookie(response, access_token)
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=True,
                        samesite=cookie_samesite(), max_age=REFRESH_COOKIE_MAX_AGE, path="/")


def clear_auth_cookies(response):
    """Διαγραφή με τα ίδια χαρακτηριστικά που είχε το cookie όταν ορίστηκε (διαδρομή, Secure, SameSite)."""
    for key in ("access_token", "refresh_token"):
        response.delete_cookie(key, path="/", secure=True, httponly=True, samesite=cookie_samesite())


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Δεν έχετε συνδεθεί")
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Μη έγκυρος τύπος token")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="Ο χρήστης δεν βρέθηκε")
        if token_predates_password_change(payload, user):
            raise HTTPException(status_code=401, detail="Ο κωδικός άλλαξε. Συνδεθείτε ξανά.")
        user["_id"] = str(user["_id"])
        # Το middleware του ελεγκτικού ίχνους τρέχει αφού ολοκληρωθεί το route και δεν
        # μπορεί να λύσει το Depends μόνο του· ο ταυτοποιημένος χρήστης αφήνεται εδώ.
        request.state.user = user
        # Τα μυστικά δεν ταξιδεύουν με το request-scoped user object: όποιο endpoint τα
        # χρειάζεται τα φορτώνει ρητά από τη βάση.
        for secret_field in ("password_hash", "mfa_secret", "mfa_pending_secret", "mfa_backup_codes"):
            user.pop(secret_field, None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Η συνεδρία έληξε")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Μη έγκυρο token")


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Απαιτούνται δικαιώματα διαχειριστή")
    return user


def require_roles(*roles: str, detail: str = "Δεν έχετε δικαίωμα για αυτή την ενέργεια"):
    """Εξάρτηση διαδρομής που δέχεται μόνο τους ρόλους που αναφέρονται, π.χ. `Depends(require_roles("admin", "accountant"))`.
    Όποιος έχει άλλον ρόλο παίρνει 403 με το μήνυμα `detail`, που διαβάζει ο χρήστης στην οθόνη."""
    def dependency(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail=detail)
        return user
    return dependency


MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


async def check_lockout(identifier: str):
    rec = await db.login_attempts.find_one({"identifier": identifier})
    if rec and rec.get("count", 0) >= MAX_ATTEMPTS:
        locked_at = rec.get("locked_at")
        if locked_at:
            locked_dt = datetime.fromisoformat(locked_at)
            if datetime.now(timezone.utc) - locked_dt < timedelta(minutes=LOCKOUT_MINUTES):
                raise HTTPException(status_code=429, detail="Πολλές αποτυχημένες προσπάθειες. Δοκιμάστε ξανά σε 15 λεπτά.")


async def record_failed_attempt(identifier: str):
    rec = await db.login_attempts.find_one({"identifier": identifier})
    count = (rec.get("count", 0) if rec else 0) + 1
    update = {"count": count}
    if count >= MAX_ATTEMPTS:
        update["locked_at"] = datetime.now(timezone.utc).isoformat()
    await db.login_attempts.update_one({"identifier": identifier}, {"$set": update}, upsert=True)


async def clear_attempts(identifier: str):
    await db.login_attempts.delete_one({"identifier": identifier})
