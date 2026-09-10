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

from urllib.parse import quote

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
