"""Portal Chat — Ασφαλής συνομιλία δικηγόρου ↔ πελάτη.
Εναλλακτική στο WhatsApp που είναι GDPR problematic. Auto-log στην υπόθεση."""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from bson import ObjectId
from db import db, fetch_capped
from http_utils import disclose_truncation
from models import PortalMessage
from auth import get_current_user
from rate_limit import enforce, key_for_token
from routes_client_portal import _validate_token
from tenant import tenant_query, firm_id_for

# Το portal δεχόταν μήνυμα δύο εκατομμυρίων χαρακτήρων και το αποθήκευε ολόκληρο, καθώς
# και 60 διαδοχικά μηνύματα χωρίς παύση: και τα δύο επαληθεύτηκαν σε ζωντανή δοκιμή.
# Δέκα μηνύματα το λεπτό καλύπτουν άνετα κάθε πραγματική συνομιλία.
CHAT_LIMIT_PER_MINUTE = 10
MAX_MESSAGE_LENGTH = 4000
VISIBLE_MESSAGES = 500        # πόσα μηνύματα δείχνει μια συνομιλία: τα ΝΕΟΤΕΡΑ (παλιότερα: τα παλαιότερα, και τα νεότερα χάνονταν)

router = APIRouter(prefix="/portal-chat", tags=["portal-chat"])
public_router = APIRouter(prefix="/portal", tags=["portal-chat-public"])


class MessageIn(BaseModel):
    # Το όριο μπαίνει στο μοντέλο αιτήματος, όχι στο ``PortalMessage``: από εδώ δεν
    # διαβάζονται ποτέ υπάρχουσες εγγραφές, οπότε δεν κινδυνεύει παλιό μήνυμα να γίνει
    # μη αναγνώσιμο. Η απόρριψη είναι 422, πριν αγγίξει τη βάση.
    text: str = Field(max_length=MAX_MESSAGE_LENGTH)
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


def _before_filter(before: Optional[str]) -> dict:
    """Το φίλτρο «παλαιότερα από» για τη σελίδα που ζητά ο χρήστης. Άκυρη τιμή: 400 (δεν φτάνει ποτέ στη βάση).

    Οι ημερομηνίες των μηνυμάτων είναι κείμενα που ταξινομούνται αλφαβητικά (`now_iso()`: πάντα UTC, `+00:00`). Η
    `fromisoformat` της Python 3.11 δέχεται όμως και `…Z`, άλλη ζώνη ώρας, `20261001T100000`, εβδομάδα ISO ή μόνο ημερομηνία —
    μορφές που, συγκρινόμενες ως κείμενο, θα έβγαζαν σελίδα με διπλά ή χαμένα μηνύματα. Γι' αυτό το όριο μετατρέπεται στην
    ίδια στιγμή σε UTC και γράφεται όπως τα αποθηκευμένα (χωρίς ζώνη ώρας εννοείται UTC). Ο πελάτης (frontend) στέλνει πίσω
    ακριβώς το `created_at` που πήρε, οπότε κανονικά η μετατροπή δεν αλλάζει τίποτα."""
    if before is None:
        return {}
    try:
        moment = datetime.fromisoformat(before)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        boundary = moment.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(status_code=400, detail="Μη έγκυρη ημερομηνία για τα παλαιότερα μηνύματα.")
    return {"created_at": {"$lt": boundary}}


async def _newest_messages(query: dict, before: Optional[str] = None) -> tuple[list[dict], bool]:
    """Τα ``VISIBLE_MESSAGES`` νεότερα μηνύματα (ή, με ``before``, τα νεότερα από όσα προηγούνται εκείνης της στιγμής), με το
    παλαιότερο πρώτο, και αν υπάρχουν κι άλλα παλαιότερα.

    Το ``sort(created_at, 1).to_list(500)`` επέστρεφε τα **παλαιότερα** 500: σε μακρά συνομιλία τα νεότερα μηνύματα δεν
    φαίνονταν ποτέ — ούτε στον δικηγόρο ούτε στον πελάτη. Και τα μηνύματα έξω από το παράθυρο έμεναν αδιάβαστα για πάντα,
    χωρίς τρόπο να εμφανιστούν· τώρα η απάντηση δηλώνει ότι υπάρχουν παλαιότερα (``X-List-Truncated``) και το
    ``?before=<created_at του παλαιότερου που βλέπετε>`` φέρνει την προηγούμενη σελίδα."""
    page_query = {**query, **_before_filter(before)}
    newest_first, more = await fetch_capped(db.portal_messages.find(page_query).sort("created_at", -1), VISIBLE_MESSAGES)
    return newest_first[::-1], more


async def _mark_read(shown: list[dict], sender: str, base_query: dict) -> None:
    """Σημαίνει ως διαβασμένα μόνο όσα μηνύματα του ``sender`` **εμφανίστηκαν**· ό,τι έμεινε έξω από το παράθυρο
    παραμένει αδιάβαστο (και μετρά στον μετρητή αδιάβαστων), αντί να χαθεί σιωπηλά."""
    ids = [d["_id"] for d in shown if d.get("sender") == sender and d.get("read_at") is None]
    if ids:
        await db.portal_messages.update_many(
            {**base_query, "_id": {"$in": ids}},
            {"$set": {"read_at": datetime.now(timezone.utc).isoformat()}},
        )


@router.get("/messages/{client_id}")
async def get_messages(client_id: str, response: Response, before: Optional[str] = None, user: dict = Depends(get_current_user)):
    query = tenant_query(user, {"client_id": client_id})
    docs, more = await _newest_messages(query, before)
    disclose_truncation(response, more, VISIBLE_MESSAGES)
    await _mark_read(docs, "client", query)               # Auto-mark ως read όσα ήταν από τον client
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
#
# Η επικύρωση του token γίνεται από τη μοναδική υλοποίηση στο ``routes_client_portal``.
# Το τοπικό αντίγραφο που υπήρχε εδώ δεν ενημέρωνε το ``last_used_at``, με αποτέλεσμα ο
# δικηγόρος να βλέπει ως αχρησιμοποίητο έναν σύνδεσμο που ο πελάτης χρησιμοποιούσε.


@public_router.get("/{token}/chat")
async def portal_get_messages(token: str, request: Request, response: Response, before: Optional[str] = None):
    tk = await _validate_token(token, request)
    query = {"client_id": tk["client_id"], "firm_id": tk.get("firm_id", "legacy-default-firm")}
    docs, more = await _newest_messages(query, before)
    disclose_truncation(response, more, VISIBLE_MESSAGES)
    await _mark_read(docs, "lawyer", query)               # Auto-mark lawyer messages as read from client side
    return [PortalMessage.from_mongo(d) for d in docs]


@public_router.post("/{token}/chat")
async def portal_send_message(token: str, payload: MessageIn, request: Request):
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Κενό μήνυμα")
    tk = await _validate_token(token, request)
    await enforce("portal:chat", key_for_token(token), CHAT_LIMIT_PER_MINUTE, 60,
                  "Στείλατε πολλά μηνύματα. Περιμένετε λίγο πριν στείλετε ξανά.")
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
