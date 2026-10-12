"""Βοηθήματα HTTP — κωδικοποίηση ονομάτων αρχείων με ελληνικούς χαρακτήρες.

Οι κεφαλίδες HTTP μεταφέρονται σε latin-1. Ένα ελληνικό όνομα αρχείου μέσα σε
``Content-Disposition`` ρίχνει τον server με ``UnicodeEncodeError`` — HTTP 500 τη στιγμή
της λήψης, χωρίς κανένα χρήσιμο μήνυμα.

Ο κώδικας αντιμετώπιζε το πρόβλημα με χειροκίνητη αντικατάσταση ενός συγκεκριμένου
προθέματος κάθε φορά (``"ΤΠΥ" -> "TPY"``, ``"ΓΡΠ" -> "GRP"``). Δούλευε κατά τύχη για τη
συνηθισμένη περίπτωση και έσπαγε για τις υπόλοιπες: το πιστωτικό (``ΠΙΣ-``), η εξαγωγή
ΓΚΠΔ (όνομα πελάτη) και τα έγγραφα σύνταξης (ελληνικός τίτλος) επέστρεφαν όλα 500.

Η σωστή λύση είναι το RFC 6266: μια ASCII εκδοχή για παλιούς πελάτες και ``filename*``
με UTF-8 percent-encoding για όλους τους σύγχρονους browsers, οι οποίοι προτιμούν το
δεύτερο και εμφανίζουν το όνομα ακέραιο στα ελληνικά.
"""

import os
from urllib.parse import quote, urlsplit

# Μεταγραφή για το ASCII fallback. Δεν είναι πρότυπο μεταγραφής — αρκεί να παράγει
# αναγνωρίσιμο όνομα στους ελάχιστους πελάτες που αγνοούν το filename*.
_TRANSLITERATION = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o", "ά": "a", "έ": "e", "ή": "i", "ί": "i", "ό": "o", "ύ": "y", "ώ": "o",
    "ϊ": "i", "ϋ": "y", "ΐ": "i", "ΰ": "y",
}


def ascii_filename(name: str, default: str = "download") -> str:
    """Μεταγραφή σε ASCII, για χρήση ως fallback."""
    out = []
    for ch in name or "":
        lower = ch.lower()
        if lower in _TRANSLITERATION:
            mapped = _TRANSLITERATION[lower]
            out.append(mapped.upper() if ch.isupper() else mapped)
        elif ch.isascii() and (ch.isalnum() or ch in "-_. "):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip(" ._") or default
    return cleaned[:80]


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Κεφαλίδα ``Content-Disposition`` που δεν σκάει με ελληνικά (RFC 6266).

    >>> content_disposition("ΠΙΣ-2026-0001.pdf")
    'attachment; filename="PIS-2026-0001.pdf"; filename*=UTF-8\\'\\'%CE%A0%CE%99%CE%A3-2026-0001.pdf'
    """
    fallback = ascii_filename(filename)
    quoted = quote(filename or "download", safe="")
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quoted}'


# ------------------------------------------------------------------ επιτρεπτές διευθύνσεις ---

DEFAULT_DEV_ORIGINS = ["http://localhost:3000", "http://localhost:8080"]
_DEFAULT_PORTS = {"http": 80, "https": 443}


def allowed_origins() -> list[str]:
    """Οι διευθύνσεις (scheme://host[:port]) από τις οποίες λειτουργεί η εφαρμογή του γραφείου.

    **Μία** λίστα για δύο χρήσεις: το CORS (credentials + wildcard απορρίπτεται από τους browsers) και οι διευθύνσεις
    επιστροφής μετά την πληρωμή. Δύο αντίγραφα θα αποκλίνονταν. Προτεραιότητα: ``CORS_ORIGINS`` (κόμμα-χωρισμένα) →
    ``PUBLIC_URL`` → ``PREVIEW_ENDPOINT`` → διευθύνσεις ανάπτυξης.
    """
    raw = os.environ.get("CORS_ORIGINS") or os.environ.get("PUBLIC_URL") or os.environ.get("PREVIEW_ENDPOINT") or ""
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or list(DEFAULT_DEV_ORIGINS)


def _origin_key(scheme: str, host: str, port: int | None) -> str:
    host = host.lower()
    if ":" in host:                                   # IPv6
        host = f"[{host}]"
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def checked_return_url(url, allowed, *, allowed_paths: tuple[str, ...] = ("",)) -> str | None:
    """Η διεύθυνση επιστροφής της πληρωμής σε κανονική μορφή — ή ``None`` αν δεν επιτρέπεται.

    Το ``origin_url`` γίνεται ``success_url``/``cancel_url`` της συνεδρίας Stripe, δηλαδή η σελίδα όπου οδηγείται ο
    πελάτης αφού πληρώσει σε γνήσια σελίδα πληρωμής. Αν το όριζε ο καλών, ένας κακόβουλος θα έστηνε πληρωμή που
    καταλήγει στη δική του διεύθυνση. Επιτρέπεται μόνο: σχήμα http(s), origin που ανήκει στη λίστα ``allowed``, χωρίς
    στοιχεία χρήστη, query ή fragment, και διαδρομή **ακριβώς** μία από τις ``allowed_paths`` (π.χ. ``/portal/<token>``).
    Η κανονική μορφή δεν έχει τελική κάθετο ούτε προεπιλεγμένη θύρα.
    """
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or any(ch in url for ch in " \\") or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc or parts.query or parts.fragment:
        return None
    origin = _origin_key(parts.scheme, parts.hostname, port)
    permitted = set()
    for entry in allowed:
        try:
            ep = urlsplit(str(entry).strip())
            if ep.scheme in _DEFAULT_PORTS and ep.hostname:
                permitted.add(_origin_key(ep.scheme, ep.hostname, ep.port))
        except ValueError:
            continue
    if origin not in permitted:
        return None
    path = parts.path.rstrip("/")
    if path not in allowed_paths:
        return None
    return origin + path


def configured_origin(*candidates) -> str:
    """Η πρώτη έγκυρη διεύθυνση εφαρμογής (``scheme://host[:port]``, κανονική μορφή) από τις υποψήφιες τιμές, ή ``""``.

    Για σημεία όπου η διεύθυνση δεν προκύπτει από αίτημα αλλά από ρύθμιση (``public_url`` του γραφείου, ``PUBLIC_URL``):
    ο σύνδεσμος πληρωμής των υπενθυμίσεων και ο σύνδεσμος της πρόσκλησης χωρίς διεύθυνση από τον browser. Μια τιμή που δεν
    είναι καθαρό origin http(s) (κενή, με διαδρομή, άλλο σχήμα, σκουπίδια) αγνοείται — δεν μπαίνει ποτέ σε email."""
    for candidate in candidates:
        url = checked_return_url(candidate, [candidate]) if isinstance(candidate, str) else None
        if url:
            return url
    return ""
