"""Backup/Restore & GDPR export.
- Ολικό backup: εξάγει όλες τις συλλογές (εκτός password_hashes και tokens) σε ZIP με JSON files.
- Restore: διαβάζει ZIP και επαναφέρει τα δεδομένα (τα ήδη υπάρχοντα με ίδιο _id αντικαθίστανται).
- GDPR export ανά πελάτη: εξάγει ό,τι αφορά τον συγκεκριμένο πελάτη (Data Subject Request).
"""

import io
import re
import json
import zipfile
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from audit import audit_read
from fastapi.responses import Response
from bson import ObjectId
from db import db
from http_utils import content_disposition
from auth import get_current_user, require_admin
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/backup", tags=["backup"])

# Συλλογές που εξάγουμε ολικά (χωρίς ευαίσθητα auth data)
COLLECTIONS = [
    "clients", "invoices", "expenses", "payroll_employees", "payroll_runs",
    "cases", "grammatia", "paravola", "hearings", "settings",
    "trust_accounts", "trust_transactions", "documents", "counters",
    "kyc", "signatures", "payment_transactions", "client_portal_tokens",
    "audit_log", "copilot_messages",
]


def _scope(user: dict, collection: str) -> dict:
    """Counters are keyed by firm in their _id; everything else carries firm_id."""
    if collection == "counters":
        return {"_id": {"$regex": f"-{re.escape(firm_id_for(user))}-"}}
    return tenant_query(user)


def _to_json_safe(doc):
    """Μετατρέπει ObjectIds σε strings ώστε να γίνεται JSON serialization."""
    if isinstance(doc, dict):
        return {k: _to_json_safe(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_to_json_safe(v) for v in doc]
    if isinstance(doc, ObjectId):
        return {"__oid__": str(doc)}
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def _from_json_safe(doc):
    """Αντίστροφη μετατροπή για restore."""
    if isinstance(doc, dict):
        if "__oid__" in doc and len(doc) == 1:
            return ObjectId(doc["__oid__"])
        return {k: _from_json_safe(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_from_json_safe(v) for v in doc]
    return doc


@router.get("/export")
async def export_backup(request: Request, user: dict = Depends(require_admin)):
    """Πλήρες ZIP backup όλων των collections."""
    await audit_read(request, user, "backup_export", details="Πλήρης εξαγωγή όλων των δεδομένων του γραφείου")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in COLLECTIONS:
            docs = await db[name].find(_scope(user, name)).to_list(50000)
            counts[name] = len(docs)
            safe = [_to_json_safe(d) for d in docs]
            zf.writestr(f"{name}.json", json.dumps(safe, ensure_ascii=False, indent=2))
        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": user["email"],
            "version": 1,
            "counts": counts,
        }
        zf.writestr("_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"nomos-one-backup-{ts}.zip")},
    )


@router.post("/import")
async def import_backup(file: UploadFile = File(...), user: dict = Depends(require_admin)):
    """Restore από ZIP. Κάνει upsert με βάση το _id."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Το αρχείο backup είναι κενό.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Μη έγκυρο αρχείο ZIP.")
    names = set(zf.namelist())
    if "_meta.json" not in names:
        raise HTTPException(status_code=400, detail="Το ZIP δεν είναι έγκυρο backup του Nomos One (λείπει _meta.json).")

    restored = {}
    errors = []
    for coll in COLLECTIONS:
        fname = f"{coll}.json"
        if fname not in names:
            continue
        try:
            data = json.loads(zf.read(fname).decode("utf-8"))
        except Exception as e:
            errors.append(f"{fname}: {e}")
            continue
        n = 0
        for raw in data:
            doc = _from_json_safe(raw)
            if coll != "counters":
                # Never let a restored file re-assign documents to another firm.
                doc["firm_id"] = firm_id_for(user)
            _id = doc.get("_id")
            if _id is not None:
                await db[coll].replace_one({"_id": _id}, doc, upsert=True)
            else:
                await db[coll].insert_one(doc)
            n += 1
        restored[coll] = n

    return {
        "message": "Η επαναφορά ολοκληρώθηκε.",
        "restored": restored,
        "errors": errors[:15],
    }


@router.get("/gdpr/client/{client_id}")
async def gdpr_export_client(client_id: str, request: Request, user: dict = Depends(get_current_user)):
    """DSR (Data Subject Request) — εξάγει όλα τα δεδομένα ενός πελάτη σε ZIP.
    Απαιτούμενο από τον GDPR (άρθρο 15 & 20)."""
    try:
        client = await db.clients.find_one(tenant_query(user, {"_id": ObjectId(client_id)}))
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη.")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε.")
    await audit_read(request, user, "gdpr_export", client_id,
                     details=f"Εξαγωγή δεδομένων πελάτη «{client.get('name', '')}» (ΓΚΠΔ άρ. 15 & 20)")

    invoices = await db.invoices.find(tenant_query(user, {"client_id": client_id})).to_list(5000)
    cases = await db.cases.find(tenant_query(user, {"client_id": client_id})).to_list(5000)
    grammatia = await db.grammatia.find(tenant_query(user, {"client_id": client_id})).to_list(5000)
    trust_accounts = await db.trust_accounts.find(tenant_query(user, {"client_id": client_id})).to_list(100)
    trust_txs = await db.trust_transactions.find(tenant_query(user, {"client_id": client_id})).to_list(5000)

    dataset = {
        "client": _to_json_safe(client),
        "invoices": [_to_json_safe(d) for d in invoices],
        "cases": [_to_json_safe(d) for d in cases],
        "grammatia": [_to_json_safe(d) for d in grammatia],
        "trust_accounts": [_to_json_safe(d) for d in trust_accounts],
        "trust_transactions": [_to_json_safe(d) for d in trust_txs],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(dataset, ensure_ascii=False, indent=2))
        zf.writestr("_readme.txt", (
            "Nomos One — GDPR Data Subject Access (Άρθρα 15 & 20 ΓΚΠΔ)\n"
            f"Πελάτης: {client.get('name', '')}\n"
            f"Εξήχθη: {datetime.now(timezone.utc).isoformat()}\n"
            f"Εξήχθη από: {user['email']}\n"
            "Το data.json περιέχει όλα τα προσωπικά δεδομένα που τηρούνται στο σύστημα Nomos One.\n"
        ))
    buf.seek(0)
    safe_name = (client.get("name", "client")).replace(" ", "_")[:40]
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"gdpr-{safe_name}.zip")},
    )
