"""Ελεγκτικό ίχνος — ποιος είδε ή άλλαξε τι, πότε και από πού.

Το μοντέλο ``AuditLog`` και η collection ``audit_logs`` υπήρχαν από την αρχή, με index
και backfill, αλλά **κανείς δεν έγραφε ποτέ σε αυτήν**. Ήταν συμμόρφωση που φαινόταν να
υπάρχει: σε έλεγχο κατά Ν.4557/2018 ή σε αίτημα υποκειμένου κατά ΓΚΠΔ, το γραφείο δεν
μπορούσε να απαντήσει ποιος άνοιξε τον φάκελο ενός πελάτη.

Δύο μηχανισμοί, σκόπιμα:

* **Middleware** για κάθε αίτημα που αλλάζει κατάσταση (POST/PUT/PATCH/DELETE). Η
  πληρότητα είναι το ζητούμενο σε έλεγχο — δεν επιτρέπεται να ξεχαστεί ένα endpoint.
* **``audit_read()``** ρητά, στα λίγα σημεία όπου μια απλή *ανάγνωση* εκθέτει προσωπικά
  δεδομένα (φάκελος KYC, εξαγωγή ΓΚΠΔ, PDF τιμολογίου από το portal). Το να καταγράφεται
  κάθε GET θα έπνιγε το ίχνος σε θόρυβο.

**Δεν καταγράφεται ποτέ σώμα αιτήματος.** Θα κατέληγαν στο ίχνος κωδικοί, κλειδιά ΑΑΔΕ και
συνθηματικά SMTP. Καταγράφεται το *σχήμα* της ενέργειας: οντότητα και αναγνωριστικό.

**Append-only.** Το module δεν εκθέτει ενημέρωση ή διαγραφή, και η collection εξαιρείται από
τον βρόχο backfill του ``startup_event`` — γραμμή ελεγκτικού ίχνους δεν ξαναγράφεται ποτέ.

Δεν υλοποιείται αλυσίδα κατακερματισμού (hash chain). Θα απαιτούσε σειριοποιητή ανά γραφείο,
δηλαδή ένα σειριακό round-trip στη βάση σε **κάθε** αίτημα που αλλάζει κατάσταση. Η
προστασία από αλλοίωση εδώ στηρίζεται στο ότι ο κώδικας δεν γράφει ποτέ πάνω σε υπάρχουσα
γραμμή, στα δικαιώματα της βάσης και στα αντίγραφα ασφαλείας. Είναι προσθετική αλλαγή αν
χρειαστεί αργότερα.

**Διατήρηση:** τα στοιχεία δέουσας επιμέλειας κατά Ν.4557/2018 (άρθρο 30) τηρούνται πέντε
έτη. Δεν υλοποιείται εδώ αυτόματη διαγραφή: το σβήσιμο ελεγκτικού ίχνους είναι απόφαση του
γραφείου, όχι προεπιλογή του λογισμικού.
"""

import logging
from datetime import datetime, timezone

from fastapi import Request

from db import db
from tenant import LEGACY_FIRM_ID

logger = logging.getLogger(__name__)

# Διαδρομές που δεν αξίζουν γραμμή ελεγκτικού ίχνους: θόρυβος ή ήδη καταγεγραμμένες αλλού.
_SKIP_PATHS = ("/api/health", "/api/auth/refresh")

# Ενέργειες όπου το αποτέλεσμα έχει σημασία ακόμη κι όταν αποτυγχάνει (π.χ. αποτυχημένη
# σύνδεση). Για τις υπόλοιπες, ένα 4xx από επικύρωση δεν χρειάζεται να γεμίζει το ίχνος.
_ALWAYS_LOG_PATHS = ("/api/auth/login", "/api/auth/2fa", "/api/auth/reset-password",
                     "/api/auth/forgot-password", "/api/auth/register")


def _client_ip(request: Request) -> str:
    """Η IP του χρήστη. Απαιτεί το ``--proxy-headers`` του uvicorn πίσω από nginx —
    χωρίς αυτό επιστρέφει την IP του container και το ίχνος γίνεται άχρηστο."""
    return request.client.host if request.client else ""


def _looks_like_object_id(value: str) -> bool:
    return len(value) == 24 and all(c in "0123456789abcdef" for c in value.lower())


def _entity_from_path(path: str) -> tuple[str, str, str]:
    """Οντότητα, αναγνωριστικό και υπο-ενέργεια από τη διαδρομή.

    ``/api/invoices/68f.../issue`` -> ``("invoices", "68f...", "issue")``. Η υπο-ενέργεια
    είναι το πιο χρήσιμο κομμάτι σε έλεγχο: χωρίς αυτήν, η έκδοση και η δημιουργία
    τιμολογίου καταγράφονταν και οι δύο ως «post:invoices» και δεν ξεχώριζαν.
    """
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "api":
        parts = parts[1:]
    if not parts:
        return "", "", ""
    entity = parts[0]
    entity_id = ""
    sub = ""
    for p in parts[1:]:
        if _looks_like_object_id(p):
            entity_id = p
        elif not sub and len(p) < 40:
            # Τα portal tokens είναι μακριά και είναι διαπιστευτήρια: δεν καταγράφονται.
            sub = p
    return entity, entity_id, sub


async def record(
    *,
    action: str,
    firm_id: str = LEGACY_FIRM_ID,
    entity: str = "",
    entity_id: str = "",
    user_email: str = "",
    actor_type: str = "user",
    details: str = "",
    ip_address: str = "",
    user_agent: str = "",
    method: str = "",
    path: str = "",
    status_code: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Γράφει μία γραμμή. Ποτέ δεν σκάει: αποτυχία καταγραφής δεν ρίχνει το αίτημα.

    Μια αποτυχία εδώ είναι σοβαρό γεγονός και καταγράφεται στο log της εφαρμογής, αλλά
    δεν επιτρέπεται να εμποδίσει τον δικηγόρο να εκδώσει τιμολόγιο.
    """
    try:
        await db.audit_logs.insert_one({
            "firm_id": firm_id or LEGACY_FIRM_ID,
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "user_email": user_email,
            "actor_type": actor_type,
            "details": details[:500],
            "ip_address": ip_address,
            "user_agent": (user_agent or "")[:200],
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:  # pragma: no cover - αμυντικό
        logger.error("Αποτυχία εγγραφής ελεγκτικού ίχνους (%s %s): %s", method, path, e)


async def audit_read(request: Request, user: dict, entity: str, entity_id: str = "",
                     details: str = "") -> None:
    """Καταγράφει *ανάγνωση* προσωπικών δεδομένων.

    Καλείται ρητά, μόνο εκεί όπου ένα GET εκθέτει δεδομένα που ο ΓΚΠΔ θεωρεί ευαίσθητα.
    """
    from tenant import firm_id_for

    await record(
        action=f"read:{entity}",
        firm_id=firm_id_for(user),
        entity=entity,
        entity_id=entity_id,
        user_email=user.get("email", ""),
        actor_type="user",
        details=details,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        method="GET",
        path=request.url.path,
        status_code=200,
    )


async def _portal_actor(path: str) -> tuple[str, str]:
    """Ο πελάτης πίσω από ένα portal token — χωρίς να καταγραφεί το ίδιο το token.

    Το token είναι διαπιστευτήριο: αν έμπαινε στο ίχνος, όποιος διάβαζε το ίχνος θα
    αποκτούσε πρόσβαση στον λογαριασμό του πελάτη.
    """
    parts = [p for p in path.split("/") if p]
    try:
        token = parts[parts.index("portal") + 1]
    except (ValueError, IndexError):
        return LEGACY_FIRM_ID, ""
    try:
        rec = await db.client_portal_tokens.find_one({"token": token})
    except Exception:
        rec = None
    if not rec:
        return LEGACY_FIRM_ID, ""
    return rec.get("firm_id", LEGACY_FIRM_ID), rec.get("client_id", "")


async def log_request(request: Request, response, duration_ms: int) -> None:
    """Καλείται από το middleware μετά την απόκριση, για κάθε αλλαγή κατάστασης."""
    path = request.url.path
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if any(path.startswith(p) for p in _SKIP_PATHS):
        return

    status = getattr(response, "status_code", None)
    interesting = any(path.startswith(p) for p in _ALWAYS_LOG_PATHS)
    # Απορρίψεις επικύρωσης σε συνηθισμένα endpoints δεν γεμίζουν το ίχνος· οι αποτυχίες
    # ταυτοποίησης όμως είναι ακριβώς αυτό που ψάχνει ένας έλεγχος.
    if status is not None and status >= 400 and not interesting:
        if status not in (401, 403):
            return

    entity, entity_id, sub = _entity_from_path(path)
    user = getattr(request.state, "user", None)

    if user:
        firm_id = user.get("firm_id") or LEGACY_FIRM_ID
        email = user.get("email", "")
        actor = "user"
    elif "/portal/" in path:
        firm_id, client_id = await _portal_actor(path)
        email = ""
        actor = "portal_client"
        if client_id:
            entity, entity_id = "client_portal", client_id
    elif path.startswith("/api/stripe/"):
        firm_id, email, actor = LEGACY_FIRM_ID, "", "system"
    else:
        # Ανώνυμο αίτημα: σύνδεση, εγγραφή, επαναφορά κωδικού. Το γραφείο δεν προκύπτει
        # από συνεδρία — το αφήνει το ίδιο το endpoint όταν αναγνωρίσει τον λογαριασμό.
        firm_id = getattr(request.state, "audit_firm_id", None) or LEGACY_FIRM_ID
        actor = "user"
        email = getattr(request.state, "audit_email", "") or ""

    verb = request.method.lower()
    action = f"{verb}:{entity}" if entity else verb
    if sub:
        action = f"{action}:{sub}"

    await record(
        action=action,
        firm_id=firm_id,
        entity=entity,
        entity_id=entity_id,
        user_email=email,
        actor_type=actor,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        method=request.method,
        path=path,
        status_code=status,
        duration_ms=duration_ms,
    )
