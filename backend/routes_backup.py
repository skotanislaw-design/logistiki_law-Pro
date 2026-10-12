"""Backup/Restore & GDPR export.
- Ολικό backup: εξάγει τις συλλογές του γραφείου (βλ. COLLECTIONS) σε ZIP με αρχεία JSON. Δεν περιλαμβάνει
  διαπιστευτήρια χρηστών ούτε παράγωγα/προσωρινά δεδομένα — η λίστα και ο λόγος είναι στο NOT_BACKED_UP και
  γράφονται και στο `_meta.json` του αρχείου.
- Restore: διαβάζει ZIP και επαναφέρει τα δεδομένα του γραφείου που το εκτελεί. Τα επιχειρησιακά έγγραφα με ίδιο
  _id αντικαθίστανται· το ελεγκτικό ίχνος (append-only) **μόνο συμπληρώνεται**· οι μετρητές αρίθμησης **μόνο
  προχωρούν**· έγγραφο που ανήκει σε άλλο γραφείο δεν αγγίζεται ποτέ.
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
from pymongo.errors import BulkWriteError, DuplicateKeyError
from db import db
from http_utils import content_disposition
from auth import get_current_user, require_admin
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/backup", tags=["backup"])

# Συλλογές που εξάγονται. Κάθε συλλογή που αγγίζει ο κώδικας πρέπει να είναι εδώ ή στο NOT_BACKED_UP — το
# tests/test_unit_backup_r5.py αποτυγχάνει αλλιώς. Το όνομα «audit_log» (αντί για «audit_logs») και η ανύπαρκτη
# «documents» έμειναν απαρατήρητα μέχρι τον γύρο 5: το αρχείο του ίχνους εξαγόταν πάντα κενό.
COLLECTIONS = [
    "clients", "invoices", "expenses", "payroll_employees", "payroll_runs",
    "cases", "grammatia", "paravola", "hearings", "settings",
    "trust_accounts", "trust_transactions", "counters",
    "kyc", "signatures", "payment_transactions", "client_portal_tokens",
    "audit_logs", "copilot_messages",
    "bank_transactions", "vat_periods", "portal_messages", "case_tracker_events",
    "drafting_templates", "drafting_clauses",
]

# Συλλογές που σκόπιμα ΔΕΝ εξάγονται, με τον λόγο. Γράφονται στο `_meta.json` ώστε όποιος κρατά το αντίγραφο να
# ξέρει τι δεν περιέχει.
NOT_BACKED_UP = {
    "users": "διαπιστευτήρια (κατακερματισμένοι κωδικοί, 2FA)· δεν μεταφέρονται σε αρχείο — ο διαχειριστής δημιουργείται στην εκκίνηση",
    "password_reset_tokens": "προσωρινά διαπιστευτήρια που λήγουν μέσα σε μία ώρα",
    "login_attempts": "μετρητής ασφαλείας· καθαρίζεται μόνος του",
    "rate_limits": "μετρητές ρυθμού αιτημάτων· λήγουν μόνοι τους",
    "automation_locks": "κλειδώματα εκτέλεσης του Autopilot· προσωρινά",
    "automation_runs": "ιστορικό εκτελέσεων του Autopilot· παράγωγο",
    "notifications": "ειδοποιήσεις του Autopilot· ξαναδημιουργούνται από τις εργασίες του",
    "inbox_processed": "κλειδιά αποφυγής διπλοεισαγωγής από το γραμματοκιβώτιο ΑΑΔΕ· τη διπλοεισαγωγή εξόδων την κλείνει ο μοναδικός δείκτης των εξόδων",
    "outbox": "ουρά email· μια επαναφορά θα ξαναέστελνε μηνύματα που έχουν ήδη σταλεί",
}

# Append-only: η επαναφορά δεν αντικαθιστά ποτέ υπάρχουσα γραμμή, μόνο προσθέτει όσες λείπουν.
APPEND_ONLY = ("audit_logs",)
RESTORE_CHUNK = 500
MAX_ERRORS_KEPT = 50
_RESTORE_MARKERS = ("restored_at", "restored_by")


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


async def _write_collection(zf: zipfile.ZipFile, name: str, scope: dict) -> int:
    """Γράφει μία συλλογή στο ZIP **έγγραφο προς έγγραφο**, χωρίς όριο.

    Ο παλιός κώδικας έκανε `find(...).to_list(50000)`: από την 50.001η εγγραφή και μετά το αντίγραφο έκοβε σιωπηλά.
    Ένα αντίγραφο που λείπει το τέλος του δεν φαίνεται λειψό — φαίνεται κανονικό. Εδώ κάθε έγγραφο γράφεται μόλις
    διαβαστεί, άρα ούτε υπάρχει όριο ούτε φορτώνεται ολόκληρη η συλλογή (ή το JSON της) στη μνήμη.
    """
    count = 0
    with zf.open(f"{name}.json", "w", force_zip64=True) as out:
        out.write(b"[")
        async for doc in db[name].find(scope):
            out.write((b",\n" if count else b"\n") + json.dumps(_to_json_safe(doc), ensure_ascii=False).encode("utf-8"))
            count += 1
        out.write(b"\n]" if count else b"]")
    return count


@router.get("/export")
async def export_backup(request: Request, user: dict = Depends(require_admin)):
    """Πλήρες ZIP backup των συλλογών του γραφείου (COLLECTIONS)."""
    await audit_read(request, user, "backup_export", details="Πλήρης εξαγωγή όλων των δεδομένων του γραφείου")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in COLLECTIONS:
            counts[name] = await _write_collection(zf, name, _scope(user, name))
        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generated_by": user["email"],
            "version": 1,
            "counts": counts,
            "not_included": NOT_BACKED_UP,
        }
        zf.writestr("_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"nomos-one-backup-{ts}.zip")},
    )


def _label(doc: dict) -> str:
    """Πώς αναφέρεται ένα έγγραφο σε μήνυμα προς τον χρήστη."""
    return str(doc.get("number") or doc.get("mydata_mark") or doc.get("period") or doc.get("external_id")
               or doc.get("_id"))


def _comparable(doc: dict):
    """Το περιεχόμενο μιας γραμμής χωρίς ό,τι αλλάζει η ίδια η επαναφορά (γραφείο, σήμανση επαναφοράς)."""
    return _to_json_safe({k: v for k, v in doc.items() if k != "firm_id" and k not in _RESTORE_MARKERS})


def _counter_belongs_to_firm(counter_id, firm_id: str) -> bool:
    """Οι μετρητές δεν έχουν `firm_id`· το γραφείο φαίνεται στο `_id` (`invoice-<γραφείο>-<έτος>` ή
    `invoice-<σειρά>-<γραφείο>-<έτος>`)."""
    return isinstance(counter_id, str) and re.fullmatch(
        rf"invoice-(?:[^-]+-)?{re.escape(firm_id)}-\d{{4}}", counter_id) is not None


async def _conflict_message(coll: str, doc: dict, firm_id: str) -> str:
    """Γιατί δεν μπήκε ένα έγγραφο: ανήκει σε ΑΛΛΟ γραφείο ή συγκρούεται με μοναδικό κλειδί του δικού μας."""
    try:
        owner = await db[coll].find_one({"_id": doc.get("_id")}, {"firm_id": 1})
    except Exception:  # noqa: BLE001 — το μήνυμα είναι πληροφορία, όχι έλεγχος
        owner = None
    if owner is not None and owner.get("firm_id") not in (None, firm_id):
        return f"{coll}: το έγγραφο {_label(doc)} ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε"
    # Μοναδικό κλειδί (π.χ. αριθμός τιμολογίου που έχει ήδη άλλο παραστατικό): η επαναφορά δεν σταματά στη μέση —
    # το έγγραφο παραλείπεται και αναφέρεται, ώστε να το δει ο λογιστής.
    return (f"{coll}: το έγγραφο {_label(doc)} συγκρούεται με υπάρχον έγγραφο (μοναδικό κλειδί) "
            "και δεν επαναφέρθηκε")


async def _restore_append_only(coll: str, docs: list, user: dict, fail) -> tuple[int, int, int]:
    """Συμπληρώνει μια append-only συλλογή (το ελεγκτικό ίχνος) με όσες γραμμές ΛΕΙΠΟΥΝ.

    Υπάρχουσα γραμμή δεν αντικαθίσταται ποτέ — ούτε από νεότερο ούτε από παλαιότερο αντίγραφο: ένα ίχνος που μπορεί
    να ξαναγραφεί με μια επαναφορά δεν αποδεικνύει τίποτα. Οι γραμμές που προστίθενται σημαίνονται με
    `restored_at`/`restored_by`, ώστε ο ελεγκτής να ξεχωρίζει ό,τι γράφτηκε ζωντανά από ό,τι επανήλθε από αντίγραφο.
    Αν μια υπάρχουσα γραμμή διαφέρει από αυτήν του αντιγράφου, μένει η υπάρχουσα και η διαφορά αναφέρεται.

    Επιστρέφει `(προστέθηκαν, υπήρχαν_ήδη, διαφέρουν)`.
    """
    firm = firm_id_for(user)
    stamp = datetime.now(timezone.utc).isoformat()
    inserted = present = differing = 0
    for start in range(0, len(docs), RESTORE_CHUNK):
        chunk = docs[start:start + RESTORE_CHUNK]
        batch = [{**d, "restored_at": stamp, "restored_by": user["email"]} for d in chunk]
        clashes = []
        try:
            result = await db[coll].insert_many(batch, ordered=False)
            inserted += len(result.inserted_ids)
        except BulkWriteError as e:
            details = e.details or {}
            inserted += int(details.get("nInserted") or 0)
            for w in details.get("writeErrors") or []:
                if w.get("code") == 11000:
                    clashes.append(chunk[int(w["index"])])
                else:
                    fail(f"{coll}: {w.get('errmsg') or 'σφάλμα εγγραφής'}")
        if not clashes:
            continue
        live = {d["_id"]: d async for d in db[coll].find({"_id": {"$in": [c.get("_id") for c in clashes]}})}
        for row in clashes:
            current = live.get(row.get("_id"))
            if current is None:
                continue                                   # διαγράφηκε στο μεταξύ: δεν υπάρχει τι να συγκριθεί
            if current.get("firm_id") != firm:
                fail(f"{coll}: το έγγραφο {_label(row)} ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε")
                continue
            present += 1
            if _comparable(current) != _comparable(row):
                differing += 1
    return inserted, present, differing


async def _restore_counter(doc: dict, firm: str, fail) -> str:
    """Μετρητής αρίθμησης: επαναφέρεται μόνο αν ανήκει στο γραφείο και μόνο αν προχωρά τον ζωντανό.

    Ο μετρητής δεν γυρίζει ποτέ πίσω (βλ. numbering.py)· παλαιότερο αντίγραφο πάνω σε ζωντανή βάση θα τον έφερνε
    πίσω ενώ τα τιμολόγια που εκδόθηκαν μετά μένουν. Το φίλτρο `seq < νέο` το εξασφαλίζει ατομικά (χωρίς
    ανάγνωση πριν την εγγραφή): αν ο ζωντανός είναι ίσος ή μπροστά, η upsert προσπαθεί να δημιουργήσει δεύτερο
    έγγραφο με το ίδιο `_id` και αποτυγχάνει. Επιστρέφει "restored", "present" ή "failed".
    """
    counter_id = doc.get("_id")
    if not _counter_belongs_to_firm(counter_id, firm):
        fail(f"counters: ο μετρητής {counter_id} δεν ανήκει στο γραφείο και δεν επαναφέρθηκε")
        return "failed"
    try:
        seq = int(doc.get("seq") or 0)
    except (TypeError, ValueError):
        fail(f"counters: ο μετρητής {counter_id} έχει μη έγκυρη τιμή και δεν επαναφέρθηκε")
        return "failed"
    try:
        await db.counters.replace_one({"_id": counter_id, "seq": {"$lt": seq}}, doc, upsert=True)
    except DuplicateKeyError:
        return "present"
    return "restored"


@router.post("/import")
async def import_backup(file: UploadFile = File(...), user: dict = Depends(require_admin)):
    """Restore από ZIP — μόνο για το γραφείο του διαχειριστή που το εκτελεί.

    * Επιχειρησιακά έγγραφα: upsert με βάση το `_id`, **μέσα στο γραφείο**. Έγγραφο με `_id` που ανήκει σε άλλο
      γραφείο δεν αντικαθίσταται ούτε περνά σε αυτό — αναφέρεται.
    * Ελεγκτικό ίχνος (`APPEND_ONLY`): μόνο προσθήκη όσων λείπουν· το υπάρχον δεν αγγίζεται.
    * Μετρητές αρίθμησης: μόνο αν ανήκουν στο γραφείο και μόνο προς τα εμπρός.
    """
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

    firm = firm_id_for(user)
    restored: dict[str, int] = {}
    already_present: dict[str, int] = {}
    differing: dict[str, int] = {}
    errors: list[str] = []
    failed = 0

    def fail(text: str) -> None:
        # Το πλήθος μετρά όλες τις αποτυχίες· η λίστα κρατά τις πρώτες (ένα κακό αρχείο δεν πρέπει να γεμίσει τη μνήμη).
        nonlocal failed
        failed += 1
        if len(errors) < MAX_ERRORS_KEPT:
            errors.append(text)

    for coll in COLLECTIONS:
        fname = f"{coll}.json"
        if fname not in names:
            continue
        try:
            data = json.loads(zf.read(fname).decode("utf-8"))
        except Exception as e:
            fail(f"{fname}: {e}")
            continue
        if not isinstance(data, list):
            fail(f"{fname}: το αρχείο δεν περιέχει λίστα εγγράφων")
            continue

        docs = []
        for raw in data:
            try:
                doc = _from_json_safe(raw)
            except Exception:  # noqa: BLE001 — κακοσχηματισμένο αναγνωριστικό
                doc = None
            if not isinstance(doc, dict):
                fail(f"{fname}: εγγραφή που δεν είναι έγγραφο")
                continue
            if coll != "counters":
                # Never let a restored file re-assign documents to another firm.
                doc["firm_id"] = firm
            docs.append(doc)

        if coll in APPEND_ONLY:
            n, present, diff = await _restore_append_only(coll, docs, user, fail)
            restored[coll] = n
            if present:
                already_present[coll] = present
            if diff:
                differing[coll] = diff
            continue

        n = present = 0
        for doc in docs:
            _id = doc.get("_id")
            if coll == "counters":
                outcome = await _restore_counter(doc, firm, fail)
                n += outcome == "restored"
                present += outcome == "present"
                continue
            try:
                if _id is not None:
                    # Το φίλτρο περιλαμβάνει το γραφείο: με φίλτρο μόνο `_id`, αρχείο με `_id` εγγράφου άλλου γραφείου
                    # θα το αντικαθιστούσε και θα το περνούσε σε αυτό που κάνει την επαναφορά.
                    await db[coll].replace_one({"_id": _id, "firm_id": firm}, doc, upsert=True)
                else:
                    await db[coll].insert_one(doc)
            except DuplicateKeyError:
                fail(await _conflict_message(coll, doc, firm))
                continue
            n += 1
        restored[coll] = n
        if present:
            already_present[coll] = present

    notes = []
    trail_added = restored.get("audit_logs", 0)
    if "audit_logs" in restored:
        kept = already_present.get("audit_logs", 0)
        notes.append(f"Το ελεγκτικό ίχνος συμπληρώθηκε με {trail_added} γραμμές· {kept} υπήρχαν ήδη και δεν αγγίχθηκαν.")
    if differing:
        notes.append(f"ΠΡΟΣΟΧΗ: {sum(differing.values())} υπάρχουσες γραμμές ελεγκτικού ίχνους διαφέρουν από το αντίγραφο — "
                     "κρατήθηκαν οι υπάρχουσες· ελέγξτε αν έγινε αλλοίωση ή εκ των υστέρων διόρθωση.")
    if already_present.get("counters"):
        notes.append(f"{already_present['counters']} μετρητές αρίθμησης ήταν ήδη στο ίδιο ή σε μεταγενέστερο σημείο και δεν γύρισαν πίσω.")
    if failed:
        notes.append(f"{failed} έγγραφα δεν επαναφέρθηκαν — δείτε τις λεπτομέρειες.")

    return {
        "message": "Η επαναφορά ολοκληρώθηκε.",
        "restored": restored,
        "already_present": already_present,
        "differing": differing,
        "failed": failed,
        "notes": notes,
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
