"""Περιορισμός ρυθμού αιτημάτων, με μετρητή στη Mongo.

Το `check_lockout` του `auth.py` έκανε ήδη σταθερό παράθυρο για τις συνδέσεις. Εδώ
γενικεύεται, ώστε να καλυφθεί και η **δημόσια** επιφάνεια του portal, που δεν είχε κανένα
όριο: επαληθεύτηκε ότι δεχόταν 60 διαδοχικά μηνύματα και ένα μήνυμα δύο εκατομμυρίων
χαρακτήρων, χωρίς καμία αντίσταση.

Σκόπιμα χωρίς Redis ή slowapi: ένα ακόμη εξάρτημα στην υποδομή ενός δικηγορικού γραφείου
είναι κάτι που πρέπει να συντηρείται. Ένα ατομικό ``find_one_and_update`` με ``$inc``
κοστίζει ένα round-trip και αρκεί απόλυτα για αυτή την κλίμακα.

Το κλειδί δεν περιέχει ποτέ το ίδιο το portal token — είναι διαπιστευτήριο. Χρησιμοποιείται
ο κατακερματισμός του, με την ίδια λογική που το token δεν μπαίνει στο ελεγκτικό ίχνος.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pymongo import ReturnDocument

from db import db


def key_for_token(token: str) -> str:
    """Σταθερό κλειδί από token, χωρίς να αποθηκευτεί το token."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:32]


async def check(bucket: str, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """``(επιτρέπεται, δευτερόλεπτα_αναμονής)`` για σταθερό παράθυρο.

    Σε αποτυχία της βάσης επιτρέπει το αίτημα: ο περιορισμός ρυθμού δεν είναι έλεγχος
    πρόσβασης, και δεν πρέπει να αποκλείσει έναν πελάτη επειδή κόλλησε ένας μετρητής.
    """
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    window_start = epoch - (epoch % window_seconds)
    doc_id = f"{bucket}:{key}:{window_start}"
    try:
        rec = await db.rate_limits.find_one_and_update(
            {"_id": doc_id},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {
                    # Το TTL index καθαρίζει μόνο του· διπλό παράθυρο για ασφάλεια.
                    "expires_at": datetime.fromtimestamp(window_start, timezone.utc)
                    + timedelta(seconds=window_seconds * 2),
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        return True, 0

    if (rec or {}).get("count", 1) > limit:
        return False, max(window_start + window_seconds - epoch, 1)
    return True, 0


async def enforce(bucket: str, key: str, limit: int, window_seconds: int,
                  message: str = "Πολλά αιτήματα. Δοκιμάστε ξανά σε λίγο.") -> None:
    """Ίδιο με το ``check``, αλλά σηκώνει 429 με ``Retry-After``."""
    allowed, retry_after = await check(bucket, key, limit, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"{message} ({retry_after}s)",
            headers={"Retry-After": str(retry_after)},
        )
