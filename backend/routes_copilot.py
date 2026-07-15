from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from datetime import date, datetime, timezone
import tempfile
import os
from db import db
from auth import get_current_user
from models import Invoice, InvoiceLineItem
from tax_utils import calculate_invoice_totals
from copilot import copilot_llm, _try_action
from routes_invoices import _next_invoice_number

router = APIRouter(prefix="/copilot", tags=["copilot"])


class ChatPayload(BaseModel):
    session_id: str
    message: str


def _fmt(v):
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


async def _save_msg(session_id: str, role: str, content: str, meta: dict = None):
    await db.copilot_messages.insert_one({
        "session_id": session_id, "role": role, "content": content,
        "meta": meta or {}, "created_at": datetime.now(timezone.utc).isoformat(),
    })


async def _execute_create_invoice(action: dict, user: dict):
    name = (action.get("client_name") or "").strip().lower()
    if not name:
        return None, "Δεν κατάλαβα για ποιον πελάτη. Πες μου το όνομα του πελάτη."
    clients = await db.clients.find({}).to_list(200)
    matches = [c for c in clients if name in c["name"].lower() or c["name"].lower() in name]
    if not matches:
        available = ", ".join(c["name"] for c in clients[:10]) or "κανένας"
        return None, f"Δεν βρήκα πελάτη «{action.get('client_name')}». Διαθέσιμοι πελάτες: {available}. Θες να τον καταχωρήσεις πρώτα στη σελίδα Πελάτες;"
    if len(matches) > 1:
        return None, "Βρήκα πολλούς πελάτες με παρόμοιο όνομα: " + ", ".join(c["name"] for c in matches[:5]) + ". Ποιον εννοείς;"
    client = matches[0]
    amount = float(action.get("amount") or 0)
    if amount <= 0:
        return None, "Χρειάζομαι το ποσό του τιμολογίου. Πόσο είναι η καθαρή αξία;"
    vat_rate = int(action.get("vat_rate") or 24)
    if vat_rate not in (24, 13, 6, 0):
        vat_rate = 24
    inv = Invoice(
        client_id=str(client["_id"]),
        client_name=client["name"],
        date=date.today().isoformat(),
        line_items=[InvoiceLineItem(description=action.get("description") or "Νομικές υπηρεσίες", quantity=1, unit_price=amount, vat_rate=vat_rate)],
        created_by=user["email"],
    )
    totals = calculate_invoice_totals(inv.line_items)
    inv.subtotal, inv.vat_amount, inv.total, inv.vat_breakdown = totals["subtotal"], totals["vat_amount"], totals["total"], totals["vat_breakdown"]
    inv.number = await _next_invoice_number()
    result = await db.invoices.insert_one(inv.to_mongo())
    doc = await db.invoices.find_one({"_id": result.inserted_id})
    created = Invoice.from_mongo(doc)
    reply = (
        f"✅ Δημιούργησα το τιμολόγιο **{created.number}** για τον πελάτη **{client['name']}**:\n"
        f"• {action.get('description') or 'Νομικές υπηρεσίες'}\n"
        f"• Καθαρή αξία: {_fmt(created.subtotal)} · ΦΠΑ {vat_rate}%: {_fmt(created.vat_amount)} · Σύνολο: {_fmt(created.total)}\n"
        f"Είναι σε κατάσταση «Πρόχειρο» — μπορείς να το εκδώσεις (myDATA) από τη σελίδα Τιμολόγια."
    )
    return created, reply


@router.post("/chat")
async def copilot_chat(payload: ChatPayload, user: dict = Depends(get_current_user)):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Κενό μήνυμα")
    history = await db.copilot_messages.find({"session_id": payload.session_id}).sort("created_at", 1).to_list(50)
    await _save_msg(payload.session_id, "user", payload.message)

    try:
        raw = await copilot_llm([{"role": m["role"], "content": m["content"]} for m in history], payload.message)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Το Nomos AI δεν είναι διαθέσιμο αυτή τη στιγμή: {e}")

    action = _try_action(raw)
    invoice_data = None
    document_data = None
    reply = raw

    if action and action.get("action") == "create_invoice":
        created, reply = await _execute_create_invoice(action, user)
        if created:
            invoice_data = {"id": created.id, "number": created.number, "client_name": created.client_name, "total": created.total}
    elif action and action.get("action") == "draft_document":
        document_data = {
            "recipient": action.get("recipient") or "",
            "subject": action.get("subject") or "",
            "body": action.get("body") or "",
        }
        reply = f"📄 Συνέταξα το έγγραφο «{document_data['subject']}». Δες την προεπισκόπηση — μπορείς να το κατεβάσεις σε Word ή PDF με το letterhead της εταιρείας."

    await _save_msg(payload.session_id, "assistant", reply, {"invoice": invoice_data, "document": document_data})
    return {"reply": reply, "invoice": invoice_data, "document": document_data}


@router.post("/voice")
async def copilot_voice(audio: UploadFile = File(...), user: dict = Depends(get_current_user)):
    from ai_helpers import transcribe_audio
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name
    try:
        transcript = await transcribe_audio(tmp_path)
        if not transcript.strip():
            raise HTTPException(status_code=400, detail="Δεν αναγνωρίστηκε ομιλία. Δοκιμάστε ξανά.")
        return {"transcript": transcript}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία αναγνώρισης φωνής: {e}")
    finally:
        os.unlink(tmp_path)


@router.get("/history/{session_id}")
async def copilot_history(session_id: str, user: dict = Depends(get_current_user)):
    msgs = await db.copilot_messages.find({"session_id": session_id}).sort("created_at", 1).to_list(100)
    return [
        {"role": m["role"], "content": m["content"], "invoice": (m.get("meta") or {}).get("invoice"), "document": (m.get("meta") or {}).get("document")}
        for m in msgs
    ]
