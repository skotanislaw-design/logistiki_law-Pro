import os
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from db import db
from models import User, UserPublic
from auth import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    set_auth_cookies, get_current_user, check_lockout, record_failed_attempt, clear_attempts,
    get_jwt_secret, JWT_ALGORITHM,
)
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
    return {"id": str(user["_id"]) if "_id" in user else user.get("id"), "email": user["email"], "name": user["name"], "role": user["role"], "created_at": user.get("created_at", ""), "firm_id": firm_id_for(user)}


@router.post("/register")
async def register(payload: RegisterRequest, response: Response):
    email = payload.email.lower()
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
    identifier = f"{request.client.host}:{email}"
    await check_lockout(identifier)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        await record_failed_attempt(identifier)
        raise HTTPException(status_code=401, detail="Λάθος email ή κωδικός πρόσβασης")
    await clear_attempts(identifier)
    user_id = str(user["_id"])
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
        print(f"[PASSWORD RESET LINK] /reset-password?token={token} για {email}")
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
    await db.users.update_one({"email": rec["email"]}, {"$set": {"password_hash": hash_password(payload.new_password)}})
    await db.password_reset_tokens.update_one({"token": payload.token}, {"$set": {"used": True}})
    return {"message": "Ο κωδικός πρόσβασης άλλαξε επιτυχώς"}


@router.get("/users")
async def list_users(user: dict = Depends(get_current_user)):
    users = await db.users.find(tenant_query(user)).to_list(200)
    return [to_public(u) for u in users]
