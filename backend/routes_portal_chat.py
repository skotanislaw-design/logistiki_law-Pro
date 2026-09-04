"""Portal Chat — Ασφαλής συνομιλία δικηγόρου ↔ πελάτη.
Εναλλακτική στο WhatsApp που είναι GDPR problematic. Auto-log στην υπόθεση."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import PortalMessage
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/portal-chat", tags=["portal-chat"])
public_router = APIRouter(prefix="/portal", tags=["portal-chat-public"])


class MessageIn(BaseModel):
    text: str
    case_id: str = ""


# ================= LAWYER-SIDE (authenticated) =================

@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user)):
    """Επιστρέφει όλες τις συνομιλίες ομαδοποιημένες ανά πελάτη."""
    pipeline = [
        {"$sort": {"created_at": -1}},
        {"$group": {
            "_id": "$client_id",
            "client_name": {"$first": "$client_name"},
            "last_message": {"$first": "$text"},
            "last_sender": {"$first": "$sender"},
            "last_at": {"$first": "$created_at"},
            "unread": {"$sum": {"$cond": [{"$and": [{"$eq": ["$sender", "client"]}, {"$eq": ["$read_at", None]}]}, 1, 0]}},
            "total": {"$sum": 1},
        }},
        {"$sort": {"last_at": -1}},
    ]
    convos = []
    pipeline.insert(0, {"$match": {"firm_id": firm_id_for(user)}})
    async for c in db.portal_messages.aggregate(pipeline):
        convos.append({
            "client_id": c["_id"],
            "client_name": c.get("client_name", ""),
            "last_message": c.get("last_message", "")[:80],
            "last_sender": c.get("last_sender", ""),
            "last_at": c.get("last_at"),
            "unread": c.get("unread", 0),
            "total": c.get("total", 0),
        })
    return convos


@router.get("/messages/{client_id}")
async def get_messages(client_id: str, user: dict = Depends(get_current_user)):
    docs = await db.portal_messages.find(tenant_query(user, {"client_id": client_id})).sort("created_at", 1).to_list(500)
    # Auto-mark ως read όσα ήταν από τον client
    await db.portal_messages.update_many(
        tenant_query(user, {"client_id": client_id, "sender": "client", "read_at": None}),
        {"$set": {"read_at": datetime.now(timezone.utc).isoformat()}},
    )
    return [PortalMessage.from_mongo(d) for d in docs]


@router.post("/messages/{client_id}")
async def send_from_lawyer(client_id: str, payload: MessageIn, user: dict = Depends(get_current_user)):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Κενό μήνυμα")
    try:
        client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(client_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")

    msg = PortalMessage(
        client_id=client_id,
        client_name=client.get("name", ""),
        sender="lawyer",
        sender_name=user.get("name") or user["email"],
        text=payload.text.strip(),
        case_id=payload.case_id or None,
        firm_id=firm_id_for(user),
    )
    result = await db.portal_messages.insert_one(msg.to_mongo())

    # Auto-log στην υπόθεση αν έχει δοθεί
    if payload.case_id:
        try:
            from routes_cases import log_case_event
            await log_case_event(payload.case_id, f"💬 Μήνυμα προς πελάτη: {payload.text.strip()[:80]}", user)
        except Exception:
            pass

    doc = await db.portal_messages.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return PortalMessage.from_mongo(doc)


@router.get("/unread-count")
async def unread_count(user: dict = Depends(get_current_user)):
    n = await db.portal_messages.count_documents(tenant_query(user, {"sender": "client", "read_at": None}))
    return {"unread": n}


# ================= CLIENT-SIDE (via portal token) =================

async def _validate_token(token: str) -> dict:
    tk = await db.client_portal_tokens.find_one({"token": token})
    if not tk:
        raise HTTPException(status_code=404, detail="Ο σύνδεσμος δεν είναι έγκυρος")
    if tk.get("expires_at", "") < datetime.now(timezone.utc).isoformat():
        raise HTTPException(status_code=401, detail="Ο σύνδεσμος έχει λήξει")
    return tk


@public_router.get("/{token}/chat")
async def portal_get_messages(token: str):
    tk = await _validate_token(token)
    docs = await db.portal_messages.find({"client_id": tk["client_id"], "firm_id": tk.get("firm_id", "legacy-default-firm")}).sort("created_at", 1).to_list(500)
    # Auto-mark lawyer messages as read from client side
    await db.portal_messages.update_many(
        {"client_id": tk["client_id"], "firm_id": tk.get("firm_id", "legacy-default-firm"), "sender": "lawyer", "read_at": None},
        {"$set": {"read_at": datetime.now(timezone.utc).isoformat()}},
    )
    return [PortalMessage.from_mongo(d) for d in docs]


@public_router.post("/{token}/chat")
async def portal_send_message(token: str, payload: MessageIn):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Κενό μήνυμα")
    tk = await _validate_token(token)
    msg = PortalMessage(
        client_id=tk["client_id"],
        client_name=tk.get("client_name", ""),
        sender="client",
        sender_name=tk.get("client_name", ""),
        text=payload.text.strip(),
        firm_id=tk.get("firm_id", "legacy-default-firm"),
    )
    result = await db.portal_messages.insert_one(msg.to_mongo())
    doc = await db.portal_messages.find_one({"_id": result.inserted_id, "firm_id": tk.get("firm_id", "legacy-default-firm")})
    return PortalMessage.from_mongo(doc)
