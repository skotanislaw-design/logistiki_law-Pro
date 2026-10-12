"""Αριθμοδότηση εγγράφων ανά γραφείο και έτος, με ατομικό μετρητή.

Η προηγούμενη λύση στα γραμμάτια, τα παράβολα και τις υποθέσεις ήταν «πλήθος εγγράφων + 1».
Αρκεί να διαγραφεί ένα ενδιάμεσο έγγραφο για να δοθεί ξανά ο αριθμός του τελευταίου: με
τρία γραμμάτια (0001–0003) και διαγραφή του δεύτερου, το επόμενο έπαιρνε το 0003, δηλαδή
δύο έγγραφα με τον ίδιο αριθμό — και η αναζήτηση «ανά αριθμό» έβρισκε το λάθος.

Ο μετρητής (`counters`) αυξάνεται ατομικά και δεν γυρίζει ποτέ πίσω, ό,τι κι αν διαγραφεί.

Για τα φορολογικά παραστατικά ο μετρητής κάνει δύο ακόμη πράγματα, και τα δύο **μέσα στην ίδια ατομική
εγγραφή** με την παραχώρηση του αριθμού:

* θυμάται την **τελευταία ημερομηνία** που δόθηκε στη σειρά (`last_date`) και αρνείται (με
  `SeriesDateOutOfOrder`) αριθμό σε ημερομηνία προγενέστερη. Ο έλεγχος «όχι πριν από τον προηγούμενο
  αριθμό» ενός πρώτου ελέγχου που γίνεται πριν την παραχώρηση δεν αρκεί: δύο πρόχειρα με διαφορετική
  ημερομηνία που εκδίδονται ταυτόχρονα περνούν και τα δύο τον έλεγχο·
* δεν μένει ποτέ πίσω από υπάρχοντες αριθμούς (`floor`): μετά από επαναφορά αντιγράφου ή χειροκίνητη
  διόρθωση ο μετρητής μπορεί να δείχνει μικρότερο αριθμό από τον τελευταίο που υπάρχει.
"""
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from db import db


class SeriesDateOutOfOrder(Exception):
    """Η ημερομηνία του παραστατικού είναι προγενέστερη της τελευταίας που δόθηκε στη σειρά."""

    def __init__(self, last_date: str | None):
        super().__init__(f"Η σειρά έχει ήδη δώσει αριθμό με ημερομηνία {last_date}")
        self.last_date = last_date


async def next_in_series(collection, counter_id: str, firm_id: str, prefix: str, year: int, *,
                         date_iso: str | None = None, floor: int = 0) -> str:
    """Επόμενος αριθμός της σειράς `prefix-year-NNNN` για το γραφείο.

    `date_iso`: η ημερομηνία (YYYY-MM-DD) του παραστατικού που παίρνει τον αριθμό. Αν δοθεί, ο αριθμός
    δίνεται μόνο αν δεν υπάρχει ήδη αριθμός της σειράς με μεταγενέστερη ημερομηνία· αλλιώς
    `SeriesDateOutOfOrder` και ο μετρητής μένει όπως ήταν.

    `floor`: ο μεγαλύτερος αριθμός που ξέρει ο καλών ότι υπάρχει ήδη στη σειρά. Ο επόμενος αριθμός είναι
    πάντα μεγαλύτερος από αυτόν.
    """
    flt: dict = {"_id": counter_id}
    update: dict = {"$inc": {"seq": 1}}
    if date_iso:
        # Ο μετρητής από παλιά έκδοση δεν έχει ημερομηνία: δέχεται την πρώτη.
        flt["$or"] = [{"last_date": {"$exists": False}}, {"last_date": None}, {"last_date": {"$lte": date_iso}}]
        update["$set"] = {"last_date": date_iso}
    try:
        counter = await db.counters.find_one_and_update(flt, update, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        # Ο μετρητής υπάρχει αλλά το φίλτρο ημερομηνίας δεν ταίριαξε· το upsert προσπάθησε να ξαναφτιάξει το ίδιο _id.
        current = await db.counters.find_one({"_id": counter_id}, {"last_date": 1})
        raise SeriesDateOutOfOrder((current or {}).get("last_date"))
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
    # Ο μετρητής έμεινε πίσω από υπάρχοντες αριθμούς. Η μετακίνηση είναι υπό όρο (`seq <= floor`): αν άλλο
    # αίτημα τον πρόλαβε, δεν γράφουμε ξανά την ίδια τιμή — παίρνουμε νέο αριθμό. Αλλιώς δύο αιτήματα θα
    # έπαιρναν τον ίδιο αριθμό.
    while floor and seq <= floor:
        moved = await db.counters.find_one_and_update(
            {"_id": counter_id, "seq": {"$lte": floor}}, {"$set": {"seq": floor + 1}},
            return_document=ReturnDocument.AFTER,
        )
        if moved is None:
            moved = await db.counters.find_one_and_update(
                {"_id": counter_id}, {"$inc": {"seq": 1}}, return_document=ReturnDocument.AFTER,
            )
        seq = moved["seq"]
    return f"{prefix}-{year}-{seq:04d}"


async def reset_series_date(counter_id: str, *, expected: str | None, to: str | None) -> bool:
    """Διορθώνει την «τελευταία ημερομηνία» του μετρητή όταν δεν αντιστοιχεί σε κανένα υπαρκτό παραστατικό.

    Γίνεται μόνο αν ο μετρητής έχει ακόμη την τιμή `expected` (δεν πρόλαβε άλλο αίτημα να την αλλάξει).
    """
    flt = {"_id": counter_id, "last_date": expected}
    if to is None:
        result = await db.counters.update_one(flt, {"$unset": {"last_date": ""}})
    else:
        result = await db.counters.update_one(flt, {"$set": {"last_date": to}})
    return result.matched_count > 0
