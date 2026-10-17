"""Εκπτωσιμότητα του ΦΠΑ εισροών (Φάση 1 του «ψηφιακού λογιστή»).

Μέχρι τώρα κάθε έξοδο εκπτωνόταν στο 100% του ΦΠΑ του. Στην πράξη ένα μέρος του ΦΠΑ ορισμένων δαπανών δεν εκπίπτει
(π.χ. ό,τι σχετίζεται με εστίαση, διασκέδαση, ορισμένα οχήματα, μικτή χρήση) — **ποιο και πόσο το ορίζει ο λογιστής του
γραφείου, όχι ο κώδικας**. Γι' αυτό εδώ δεν υπάρχει καμία προεπιλεγμένη λίστα κατηγοριών: οι κανόνες είναι δεδομένα
(`Settings.vat_deductibility_rules`, {κατηγορία: ποσοστό 0–100}) και, ώσπου να οριστούν, ισχύει ό,τι ίσχυε πάντα (100%).

Ποσοστό ανά έξοδο, με σειρά προτεραιότητας:
  1. ρητό `vat_deductible_pct` στο ίδιο το έξοδο (π.χ. μικτή χρήση ενός συγκεκριμένου παραστατικού),
  2. κανόνας της κατηγορίας του,
  3. 100% — **η σιωπηλή υπόθεση**. Δεν κρύβεται: κάθε έξοδο με ΦΠΑ που κατέληξε εδώ μετριέται ως «χωρίς κανόνα»
     (`unruled`), ώστε η δήλωση και ο πίνακας περιόδων να δείχνουν πόσο ΦΠΑ στηρίζεται σε υπόθεση και όχι σε απόφαση.

Αναλλοίωτο που ισχύει σε κάθε άθροισμα: `net_cost + input_vat == gross` — το ΦΠΑ που δεν εκπίπτει δεν εξαφανίζεται, γίνεται
κόστος της δαπάνης. Και: χωρίς κανόνες και χωρίς ρητά ποσοστά, τα αποτελέσματα είναι όσα έβγαζε το προηγούμενο άθροισμα
`Σ vat_amount` (το ποσοστό 100% δεν στρογγυλοποιείται ανά έξοδο). Και τα δύο ισχύουν για ποσά με **έως δύο δεκαδικά**, που
είναι ό,τι αποθηκεύει πλέον το API (στρογγυλοποιεί στο λεπτό). Παλαιότερες ή εισαγμένες εγγραφές με περισσότερα ψηφία μπορεί
να διαφέρουν κατά ένα λεπτό: το άθροισμα στρογγυλοποιείται μισό-προς-τα-πάνω στο Decimal, όχι με τη δυαδική `round`.
Ποσά πάνω από το `MAX_LINE_VALUE` (μόνο από διαδρομές χωρίς έλεγχο, π.χ. OCR) μετρούν ως 0, όπως κάθε κατεστραμμένο ποσό.

Το module δεν εξαρτάται από FastAPI/βάση: δέχεται λεξικά εγγράφων και το λεξικό ρυθμίσεων.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from tax_utils import MAX_LINE_VALUE, is_finite_number

CENT = Decimal("0.01")
MAX_RULES = 100                  # πόσες κατηγορίες μπορεί να έχουν κανόνα (οι ενσωματωμένες είναι 11)
MAX_CATEGORY_LENGTH = 80
UNSAFE_KEY_CHARS = (".", "$", "\x00")

SOURCE_EXPENSE = "expense"           # ρητό ποσοστό στο ίδιο το έξοδο
SOURCE_RULE = "category_rule"        # κανόνας κατηγορίας από τις Ρυθμίσεις
SOURCE_DEFAULT = "default"           # δεν υπάρχει απόφαση: 100% από υπόθεση

NO_CATEGORY_LABEL = "Χωρίς κατηγορία"


def _num(value) -> float:
    """Πεπερασμένος αριθμός εντός του ορίου ποσών της εφαρμογής, αλλιώς 0 — ένα κατεστραμμένο πεδίο δεν πρέπει να ρίχνει
    ολόκληρο το άθροισμα. Το όριο (`MAX_LINE_VALUE`) είναι το ίδιο που εφαρμόζει το API στα νέα έξοδα· ποσό πάνω από αυτό
    μπορεί να έρθει μόνο από διαδρομή που δεν το ελέγχει (OCR email, myDATA) και, πολύ μεγάλο (>~1e26), έσπαγε τη
    στρογγυλοποίηση του Decimal και μαζί με αυτήν το dashboard, το Φ2 και την πρόβλεψη ταμείου του γραφείου."""
    return float(value) if is_finite_number(value) and abs(value) <= MAX_LINE_VALUE else 0.0


def _dec(value: float) -> Decimal:
    # Μέσω repr (όχι απευθείας από το float): το 0.1 μένει 0.1 και όχι 0.1000000000000000055…
    return Decimal(repr(float(value)))


def _clamp_pct(value) -> float | None:
    if not is_finite_number(value):
        return None
    return float(min(max(value, 0.0), 100.0))


def category_rule(category, settings: dict | None) -> float | None:
    """Το ποσοστό του κανόνα της κατηγορίας, ή `None` αν δεν υπάρχει (έγκυρος) κανόνας."""
    rules = (settings or {}).get("vat_deductibility_rules")
    if not isinstance(rules, dict):
        return None
    return _clamp_pct(rules.get(category))


def effective_pct(expense: dict, settings: dict | None) -> tuple[float, str]:
    """(ποσοστό 0–100, πηγή του ποσοστού) για ένα έξοδο."""
    explicit = _clamp_pct(expense.get("vat_deductible_pct"))
    if explicit is not None:
        return explicit, SOURCE_EXPENSE
    rule = category_rule(expense.get("category"), settings)
    if rule is not None:
        return rule, SOURCE_RULE
    return 100.0, SOURCE_DEFAULT


def deductible_vat(expense: dict, settings: dict | None) -> float:
    """Το μέρος του ΦΠΑ του εξόδου που εκπίπτει. Έχει το πρόσημο του `vat_amount` (πιστωτικό προμηθευτή → αρνητικό).

    Στο 100% επιστρέφεται το `vat_amount` ως έχει, χωρίς στρογγυλοποίηση: έτσι, όσο δεν υπάρχουν κανόνες, τίποτε δεν
    αλλάζει ούτε στο τελευταίο δεκαδικό. Σε μερική έκπτωση το εκπιπτόμενο στρογγυλοποιείται στο λεπτό (μισό → προς τα
    πάνω σε απόλυτη τιμή, συμμετρικά για τα πιστωτικά) και το υπόλοιπο είναι ακριβώς η διαφορά.
    """
    vat = _num(expense.get("vat_amount"))
    pct, _ = effective_pct(expense, settings)
    if pct >= 100.0:
        return vat
    return float((_dec(vat) * _dec(pct) / 100).quantize(CENT, rounding=ROUND_HALF_UP))


def summarize_expenses(expenses: list[dict], settings: dict | None) -> dict:
    """Συγκεντρωτικά ενός συνόλου εξόδων, με την εκπτωσιμότητα εφαρμοσμένη.

    * `gross` — Σ ποσών (με ΦΠΑ).
    * `input_vat` — το ΦΠΑ εισροών που εκπίπτει (ό,τι μπαίνει στη δήλωση).
    * `input_vat_nondeductible` — το ΦΠΑ που δεν εκπίπτει (επιβαρύνει το κόστος).
    * `net_cost` — το κόστος των εξόδων: `gross − input_vat`. Ισούται με το παλιό «Σ (ποσό − ΦΠΑ)» όσο δεν υπάρχουν κανόνες.
    * `unruled_vat` — πόσο από το `input_vat` εκπίπτεται πλήρως ΜΟΝΟ επειδή δεν υπάρχει κανόνας/ρητό ποσοστό.
    * `unruled` — το ίδιο ανά κατηγορία: [{category, count, vat_amount}], φθίνουσα ως προς το ΦΠΑ.
    Όλα τα ποσά στρογγυλοποιημένα στο λεπτό.
    """
    gross = Decimal(0)
    deductible = Decimal(0)
    non_deductible = Decimal(0)
    unruled_vat = Decimal(0)
    by_category: dict[str, dict] = {}
    for expense in expenses:
        amount = _num(expense.get("amount"))
        vat = _num(expense.get("vat_amount"))
        deducted = deductible_vat(expense, settings)
        gross += _dec(amount)
        deductible += _dec(deducted)
        non_deductible += _dec(vat) - _dec(deducted)
        _, source = effective_pct(expense, settings)
        if source == SOURCE_DEFAULT and vat != 0:
            unruled_vat += _dec(vat)
            label = expense.get("category") or NO_CATEGORY_LABEL
            bucket = by_category.setdefault(label, {"category": label, "count": 0, "vat_amount": Decimal(0)})
            bucket["count"] += 1
            bucket["vat_amount"] += _dec(vat)

    def money(value: Decimal) -> float:
        return float(value.quantize(CENT, rounding=ROUND_HALF_UP))

    unruled = sorted(
        ({"category": b["category"], "count": b["count"], "vat_amount": money(b["vat_amount"])} for b in by_category.values()),
        key=lambda b: (-abs(b["vat_amount"]), b["category"]),
    )
    return {
        "gross": money(gross),
        "input_vat": money(deductible),
        "input_vat_nondeductible": money(non_deductible),
        "net_cost": money(gross - deductible),
        "unruled_vat": money(unruled_vat),
        "unruled": unruled,
    }


class RulesError(ValueError):
    """Ο πίνακας κανόνων που υποβλήθηκε δεν είναι έγκυρος (το μήνυμα είναι έτοιμο για τον χρήστη)."""


def normalize_rules(raw) -> dict[str, float]:
    """Ελέγχει και ομαλοποιεί τον πίνακα {κατηγορία: ποσοστό εκπτωσιμότητας 0–100}.

    Επιστρέφει αυτό που θα αποθηκευτεί (κλειδιά χωρίς περιττά κενά, τιμές σε float) — «αποθηκεύεται όπως ελέγχθηκε».
    Το PATCH των ρυθμίσεων δέχεται ελεύθερο JSON, οπότε κάθε λάθος σχήμα δίνει `RulesError` και όχι 500. Η τιμή 100 είναι
    έγκυρος κανόνας («επιβεβαιώθηκε ότι εκπίπτει πλήρως») και διαφέρει από την απουσία κανόνα.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RulesError("Ο πίνακας εκπτωσιμότητας ΦΠΑ πρέπει να είναι λίστα κατηγοριών με ποσοστά.")
    if len(raw) > MAX_RULES:
        raise RulesError(f"Ο πίνακας εκπτωσιμότητας ΦΠΑ δέχεται έως {MAX_RULES} κατηγορίες.")
    rules: dict[str, float] = {}
    for key, value in raw.items():
        category = key.strip() if isinstance(key, str) else ""
        if not category or len(category) > MAX_CATEGORY_LENGTH:
            raise RulesError(f"Μη έγκυρη κατηγορία στον πίνακα εκπτωσιμότητας ΦΠΑ (έως {MAX_CATEGORY_LENGTH} χαρακτήρες, όχι κενή).")
        if any(ch in category for ch in UNSAFE_KEY_CHARS):
            # Οι κατηγορίες αποθηκεύονται ως ονόματα πεδίων της Mongo, όπου η τελεία και το «$» έχουν ειδική σημασία (σε
            # παλαιότερες εκδόσεις απορρίπτονται, αλλού δεν μπορεί να ενημερωθεί μεμονωμένο πεδίο).
            raise RulesError(f"Η κατηγορία «{category}» δεν μπορεί να περιέχει τελεία ή το σύμβολο «$».")
        if category in rules:
            raise RulesError(f"Η κατηγορία «{category}» εμφανίζεται δύο φορές στον πίνακα εκπτωσιμότητας ΦΠΑ.")
        if isinstance(value, str):
            try:
                value = float(value.strip().replace(",", "."))
            except ValueError:
                value = None
        if not is_finite_number(value) or not (0 <= value <= 100):
            raise RulesError(f"Το ποσοστό εκπτωσιμότητας της κατηγορίας «{category}» πρέπει να είναι αριθμός από 0 έως 100.")
        rules[category] = float(value)
    return rules
