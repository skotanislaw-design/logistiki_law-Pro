"""Αριθμοδότηση εγγράφων ανά γραφείο και έτος, με ατομικό μετρητή.

Η προηγούμενη λύση στα γραμμάτια, τα παράβολα και τις υποθέσεις ήταν «πλήθος εγγράφων + 1».
Αρκεί να διαγραφεί ένα ενδιάμεσο έγγραφο για να δοθεί ξανά ο αριθμός του τελευταίου: με
τρία γραμμάτια (0001–0003) και διαγραφή του δεύτερου, το επόμενο έπαιρνε το 0003, δηλαδή
δύο έγγραφα με τον ίδιο αριθμό — και η αναζήτηση «ανά αριθμό» έβρισκε το λάθος.

Ο μετρητής (`counters`) δεν γυρίζει ποτέ πίσω, ό,τι κι αν διαγραφεί.

**Μία μόνο εγγραφή αποφασίζει τα πάντα.** Κάθε παραχώρηση αριθμού είναι ένα *compare-and-set* πάνω στο έγγραφο του
μετρητή: διαβάζεται η κατάσταση (`seq`, `last_date`), υπολογίζεται ο επόμενος αριθμός, και γράφεται με φίλτρο που
απαιτεί η κατάσταση να είναι ακόμη αυτή που διαβάστηκε. Αν άλλο αίτημα πρόλαβε, η εγγραφή δεν ταιριάζει, δεν
καταναλώνεται κανένας αριθμός και η διαδικασία ξαναρχίζει από την ανάγνωση. Έτσι —και μόνο έτσι— μπορούν να ισχύουν
μαζί, χωρίς παράθυρο ανάμεσα σε δύο εγγραφές:

* δεν υπάρχουν δύο ίδιοι αριθμοί·
* ένας μεταγενέστερος αριθμός δεν παίρνει ποτέ προγενέστερη ημερομηνία (`last_date`)· αν η ημερομηνία είναι
  παλαιότερη από την τελευταία που δόθηκε, ο μετρητής αρνείται (`SeriesDateOutOfOrder`) χωρίς να αλλάξει·
* ο μετρητής δεν μένει ποτέ πίσω από υπάρχοντες αριθμούς (`floor`): μετά από επαναφορά αντιγράφου ή χειροκίνητη
  διόρθωση μπορεί να δείχνει μικρότερο αριθμό από τον τελευταίο που υπάρχει· η μετακίνηση πάνω από το `floor`
  γίνεται στην ΙΔΙΑ εγγραφή με την παραχώρηση·
* η πρώτη χρήση ενός μετρητή (δημιουργία του εγγράφου) ξεκινά πάνω από όσους αριθμούς υπάρχουν ήδη στη συλλογή
  (έγγραφα από την εποχή του «πλήθος + 1»).

Οι προηγούμενες εκδοχές έκαναν τα τρία τελευταία με ξεχωριστές εγγραφές (ένα `$inc` και, αν χρειαζόταν, ένα δεύτερο
`$set`)· ανάμεσά τους μπορούσε να παρεμβληθεί άλλο αίτημα και να βγουν αριθμοί εκτός χρονολογικής σειράς ή να
«χαθεί» ο αριθμός 0001 χωρίς ίχνος.
"""
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from db import db

CAS_ATTEMPTS = 50      # κάθε αποτυχία σημαίνει ότι άλλο αίτημα πήρε αριθμό — η πρόοδος είναι εγγυημένη


class SeriesDateOutOfOrder(Exception):
    """Η ημερομηνία του παραστατικού είναι προγενέστερη της τελευταίας που δόθηκε στη σειρά."""

    def __init__(self, last_date: str | None, seq: int | None = None, allocated_at: str | None = None):
        super().__init__(f"Η σειρά έχει ήδη δώσει αριθμό με ημερομηνία {last_date}")
        self.last_date = last_date
        self.seq = seq                      # ο μετρητής τη στιγμή της άρνησης
        self.allocated_at = allocated_at    # πότε δόθηκε ο τελευταίος αριθμός


class SeriesBusy(Exception):
    """Ο μετρητής ήταν συνεχώς απασχολημένος από άλλα αιτήματα· δεν δόθηκε αριθμός (και δεν χάθηκε κανένας)."""


# Το μήνυμα προς τον χρήστη όταν η αρίθμηση υποθέσεων, γραμμάτιων και παράβολων (και κάθε σειρά που δεν έχει δικό της
# χειρισμό) βρίσκει τον μετρητή απασχολημένο. Η έκδοση τιμολογίου έχει δικό της κείμενο (`SERIES_BUSY_MESSAGE`).
BUSY_MESSAGE = "Η αρίθμηση ενημερώθηκε ταυτόχρονα από άλλο αίτημα. Δοκιμάστε ξανά σε λίγο."


def format_number(prefix: str, year: int, seq: int) -> str:
    return f"{prefix}-{year}-{seq:04d}"


MAX_SEQ = 999_999_999      # πολύ πάνω από κάθε πραγματική σειρά, πολύ κάτω από το όριο των 8 byte της Mongo


def as_int(value) -> int:
    """Ο αριθμός του μετρητή ως ακέραιος. Ό,τι δεν είναι αριθμός (χειροκίνητη επέμβαση) μετράει ως 0· ο έλεγχος
    ύπαρξης αριθμών (`floor`, μοναδικό ευρετήριο) προστατεύει τη σειρά και η επόμενη εγγραφή τον διορθώνει.

    Το ίδιο ισχύει για αρνητικό ή υπέρογκο αριθμό (π.χ. `1e30` ως double): ο επόμενος αριθμός θα ήταν πάνω από 8 byte
    και η εγγραφή του θα έσκαγε με OverflowError σε κάθε έκδοση παραστατικού, για πάντα."""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if 0 <= number <= MAX_SEQ else 0


async def _scan_existing_max(collection, firm_id: str, prefix: str, year: int) -> int:
    """Ο μεγαλύτερος αριθμός της σειράς που υπάρχει ήδη στη συλλογή (για την πρώτη χρήση του μετρητή)."""
    existing_max = 0
    async for d in collection.find({"firm_id": firm_id, "number": {"$regex": f"^{prefix}-{year}-"}}, {"number": 1}):
        tail = str(d.get("number") or "").rsplit("-", 1)[-1]
        # Μόνο αριθμός σειράς (1–9 ψηφία): ένας αριθμός 11 ψηφίων από εισαγωγή δεν γίνεται αφετηρία του μετρητή.
        if tail.isascii() and tail.isdigit() and len(tail) <= len(str(MAX_SEQ)):
            existing_max = max(existing_max, int(tail))
    return existing_max


async def next_in_series(collection, counter_id: str, firm_id: str, prefix: str, year: int, *,
                         date_iso: str | None = None, floor: int = 0) -> str:
    """Επόμενος αριθμός της σειράς `prefix-year-NNNN` για το γραφείο.

    `date_iso`: η ημερομηνία (YYYY-MM-DD) του παραστατικού που παίρνει τον αριθμό. Αν δοθεί, ο αριθμός
    δίνεται μόνο αν δεν υπάρχει ήδη αριθμός της σειράς με μεταγενέστερη ημερομηνία· αλλιώς
    `SeriesDateOutOfOrder` και ο μετρητής μένει όπως ήταν.

    `floor`: ο μεγαλύτερος αριθμός που ξέρει ο καλών ότι υπάρχει ήδη στη σειρά. Ο επόμενος αριθμός είναι
    πάντα μεγαλύτερος από αυτόν.
    """
    for _ in range(CAS_ATTEMPTS):
        current = await db.counters.find_one({"_id": counter_id})
        now = datetime.now(timezone.utc).isoformat()

        if current is None:
            # Πρώτη χρήση: υπάρχοντα έγγραφα (από την εποχή του «πλήθος + 1») μπορεί να έχουν ήδη αριθμούς.
            target = max(floor, await _scan_existing_max(collection, firm_id, prefix, year)) + 1
            created = {"_id": counter_id, "seq": target, "last_alloc_at": now}
            if date_iso:
                created["last_date"] = date_iso
            try:
                await db.counters.insert_one(created)
            except DuplicateKeyError:
                continue                        # τον δημιούργησε άλλο αίτημα: διάβασέ τον και συνέχισε από εκεί
            return format_number(prefix, year, target)

        last_date = current.get("last_date")
        if date_iso and isinstance(last_date, str) and last_date > date_iso:
            raise SeriesDateOutOfOrder(last_date, as_int(current.get("seq")), current.get("last_alloc_at"))

        target = max(as_int(current.get("seq")), floor) + 1
        change: dict = {"seq": target, "last_alloc_at": now}
        if date_iso:
            change["last_date"] = date_iso
        # Compare-and-set: ισχύει μόνο αν ούτε ο αριθμός ούτε η ημερομηνία άλλαξαν από τότε που διαβάστηκαν.
        # (`None` ταιριάζει και με απόντα πεδίο: μετρητές από παλιότερες εκδόσεις δεν έχουν `last_date`.)
        won = await db.counters.update_one(
            {"_id": counter_id, "seq": current.get("seq"), "last_date": last_date}, {"$set": change})
        if won.matched_count:
            return format_number(prefix, year, target)
    raise SeriesBusy(f"Δεν δόθηκε αριθμός στη σειρά {prefix}-{year}: ο μετρητής ήταν συνεχώς απασχολημένος")


async def reset_series_date(counter_id: str, *, expected: str | None, to: str | None, seq: int | None = None) -> bool:
    """Διορθώνει την «τελευταία ημερομηνία» του μετρητή όταν δεν αντιστοιχεί σε κανένα υπαρκτό παραστατικό.

    Γίνεται μόνο αν ο μετρητής έχει ακόμη την τιμή `expected` (δεν πρόλαβε άλλο αίτημα να την αλλάξει) και, αν δοθεί
    `seq`, μόνο αν δεν έχει δοθεί κανένας αριθμός από τότε που διαβάστηκε ο μετρητής.
    """
    flt: dict = {"_id": counter_id, "last_date": expected}
    if seq is not None:
        flt["seq"] = seq
    if to is None:
        result = await db.counters.update_one(flt, {"$unset": {"last_date": ""}})
    else:
        result = await db.counters.update_one(flt, {"$set": {"last_date": to}})
    return result.matched_count > 0
