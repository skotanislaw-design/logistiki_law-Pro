"""Δύο παράγοντες ταυτοποίησης (TOTP, RFC 6238) + εφεδρικοί κωδικοί.

Υλοποιείται με τη στάνταρ βιβλιοθήκη (hmac/hashlib/base64) — καμία νέα εξάρτηση:
ένας κλεμμένος κωδικός δεν αρκεί πλέον για πρόσβαση στα οικονομικά και στα
δεδομένα πελατών του γραφείου.

* Το TOTP secret αποθηκεύεται **κρυπτογραφημένο** (βλ. `crypto.py`).
* Οι εφεδρικοί κωδικοί αποθηκεύονται μόνο ως bcrypt hashes και είναι μιας χρήσης.
* Επιτρέπεται ανοχή ±1 χρονικού βήματος (30″) για απόκλιση ρολογιού, και κάθε
  κωδικός δεν μπορεί να ξαναχρησιμοποιηθεί (replay) στο ίδιο παράθυρο.
"""
import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from urllib.parse import quote

import bcrypt

DIGITS = 6
PERIOD = 30
WINDOW = 1                 # ± ένα βήμα (±30 δευτερόλεπτα)
BACKUP_CODE_COUNT = 8
ISSUER = "Nomos One"


def generate_secret(length: int = 20) -> str:
    """Base32 secret, όπως το περιμένουν Google Authenticator / Authy / 1Password."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    padding = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** DIGITS)).zfill(DIGITS)


def totp_now(secret_b32: str, at: float | None = None) -> str:
    return _hotp(secret_b32, int((at if at is not None else time.time()) // PERIOD))


def verify_totp(secret_b32: str, code: str, at: float | None = None,
                used_counter: int | None = None) -> tuple[bool, int | None]:
    """Επιστρέφει (έγκυρο, counter). Ο counter αποθηκεύεται ώστε ο ίδιος κωδικός
    να μην γίνεται δεκτός δεύτερη φορά (replay εντός του παραθύρου)."""
    code = re.sub(r"\D", "", code or "")
    if len(code) != DIGITS or not secret_b32:
        return False, None
    now = int((at if at is not None else time.time()) // PERIOD)
    for drift in range(-WINDOW, WINDOW + 1):
        counter = now + drift
        if used_counter is not None and counter <= used_counter:
            continue
        if hmac.compare_digest(_hotp(secret_b32, counter), code):
            return True, counter
    return False, None


def provisioning_uri(secret_b32: str, account: str, issuer: str = ISSUER) -> str:
    """otpauth:// URI για σάρωση με QR ή χειροκίνητη καταχώρηση."""
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


def format_secret(secret_b32: str) -> str:
    """Σε ομάδες των 4 για ευανάγνωστη χειροκίνητη καταχώρηση."""
    return " ".join(secret_b32[i:i + 4] for i in range(0, len(secret_b32), 4))


# ------------------------------------------------------------ backup codes --

def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    """Κωδικοί μιας χρήσης, μορφής ABCD-EFGH (χωρίς εύκολα συγχεόμενους χαρακτήρες)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def normalize_backup_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def hash_backup_codes(codes: list[str]) -> list[str]:
    return [bcrypt.hashpw(normalize_backup_code(c).encode(), bcrypt.gensalt()).decode() for c in codes]


def consume_backup_code(code: str, hashes: list[str]) -> tuple[bool, list[str]]:
    """Επιστρέφει (επιτυχία, οι hashes που απομένουν). Ο κωδικός καταναλώνεται."""
    candidate = normalize_backup_code(code).encode()
    if not candidate:
        return False, hashes
    for h in list(hashes or []):
        try:
            if bcrypt.checkpw(candidate, h.encode()):
                remaining = [x for x in hashes if x != h]
                return True, remaining
        except ValueError:
            continue
    return False, list(hashes or [])
