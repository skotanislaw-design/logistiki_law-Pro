"""Client Portal — Δημόσιο portal για πελάτες με magic-link token.
Ο δικηγόρος στέλνει στον πελάτη magic link (via email) → ο πελάτης βλέπει τιμολόγια/υποθέσεις/έγγραφα του.
"""
import html
import os
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from bson import ObjectId
from audit import audit_portal_read
from db import db, fetch_all
from http_utils import allowed_origins, checked_return_url, configured_origin, content_disposition
from models import ClientPortalToken
from auth import get_current_user
from rate_limit import enforce, key_for_token
from tenant import tenant_query, firm_id_for

# Όρια ανά λεπτό για τη **δημόσια** επιφάνεια. Το portal δεν είχε κανένα: επαληθεύτηκε
# ότι δεχόταν 60 διαδοχικά αιτήματα χωρίς αντίσταση. Τα όρια είναι γενναιόδωρα για
# πραγματικό πελάτη (το frontend κάνει polling) και σφιχτά για αυτοματοποιημένη κατάχρηση.
PORTAL_TOKEN_LIMIT = 60      # ανά token
PORTAL_IP_LIMIT = 120        # ανά IP, ώστε να πιάνεται και η σάρωση πολλών tokens
PORTAL_PAID_LIMIT = 200      # πόσα εξοφλημένα τιμολόγια δείχνει η πύλη ως ιστορικό (τα ανεξόφλητα δείχνονται ΠΑΝΤΑ όλα)

router = APIRouter(prefix="/client-portal", tags=["client-portal"])
public_router = APIRouter(prefix="/portal", tags=["portal-public"])


class InviteRequest(BaseModel):
    client_id: str
    origin_url: str = ""


def _portal_base(origin_url: str, settings: dict) -> str:
    """Η διεύθυνση της εφαρμογής για τον σύνδεσμο του email, επικυρωμένη (βλ. ``http_utils.checked_return_url``).

    Ο σύνδεσμος φτάνει στον πελάτη σε email του γραφείου· αν το ``origin_url`` του αιτήματος μπαινόταν αυτούσιο, ένας
    σύνδεσμος προς ξένο ιστότοπο θα έφτανε ως «ασφαλής πρόσβαση στον φάκελό σας». Επιτρέπονται οι διευθύνσεις της
    εγκατάστασης και η ``public_url`` των Ρυθμίσεων *αυτού* του γραφείου. Χωρίς διεύθυνση από τον browser, χρησιμοποιείται η
    ``public_url`` των Ρυθμίσεων και μετά το ``PUBLIC_URL``· χωρίς καμία, ο σύνδεσμος μένει σχετικός (``/portal/<token>``).
    """
    public_url = settings.get("public_url")
    if (origin_url or "").strip():
        base = checked_return_url(origin_url, [*allowed_origins(), *([public_url] if public_url else [])])
        if base is None:
            raise HTTPException(
                status_code=400,
                detail="Μη έγκυρη διεύθυνση εφαρμογής για τον σύνδεσμο. Ανοίξτε την εφαρμογή από τη διεύθυνση του γραφείου.")
        return base
    return configured_origin(public_url, os.environ.get("PUBLIC_URL"))


@router.post("/invite")
async def invite_client(payload: InviteRequest, user: dict = Depends(get_current_user)):
    """Δημιουργεί magic link για πρόσβαση πελάτη και στέλνει email."""
    try:
        client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(payload.client_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    if not client.get("email"):
        raise HTTPException(status_code=400, detail="Ο πελάτης δεν έχει email — προσθέστε ένα στην καρτέλα του.")

    settings = await db.settings.find_one(tenant_query(user, {})) or {}
    base = _portal_base(payload.origin_url, settings)              # πριν δημιουργηθεί token: το απορριπτέο αίτημα δεν αφήνει ίχνος

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=30)
    tk = ClientPortalToken(
        token=token,
        client_id=payload.client_id,
        client_name=client.get("name", ""),
        expires_at=expires.isoformat(),
        firm_id=firm_id_for(user),
    )
    await db.client_portal_tokens.insert_one(tk.to_mongo())

    portal_link = f"{base}/portal/{token}"

    # Στείλε email αν είναι διαθέσιμο SMTP
    email_sent = False
    from email_utils import smtp_ready, send_email_with_pdf
    if smtp_ready(settings):
        try:
            company = settings.get("company_name") or "Nomos One"
            subject = f"Πρόσβαση στον πελατολογικό σας φάκελο — {company}"
            # Ό,τι μπαίνει στο HTML περνά από escape: το όνομα του πελάτη (εισαγωγές, OCR) δεν είναι αξιόπιστο κείμενο.
            html_body = f"""
            <html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:560px;">
              <div style="background:#0a0f1e;padding:20px 24px;border-bottom:3px solid #c9a227;">
                <span style="color:#c9a227;font-size:20px;font-weight:bold;letter-spacing:2px;">NOMOS ONE</span>
              </div>
              <div style="padding:24px;">
                <p>Αγαπητέ/ή <strong>{html.escape(str(client.get('name') or ''))}</strong>,</p>
                <p>Σας παρέχουμε ασφαλή πρόσβαση στον πελατολογικό σας φάκελο.
                Μπορείτε να δείτε τα τιμολόγιά σας, το ιστορικό υποθέσεων και να πληρώσετε online.</p>
                <p style="margin:20px 0;">
                  <a href="{html.escape(portal_link)}" style="background:#c9a227;color:#0a0f1e;padding:12px 24px;
                     text-decoration:none;font-weight:600;border-radius:4px;">Είσοδος στον φάκελο</a>
                </p>
                <p style="font-size:11px;color:#8a93a8;">Ο σύνδεσμος λήγει σε 30 ημέρες.
                Αν δεν ζητήσατε πρόσβαση, αγνοήστε αυτό το email.</p>
              </div>
            </body></html>
            """
            text = f"Αγαπητέ/ή {client.get('name')},\n\nΠρόσβαση στον φάκελό σας: {portal_link}\n\nΛήγει σε 30 ημέρες."
            await send_email_with_pdf(settings, client["email"], subject, text, html_body)
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
    tokens = await db.client_portal_tokens.find(tenant_query(user, {"client_id": client_id})).sort("created_at", -1).to_list(20)
    return [{
        "id": str(t["_id"]),
        "token": t["token"][:8] + "…" + t["token"][-4:],
        "created_at": t.get("created_at"),
        "expires_at": t.get("expires_at"),
        "last_used_at": t.get("last_used_at"),
        # Ίδιος κανόνας λήξης με το ``_validate_token``: αν η οθόνη του δικηγόρου
        # έκρινε αλλιώς, θα έδειχνε ως ενεργό σύνδεσμο που το portal απορρίπτει.
        "active": not _is_expired(t.get("expires_at", "")),
    } for t in tokens]


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, user: dict = Depends(get_current_user)):
    try:
        result = await db.client_portal_tokens.delete_one(tenant_query(user, {"_id": ObjectId(token_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Το token δεν βρέθηκε")
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Το token δεν βρέθηκε")
    return {"message": "Ο σύνδεσμος ανακλήθηκε"}


# === PUBLIC ENDPOINTS (no auth, token-based) ===

def _is_expired(expires_at: str) -> bool:
    """Λήξη με σύγκριση ημερομηνιών, όχι συμβολοσειρών.

    Η σύγκριση ISO συμβολοσειρών δούλευε όσο και τα δύο μέλη παράγονταν από τον ίδιο
    κώδικα. Ένα token γραμμένο με άλλη ζώνη ώρας ή χωρίς μικροδευτερόλεπτα θα έσπαγε
    σιωπηλά τη σύγκριση — και το σφάλμα θα ήταν προς την επικίνδυνη κατεύθυνση.
    Απουσία ή μη αναγνώσιμη τιμή θεωρείται ληγμένη.
    """
    if not expires_at:
        return True
    try:
        exp = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


async def _validate_token(token: str, request: Request | None = None) -> dict:
    """Επικύρωση portal token — **η μοναδική** υλοποίηση για όλη τη δημόσια επιφάνεια.

    Υπήρχε δεύτερο, σχεδόν πανομοιότυπο αντίγραφο στο ``routes_portal_chat.py`` που δεν
    ενημέρωνε το ``last_used_at``. Αποτέλεσμα: αν ο πελάτης χρησιμοποιούσε μόνο τη
    συνομιλία, η στήλη «τελευταία χρήση» στην οθόνη του δικηγόρου έλεγε ψέματα — και ο
    δικηγόρος θα ανακαλούσε έναν σύνδεσμο που φαινόταν αχρησιμοποίητος ενώ ήταν ενεργός.
    """
    # Ο περιορισμός προηγείται της αναζήτησης: το κόστος του είναι μία εγγραφή με κλειδί
    # το ``_id``, ενώ κάθε αποτυχημένη δοκιμή token χτυπούσε τη collection των tokens.
    await enforce("portal:token", key_for_token(token), PORTAL_TOKEN_LIMIT, 60)
    if request is not None and request.client:
        await enforce("portal:ip", request.client.host, PORTAL_IP_LIMIT, 60)

    tk = await db.client_portal_tokens.find_one({"token": token})
    if not tk:
        raise HTTPException(status_code=404, detail="Ο σύνδεσμος δεν είναι έγκυρος")
    if request is not None:
        # Για το ελεγκτικό ίχνος: το γραφείο και ο πελάτης πίσω από το token (όχι το ίδιο το token). Ορίζεται πριν τον
        # έλεγχο λήξης ώστε και η προσπάθεια με ληγμένο σύνδεσμο να φαίνεται στο γραφείο που ανήκει.
        request.state.audit_portal = {"firm_id": tk.get("firm_id"), "client_id": tk.get("client_id")}
    if _is_expired(tk.get("expires_at", "")):
        raise HTTPException(status_code=401, detail="Ο σύνδεσμος έχει λήξει")
    await db.client_portal_tokens.update_one(
        {"_id": tk["_id"]},
        {"$set": {"last_used_at": datetime.now(timezone.utc).isoformat()}},
    )
    return tk


@public_router.get("/{token}")
async def portal_data(token: str, request: Request):
    """Επιστρέφει όλα τα δεδομένα που βλέπει ο πελάτης."""
    tk = await _validate_token(token, request)
    client_id = tk["client_id"]

    settings = await db.settings.find_one({"firm_id": tk.get("firm_id", "legacy-default-firm")}) or {}
    try:
        client = await db.clients.find_one({"_id": ObjectId(client_id), "firm_id": tk.get("firm_id", "legacy-default-firm")})
    except Exception:
        client = None
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")

    firm = tk.get("firm_id", "legacy-default-firm")
    # Όλα τα ανεξόφλητα (ό,τι οφείλει ο πελάτης δεν επιτρέπεται να κρύβεται επειδή το τιμολόγιο είναι παλιό) και, για
    # ιστορικό, τα τελευταία εξοφλημένα. Παλιότερα: «τα 200 πρόσφατα» — ένας πελάτης με πολλά τιμολόγια έβλεπε
    # μικρότερο ποσό οφειλής από το πραγματικό.
    owner = {"client_id": client_id, "firm_id": firm}
    issued = await fetch_all(db.invoices.find({**owner, "status": "issued"}).sort("date", -1))
    paid = await db.invoices.find({**owner, "status": "paid"}).sort("date", -1).limit(PORTAL_PAID_LIMIT).to_list(PORTAL_PAID_LIMIT)
    paid_total = await db.invoices.count_documents({**owner, "status": "paid"})
    invoices = sorted(issued + paid, key=lambda d: d.get("date") or "", reverse=True)
    cases = await db.cases.find({"client_id": client_id, "firm_id": firm}).sort("created_at", -1).to_list(50)
    trust = await db.trust_accounts.find_one({"client_id": client_id, "firm_id": firm})

    # Το ποσό που πράγματι οφείλει ο πελάτης: πληρωτέο (μετά την παρακράτηση που κρατά ο ίδιος) μείον ό,τι
    # έχει εξοφληθεί από τον πελατολογικό του λογαριασμό. Είναι το ίδιο ποσό που χρεώνει το checkout του
    # portal· αν η λίστα έδειχνε μόνο το σύνολο, ο πελάτης θα έβλεπε άλλο ποσό από αυτό που πληρώνει.
    # Ο υπολογισμός είναι κοινός με το dashboard, τις αναλύσεις και τη λίστα τιμολογίων (invoice_services.invoice_balances):
    # το γραφείο και ο πελάτης βλέπουν πάντα τον ίδιο αριθμό. Το πιστωτικό κρατά το αρνητικό ποσό του και συμψηφίζεται.
    from invoice_services import invoice_balances, open_invoices, open_total
    amounts = await invoice_balances(firm, invoices, settings, {client_id: client})

    payload = {
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
            "payable": amounts[str(i["_id"])][0],
            "outstanding": amounts[str(i["_id"])][1],
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
            "unpaid_count": len(open_invoices(invoices, amounts)),
            # Το άθροισμα είναι προσημασμένο: ένα πιστωτικό συμψηφίζει το τιμολόγιο που πιστώνει. Μόνο το σύνολο
            # δεν πέφτει κάτω από το μηδέν (πιστωτικό μεγαλύτερο από τα ανεξόφλητα δεν είναι «οφειλή» του πελάτη).
            "unpaid_total": open_total(invoices, amounts),
            "paid_count": paid_total,
            "active_cases": sum(1 for c in cases if c.get("status") == "active"),
        },
    }
    await audit_portal_read(request, tk, "client_portal",
                            details=f"Άνοιγμα φακέλου: {len(invoices)} τιμολόγια, {len(cases)} υποθέσεις")
    return payload


@public_router.get("/{token}/invoice/{invoice_id}/pdf")
async def portal_invoice_pdf(token: str, invoice_id: str, request: Request):
    from fastapi.responses import Response
    from pdf_utils import build_invoice_pdf
    tk = await _validate_token(token, request)
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    # Μέσα στα τιμολόγια αυτού του πελάτη και γραφείου: το τιμολόγιο άλλου πελάτη είναι ίδιο με ένα που δεν υπάρχει (404),
    # αντί για 403 που θα επιβεβαίωνε ότι υπάρχει (βλ. routes_payments._validate_invoice_and_get_amount).
    inv = await db.invoices.find_one({"_id": oid, "firm_id": tk.get("firm_id", "legacy-default-firm"), "client_id": tk["client_id"]})
    if not inv:
        raise HTTPException(status_code=404, detail="Το τιμολόγιο δεν βρέθηκε")
    client = await db.clients.find_one({"_id": ObjectId(tk["client_id"]), "firm_id": tk.get("firm_id", "legacy-default-firm")})
    settings = await db.settings.find_one({"firm_id": tk.get("firm_id", "legacy-default-firm")}) or {}
    pdf_bytes = build_invoice_pdf(inv, client or {}, settings)
    safe = inv.get("number") or "invoice"
    await audit_portal_read(request, tk, "invoices", invoice_id,
                            details=f"Λήψη PDF τιμολογίου {safe} από το portal")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": content_disposition(f"{safe}.pdf")})
