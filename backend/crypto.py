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
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# Διακριτικά ώστε να ξεχωρίζει μια κρυπτογραφημένη τιμή από μια παλιά plaintext.
# v1: σκέτο SHA-256 πάνω στο passphrase — χωρίς KDF, χωρίς salt, μία πράξη ανά δοκιμή.
#     Διατηρείται ΜΟΝΟ για ανάγνωση ήδη αποθηκευμένων τιμών.
# v2: PBKDF2-HMAC-SHA256. Το κόστος ανά δοκιμή κλειδιού μεγαλώνει κατά ~6 τάξεις μεγέθους,
#     ώστε ένα ανθρώπινο passphrase να μην είναι πλέον εύκολα εξαντλητικά επιτιθέμενο.
#     Το salt είναι σταθερό ανά εγκατάσταση (το κλειδί πρέπει να παράγεται ξανά σε κάθε
#     εκκίνηση χωρίς αποθηκευμένη κατάσταση)· αυτό αποτρέπει γενικούς rainbow πίνακες
#     αλλά όχι στοχευμένη επίθεση — γι' αυτό το SECRETS_KEY παραμένει η σωστή ρύθμιση.
PREFIX_V1 = "enc:v1:"
PREFIX_V2 = "enc:v2:"
PREFIX = PREFIX_V1  # συμβατότητα με υπάρχοντα imports
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT = b"nomos-one/secrets/v2"

# Πεδία του Settings που δεν επιτρέπεται να φύγουν ποτέ προς τον client.
SECRET_SETTINGS_FIELDS = ("smtp_password", "imap_password", "aade_subscription_key", "gocardless_secret_key")
MASK = "••••••••"

_fernet_v1: Fernet | None = None
_fernet_v2: Fernet | None = None


def _ready_fernet_key() -> bytes | None:
    """Αν το SECRETS_KEY είναι ήδη έτοιμο Fernet key (32 bytes base64), χρησιμοποιείται
    αυτούσιο: είναι τυχαίο κλειδί, δεν χρειάζεται — ούτε ωφελείται από — KDF."""
    raw = os.environ.get("SECRETS_KEY") or ""
    if not raw:
        return None
    try:
        candidate = raw.encode()
        if len(base64.urlsafe_b64decode(candidate)) == 32:
            return candidate
    except Exception:
        pass
    return None


def _key_material() -> str:
    raw = os.environ.get("SECRETS_KEY") or ""
    if raw:
        return raw
    fallback = os.environ.get("JWT_SECRET")
    if not fallback:
        raise RuntimeError("Ορίστε SECRETS_KEY (ή τουλάχιστον JWT_SECRET) για την κρυπτογράφηση μυστικών.")
    return f"nomos-secrets:{fallback}"


def _derive_key_v1() -> bytes:
    """Παλιά παραγωγή κλειδιού — μόνο για αποκρυπτογράφηση υπαρχόντων τιμών."""
    ready = _ready_fernet_key()
    if ready:
        return ready
    return base64.urlsafe_b64encode(hashlib.sha256(_key_material().encode()).digest())


def _derive_key_v2() -> bytes:
    ready = _ready_fernet_key()
    if ready:
        return ready
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=_PBKDF2_SALT, iterations=_PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(_key_material().encode()))


def _cipher_v1() -> Fernet:
    global _fernet_v1
    if _fernet_v1 is None:
        _fernet_v1 = Fernet(_derive_key_v1())
    return _fernet_v1


def _cipher_v2() -> Fernet:
    global _fernet_v2
    if _fernet_v2 is None:
        _fernet_v2 = Fernet(_derive_key_v2())
    return _fernet_v2


def reset_cipher_cache():
    """Για tests που αλλάζουν το SECRETS_KEY μέσα στη διαδικασία."""
    global _fernet_v1, _fernet_v2
    _fernet_v1 = None
    _fernet_v2 = None


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith((PREFIX_V1, PREFIX_V2))


def encrypt(value: str) -> str:
    """Κρυπτογραφεί μια τιμή. Κενό μένει κενό· ήδη κρυπτογραφημένο δεν διπλοκρυπτογραφείται.

    Τιμή αποθηκευμένη με v1 αναβαθμίζεται σε v2 με την πρώτη επανεγγραφή — αν δεν
    μπορεί να αποκρυπτογραφηθεί (αλλαγμένο κλειδί) μένει ως έχει, χωρίς απώλεια.
    """
    if not value:
        return value
    if value.startswith(PREFIX_V2):
        return value
    if value.startswith(PREFIX_V1):
        plain = decrypt(value)
        if not plain:
            return value
        value = plain
    return PREFIX_V2 + _cipher_v2().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    """Αποκρυπτογραφεί. Παλιά plaintext τιμή επιστρέφεται ως έχει (backwards compatible)."""
    if not value or not is_encrypted(value):
        return value or ""
    if value.startswith(PREFIX_V2):
        token, cipher = value[len(PREFIX_V2):], _cipher_v2()
    else:
        token, cipher = value[len(PREFIX_V1):], _cipher_v1()
    try:
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
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
