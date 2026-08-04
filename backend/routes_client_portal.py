"""Client Portal — Δημόσιο portal για πελάτες με magic-link token.
Ο δικηγόρος στέλνει στον πελάτη magic link (via email) → ο πελάτης βλέπει τιμολόγια/υποθέσεις/έγγραφα του.
"""
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import ClientPortalToken
from auth import get_current_user

router = APIRouter(prefix="/client-portal", tags=["client-portal"])
public_router = APIRouter(prefix="/portal", tags=["portal-public"])


class InviteRequest(BaseModel):
    client_id: str
    origin_url: str = ""


@router.post("/invite")
async def invite_client(payload: InviteRequest, user: dict = Depends(get_current_user)):
    """Δημιουργεί magic link για πρόσβαση πελάτη και στέλνει email."""
    try:
        client = await db.clients.find_one({"_id": ObjectId(payload.client_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    if not client.get("email"):
        raise HTTPException(status_code=400, detail="Ο πελάτης δεν έχει email — προσθέστε ένα στην καρτέλα του.")

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    tk = ClientPortalToken(
        token=token,
        client_id=payload.client_id,
        client_name=client.get("name", ""),
        expires_at=expires.isoformat(),
    )
    await db.client_portal_tokens.insert_one(tk.to_mongo())

    portal_link = f"{payload.origin_url}/portal/{token}" if payload.origin_url else f"/portal/{token}"

    # Στείλε email αν είναι διαθέσιμο SMTP
    settings = await db.settings.find_one({}) or {}
    email_sent = False
    from email_utils import smtp_ready, send_email_with_pdf
    if smtp_ready(settings):
        try:
            company = settings.get("company_name") or "Nomos One"
            subject = f"Πρόσβαση στον πελατολογικό σας φάκελο — {company}"
            html = f"""
            <html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:560px;">
              <div style="background:#0a0f1e;padding:20px 24px;border-bottom:3px solid #c9a227;">
                <span style="color:#c9a227;font-size:20px;font-weight:bold;letter-spacing:2px;">NOMOS ONE</span>
              </div>
              <div style="padding:24px;">
                <p>Αγαπητέ/ή <strong>{client.get('name')}</strong>,</p>
                <p>Σας παρέχουμε ασφαλή πρόσβαση στον πελατολογικό σας φάκελο.
                Μπορείτε να δείτε τα τιμολόγιά σας, το ιστορικό υποθέσεων και να πληρώσετε online.</p>
                <p style="margin:20px 0;">
                  <a href="{portal_link}" style="background:#c9a227;color:#0a0f1e;padding:12px 24px;
                     text-decoration:none;font-weight:600;border-radius:4px;">Είσοδος στον φάκελο</a>
                </p>
                <p style="font-size:11px;color:#8a93a8;">Ο σύνδεσμος λήγει σε 30 ημέρες.
                Αν δεν ζητήσατε πρόσβαση, αγνοήστε αυτό το email.</p>
              </div>
            </body></html>
            """
            text = f"Αγαπητέ/ή {client.get('name')},\n\nΠρόσβαση στον φάκελό σας: {portal_link}\n\nΛήγει σε 30 ημέρες."
            await send_email_with_pdf(settings, client["email"], subject, text, html)
            email_sent = True
        except Exception:
            pass

    return {
        "message": f"Δημιουργήθηκε σύνδεσμος πρόσβασης για {client.get('name')}." + (" Στάλθηκε email." if email_sent else ""),
        "token": token,
        "portal_link": portal_link,
        "expires_at": expires.isoformat(),
        "email_sent": email_sent,
    }


@router.get("/tokens/{client_id}")
async def list_client_tokens(client_id: str, user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    tokens = await db.client_portal_tokens.find({"client_id": client_id}).sort("created_at", -1).to_list(20)
    return [{
        "id": str(t["_id"]),
        "token": t["token"][:8] + "…" + t["token"][-4:],
        "created_at": t.get("created_at"),
        "expires_at": t.get("expires_at"),
        "last_used_at": t.get("last_used_at"),
        "active": t.get("expires_at", "") > now,
    } for t in tokens]


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, user: dict = Depends(get_current_user)):
    try:
        result = await db.client_portal_tokens.delete_one({"_id": ObjectId(token_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Το token δεν βρέθηκε")
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το token δεν βρέθηκε")
    return {"message": "Ο σύνδεσμος ανακλήθηκε"}


# === PUBLIC ENDPOINTS (no auth, token-based) ===

async def _validate_token(token: str) -> dict:
    tk = await db.client_portal_tokens.find_one({"token": token})
    if not tk:
        raise HTTPException(status_code=404, detail="Ο σύνδεσμος δεν είναι έγκυρος")
    if tk.get("expires_at", "") < datetime.now(timezone.utc).isoformat():
        raise HTTPException(status_code=401, detail="Ο σύνδεσμος έχει λήξει")
    await db.client_portal_tokens.update_one(
        {"_id": tk["_id"]},
        {"$set": {"last_used_at": datetime.now(timezone.utc).isoformat()}},
    )
    return tk


@public_router.get("/{token}")
async def portal_data(token: str):
    """Επιστρέφει όλα τα δεδομένα που βλέπει ο πελάτης."""
    tk = await _validate_token(token)
    client_id = tk["client_id"]

    settings = await db.settings.find_one({}) or {}
    try:
        client = await db.clients.find_one({"_id": ObjectId(client_id)})
    except Exception:
        client = None
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")

    invoices = await db.invoices.find({"client_id": client_id, "status": {"$in": ["issued", "paid"]}}).sort("date", -1).to_list(200)
    cases = await db.cases.find({"client_id": client_id}).sort("created_at", -1).to_list(50)
    trust = await db.trust_accounts.find_one({"client_id": client_id})

    return {
        "firm": {
            "name": settings.get("company_name", "Nomos One"),
            "address": settings.get("address", ""),
            "phone": settings.get("phone", ""),
            "email": settings.get("email", ""),
        },
        "client": {
            "name": client.get("name", ""),
            "afm": client.get("afm", ""),
            "email": client.get("email", ""),
        },
        "invoices": [{
            "id": str(i["_id"]),
            "number": i.get("number", ""),
            "date": i.get("date", ""),
            "total": i.get("total", 0),
            "status": i.get("status", ""),
            "paid_at": i.get("paid_at"),
            "payment_method": i.get("payment_method", ""),
        } for i in invoices],
        "cases": [{
            "id": str(c["_id"]),
            "number": c.get("number", ""),
            "title": c.get("title", ""),
            "court": c.get("court", ""),
            "case_type": c.get("case_type", ""),
            "status": c.get("status", ""),
            "next_action": c.get("next_action", ""),
            "next_action_date": c.get("next_action_date", ""),
        } for c in cases],
        "trust": {
            "balance": trust.get("balance", 0) if trust else 0,
            "total_deposits": trust.get("total_deposits", 0) if trust else 0,
            "total_withdrawals": trust.get("total_withdrawals", 0) if trust else 0,
        } if trust else None,
        "totals": {
            "unpaid_count": sum(1 for i in invoices if i.get("status") == "issued"),
            "unpaid_total": round(sum(float(i.get("total", 0)) for i in invoices if i.get("status") == "issued"), 2),
            "paid_count": sum(1 for i in invoices if i.get("status") == "paid"),
            "active_cases": sum(1 for c in cases if c.get("status") == "active"),
        },
    }


@public_router.get("/{token}/invoice/{invoice_id}/pdf")
async def portal_invoice_pdf(token: str, invoice_id: str):
    from fastapi.responses import Response
    from pdf_utils import build_invoice_pdf
    tk = await _validate_token(token)
    try:
        inv = await db.invoices.find_one({"_id": ObjectId(invoice_id)})
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    if not inv or inv.get("client_id") != tk["client_id"]:
        raise HTTPException(status_code=403, detail="Δεν επιτρέπεται η πρόσβαση σε αυτό το τιμολόγιο.")
    client = await db.clients.find_one({"_id": ObjectId(tk["client_id"])})
    settings = await db.settings.find_one({}) or {}
    pdf_bytes = build_invoice_pdf(inv, client or {}, settings)
    safe = (inv.get("number") or "invoice").replace("ΤΠΥ", "TPY")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})
