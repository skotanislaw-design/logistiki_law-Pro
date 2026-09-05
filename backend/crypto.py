"""Κρυπτογράφηση μυστικών «σε ηρεμία» (at rest).

Τα SMTP/IMAP passwords και το subscription key της ΑΑΔΕ αποθηκεύονταν ως απλό
κείμενο στη Mongo: όποιος αποκτούσε αντίγραφο της βάσης (backup, dump, read-only
replica) έπαιρνε μαζί και το γραμματοκιβώτιο και τη διαβίβαση παραστατικών του
γραφείου. Εδώ κρυπτογραφούνται με Fernet (AES-128-CBC + HMAC-SHA256).

Το κλειδί προέρχεται από το ``SECRETS_KEY``. Αν δεν οριστεί, παράγεται από το
``JWT_SECRET`` ώστε οι υπάρχουσες εγκαταστάσεις να συνεχίσουν να δουλεύουν χωρίς
νέα μεταβλητή — αλλά η αλλαγή του JWT_SECRET τότε καθιστά τα μυστικά μη
αναγνώσιμα, γι' αυτό το ``SECRETS_KEY`` είναι η συνιστώμενη ρύθμιση.

Η αποκρυπτογράφηση είναι ανεκτική: τιμή που δεν φέρει το prefix θεωρείται
παλιά, μη κρυπτογραφημένη και επιστρέφεται ως έχει, ώστε η μετάβαση να γίνεται
σταδιακά με την πρώτη εγγραφή (βλ. ``encrypt_settings_secrets``).
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Διακριτικό ώστε να ξεχωρίζει μια κρυπτογραφημένη τιμή από μια παλιά plaintext.
PREFIX = "enc:v1:"

# Πεδία του Settings που δεν επιτρέπεται να φύγουν ποτέ προς τον client.
SECRET_SETTINGS_FIELDS = ("smtp_password", "imap_password", "aade_subscription_key")
MASK = "••••••••"

_fernet: Fernet | None = None


def _derive_key() -> bytes:
    raw = os.environ.get("SECRETS_KEY") or ""
    if raw:
        # Δεκτό είτε έτοιμο Fernet key (44 chars urlsafe base64) είτε οποιοδήποτε passphrase.
        try:
            candidate = raw.encode()
            if len(base64.urlsafe_b64decode(candidate)) == 32:
                return candidate
        except Exception:
            pass
        return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    fallback = os.environ.get("JWT_SECRET")
    if not fallback:
        raise RuntimeError("Ορίστε SECRETS_KEY (ή τουλάχιστον JWT_SECRET) για την κρυπτογράφηση μυστικών.")
    return base64.urlsafe_b64encode(hashlib.sha256(f"nomos-secrets:{fallback}".encode()).digest())


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_derive_key())
    return _fernet


def reset_cipher_cache():
    """Για tests που αλλάζουν το SECRETS_KEY μέσα στη διαδικασία."""
    global _fernet
    _fernet = None


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str) -> str:
    """Κρυπτογραφεί μια τιμή. Κενό μένει κενό· ήδη κρυπτογραφημένο δεν διπλοκρυπτογραφείται."""
    if not value:
        return value
    if is_encrypted(value):
        return value
    return PREFIX + _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Αποκρυπτογραφεί. Παλιά plaintext τιμή επιστρέφεται ως έχει (backwards compatible)."""
    if not value or not is_encrypted(value):
        return value or ""
    try:
        return _cipher().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Λάθος/αλλαγμένο κλειδί: μην σκάσεις — το feature απλώς δεν θα λειτουργήσει
        # και ο χρήστης θα κληθεί να ξανακαταχωρήσει το μυστικό.
        logger.error("Αποτυχία αποκρυπτογράφησης μυστικού — έχει αλλάξει το SECRETS_KEY;")
        return ""


def encrypt_settings_secrets(data: dict) -> dict:
    """In-place κρυπτογράφηση των μυστικών πεδίων πριν την εγγραφή στη βάση."""
    for field in SECRET_SETTINGS_FIELDS:
        if field in data and isinstance(data[field], str):
            data[field] = encrypt(data[field])
    return data


def decrypt_settings_secrets(doc: dict | None) -> dict | None:
    """Αντίγραφο του settings document με τα μυστικά σε καθαρή μορφή — για χρήση από τον server."""
    if not doc:
        return doc
    out = dict(doc)
    for field in SECRET_SETTINGS_FIELDS:
        if field in out and isinstance(out[field], str):
            out[field] = decrypt(out[field])
    return out


def mask_settings_secrets(doc: dict | None) -> dict | None:
    """Αντίγραφο για τον client: τα μυστικά δεν φεύγουν ποτέ από τον server.

    Επιστρέφει τη μάσκα όταν υπάρχει τιμή και κενό όταν δεν έχει καταχωρηθεί, ώστε
    το UI να μπορεί να δείξει «έχει ρυθμιστεί» χωρίς να μάθει το μυστικό.
    """
    if not doc:
        return doc
    out = dict(doc)
    for field in SECRET_SETTINGS_FIELDS:
        if field in out:
            out[field] = MASK if out.get(field) else ""
    return out


def merge_masked_secrets(incoming: dict, current: dict | None) -> dict:
    """Κρατά την αποθηκευμένη τιμή όταν ο client στέλνει πίσω τη μάσκα (ή κενό).

    Έτσι ένα «Αποθήκευση» στη σελίδα Ρυθμίσεων δεν σβήνει κατά λάθος τα μυστικά.
    """
    current = current or {}
    for field in SECRET_SETTINGS_FIELDS:
        if field not in incoming:
            continue
        value = incoming.get(field)
        if not value or value == MASK:
            incoming[field] = current.get(field, "")
    return incoming
