"""Διαστάσεις οργάνωσης των οικονομικών εγγράφων: υπόθεση, πελάτης, δικηγόρος (Φάση 1 του «ψηφιακού λογιστή»).

Ένα έξοδο ή τιμολόγιο γίνεται χρήσιμο για αποφάσεις (κερδοφορία ανά υπόθεση/πελάτη/δικηγόρο) μόνο αν λέει σε ποιον ανήκει.
Εδώ ζει ο **μοναδικός** έλεγχος αυτών των συνδέσεων, ώστε τα endpoints (και, αργότερα, το κανάλι Telegram) να μην
ξαναγράφουν κανόνες:

* Κάθε αναφορά ελέγχεται **μέσα στο γραφείο του χρήστη**. Υπόθεση ή δικηγόρος άλλου γραφείου δεν διακρίνεται από ανύπαρκτο:
  ίδια απάντηση 404, ώστε να μη διαρρέει η ύπαρξη ξένων εγγράφων.
* Μια αναφορά ελέγχεται **όταν τη θέτει ή την αλλάζει ο χρήστης** — όχι όταν απλώς ξαναστέλνεται όπως ήδη ήταν
  αποθηκευμένη (η φόρμα επεξεργασίας ξαναστέλνει τα πάντα). Αλλιώς ένα έξοδο με δικηγόρο που δεν υπάρχει πια (οι χρήστες δεν
  επαναφέρονται από αντίγραφο ασφαλείας) ή με πελάτη που διαγράφηκε δεν θα μπορούσε ούτε να διορθωθεί το ποσό του.
  Ο δικηγόρος που κληρονομείται από την υπόθεση δεν ελέγχεται ποτέ: δεν τον όρισε ο χρήστης.
* Ο υπεύθυνος δικηγόρος είναι χρήστης του γραφείου με ρόλο `admin` ή `lawyer`· ο `accountant` δεν είναι δικηγόρος.
* Αντιφατικός συνδυασμός απορρίπτεται (400) αντί να «διορθώνεται» σιωπηλά — εξαίρεση ο πελάτης ενός εξόδου υπόθεσης, που
  προκύπτει πάντα από την υπόθεση και ποτέ δεν δίνεται από τον client.
* Τα ids αποθηκεύονται **πάντα σε μία μορφή** (πεζά hex). Η `ObjectId.is_valid` δέχεται και κεφαλαία, και κάθε μετέπειτα
  σύγκριση κειμένου (σύνοψη υπόθεσης, 409 στη διαγραφή, διάδοση πελάτη, εξαγωγή ΓΚΠΔ) θα έχανε το έγγραφο.
* Ο δικηγόρος αποθηκεύεται **υλοποιημένος** (κληρονομείται από την υπόθεση τη στιγμή της δημιουργίας). Όταν ένα έγγραφο
  αλλάζει υπόθεση **χωρίς να δηλωθεί δικηγόρος**, ο δικηγόρος που ισούται με τον υπεύθυνο της παλιάς υπόθεσης (ή είναι
  κενός) ακολουθεί τη νέα· ο δικηγόρος που διαφέρει έχει οριστεί ρητά και μένει. Ρητός δικηγόρος στο αίτημα ισχύει πάντα·
  το κενό `""` σημαίνει «ο υπεύθυνος της υπόθεσης».
"""
from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from db import db
from tenant import tenant_query

LAWYER_ROLES = ("admin", "lawyer")
EXPENSE_SCOPES = ("unassigned", "office", "client", "case")
CLIENT_SCOPES = ("client", "case")          # τα εύρη στα οποία το έξοδο ανήκει σε πελάτη (και άρα μπορεί να χρεωθεί)
# Τα πεδία «σε ποιον ανήκει» ενός εξόδου, με την τιμή που παίρνουν όταν δεν δοθούν.
MATTER_DEFAULTS = {"scope": "unassigned", "case_id": "", "client_id": "", "lawyer_id": "", "rebillable": False}
MATTER_FIELDS = tuple(MATTER_DEFAULTS)


def canonical_id(value):
    """Το id στη μορφή που αποθηκεύει η MongoDB (πεζά hex). Ό,τι δεν είναι έγκυρο id επιστρέφεται αμετάβλητο — τον
    κακοσχηματισμένο αριθμό τον απορρίπτει ο καλών με το δικό του μήνυμα."""
    if isinstance(value, str) and ObjectId.is_valid(value):
        return str(ObjectId(value))
    return value


def _oid(value, message: str) -> ObjectId:
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise HTTPException(status_code=400, detail=message)
    return ObjectId(value)


async def load_case(user: dict, case_id: str) -> dict:
    """Η υπόθεση του γραφείου του χρήστη, ή 400 (μη έγκυρο id) / 404 (δεν υπάρχει στο γραφείο)."""
    doc = await db.cases.find_one(tenant_query(user, {"_id": _oid(case_id, "Μη έγκυρη υπόθεση.")}))
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε.")
    return doc


async def load_client(user: dict, client_id: str) -> dict:
    doc = await db.clients.find_one(tenant_query(user, {"_id": _oid(client_id, "Μη έγκυρος πελάτης.")}))
    if not doc:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε.")
    return doc


async def load_lawyer(user: dict, lawyer_id: str) -> dict:
    doc = await db.users.find_one(tenant_query(user, {"_id": _oid(lawyer_id, "Μη έγκυρος δικηγόρος.")}), {"name": 1, "role": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Ο δικηγόρος δεν βρέθηκε.")
    if doc.get("role") not in LAWYER_ROLES:
        raise HTTPException(status_code=400, detail="Ο χρήστης που επιλέχθηκε δεν είναι δικηγόρος του γραφείου.")
    return doc


async def _find_case(user: dict, case_id) -> dict | None:
    """Η υπόθεση του γραφείου αν υπάρχει — χωρίς σφάλμα (για αναφορές που δεν αλλάζουν και μπορεί να δείχνουν στο κενό)."""
    if not isinstance(case_id, str) or not ObjectId.is_valid(case_id):
        return None
    return await db.cases.find_one(tenant_query(user, {"_id": ObjectId(case_id)}))


async def inherited_matter(firm_id: str, case_id) -> tuple[str, str]:
    """`(case_id, lawyer_id)` που κληρονομεί ένα έγγραφο από μια υπόθεση (π.χ. γραμμάτιο → πρόχειρο τιμολόγιο).

    Δεν πετά σφάλμα, γιατί δεν απαντά σε αίτημα του χρήστη για σύνδεση αλλά συμπληρώνει μια σύνδεση που ήδη υπήρχε: αν η
    υπόθεση δεν υπάρχει πια στο γραφείο, επιστρέφει κενά και το νέο έγγραφο απλώς δεν συνδέεται (αντί να κρατά id που
    δείχνει στο κενό). Ο υπεύθυνος δικηγόρος είναι κενός όταν η υπόθεση δεν έχει οριστεί."""
    if not isinstance(case_id, str) or not ObjectId.is_valid(case_id):
        return "", ""
    doc = await db.cases.find_one({"_id": ObjectId(case_id), "firm_id": firm_id}, {"lawyer_id": 1})
    if not doc:
        return "", ""
    return str(doc["_id"]), doc.get("lawyer_id") or ""


async def resolve_case_lawyer(user: dict, lawyer_id: str) -> str:
    """Ο υπεύθυνος δικηγόρος μιας υπόθεσης: κενός (δεν έχει οριστεί) ή υπαρκτός δικηγόρος του γραφείου."""
    lawyer_id = (lawyer_id or "").strip()
    if lawyer_id:
        lawyer_id = str((await load_lawyer(user, lawyer_id))["_id"])
    return lawyer_id


async def resolve_case_client(user: dict, client_id: str) -> tuple[str, str]:
    """`(id, όνομα)` του πελάτη μιας υπόθεσης: κενά (χωρίς πελάτη) ή υπαρκτός πελάτης του γραφείου. Από αυτόν προκύπτει ο
    πελάτης των εξόδων της υπόθεσης, άρα δεν μένει id που δείχνει σε ξένο ή ανύπαρκτο πελάτη."""
    client_id = (client_id or "").strip()
    if not client_id:
        return "", ""
    doc = await load_client(user, client_id)
    return str(doc["_id"]), doc.get("name") or ""


async def matter_label(user: dict, case_id: str, lawyer_id: str) -> str:
    """«ΥΠΘ-2026-0001 Τίτλος, δικηγόρος Όνομα» για το ελεγκτικό ίχνος. Ό,τι δεν υπάρχει πια γράφεται ως άγνωστο, και η
    σύνταξη δεν αποτυγχάνει ποτέ: το ίχνος δεν επιτρέπεται να εμποδίσει την αλλαγή που περιγράφει."""
    case_part, lawyer_part = "καμία υπόθεση", "κανένας δικηγόρος"
    if case_id:
        case = await _find_case(user, case_id)
        case_part = f"{case.get('number') or ''} {case.get('title') or ''}".strip() if case else "(άγνωστη υπόθεση)"
    if lawyer_id and ObjectId.is_valid(lawyer_id):
        lawyer = await db.users.find_one(tenant_query(user, {"_id": ObjectId(lawyer_id)}), {"name": 1, "email": 1})
        lawyer_part = f"δικηγόρος {(lawyer or {}).get('name') or (lawyer or {}).get('email')}" if lawyer else "δικηγόρος (άγνωστος χρήστης)"
    elif lawyer_id:
        lawyer_part = "δικηγόρος (άγνωστος χρήστης)"
    return f"{case_part}, {lawyer_part}"


async def lawyer_after_case_change(user: dict, *, stored_lawyer: str, old_case_id: str, new_case: dict | None) -> str:
    """Ο δικηγόρος ενός εγγράφου που αλλάζει υπόθεση **χωρίς να δηλωθεί δικηγόρος** στο αίτημα.

    Ο δικηγόρος αποθηκεύεται υλοποιημένος, άρα δεν φαίνεται από μόνος του αν τον όρισε ο χρήστης ή προήλθε από την παλιά
    υπόθεση. Αν είναι κενός ή ισούται με τον υπεύθυνο της παλιάς υπόθεσης, είναι ό,τι θα έδινε και η κληρονομιά, άρα
    ακολουθεί τη νέα υπόθεση. Αν διαφέρει, τον έχει ορίσει κάποιος ρητά και μένει."""
    if stored_lawyer:
        old_case = await _find_case(user, old_case_id)
        if stored_lawyer != ((old_case or {}).get("lawyer_id") or ""):
            return stored_lawyer
    return (new_case or {}).get("lawyer_id") or ""


async def resolve_invoice_matter(user: dict, case_id: str | None, lawyer_id: str | None,
                                 stored: dict | None = None) -> tuple[str, str]:
    """`(case_id, lawyer_id)` ενός τιμολογίου. Η υπόθεση δεν χρειάζεται να έχει τον ίδιο πελάτη με το τιμολόγιο (μπορεί να
    τιμολογείται τρίτος που πληρώνει).

    `None` σε μία από τις δύο τιμές σημαίνει «δεν στάλθηκε στο αίτημα» και κρατά την αποθηκευμένη (`stored`, το έγγραφο
    που ενημερώνεται· χωρίς αυτό, στη δημιουργία, τίποτα δεν είναι αποθηκευμένο). Κενό κείμενο σημαίνει ρητό «καμία
    υπόθεση» και «ο υπεύθυνος της υπόθεσης» αντίστοιχα. Ελέγχονται μόνο οι αναφορές που ορίζονται ή αλλάζουν."""
    created = not stored
    stored = stored or {}
    stored_case = canonical_id((stored.get("case_id") or "").strip())
    stored_lawyer = canonical_id((stored.get("lawyer_id") or "").strip())
    new_case = stored_case if case_id is None else canonical_id((case_id or "").strip())
    case_changed = new_case != stored_case
    case = None
    if new_case:
        if created or case_changed:
            case = await load_case(user, new_case)
            new_case = str(case["_id"])
        else:
            case = await _find_case(user, new_case)       # αμετάβλητη αναφορά: δεν σφάλλει αν η υπόθεση δεν υπάρχει πια

    if lawyer_id is None:
        lawyer = stored_lawyer
        if case_changed and new_case:
            lawyer = await lawyer_after_case_change(user, stored_lawyer=stored_lawyer, old_case_id=stored_case, new_case=case)
    else:
        lawyer = canonical_id((lawyer_id or "").strip())
        if not lawyer:
            lawyer = (case or {}).get("lawyer_id") or ""                # κληρονομείται: δεν ελέγχεται
        elif lawyer != stored_lawyer:
            lawyer = str((await load_lawyer(user, lawyer))["_id"])
    return new_case, lawyer


async def merge_expense_matter(user: dict, stored: dict, sent: set, values: dict) -> dict:
    """Οι τιμές `{scope, case_id, client_id, lawyer_id, rebillable}` που θα ελεγχθούν σε ένα PUT εξόδου.

    * Αν στάλθηκε `scope`: το αίτημα ορίζει ολόκληρη την ομάδα και ό,τι λείπει από αυτήν παίρνει την προεπιλογή του
      (αλλιώς, π.χ., η αλλαγή από «υπόθεση» σε «γραφείο» θα κρατούσε την παλιά υπόθεση και θα αποτύγχανε ως αντίφαση).
    * Αλλιώς: ξεκινά από το αποθηκευμένο έξοδο και αλλάζει **μόνο ό,τι στάλθηκε** — ένα αίτημα που στέλνει μόνο
      `lawyer_id` ή μόνο `rebillable` δεν επιστρέφει το έξοδο σε «χωρίς ταξινόμηση».
      Αν αλλάζει μόνο η υπόθεση, ο δικηγόρος ακολουθεί τον κανόνα του `lawyer_after_case_change`."""
    if "scope" in sent:
        merged = dict(MATTER_DEFAULTS)
    else:
        merged = {f: (stored.get(f) if stored.get(f) is not None else MATTER_DEFAULTS[f]) for f in MATTER_FIELDS}
    for field in MATTER_FIELDS:
        if field in sent:
            merged[field] = values[field]
    if "scope" not in sent and "case_id" in sent and "lawyer_id" not in sent and merged["scope"] == "case":
        old_case = canonical_id((stored.get("case_id") or "").strip())
        new_case = canonical_id((merged["case_id"] or "").strip())
        if new_case != old_case:
            merged["lawyer_id"] = await lawyer_after_case_change(
                user, stored_lawyer=canonical_id((stored.get("lawyer_id") or "").strip()), old_case_id=old_case,
                new_case=await _find_case(user, new_case))
    return merged


async def resolve_expense_matter(user: dict, *, scope: str, case_id: str, client_id: str, lawyer_id: str,
                                 rebillable: bool, stored: dict | None = None) -> dict:
    """Ελέγχει και συμπληρώνει τις διαστάσεις ενός εξόδου. Επιστρέφει τις τιμές που θα αποθηκευτούν:
    `{scope, case_id, client_id, lawyer_id, rebillable}`.

    Πίνακας εύρους (scope):
      * unassigned / office — χωρίς υπόθεση ή πελάτη· ο δικηγόρος είναι προαιρετικός. Δεν χρεώνεται σε πελάτη.
      * client — απαιτεί πελάτη, όχι υπόθεση.
      * case   — απαιτεί υπόθεση· ο πελάτης προκύπτει από αυτήν (ό,τι κι αν στάλθηκε αγνοείται) και, αν δεν δοθεί δικηγόρος,
                 κληρονομείται ο υπεύθυνος της υπόθεσης.

    Με `stored` (το έξοδο που ενημερώνεται) οι αναφορές που δεν αλλάζουν δεν ξαναελέγχονται — βλ. docstring του module."""
    created = not stored
    stored = stored or {}
    if scope not in EXPENSE_SCOPES:
        raise HTTPException(status_code=400, detail="Μη έγκυρο εύρος εξόδου.")
    case_id = canonical_id((case_id or "").strip())
    client_id = canonical_id((client_id or "").strip())
    lawyer_id = canonical_id((lawyer_id or "").strip())
    stored_case = canonical_id((stored.get("case_id") or "").strip())
    stored_client = canonical_id((stored.get("client_id") or "").strip())
    stored_lawyer = canonical_id((stored.get("lawyer_id") or "").strip())
    inherited = False

    if scope in ("unassigned", "office"):
        if case_id or client_id:
            raise HTTPException(status_code=400, detail=(
                "Έξοδο χωρίς ταξινόμηση ή γενικό έξοδο γραφείου δεν συνδέεται με υπόθεση ή πελάτη· "
                "επιλέξτε εύρος «Πελάτης» ή «Υπόθεση»."))
        if rebillable:
            raise HTTPException(status_code=400, detail="Μόνο έξοδο πελάτη ή υπόθεσης μπορεί να χρεωθεί στον πελάτη.")
    elif scope == "client":
        if case_id:
            raise HTTPException(status_code=400, detail="Το εύρος «Πελάτης» δεν δέχεται υπόθεση· επιλέξτε εύρος «Υπόθεση».")
        if not client_id:
            raise HTTPException(status_code=400, detail="Επιλέξτε πελάτη για το έξοδο.")
        if created or client_id != stored_client:
            client_id = str((await load_client(user, client_id))["_id"])
    else:  # case
        if not case_id:
            raise HTTPException(status_code=400, detail="Επιλέξτε υπόθεση για το έξοδο.")
        if created or case_id != stored_case:
            case = await load_case(user, case_id)
            case_id = str(case["_id"])
        else:
            case = await _find_case(user, case_id)         # αμετάβλητη αναφορά: δεν σφάλλει αν η υπόθεση δεν υπάρχει πια
        client_id = (case.get("client_id") or "") if case else stored_client          # πάντα από την υπόθεση
        if not lawyer_id:
            lawyer_id = (case or {}).get("lawyer_id") or ""
            inherited = True

    if lawyer_id and not inherited and lawyer_id != stored_lawyer:
        lawyer_id = str((await load_lawyer(user, lawyer_id))["_id"])
    return {"scope": scope, "case_id": case_id, "client_id": client_id, "lawyer_id": lawyer_id, "rebillable": bool(rebillable)}
