"""Backup/Restore & GDPR export.
- Ολικό backup: εξάγει τις συλλογές του γραφείου (βλ. COLLECTIONS) σε ZIP με αρχεία JSON. Δεν περιλαμβάνει
  διαπιστευτήρια χρηστών, συνδέσμους πύλης ούτε παράγωγα/προσωρινά δεδομένα — η λίστα και ο λόγος είναι στο
  NOT_BACKED_UP και γράφονται και στο `_meta.json` του αρχείου, μαζί με όσα το αντίγραφο δεν μπορεί να περιέχει
  (αρχεία, κλειδί κρυπτογράφησης μυστικών).
- Restore: διαβάζει ZIP και επαναφέρει τα δεδομένα του γραφείου που το εκτελεί. **Από προεπιλογή μόνο προσθέτει ό,τι
  λείπει** (`mode=merge`): κανένα υπάρχον έγγραφο δεν αντικαθίσταται, άρα μια επαναφορά πάνω σε γραφείο που δουλεύει
  δεν γυρίζει πίσω εκδοθέντα τιμολόγια, πληρωμές, τράπεζα ή πελατολογικό λογαριασμό. Με ρητή επιλογή
  (`mode=overwrite`) αντικαθίστανται μόνο τα δεδομένα εργασίας (OVERWRITABLE) — ποτέ τα βιβλία. Το ελεγκτικό ίχνος
  (append-only) **μόνο συμπληρώνεται**· οι μετρητές αρίθμησης **μόνο προχωρούν**· οι ρυθμίσεις μένουν **ένα έγγραφο
  ανά γραφείο**· έγγραφο που ανήκει σε άλλο γραφείο δεν αγγίζεται ποτέ. Ένα κακό έγγραφο δεν σταματά την επαναφορά —
  παραλείπεται και αναφέρεται. Στο τέλος τρέχουν ο έλεγχος αρίθμησης και ο έλεγχος πελατολογικού λογαριασμού.
- GDPR export ανά πελάτη: εξάγει ό,τι αφορά τον συγκεκριμένο πελάτη (Data Subject Request).
"""

import io
import json
import logging
import re
import struct
import tempfile
import zipfile
from datetime import date, datetime, timezone
from typing import Literal

from bson import ObjectId
from bson import encode as bson_encode
from bson.errors import BSONError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pymongo.errors import BulkWriteError, DuplicateKeyError, WriteError

from audit import audit_read
from auth import get_current_user, require_admin
from crypto import undecryptable_secrets
from db import db, fetch_all
from http_utils import content_disposition
from json_guard import contains_non_finite
from tax_utils import ATHENS
from tenant import firm_id_for, tenant_query

router = APIRouter(prefix="/backup", tags=["backup"])
logger = logging.getLogger(__name__)

BACKUP_VERSION = 1

# Συλλογές που εξάγονται. Κάθε συλλογή που αγγίζει ο κώδικας πρέπει να είναι εδώ ή στο NOT_BACKED_UP — το
# tests/test_unit_backup_r5.py αποτυγχάνει αλλιώς. Το όνομα «audit_log» (αντί για «audit_logs») και η ανύπαρκτη
# «documents» έμειναν απαρατήρητα μέχρι τον γύρο 5: το αρχείο του ίχνους εξαγόταν πάντα κενό.
COLLECTIONS = [
    "clients", "invoices", "expenses", "payroll_employees", "payroll_runs",
    "cases", "grammatia", "paravola", "hearings", "settings",
    "trust_accounts", "trust_transactions", "counters",
    "kyc", "signatures", "payment_transactions",
    "audit_logs", "copilot_messages",
    "bank_transactions", "vat_periods", "portal_messages", "case_tracker_events",
    "drafting_templates", "drafting_clauses",
]

# Συλλογές που σκόπιμα ΔΕΝ εξάγονται, με τον λόγο. Γράφονται στο `_meta.json` ώστε όποιος κρατά το αντίγραφο να
# ξέρει τι δεν περιέχει.
NOT_BACKED_UP = {
    "users": "διαπιστευτήρια (κατακερματισμένοι κωδικοί, 2FA)· δεν μεταφέρονται σε αρχείο — ο διαχειριστής δημιουργείται στην εκκίνηση",
    "password_reset_tokens": "προσωρινά διαπιστευτήρια που λήγουν μέσα σε μία ώρα",
    "client_portal_tokens": "σύνδεσμοι πρόσβασης της πύλης πελατών: ισχύουν σαν κωδικοί και ανακαλούνται με διαγραφή· ένα παλιό "
                            "αντίγραφο θα ξανάνοιγε συνδέσμους που έχουν ανακληθεί. Μετά την επαναφορά δημιουργήστε νέες προσκλήσεις",
    "login_attempts": "μετρητής ασφαλείας· καθαρίζεται μόνος του",
    "rate_limits": "μετρητές ρυθμού αιτημάτων· λήγουν μόνοι τους",
    "automation_locks": "κλειδώματα εκτέλεσης του Autopilot· προσωρινά",
    "automation_runs": "ιστορικό εκτελέσεων του Autopilot· παράγωγο",
    "notifications": "ειδοποιήσεις του Autopilot· ξαναδημιουργούνται από τις εργασίες του",
    "inbox_processed": "κλειδιά αποφυγής διπλοεισαγωγής από το γραμματοκιβώτιο ΑΑΔΕ· τη διπλοεισαγωγή εξόδων την κλείνει ο μοναδικός δείκτης των εξόδων",
    "outbox": "ουρά email· μια επαναφορά θα ξαναέστελνε μηνύματα που έχουν ήδη σταλεί",
}

# Όσα ένα αντίγραφο της βάσης δεν μπορεί να περιέχει — γράφονται στο `_meta.json` και υπενθυμίζονται σε κάθε επαναφορά.
CAVEATS = [
    "Δεν περιλαμβάνονται τα αρχεία που έχουν ανέβει (φωτογραφίες αποδείξεων εξόδων, έγγραφα): βρίσκονται στον φάκελο "
    "αποθήκευσης της εγκατάστασης (UPLOAD_DIR) ή στην αποθήκευση αντικειμένων και χρειάζονται δικό τους αντίγραφο.",
    "Τα μυστικά των ρυθμίσεων (κωδικοί SMTP/IMAP, κλειδί συνδρομής ΑΑΔΕ, GoCardless) είναι κρυπτογραφημένα με το SECRETS_KEY "
    "(ή, αν δεν έχει οριστεί, το JWT_SECRET) της εγκατάστασης. Χωρίς το ίδιο κλειδί δεν αποκρυπτογραφούνται σε άλλη "
    "εγκατάσταση — κρατήστε το κλειδί ασφαλές και ξεχωριστά από το αντίγραφο.",
    "Η επαναφορά προσθέτει μόνο όσα έγγραφα λείπουν: δεν αντικαθιστά ποτέ υπάρχον τιμολόγιο, πληρωμή, τραπεζική κίνηση, "
    "κίνηση πελατολογικού λογαριασμού ή περίοδο ΦΠΑ, και οι μετρητές αρίθμησης επανέρχονται μόνο προς τα εμπρός. Αν η "
    "βάση είναι παλαιότερη από το αντίγραφο, επαναφέρετε σε κενή βάση.",
]

RESTORE_REMINDER = (
    "Το αντίγραφο δεν περιλαμβάνει και η επαναφορά δεν επανέφερε: αρχεία (αποδείξεις εξόδων, έγγραφα που ανέβηκαν), "
    "χρήστες και κωδικούς πρόσβασης, συνδέσμους πύλης πελατών. Τα μυστικά των ρυθμίσεων (SMTP, ΑΑΔΕ) διαβάζονται "
    "μόνο με το ίδιο SECRETS_KEY.")

# Τρόποι επαναφοράς. `merge` (προεπιλογή): μόνο προσθήκη όσων λείπουν — τίποτα υπάρχον δεν αλλάζει. `overwrite`: ρητή
# αντικατάσταση, αλλά ΜΟΝΟ για τα δεδομένα εργασίας της λίστας παρακάτω.
RESTORE_MODES = ("merge", "overwrite")

# Δεδομένα εργασίας: μπορούν να αντικατασταθούν από αντίγραφο όταν ο διαχειριστής το ζητήσει ρητά («αναίρεση χθεσινού
# λάθους»). Ό,τι ΔΕΝ είναι εδώ δεν αντικαθίσταται ποτέ, σε κανένα τρόπο: τα βιβλία (τιμολόγια, πληρωμές, τράπεζα,
# πελατολογικός, ΦΠΑ, έξοδα, γραμμάτια, παράβολα, μισθοδοσία, υπογραφές, KYC, μηνύματα) κινούνται μόνο προς τα εμπρός —
# ένα εκδοθέν τιμολόγιο δεν ξαναγίνεται πρόχειρο, μια εισπραγμένη πληρωμή δεν ξαναγίνεται εκκρεμής. Μια νέα συλλογή που
# δεν μπει εδώ είναι αυτόματα «βιβλίο» (η ασφαλής προεπιλογή).
OVERWRITABLE = ("clients", "cases", "hearings", "payroll_employees", "drafting_templates", "drafting_clauses")

# Τα ονόματα των συλλογών όπως τα βλέπει ο χρήστης στις σημειώσεις της αναφοράς.
COLLECTION_LABELS = {
    "clients": "πελάτες", "invoices": "τιμολόγια", "expenses": "έξοδα", "payroll_employees": "εργαζόμενοι",
    "payroll_runs": "μισθοδοσίες", "cases": "υποθέσεις", "grammatia": "γραμμάτια", "paravola": "παράβολα",
    "hearings": "δικάσιμοι", "settings": "ρυθμίσεις", "trust_accounts": "πελατολογικοί λογαριασμοί",
    "trust_transactions": "κινήσεις πελατολογικού", "counters": "μετρητές αρίθμησης", "kyc": "φάκελοι KYC",
    "signatures": "υπογραφές", "payment_transactions": "πληρωμές κάρτας", "audit_logs": "ελεγκτικό ίχνος",
    "copilot_messages": "μηνύματα βοηθού", "bank_transactions": "τραπεζικές κινήσεις", "vat_periods": "περίοδοι ΦΠΑ",
    "portal_messages": "μηνύματα πύλης", "case_tracker_events": "αλλαγές παρακολούθησης υποθέσεων",
    "drafting_templates": "πρότυπα σύνταξης", "drafting_clauses": "ρήτρες σύνταξης",
}

# Append-only: η επαναφορά δεν αντικαθιστά ποτέ υπάρχουσα γραμμή, μόνο προσθέτει όσες λείπουν.
APPEND_ONLY = ("audit_logs",)
RESTORE_CHUNK = 500
MAX_ERRORS_SHOWN = 15
_RESTORE_MARKERS = ("restored_at", "restored_by")

# Όρια για το αρχείο που ανεβαίνει. Ένα ZIP 300 KB μπορεί να δηλώνει εκατοντάδες MB (λόγος συμπίεσης 1000:1) και
# η ανάλυση JSON πολλαπλασιάζει το μέγεθος στη μνήμη (γύρω στο 3× για κανονικά έγγραφα, 25–28× για κενά αντικείμενα)·
# χωρίς όρια, ένας διαχειριστής ενός γραφείου (ή ένα κατεστραμμένο αρχείο) αρκεί για να εξαντλήσει τη μνήμη της
# διεργασίας που εξυπηρετεί όλα τα γραφεία. Τα όρια σε byte είναι πολλαπλάσια του μεγέθους που έχει ένα πραγματικό
# αντίγραφο γραφείου· το πλήθος των αντικειμένων περιορίζεται χωριστά (MAX_JSON_CONTAINERS, παρακάτω).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024          # ασυμπίεστο μέγεθος ενός αρχείου JSON
MAX_TOTAL_BYTES = 300 * 1024 * 1024           # ασυμπίεστο μέγεθος όλων των αρχείων
MAX_MEMBERS = 100                              # το αντίγραφο έχει ένα αρχείο ανά συλλογή (~25)
# Ανώτατο πλήθος αντικειμένων/λιστών JSON σε ΕΝΑ αρχείο του αντιγράφου. Η ανάλυση JSON κοστίζει ~85 byte ανά κενό
# αντικείμενο όσο μικρό κι αν είναι το κείμενο: 20 MB από `{}` (6,6 εκατ. αντικείμενα) έφτασαν τα 558 MB μνήμης (μέτρηση
# του ελεγκτή C), άρα τα όρια σε byte δεν προστατεύουν. Ένα πραγματικό αντίγραφο έχει ~10–40 αντικείμενα ανά έγγραφο —
# τα 4 εκατ. καλύπτουν πάνω από 100.000 έγγραφα και κρατούν την ανάλυση κάτω από ~350 MB.
MAX_JSON_CONTAINERS = 4_000_000
MAX_DIRECTORY_BYTES = 64 * 1024                # ο κατάλογος του ZIP (ένα ZIP με εκατομμύρια άδεια αρχεία είναι «βόμβα»)
EXPORT_SPOOL_BYTES = 8 * 1024 * 1024           # πάνω από αυτό το μέγεθος, το αρχείο της εξαγωγής πηγαίνει σε προσωρινό αρχείο

# Σφάλματα που αφορούν ΕΝΑ έγγραφο (δεν γράφεται, δεν κωδικοποιείται). Σφάλματα σύνδεσης ή διακομιστή δεν
# συμπεριλαμβάνονται σκόπιμα: αν η βάση πέσει στη μέση, η επαναφορά πρέπει να σταματήσει, όχι να αποτυγχάνει έγγραφο
# προς έγγραφο με χρονικό όριο για το καθένα.
_DOCUMENT_ERRORS = (WriteError, BSONError, OverflowError, ValueError, TypeError)

# Οικογένειες μετρητών αρίθμησης (`counters`). Το `_id` τους είναι `<οικογένεια>-[<σειρά>-]<γραφείο>-<έτος>`·
# τιμολόγια: `invoice-<γραφείο>-<έτος>` (ΤΠΥ) ή `invoice-<σειρά>-<γραφείο>-<έτος>` (ΑΠΥ, ΠΙΣ)· υποθέσεις,
# γραμμάτια και παράβολα: `case-`, `grammatio-`, `paravolo-`. Μία λίστα για την εξαγωγή ΚΑΙ την επαναφορά — όταν η
# εξαγωγή έπαιρνε όλες τις οικογένειες και η επαναφορά μία, κάθε επαναφορά γραφείου με υπόθεση έβγαινε «αποτυχημένη».
COUNTER_FAMILIES = ("invoice", "case", "grammatio", "paravolo")
MAX_COUNTER = 1_000_000_000
_DATE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")


def _counter_pattern(firm_id: str) -> str:
    families = "|".join(COUNTER_FAMILIES)
    return rf"(?:{families})-(?:[^-]+-)?{re.escape(firm_id)}-\d{{4}}"


def _counter_belongs_to_firm(counter_id, firm_id: str) -> bool:
    """Οι μετρητές δεν έχουν `firm_id`· το γραφείο φαίνεται στο `_id` τους."""
    return isinstance(counter_id, str) and re.fullmatch(_counter_pattern(firm_id), counter_id) is not None


def _scope(user: dict, collection: str) -> dict:
    """Counters are keyed by firm in their _id; everything else carries firm_id."""
    if collection == "counters":
        return {"_id": {"$regex": f"^{_counter_pattern(firm_id_for(user))}$"}}
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


# ============================================================================================ Εξαγωγή ====

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


def _stream_and_close(spool, chunk_size: int = 1024 * 1024):
    """Στέλνει το προσωρινό αρχείο κομμάτι κομμάτι και το κλείνει στο τέλος — ή αν ο client αποσυνδεθεί στη μέση."""
    try:
        while True:
            chunk = spool.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        spool.close()


@router.get("/export")
async def export_backup(request: Request, user: dict = Depends(require_admin)):
    """Πλήρες ZIP backup των συλλογών του γραφείου (COLLECTIONS).

    Το ZIP χτίζεται σε προσωρινό αρχείο (όχι στη μνήμη) και στέλνεται τμηματικά: ένα μεγάλο αντίγραφο δεν υπάρχει
    πουθενά ολόκληρο στη μνήμη της διεργασίας, ούτε μία ούτε δύο φορές.
    """
    await audit_read(request, user, "backup_export", details="Πλήρης εξαγωγή όλων των δεδομένων του γραφείου")
    # Ημερομηνία και ώρα Ελλάδας (όχι UTC): ένα αντίγραφο που εξάγεται 01:30 της 12ης δεν πρέπει να λέγεται «…-20261011-…».
    ts = datetime.now(ATHENS).strftime("%Y%m%d-%H%M%S")
    spool = tempfile.SpooledTemporaryFile(max_size=EXPORT_SPOOL_BYTES)
    try:
        counts = {}
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in COLLECTIONS:
                counts[name] = await _write_collection(zf, name, _scope(user, name))
            meta = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": user["email"],
                "version": BACKUP_VERSION,
                "counts": counts,
                "not_included": NOT_BACKED_UP,
                "caveats": CAVEATS,
            }
            zf.writestr("_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return StreamingResponse(
        _stream_and_close(spool),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(f"nomos-one-backup-{ts}.zip"),
            "Content-Length": str(size),
        },
    )


# ========================================================================================== Επαναφορά ====

class _Report:
    """Το αποτέλεσμα μιας επαναφοράς, όπως θα το δει ο χρήστης."""

    def __init__(self) -> None:
        self.restored: dict[str, int] = {}          # έγγραφα που γράφτηκαν (νέα + αντικατεστημένα)
        self.already_present: dict[str, int] = {}   # υπήρχαν ήδη (ίδια ή διαφορετικά)
        self.mode = "merge"
        self.kept: dict[str, int] = {}              # υπήρχαν, ΔΙΑΦΕΡΟΥΝ από το αντίγραφο και μένουν όπως είναι
        self.replaced: dict[str, int] = {}          # υπήρχαν, διέφεραν και αντικαταστάθηκαν (μόνο mode=overwrite)
        self.differing: dict[str, int] = {}         # ΜΟΝΟ ελεγκτικό ίχνος: υπάρχουσες γραμμές που διαφέρουν (ύποπτο)
        self.checks: dict[str, dict] = {}
        self.errors: list[str] = []
        self.notes: list[str] = []
        self.failed = 0

    def fail(self, text: str, count: int = 1) -> None:
        # Το πλήθος μετρά όλες τις αποτυχίες· η λίστα κρατά τις πρώτες (ένα κακό αρχείο δεν πρέπει να γεμίσει τη μνήμη).
        self.failed += count
        if len(self.errors) < MAX_ERRORS_SHOWN:
            self.errors.append(text)


def _label(doc: dict) -> str:
    """Πώς αναφέρεται ένα έγγραφο σε μήνυμα προς τον χρήστη."""
    return str(doc.get("number") or doc.get("mydata_mark") or doc.get("period") or doc.get("external_id")
               or doc.get("_id"))


def _reason(error: Exception) -> str:
    """Το σφάλμα σε μία σύντομη γραμμή, για μήνυμα προς τον χρήστη."""
    return " ".join(str(error).split())[:120] or type(error).__name__


def _comparable(doc: dict):
    """Το περιεχόμενο μιας γραμμής χωρίς ό,τι αλλάζει η ίδια η επαναφορά (γραφείο, σήμανση επαναφοράς)."""
    return _to_json_safe({k: v for k, v in doc.items() if k != "firm_id" and k not in _RESTORE_MARKERS})


def _too_large(what: str) -> HTTPException:
    return HTTPException(status_code=413, detail=f"Το αρχείο backup είναι υπερβολικά μεγάλο ({what}).")


def _check_directory(raw, size: int) -> None:
    """Ελέγχει τον κατάλογο του ZIP **πριν** τον διαβάσει η `zipfile`, η οποία φορτώνει στη μνήμη μία εγγραφή για
    κάθε αρχείο που δηλώνει — δηλαδή ένα ZIP με εκατομμύρια άδεια αρχεία κοστίζει gigabytes.

    Αν δεν βρεθεί η εγγραφή τέλους (EOCD), δεν είναι ZIP και το λέει η `zipfile` με το δικό της μήνυμα.
    """
    tail_length = min(size, 22 + 65535)
    raw.seek(size - tail_length)
    tail = raw.read(tail_length)
    raw.seek(0)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail) - position < 22:
        return
    entries, directory_size, directory_offset = struct.unpack_from("<HII", tail, position + 10)
    zip64 = entries == 0xFFFF or 0xFFFFFFFF in (directory_size, directory_offset)    # δεν παράγεται από την εξαγωγή
    if zip64 or entries > MAX_MEMBERS or directory_size > MAX_DIRECTORY_BYTES:
        raise _too_large("πάρα πολλά αρχεία μέσα στο ZIP")


def _open_archive(upload: UploadFile) -> zipfile.ZipFile:
    """Ανοίγει το ZIP που ανέβηκε, αφού ελέγξει όλα τα όρια μεγέθους. Δεν αγγίζει τη βάση."""
    raw = upload.file
    raw.seek(0, io.SEEK_END)
    size = raw.tell()
    raw.seek(0)
    if size == 0:
        raise HTTPException(status_code=400, detail="Το αρχείο backup είναι κενό.")
    if size > MAX_UPLOAD_BYTES:
        raise _too_large(f"όριο {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    _check_directory(raw, size)
    try:
        zf = zipfile.ZipFile(raw)
    except (zipfile.BadZipFile, NotImplementedError, OverflowError, ValueError):
        raise HTTPException(status_code=400, detail="Μη έγκυρο αρχείο ZIP.")
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise _too_large("πάρα πολλά αρχεία μέσα στο ZIP")
    if any(info.file_size > MAX_MEMBER_BYTES for info in infos):
        raise _too_large("κάποιο αρχείο του ZIP ξεπερνά το όριο μεγέθους")
    if sum(info.file_size for info in infos) > MAX_TOTAL_BYTES:
        raise _too_large("το περιεχόμενο του ZIP ξεπερνά το όριο μεγέθους")
    if "_meta.json" not in {info.filename for info in infos}:
        raise HTTPException(status_code=400, detail="Το ZIP δεν είναι έγκυρο backup του Nomos One (λείπει _meta.json).")
    return zf


def _read_meta(zf: zipfile.ZipFile) -> dict:
    """Το `_meta.json` του αντιγράφου. Αντίγραφο από νεότερη έκδοση δεν επαναφέρεται: μια μορφή που δεν
    γνωρίζουμε δεν διαβάζεται «περίπου»."""
    try:
        meta = json.loads(zf.read("_meta.json"))
    except Exception:  # noqa: BLE001 — ό,τι κι αν φταίει, το αρχείο δεν είναι αντίγραφο που ξέρουμε να διαβάσουμε
        meta = None
    if not isinstance(meta, dict):
        raise HTTPException(status_code=400, detail="Το _meta.json του αντιγράφου δεν είναι έγκυρο.")
    version = meta.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= BACKUP_VERSION:
        raise HTTPException(status_code=400, detail=(
            f"Το αντίγραφο έχει έκδοση μορφής «{version}», που δεν υποστηρίζεται από αυτή την εγκατάσταση "
            f"(υποστηρίζεται έως η {BACKUP_VERSION}). Ενημερώστε πρώτα την εφαρμογή."))
    return meta


def _read_collection_file(zf: zipfile.ZipFile, fname: str):
    return json.loads(zf.read(fname))


async def _load_collection(zf: zipfile.ZipFile, fname: str, report: _Report):
    """Τα έγγραφα ενός αρχείου του αντιγράφου ως λίστα, ή `None` (και αναφορά) αν δεν διαβάζεται."""
    try:
        data = await run_in_threadpool(_read_collection_file, zf, fname)
    except Exception as e:  # noqa: BLE001 — κατεστραμμένο μέλος, μη έγκυρο JSON, ανεπίτρεπτο βάθος κ.λπ.
        report.fail(f"{fname}: δεν διαβάζεται ({_reason(e)})")
        return None
    if not isinstance(data, list):
        report.fail(f"{fname}: το αρχείο δεν περιέχει λίστα εγγράφων")
        return None
    return data


def _prepare_documents(coll: str, fname: str, data: list, firm: str, report: _Report) -> list[dict]:
    """Τα έγγραφα που μπορούν να γραφτούν: έγγραφο που δεν είναι έγγραφο, που περιέχει `NaN`/άπειρο ή που δεν
    κωδικοποιείται σε BSON αναφέρεται και παραλείπεται — ούτε χαλά την επαναφορά ούτε μπαίνει στη βάση."""
    docs = []
    for raw in data:
        try:
            doc = _from_json_safe(raw)
            poisoned = isinstance(doc, dict) and contains_non_finite(doc)
        except Exception:  # noqa: BLE001 — κακοσχηματισμένο αναγνωριστικό, υπερβολικό βάθος
            doc, poisoned = None, False
        if not isinstance(doc, dict):
            report.fail(f"{fname}: εγγραφή που δεν είναι έγγραφο")
            continue
        if poisoned:
            report.fail(f"{coll}: το έγγραφο {_label(doc)} περιέχει μη έγκυρο αριθμό (NaN ή άπειρο) και δεν επαναφέρθηκε")
            continue
        if coll != "counters":
            # Never let a restored file re-assign documents to another firm.
            doc["firm_id"] = firm
        try:
            bson_encode(doc)
        except _DOCUMENT_ERRORS as e:
            report.fail(f"{coll}: το έγγραφο {_label(doc)} δεν αποθηκεύεται ({_reason(e)}) και δεν επαναφέρθηκε")
            continue
        docs.append(doc)
    return docs


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


def _hashable(value) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


async def _live_documents(coll: str, chunk: list) -> dict:
    """Τα έγγραφα που υπάρχουν ΗΔΗ στη βάση (οποιουδήποτε γραφείου) για τα `_id` ενός κομματιού: {_id: έγγραφο}."""
    ids = [d["_id"] for d in chunk if d.get("_id") is not None and _hashable(d["_id"])]
    if not ids:
        return {}
    return {d["_id"]: d async for d in db[coll].find({"_id": {"$in": ids}})}


async def _restore_documents(coll: str, docs: list, firm: str, report: _Report, overwrite: bool = False) -> int:
    """Επιχειρησιακά έγγραφα, μέσα στο γραφείο. Ένα έγγραφο που δεν γράφεται δεν σταματά τα υπόλοιπα.

    * Έγγραφο που **λείπει** από τη βάση προστίθεται.
    * Έγγραφο που **υπάρχει ήδη** (ίδιο `_id`, ίδιο γραφείο) δεν αντικαθίσταται — εκτός αν ο διαχειριστής ζήτησε ρητά
      `overwrite` ΚΑΙ η συλλογή είναι δεδομένα εργασίας (OVERWRITABLE). Έτσι ένα εκδοθέν τιμολόγιο δεν ξαναγίνεται
      πρόχειρο, μια εισπραγμένη πληρωμή δεν ξαναγίνεται εκκρεμής, ένα υπόλοιπο πελατολογικού δεν χωρίζει από τις κινήσεις του.
    * Έγγραφο με `_id` που ανήκει σε άλλο γραφείο δεν αγγίζεται ποτέ — αναφέρεται.

    Η αναφορά κρατά πόσα υπήρχαν ήδη (`already_present`), πόσα από αυτά διέφεραν από το αντίγραφο και έμειναν ως έχουν
    (`kept`) και πόσα αντικαταστάθηκαν (`replaced`).
    """
    allow_replace = overwrite and coll in OVERWRITABLE
    restored = present = kept = replaced = 0
    for start in range(0, len(docs), RESTORE_CHUNK):
        chunk = docs[start:start + RESTORE_CHUNK]
        live = await _live_documents(coll, chunk)
        for doc in chunk:
            _id = doc.get("_id")
            current = live.get(_id) if _id is not None and _hashable(_id) else None
            try:
                if current is None:
                    await db[coll].insert_one(doc)
                    restored += 1
                    continue
                if current.get("firm_id") not in (None, firm):
                    report.fail(f"{coll}: το έγγραφο {_label(doc)} ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε")
                    continue
                present += 1
                if _comparable(current) == _comparable(doc):
                    continue
                if not allow_replace:
                    kept += 1
                    continue
                # Το φίλτρο περιλαμβάνει το γραφείο: με φίλτρο μόνο `_id`, αρχείο με `_id` εγγράφου άλλου γραφείου
                # θα το αντικαθιστούσε και θα το περνούσε σε αυτό που κάνει την επαναφορά.
                await db[coll].replace_one({"_id": _id, "firm_id": firm}, doc, upsert=True)
                replaced += 1
                restored += 1
            except DuplicateKeyError:
                report.fail(await _conflict_message(coll, doc, firm))
            except _DOCUMENT_ERRORS as e:
                report.fail(f"{coll}: το έγγραφο {_label(doc)} δεν αποθηκεύεται ({_reason(e)}) και δεν επαναφέρθηκε")
    if present:
        report.already_present[coll] = present
    if kept:
        report.kept[coll] = kept
    if replaced:
        report.replaced[coll] = replaced
    return restored


async def _restore_append_only(coll: str, docs: list, user: dict, report: _Report) -> tuple[int, int, int]:
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
                    report.fail(f"{coll}: {w.get('errmsg') or 'σφάλμα εγγραφής'}")
        except _DOCUMENT_ERRORS as e:
            report.fail(f"{coll}: {len(chunk)} γραμμές δεν επαναφέρθηκαν ({_reason(e)})", len(chunk))
            continue
        if not clashes:
            continue
        live = {d["_id"]: d async for d in db[coll].find({"_id": {"$in": [c.get("_id") for c in clashes]}})}
        for row in clashes:
            current = live.get(row.get("_id"))
            if current is None:
                continue                                   # διαγράφηκε στο μεταξύ: δεν υπάρχει τι να συγκριθεί
            if current.get("firm_id") != firm:
                report.fail(f"{coll}: το έγγραφο {_label(row)} ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε")
                continue
            present += 1
            if _comparable(current) != _comparable(row):
                differing += 1
    return inserted, present, differing


def _clean_counter(doc: dict) -> dict | None:
    """Ο μετρητής όπως θα γραφτεί: μόνο τα γνωστά πεδία, με τον σωστό τύπο. Αντίγραφο που έχει «12» (κείμενο) ή
    1e3 (δεκαδικό) στο `seq` θα αποθηκευόταν ως έχει και κάθε έκδοση τιμολογίου μετά θα αποτύγχανε με 500 — ή, με
    δεκαδικό, θα «έκαιγε» έναν αριθμό σε κάθε προσπάθεια, σε σειρά που πρέπει να είναι αδιάλειπτη."""
    seq = doc.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= MAX_COUNTER:
        return None
    clean = {"_id": doc["_id"], "seq": seq}
    last_date = doc.get("last_date")
    if isinstance(last_date, str) and _DATE_FORMAT.fullmatch(last_date):
        try:
            date.fromisoformat(last_date)
        except ValueError:
            pass
        else:
            clean["last_date"] = last_date
    allocated_at = doc.get("last_alloc_at")
    if isinstance(allocated_at, str) and len(allocated_at) <= 40:
        clean["last_alloc_at"] = allocated_at
    return clean


async def _restore_counter(doc: dict, firm: str, report: _Report) -> str:
    """Μετρητής αρίθμησης: επαναφέρεται μόνο αν ανήκει στο γραφείο και μόνο αν προχωρά τον ζωντανό.

    Ο μετρητής δεν γυρίζει ποτέ πίσω (βλ. numbering.py)· παλαιότερο αντίγραφο πάνω σε ζωντανή βάση θα τον έφερνε
    πίσω ενώ τα τιμολόγια που εκδόθηκαν μετά μένουν. Το φίλτρο `seq < νέο` το εξασφαλίζει ατομικά (χωρίς
    ανάγνωση πριν την εγγραφή): αν ο ζωντανός είναι ίσος ή μπροστά, η upsert προσπαθεί να δημιουργήσει δεύτερο
    έγγραφο με το ίδιο `_id` και αποτυγχάνει. Επιστρέφει "restored", "present" ή "failed".
    """
    counter_id = doc.get("_id")
    if not _counter_belongs_to_firm(counter_id, firm):
        report.fail(f"counters: ο μετρητής {counter_id} δεν ανήκει στο γραφείο και δεν επαναφέρθηκε")
        return "failed"
    clean = _clean_counter(doc)
    if clean is None:
        report.fail(f"counters: ο μετρητής {counter_id} έχει μη έγκυρη τιμή και δεν επαναφέρθηκε")
        return "failed"
    try:
        await db.counters.replace_one({"_id": counter_id, "seq": {"$lt": clean["seq"]}}, clean, upsert=True)
    except DuplicateKeyError:
        return "present"
    except _DOCUMENT_ERRORS as e:
        report.fail(f"counters: ο μετρητής {counter_id} δεν αποθηκεύεται ({_reason(e)}) και δεν επαναφέρθηκε")
        return "failed"
    return "restored"


_NOT_THERE = object()
_SETTINGS_IGNORED = {"_id", "id", "firm_id", "updated_at", *_RESTORE_MARKERS}


def _is_blank_settings(live: dict) -> bool:
    """Είναι το ζωντανό έγγραφο ρυθμίσεων το κενό προεπιλεγμένο που δημιουργεί η σελίδα Ρυθμίσεων (βλ.
    `routes_settings._settings_doc`) — δηλαδή δεν έχει ρυθμιστεί ακόμη τίποτα; Πεδίο που λείπει από παλιό έγγραφο
    μετράει ως προεπιλογή· πεδίο που δεν υπάρχει στο μοντέλο, ή έχει άλλη τιμή, σημαίνει ότι το γραφείο έχει ρυθμίσεις."""
    from models import Settings
    defaults = Settings(firm_id=str(live.get("firm_id") or "")).model_dump()
    return all(value == defaults.get(key, _NOT_THERE) for key, value in live.items() if key not in _SETTINGS_IGNORED)


def _settings_view(doc: dict):
    return _to_json_safe({k: v for k, v in doc.items() if k not in _SETTINGS_IGNORED})


async def _collapse_duplicate_settings(firm: str, kept_id, report: _Report) -> None:
    removed = (await db.settings.delete_many({"firm_id": firm, "_id": {"$ne": kept_id}})).deleted_count
    if removed:
        report.notes.append(f"Αφαιρέθηκαν {removed} διπλά έγγραφα ρυθμίσεων (η εφαρμογή διαβάζει ένα ανά γραφείο).")


async def _restore_settings(docs: list, firm: str, report: _Report, overwrite: bool = False) -> int:
    """Οι ρυθμίσεις είναι **ένα έγγραφο ανά γραφείο**: η εφαρμογή διαβάζει πάντα το πρώτο που θα βρει.

    Μετά από απώλεια δεδομένων ο διαχειριστής συνδέεται και η σελίδα Ρυθμίσεων δημιουργεί ένα κενό προεπιλεγμένο
    έγγραφο ΠΡΙΝ γίνει η επαναφορά. Αν η επαναφορά έγραφε το δικό της έγγραφο δίπλα του, η εφαρμογή θα συνέχιζε να
    διαβάζει το κενό: επωνυμία, ΑΦΜ, SMTP και ρυθμίσεις ΑΑΔΕ δεν θα εμφανίζονταν — ενώ η επαναφορά θα έδινε «επιτυχία».
    Γι' αυτό το περιεχόμενο του αντιγράφου αντικαθιστά το ζωντανό έγγραφο (το οποίο κρατά το `_id` του) όταν αυτό
    είναι το κενό προεπιλεγμένο, ή όταν ο διαχειριστής ζήτησε ρητά `overwrite`. Ρυθμισμένο γραφείο σε `merge` κρατά τις
    ρυθμίσεις του (κωδικοί, ΑΦΜ, ΑΑΔΕ που έχουν αλλάξει από το αντίγραφο δεν χάνονται σιωπηλά). Τυχόν
    έγγραφα-διπλότυπα του γραφείου αφαιρούνται σε κάθε περίπτωση.
    """
    if not docs:
        return 0
    source = docs[0]
    if len(docs) > 1:
        report.notes.append(f"Το αντίγραφο περιέχει {len(docs)} έγγραφα ρυθμίσεων για το ίδιο γραφείο· επαναφέρθηκε το πρώτο.")
    body = {k: v for k, v in source.items() if k != "_id"}
    initial = await db.settings.find_one({"firm_id": firm})
    configured = initial is not None and not _is_blank_settings(initial)
    if configured:
        report.already_present["settings"] = 1
        differs = _settings_view(initial) != _settings_view(source)
        if not overwrite:
            if differs:
                report.kept["settings"] = 1
            await _collapse_duplicate_settings(firm, initial["_id"], report)
            return 0
        if differs:
            report.replaced["settings"] = 1
    kept_id = None
    for attempt in range(3):
        live = await db.settings.find_one({"firm_id": firm}, {"_id": 1})
        try:
            if live is not None:
                await db.settings.replace_one({"_id": live["_id"], "firm_id": firm}, body)
                kept_id = live["_id"]
            else:
                # Πρώτα με το αρχικό `_id`· αν αυτό συγκρούεται (σχεδόν αδύνατον), χωρίς αυτό — κανείς δεν το αναφέρει.
                created = await db.settings.insert_one(dict(source) if attempt == 0 else dict(body))
                kept_id = created.inserted_id
            break
        except DuplicateKeyError:
            continue                    # δημιουργήθηκε ταυτόχρονα άλλο έγγραφο του γραφείου: ξαναδιάβασέ το
        except _DOCUMENT_ERRORS as e:
            report.fail(f"settings: οι ρυθμίσεις δεν αποθηκεύονται ({_reason(e)}) και δεν επαναφέρθηκαν")
            report.replaced.pop("settings", None)
            return 0
    if kept_id is None:
        report.fail("settings: οι ρυθμίσεις συγκρούονται με υπάρχον έγγραφο και δεν επαναφέρθηκαν")
        report.replaced.pop("settings", None)
        return 0
    await _collapse_duplicate_settings(firm, kept_id, report)
    unreadable = undecryptable_secrets(body)
    if unreadable:
        report.notes.append(
            "ΠΡΟΣΟΧΗ: τα μυστικά των ρυθμίσεων (" + ", ".join(unreadable) + ") δεν αποκρυπτογραφούνται με το "
            "SECRETS_KEY αυτής της εγκατάστασης — καταχωρίστε ξανά τους κωδικούς στις Ρυθμίσεις.")
    return 1


def _by_label(counts: dict[str, int]) -> str:
    return ", ".join(f"{COLLECTION_LABELS.get(coll, coll)} {n}" for coll, n in counts.items())


_OVERWRITE_HINT = ("επαναλάβετε την επαναφορά με την επιλογή «Αντικατάσταση δεδομένων εργασίας» "
                   "(πελάτες, υποθέσεις, δικάσιμοι, εργαζόμενοι, πρότυπα)")


def _summary_notes(report: _Report) -> list[str]:
    notes = list(report.notes)
    kept = {coll: n for coll, n in report.kept.items() if coll != "settings"}
    if kept:
        notes.append(
            f"{sum(kept.values())} υπάρχοντα έγγραφα διέφεραν από το αντίγραφο και δεν άλλαξαν ({_by_label(kept)}). "
            "Η επαναφορά προσθέτει μόνο όσα λείπουν και δεν αντικαθιστά ποτέ τιμολόγια, πληρωμές, τραπεζικές κινήσεις, "
            "πελατολογικό λογαριασμό ή περιόδους ΦΠΑ.")
        if report.mode != "overwrite" and any(coll in OVERWRITABLE for coll in kept):
            notes.append("Για να αντικατασταθούν από το αντίγραφο και δεδομένα εργασίας, " + _OVERWRITE_HINT + ".")
    if "settings" in report.kept:
        notes.append("Οι ρυθμίσεις του γραφείου δεν αντικαταστάθηκαν — το γραφείο έχει ήδη ρυθμίσεις. "
                     "Για να επανέλθουν από το αντίγραφο, " + _OVERWRITE_HINT + ".")
    if report.replaced:
        notes.append("Από το αντίγραφο αντικαταστάθηκαν έγγραφα εργασίας: " + _by_label(report.replaced) + ".")
    if "audit_logs" in report.restored:
        added = report.restored["audit_logs"]
        kept = report.already_present.get("audit_logs", 0)
        notes.append(f"Το ελεγκτικό ίχνος συμπληρώθηκε με {added} γραμμές· {kept} υπήρχαν ήδη και δεν αγγίχθηκαν.")
    if report.differing:
        notes.append(f"ΠΡΟΣΟΧΗ: {sum(report.differing.values())} υπάρχουσες γραμμές ελεγκτικού ίχνους διαφέρουν από το αντίγραφο — "
                     "κρατήθηκαν οι υπάρχουσες· ελέγξτε αν έγινε αλλοίωση ή εκ των υστέρων διόρθωση.")
    if report.already_present.get("counters"):
        notes.append(f"{report.already_present['counters']} μετρητές αρίθμησης ήταν ήδη στο ίδιο ή σε μεταγενέστερο σημείο και δεν γύρισαν πίσω.")
    if report.failed:
        notes.append(f"{report.failed} έγγραφα δεν επαναφέρθηκαν — δείτε τις λεπτομέρειες.")
    notes.append(RESTORE_REMINDER)
    return notes


_NUMBERING_PROBLEMS = {
    "gaps": "κενά", "duplicates": "διπλοί αριθμοί", "date_order": "ημερομηνίες εκτός σειράς",
    "counter_behind": "μετρητής πίσω από τους αριθμούς", "unparseable": "αριθμοί εκτός μορφής",
    "unnumbered": "παραστατικά χωρίς αριθμό",
}


async def _post_restore_checks(user: dict, report: _Report) -> None:
    """Ό,τι μια επαναφορά μπορεί να αφήσει στραβό, ελέγχεται και φαίνεται στην αναφορά: η συνέχεια της αρίθμησης των
    τιμολογίων και η συμφωνία κάθε πελατολογικού λογαριασμού με τις κινήσεις του. Ένας έλεγχος που αποτυγχάνει δεν
    αποτυγχάνει την επαναφορά — αναφέρεται ότι δεν ολοκληρώθηκε, ώστε να τρέξει με το χέρι."""
    import invoice_services
    import routes_trust

    try:
        numbering = await invoice_services.numbering_report(firm_id_for(user))
    except Exception as e:  # noqa: BLE001 — η επαναφορά έχει ήδη γίνει· ο έλεγχος είναι ενημέρωση
        logger.exception("backup: ο έλεγχος αρίθμησης μετά την επαναφορά απέτυχε")
        report.checks["numbering"] = {"ok": None, "error": _reason(e)}
        report.notes.append(f"Ο έλεγχος αρίθμησης τιμολογίων δεν ολοκληρώθηκε μετά την επαναφορά ({_reason(e)}) — "
                            "τρέξτε τον χειροκίνητα από τα Τιμολόγια.")
    else:
        report.checks["numbering"] = {"ok": bool(numbering["ok"]), "problems": numbering["problems"]}
        if not numbering["ok"]:
            found = ", ".join(f"{_NUMBERING_PROBLEMS.get(key, key)}: {count}"
                              for key, count in numbering["problems"].items() if count)
            report.notes.append(f"ΠΡΟΣΟΧΗ: ο έλεγχος αρίθμησης τιμολογίων βρήκε προβλήματα μετά την επαναφορά ({found}) — "
                                "ενημερώστε τον λογιστή πριν εκδοθούν νέα παραστατικά.")

    try:
        ledger = await routes_trust.ledger_inconsistencies(user)
    except Exception as e:  # noqa: BLE001
        logger.exception("backup: ο έλεγχος πελατολογικού λογαριασμού μετά την επαναφορά απέτυχε")
        report.checks["trust_ledger"] = {"ok": None, "error": _reason(e)}
        report.notes.append(f"Ο έλεγχος των πελατολογικών λογαριασμών δεν ολοκληρώθηκε μετά την επαναφορά ({_reason(e)}) — "
                            "ελέγξτε τα υπόλοιπα με το χέρι.")
    else:
        report.checks["trust_ledger"] = {"ok": not ledger["inconsistent"], "accounts": ledger["accounts"],
                                         "inconsistent": ledger["inconsistent"]}
        if ledger["inconsistent"]:
            first = ledger["inconsistent"][0]
            report.notes.append(
                f"ΠΡΟΣΟΧΗ: το υπόλοιπο του πελατολογικού λογαριασμού δεν συμφωνεί με τις κινήσεις του για "
                f"{len(ledger['inconsistent'])} πελάτες (π.χ. «{first['client_name']}»: διαφορά {first['difference']:.2f} €). "
                "Ελέγξτε τον λογαριασμό πριν γίνει νέα κίνηση.")


def _count_json_containers(zf: zipfile.ZipFile, fname: str) -> int:
    """Πόσα `{` και `[` έχει ένα αρχείο του αντιγράφου — άνω φράγμα στα αντικείμενα/λίστες που θα φτιάξει η ανάλυση JSON.

    Διαβάζει κομμάτι κομμάτι (δεν κρατά το αρχείο στη μνήμη) και σταματά μόλις ξεπεράσει το όριο."""
    total = 0
    with zf.open(fname) as member:
        while True:
            chunk = member.read(1024 * 1024)
            if not chunk:
                return total
            total += chunk.count(b"{") + chunk.count(b"[")
            if total > MAX_JSON_CONTAINERS:
                return total


def _short_reason(reason: str) -> str:
    """Η πρώτη πρόταση ενός λόγου του NOT_BACKED_UP (μέχρι το πρώτο «:» ή «·»)."""
    return reason.split("·")[0].split(":")[0].strip()


def _ignored_files(names: set[str]) -> str | None:
    """Αρχεία JSON του αντιγράφου που η επαναφορά δεν διαβάζει, με τον λόγο όπου υπάρχει."""
    ignored = []
    for name in sorted(names):
        if name == "_meta.json" or not name.endswith(".json") or name[:-5] in COLLECTIONS:
            continue
        ignored.append(f"{name} ({_short_reason(NOT_BACKED_UP.get(name[:-5], 'άγνωστο αρχείο'))})")
    if not ignored:
        return None
    return "Αγνοήθηκαν αρχεία του αντιγράφου που δεν επαναφέρονται: " + ", ".join(ignored[:10]) + "."


@router.post("/import")
async def import_backup(file: UploadFile = File(...), mode: Literal["merge", "overwrite"] = "merge",
                        user: dict = Depends(require_admin)):
    """Restore από ZIP — μόνο για το γραφείο του διαχειριστή που το εκτελεί.

    * Πριν αγγιχτεί η βάση ελέγχονται το μέγεθος, ο αριθμός των αρχείων, το πλήθος αντικειμένων JSON και η έκδοση.
    * `mode=merge` (προεπιλογή): προστίθενται μόνο τα έγγραφα που **λείπουν**· κανένα υπάρχον δεν αντικαθίσταται.
    * `mode=overwrite`: αντικαθίστανται και τα υπάρχοντα, αλλά ΜΟΝΟ τα δεδομένα εργασίας (OVERWRITABLE) — ποτέ τα βιβλία.
    * Έγγραφο με `_id` που ανήκει σε άλλο γραφείο δεν αντικαθίσταται ούτε περνά σε αυτό — αναφέρεται. Έγγραφο που δεν
      γράφεται παραλείπεται και αναφέρεται.
    * Ελεγκτικό ίχνος (`APPEND_ONLY`): μόνο προσθήκη όσων λείπουν· το υπάρχον δεν αγγίζεται.
    * Μετρητές αρίθμησης: μόνο αν ανήκουν στο γραφείο και μόνο προς τα εμπρός.
    * Ρυθμίσεις: ένα έγγραφο ανά γραφείο· ρυθμισμένο γραφείο τις κρατά σε `merge`.
    * Στο τέλος τρέχουν ο έλεγχος αρίθμησης και ο έλεγχος πελατολογικών λογαριασμών (`checks`).
    """
    zf = _open_archive(file)
    meta = _read_meta(zf)
    names = {info.filename for info in zf.infolist()}
    declared_counts = meta.get("counts") if isinstance(meta.get("counts"), dict) else {}
    for coll in COLLECTIONS:
        fname = f"{coll}.json"
        if fname in names and await run_in_threadpool(_count_json_containers, zf, fname) > MAX_JSON_CONTAINERS:
            raise _too_large(f"το αρχείο {fname} περιέχει υπερβολικά πολλά αντικείμενα JSON")

    firm = firm_id_for(user)
    overwrite = mode == "overwrite"
    report = _Report()
    report.mode = mode

    for coll in COLLECTIONS:
        fname = f"{coll}.json"
        if fname not in names:
            continue
        data = await _load_collection(zf, fname, report)
        if data is None:
            continue
        declared = declared_counts.get(coll)
        if isinstance(declared, int) and not isinstance(declared, bool) and declared != len(data):
            report.notes.append(f"{fname}: το αντίγραφο δηλώνει {declared} εγγραφές αλλά το αρχείο έχει {len(data)} — "
                                "ελέγξτε ότι το αρχείο δεν έχει επεξεργαστεί.")
        docs = await run_in_threadpool(_prepare_documents, coll, fname, data, firm, report)

        if coll in APPEND_ONLY:
            n, present, diff = await _restore_append_only(coll, docs, user, report)
            report.restored[coll] = n
            if present:
                report.already_present[coll] = present
            if diff:
                report.differing[coll] = diff
        elif coll == "counters":
            outcomes = [await _restore_counter(doc, firm, report) for doc in docs]
            report.restored[coll] = outcomes.count("restored")
            if outcomes.count("present"):
                report.already_present[coll] = outcomes.count("present")
        elif coll == "settings":
            report.restored[coll] = await _restore_settings(docs, firm, report, overwrite)
        else:
            report.restored[coll] = await _restore_documents(coll, docs, firm, report, overwrite)

    ignored = _ignored_files(names)
    if ignored:
        report.notes.append(ignored)
    await _post_restore_checks(user, report)

    return {
        "message": "Η επαναφορά ολοκληρώθηκε.",
        "mode": mode,
        "restored": report.restored,
        "replaced": report.replaced,
        "already_present": report.already_present,
        "kept": report.kept,
        "differing": report.differing,
        "failed": report.failed,
        "checks": report.checks,
        "notes": _summary_notes(report),
        "errors": report.errors,
    }


# ============================================================================================== GDPR ====

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

    # Χωρίς όριο: το δικαίωμα πρόσβασης (ΓΚΠΔ άρ. 15) δεν ικανοποιείται με «τα πρώτα 5.000» εγγραφές. Και με το
    # κανονικό (πεζό) αναγνωριστικό του πελάτη, όπως αποθηκεύεται στα έγγραφά του, όχι όπως γράφτηκε στο URL.
    cid = str(client["_id"])
    by_client = tenant_query(user, {"client_id": cid})
    invoices = await fetch_all(db.invoices.find(by_client))
    cases = await fetch_all(db.cases.find(by_client))
    grammatia = await fetch_all(db.grammatia.find(by_client))
    trust_accounts = await fetch_all(db.trust_accounts.find(by_client))
    trust_txs = await fetch_all(db.trust_transactions.find(by_client))
    kyc = await fetch_all(db.kyc.find(by_client))
    payments = await fetch_all(db.payment_transactions.find(by_client))
    messages = await fetch_all(db.portal_messages.find(by_client))
    # Πότε δόθηκε και χρησιμοποιήθηκε πρόσβαση στην πύλη — όχι το ίδιο το token, που λειτουργεί σαν κωδικός.
    portal_access = await fetch_all(db.client_portal_tokens.find(by_client, {"token": 0}))

    case_ids = [str(c["_id"]) for c in cases]
    invoice_ids = [str(i["_id"]) for i in invoices]
    by_case = tenant_query(user, {"case_id": {"$in": case_ids}})
    hearings = await fetch_all(db.hearings.find(by_case))
    paravola = await fetch_all(db.paravola.find(by_case))
    tracker_events = await fetch_all(db.case_tracker_events.find(by_case))
    bank_lines = await fetch_all(db.bank_transactions.find(
        tenant_query(user, {"matched_invoice_id": {"$in": invoice_ids}})))
    # Έξοδα του γραφείου που αφορούν τον πελάτη: δηλωμένα στον ίδιο ή σε υπόθεσή του (Φάση 1 του «ψηφιακού λογιστή»).
    expenses = await fetch_all(db.expenses.find(
        tenant_query(user, {"$or": [{"client_id": cid}, {"case_id": {"$in": case_ids}}]})))
    email = (client.get("email") or "").strip()
    signatures = []
    if email:
        signatures = await fetch_all(db.signatures.find(tenant_query(user, {
            "signer_email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})))

    dataset = {
        "client": _to_json_safe(client),
        "invoices": [_to_json_safe(d) for d in invoices],
        "cases": [_to_json_safe(d) for d in cases],
        "hearings": [_to_json_safe(d) for d in hearings],
        "paravola": [_to_json_safe(d) for d in paravola],
        "case_tracker_events": [_to_json_safe(d) for d in tracker_events],
        "grammatia": [_to_json_safe(d) for d in grammatia],
        "trust_accounts": [_to_json_safe(d) for d in trust_accounts],
        "trust_transactions": [_to_json_safe(d) for d in trust_txs],
        "kyc": [_to_json_safe(d) for d in kyc],
        "payment_transactions": [_to_json_safe(d) for d in payments],
        "bank_transactions": [_to_json_safe(d) for d in bank_lines],
        "expenses": [_to_json_safe(d) for d in expenses],
        "portal_messages": [_to_json_safe(d) for d in messages],
        "portal_access": [_to_json_safe(d) for d in portal_access],
        "signatures": [_to_json_safe(d) for d in signatures],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(dataset, ensure_ascii=False, indent=2))
        zf.writestr("_readme.txt", (
            "Nomos One — GDPR Data Subject Access (Άρθρα 15 & 20 ΓΚΠΔ)\n"
            f"Πελάτης: {client.get('name', '')}\n"
            f"Εξήχθη: {datetime.now(timezone.utc).isoformat()}\n"
            f"Εξήχθη από: {user['email']}\n"
            "Το data.json περιέχει τα προσωπικά δεδομένα που τηρούνται στο σύστημα Nomos One για τον πελάτη: "
            "στοιχεία πελάτη, τιμολόγια, υποθέσεις (με δικασίμους, παράβολα και αλλαγές παρακολούθησης), γραμμάτια, "
            "πελατολογικό λογαριασμό και κινήσεις, KYC, πληρωμές κάρτας, τραπεζικές κινήσεις που συμφωνήθηκαν με "
            "τιμολόγιά του, έξοδα του γραφείου που αφορούν τον ίδιο ή τις υποθέσεις του, μηνύματα πύλης, ιστορικό "
            "πρόσβασης στην πύλη και υπογραφές με το email του.\n"
            "Δεν περιλαμβάνονται αρχεία (π.χ. έγγραφα που ανέβηκαν) και το ελεγκτικό ίχνος του συστήματος.\n"
        ))
    buf.seek(0)
    safe_name = (client.get("name", "client")).replace(" ", "_")[:40]
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"gdpr-{safe_name}.zip")},
    )
