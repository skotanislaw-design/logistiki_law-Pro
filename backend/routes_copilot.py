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
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/copilot", tags=["copilot"])


class ChatPayload(BaseModel):
    session_id: str
    message: str


def _fmt(v):
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


async def _save_msg(session_id: str, role: str, content: str, user: dict, meta: dict = None):
    await db.copilot_messages.insert_one({
        "session_id": session_id, "role": role, "content": content,
        "meta": meta or {}, "created_at": datetime.now(timezone.utc).isoformat(),
        "firm_id": firm_id_for(user),
    })


async def _execute_create_invoice(action: dict, user: dict):
    name = (action.get("client_name") or "").strip().lower()
    if not name:
        return None, "Δεν κατάλαβα για ποιον πελάτη. Πες μου το όνομα του πελάτη."
    clients = await db.clients.find(tenant_query(user)).to_list(200)
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
        firm_id=firm_id_for(user),
    )
    totals = calculate_invoice_totals(inv.line_items)
    inv.subtotal, inv.vat_amount, inv.total, inv.vat_breakdown = totals["subtotal"], totals["vat_amount"], totals["total"], totals["vat_breakdown"]
    inv.number = await _next_invoice_number(firm_id_for(user))
    result = await db.invoices.insert_one(inv.to_mongo())
    doc = await db.invoices.find_one(tenant_query(user, {"_id": result.inserted_id}))
    created = Invoice.from_mongo(doc)
    reply = (
        f"✅ Δημιούργησα το τιμολόγιο **{created.number}** για τον πελάτη **{client['name']}**:\n"
        f"• {action.get('description') or 'Νομικές υπηρεσίες'}\n"
        f"• Καθαρή αξία: {_fmt(created.subtotal)} · ΦΠΑ {vat_rate}%: {_fmt(created.vat_amount)} · Σύνολο: {_fmt(created.total)}\n"
        f"Είναι σε κατάσταση «Πρόχειρο» — μπορείς να το εκδώσεις (myDATA) από τη σελίδα Τιμολόγια."
    )
    return created, reply


async def _execute_create_grammatio(action: dict, user: dict):
    from models import Grammatio
    from routes_grammatia import GRAMMATIA_REFERENCE, _next_number as _next_grammatio_number
    from routes_cases import log_case_event
    ref = (action.get("case_ref") or "").strip().lower()
    if not ref:
        return None, "Για ποια υπόθεση θες το γραμμάτιο; Πες μου τον αριθμό ή τον τίτλο της."
    cases = await db.cases.find(tenant_query(user, {})).to_list(200)
    matches = [c for c in cases if ref in (c.get("number") or "").lower() or ref in (c.get("title") or "").lower()]
    if not matches:
        available = ", ".join(f"{c['number']} ({c['title'][:30]})" for c in cases[:8]) or "καμία"
        return None, f"Δεν βρήκα υπόθεση «{action.get('case_ref')}». Διαθέσιμες: {available}"
    if len(matches) > 1:
        return None, "Βρήκα πολλές υποθέσεις: " + ", ".join(c["number"] + " — " + c["title"][:40] for c in matches[:5]) + ". Ποια εννοείς;"
    case = matches[0]
    court_type = action.get("court_type") or ""
    procedure = action.get("procedure") or ""
    amount = float(action.get("reference_amount") or 0)
    if amount <= 0:
        found = next((r for r in GRAMMATIA_REFERENCE if court_type.lower() in r["court_type"].lower() or r["court_type"].lower() in court_type.lower()), None)
        if found:
            amount = found["amount"]
            court_type = found["court_type"]
            procedure = procedure or found["procedure"]
        else:
            return None, f"Δεν βρήκα «{court_type}» στον πίνακα αναφοράς. Πες μου το δικαστήριο (π.χ. Ειρηνοδικείο, Μονομελές Πρωτοδικείο, Εφετείο) ή το ποσό."
    contributions = round(amount * 0.12, 2)
    vat = round(amount * 0.24, 2)
    g = Grammatio(
        case_id=case["id"] if isinstance(case.get("id"), str) else str(case["_id"]),
        case_title=case.get("title", ""),
        client_id=case.get("client_id", ""),
        client_name=case.get("client_name", ""),
        court_type=court_type,
        procedure=procedure,
        reference_amount=amount,
        contributions_amount=contributions,
        vat_amount=vat,
        total=round(amount + contributions + vat, 2),
        created_by=user["email"],
    )
    g.firm_id = firm_id_for(user)
    g.number = await _next_grammatio_number(firm_id_for(user))
    result = await db.grammatia.insert_one(g.to_mongo())
    await log_case_event(str(case["_id"]), f"Δημιουργία γραμματίου {g.number} μέσω Nomos AI", user)
    doc = await db.grammatia.find_one(tenant_query(user, {"_id": result.inserted_id}))
    from models import Grammatio as _G
    created = _G.from_mongo(doc)
    reply = (
        f"✅ Ετοίμασα το γραμμάτιο **{created.number}** για την υπόθεση **{case['number']} — {case['title']}**:\n"
        f"• {court_type}{' — ' + procedure if procedure else ''}\n"
        f"• Αμοιβή αναφοράς: {_fmt(amount)} · Εισφορές: {_fmt(contributions)} · ΦΠΑ: {_fmt(vat)} · Σύνολο: {_fmt(created.total)}\n"
        f"Είναι ΠΡΟΣΧΕΔΙΟ — για την έκδοση χρειάζεται η επιβεβαίωσή σου (Γραμμάτια → Έκδοση, μέσω portal.olomeleia.gr)."
    )
    return created, reply


@router.post("/chat")
async def copilot_chat(payload: ChatPayload, user: dict = Depends(get_current_user)):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Κενό μήνυμα")
    history = await db.copilot_messages.find(tenant_query(user, {"session_id": payload.session_id})).sort("created_at", 1).to_list(50)
    await _save_msg(payload.session_id, "user", payload.message, user)

    try:
        raw = await copilot_llm([{"role": m["role"], "content": m["content"]} for m in history], payload.message, user)
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
    elif action and action.get("action") == "create_grammatio":
        created, reply = await _execute_create_grammatio(action, user)
        if created:
            invoice_data = None
            document_data = None
            await _save_msg(payload.session_id, "assistant", reply, user, {"grammatio": {"id": created.id, "number": created.number, "total": created.total}})
            return {"reply": reply, "invoice": None, "document": None, "grammatio": {"id": created.id, "number": created.number, "total": created.total}}
    elif action and action.get("action") == "draft_document":
        document_data = {
            "recipient": action.get("recipient") or "",
            "subject": action.get("subject") or "",
            "body": action.get("body") or "",
        }
        reply = f"📄 Συνέταξα το έγγραφο «{document_data['subject']}». Δες την προεπισκόπηση — μπορείς να το κατεβάσεις σε Word ή PDF με το letterhead της εταιρείας."

    await _save_msg(payload.session_id, "assistant", reply, user, {"invoice": invoice_data, "document": document_data})
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
    msgs = await db.copilot_messages.find(tenant_query(user, {"session_id": session_id})).sort("created_at", 1).to_list(100)
    return [
        {"role": m["role"], "content": m["content"], "invoice": (m.get("meta") or {}).get("invoice"), "document": (m.get("meta") or {}).get("document"), "grammatio": (m.get("meta") or {}).get("grammatio")}
        for m in msgs
    ]
