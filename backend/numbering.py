"""Αριθμοδότηση εγγράφων ανά γραφείο και έτος, με ατομικό μετρητή.

Η προηγούμενη λύση στα γραμμάτια, τα παράβολα και τις υποθέσεις ήταν «πλήθος εγγράφων + 1».
Αρκεί να διαγραφεί ένα ενδιάμεσο έγγραφο για να δοθεί ξανά ο αριθμός του τελευταίου: με
τρία γραμμάτια (0001–0003) και διαγραφή του δεύτερου, το επόμενο έπαιρνε το 0003, δηλαδή
δύο έγγραφα με τον ίδιο αριθμό — και η αναζήτηση «ανά αριθμό» έβρισκε το λάθος.

Ο μετρητής (`counters`) αυξάνεται ατομικά και δεν γυρίζει ποτέ πίσω, ό,τι κι αν διαγραφεί.
"""
from pymongo import ReturnDocument

from db import db


async def next_in_series(collection, counter_id: str, firm_id: str, prefix: str, year: int) -> str:
    """Επόμενος αριθμός της σειράς `prefix-year-NNNN` για το γραφείο."""
    counter = await db.counters.find_one_and_update(
        {"_id": counter_id}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER,
    )
    seq = counter["seq"]
    # Πρώτη χρήση του μετρητή: υπάρχοντα έγγραφα (από την εποχή του «πλήθος + 1») μπορεί να
    # έχουν ήδη αριθμούς — συνέχισε πάνω από το μεγαλύτερο, ώστε να μην υπάρξει σύγκρουση.
    if seq == 1:
        existing_max = 0
        async for d in collection.find({"firm_id": firm_id, "number": {"$regex": f"^{prefix}-{year}-"}}, {"number": 1}):
            try:
                existing_max = max(existing_max, int(d["number"].rsplit("-", 1)[-1]))
            except (ValueError, KeyError):
                pass
        if existing_max >= seq:
            counter = await db.counters.find_one_and_update(
                {"_id": counter_id}, {"$set": {"seq": existing_max + 1}}, return_document=ReturnDocument.AFTER,
            )
            seq = counter["seq"]
    return f"{prefix}-{year}-{seq:04d}"
