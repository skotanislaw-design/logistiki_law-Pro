"""Φορολογικοί υπολογισμοί: ΦΠΑ τιμολογίων, μισθοδοσία, προθεσμίες.

Δύο συμβάσεις που αξίζει να διαβαστούν πριν αλλάξει κάτι εδώ:

1. **Χρήμα.** Κάθε ποσό περνά από ``Decimal`` και στρογγυλοποιείται στο λεπτό με
   ``ROUND_HALF_UP``. Η ``compute_line_values`` είναι η **μόνη** πηγή αλήθειας για την
   καθαρή αξία και τον ΦΠΑ κάθε γραμμής — από εκεί προκύπτουν το αποθηκευμένο
   τιμολόγιο, το PDF, η χρέωση Stripe και το XML προς την ΑΑΔΕ, ώστε να μη διαφέρουν
   ποτέ μεταξύ τους.

2. **Προθεσμίες.** ``vat_deadline_for_period_end`` απαντά «πότε λήγει η δήλωση αυτής
   της περιόδου»· ``next_vat_deadline`` απαντά «ποια είναι η επόμενη προθεσμία που
   εκκρεμεί σήμερα». Είναι διαφορετικά ερωτήματα και το μπέρδεμά τους έκρυβε την
   επικείμενη προθεσμία από το φορολογικό ημερολόγιο.
"""

import calendar
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ATHENS = ZoneInfo("Europe/Athens")


def athens_today() -> date:
    """Η σημερινή ημερομηνία στην Ελλάδα — όχι του server.

    Ο server τρέχει σε UTC· μεταξύ 00:00 και 03:00 ώρα Ελλάδας η `date.today()` δίνει ακόμη
    την προηγούμενη μέρα, οπότε ένα τιμολόγιο θα έφερε ημερομηνία χθες και η σειρά
    αρίθμησης θα άλλαζε έτος με καθυστέρηση. Κάθε ημερομηνία παραστατικού βγαίνει από εδώ.
    """
    return datetime.now(ATHENS).date()


def athens_date_of(stamp) -> date | None:
    """Η ημερομηνία, ώρα Ελλάδας, μιας χρονοσφραγίδας ISO — ή ``None`` αν το κείμενο δεν διαβάζεται.

    Οι χρονοσφραγίδες αποθηκεύονται σε UTC (``2026-12-31T22:30:00+00:00``)· είναι η 1η Ιανουαρίου στην Ελλάδα. Το
    ``stamp[:10]`` ή το ``fromisoformat(stamp).date()`` δίνουν την ημερομηνία UTC, δηλαδή τη χθεσινή για κάθε σφραγίδα
    που μπήκε μεταξύ 00:00 και 03:00 ώρα Ελλάδας: μια υπενθύμιση θα ξαναστελνόταν μία μέρα νωρίτερα, ένα πρόχειρο θα
    φαινόταν δημιουργημένο την προηγούμενη. Χωρίς ζώνη ώρας (παλιά δεδομένα) θεωρείται UTC — όχι η ζώνη του
    μηχανήματος, που αλλάζει από εγκατάσταση σε εγκατάσταση.
    """
    if not isinstance(stamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(stamp.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ATHENS).date()
    except (ValueError, OverflowError):      # «9999-12-31T23:59:59Z» ξεπερνά το έτος 9999 όταν μετατραπεί σε ώρα Ελλάδας
        return None


# --------------------------------------------------------------- χρήμα ---

_CENT = Decimal("0.01")


def is_finite_number(value) -> bool:
    """Πραγματικός αριθμός (όχι `bool`) που δεν είναι NaN ούτε άπειρο.

    Η `json` της Python δέχεται τα `NaN`, `Infinity` και `1e999`, και οι συγκρίσεις με `<`/`>` δίνουν για NaN πάντα
    `False` — άρα ένας έλεγχος ορίων του τύπου «αρνητικό ή μεγαλύτερο από το μέγιστο» δεν το πιάνει ποτέ.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# Ανώτατη αξία γραμμής τιμολογίου — ίδιο όριο με την κίνηση πελατολογικού (routes_trust.MAX_AMOUNT). Πάνω από αυτό είναι
# πάντα λάθος πληκτρολόγησης, και τα πολύ μεγάλα ποσά ξεπερνούν την ακρίβεια του υπολογισμού ΦΠΑ (σφάλμα 500).
MAX_LINE_VALUE = 1_000_000_000

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def valid_iso_date(value) -> bool:
    """Υπαρκτή ημερομηνία ακριβώς της μορφής ΕΕΕΕ-ΜΜ-ΗΗ — τίποτε άλλο.

    Η `date.fromisoformat` της Python 3.11 δέχεται και `20260612`, `2026-W24-5`, ενώ η `\\d` της `re` δέχεται ψηφία
    οποιασδήποτε γραφής (πλήρους πλάτους, αραβικά-ινδικά). Όλες οι ημερομηνίες της εφαρμογής συγκρίνονται ως κείμενα
    (`$gte`/`$lte` στις περιόδους ΦΠΑ, μήνας στο dashboard): το `20260612` ταξινομείται μετά από κάθε «2026-…», οπότε ένα
    τιμολόγιο που το φέρει δεν μπαίνει στην περίοδό του και δεν φαίνεται σε κανένα σφάλμα.
    """
    if not isinstance(value, str) or _ISO_DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def money_limit_error(value, label: str, *, negative_ok: bool = False) -> str | None:
    """Μήνυμα για ποσό που δεν είναι πεπερασμένο, είναι αρνητικό (αν δεν επιτρέπεται) ή ξεπερνά το `MAX_LINE_VALUE`.

    Το όριο δεν είναι αυθαίρετο: δύο πεπερασμένα `1e308` αθροίζονται σε `inf` και το dashboard απαντά 500 για ολόκληρο το
    γραφείο, ενώ ποσό πάνω από 1 δισ. € είναι πάντα λάθος πληκτρολόγησης.
    """
    if not is_finite_number(value):
        return f"Το πεδίο «{label}» πρέπει να είναι έγκυρος αριθμός."
    if value < 0 and not negative_ok:
        return f"Το πεδίο «{label}» δεν μπορεί να είναι αρνητικό."
    if abs(value) > MAX_LINE_VALUE:
        return f"Το πεδίο «{label}» είναι υπερβολικά μεγάλο — ελέγξτε το ποσό."
    return None

# Οι συντελεστές ΦΠΑ που δέχεται το myDATA (βλ. VAT_CATEGORY στο mydata_client).
VALID_VAT_RATES = (0, 6, 13, 24)


def _money(value) -> Decimal:
    """Στρογγυλοποίηση στο λεπτό, μισού προς τα πάνω (λογιστική σύμβαση)."""
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def round_half_up(value) -> float:
    """Στρογγυλοποίηση στο λεπτό, μισού προς τα πάνω, με βάση το δεκαδικό κείμενο της τιμής και όχι τη δυαδική της μορφή.

    Η `round()` των float δίνει `round(300.5 * 0.15, 2) == 45.07`, γιατί το γινόμενο είναι στην πραγματικότητα
    45,07499999… — ο λογιστής περιμένει 45,08 (45,075 → 45,08). Δέχεται και `Decimal` (ώστε το γινόμενο να
    υπολογιστεί ακριβώς πριν στρογγυλοποιηθεί). Μη πεπερασμένη τιμή επιστρέφεται όπως είναι: το λάθος ανήκει στην είσοδο.
    """
    try:
        return float(_money(value))
    except InvalidOperation:
        return float(value)


def _attr(item, name, default=None):
    """Οι γραμμές έρχονται άλλοτε ως Pydantic models και άλλοτε ως dicts."""
    if hasattr(item, name):
        return getattr(item, name)
    return item.get(name, default)


def compute_line_values(line_items) -> list[dict]:
    """Καθαρή αξία και ΦΠΑ ανά γραμμή, στρογγυλοποιημένα στο λεπτό.

    Η στρογγυλοποίηση γίνεται **ανά γραμμή** και τα σύνολα προκύπτουν ως άθροισμα
    στρογγυλοποιημένων τιμών — έτσι ακριβώς τα δέχεται και τα επαληθεύει το myDATA
    (άθροισμα ``vatAmount`` γραμμών = ``totalVatAmount``).
    """
    values = []
    for item in line_items or []:
        qty = Decimal(str(_attr(item, "quantity", 1) or 0))
        price = Decimal(str(_attr(item, "unit_price", 0) or 0))
        rate = int(_attr(item, "vat_rate", 24) or 0)
        net = _money(qty * price)
        values.append({"net": net, "vat": _money(net * Decimal(rate) / Decimal(100)), "rate": rate})
    return values


def calculate_invoice_totals(line_items: list) -> dict:
    """Σύνολα τιμολογίου. Επιστρέφει float για συμβατότητα με Mongo/JSON."""
    lines = compute_line_values(line_items)
    subtotal = sum((ln["net"] for ln in lines), Decimal("0"))
    breakdown: dict[str, Decimal] = {}
    for ln in lines:
        key = str(ln["rate"])
        breakdown[key] = breakdown.get(key, Decimal("0")) + ln["vat"]
    vat_total = sum(breakdown.values(), Decimal("0"))
    return {
        "subtotal": float(subtotal),
        "vat_amount": float(vat_total),
        "total": float(subtotal + vat_total),
        "vat_breakdown": {k: float(v) for k, v in breakdown.items()},
    }


# ------------------------------------------------------------------ ΑΦΜ ---


def afm_check_digit(prefix8: str) -> int:
    """Το ψηφίο ελέγχου για τα οκτώ πρώτα ψηφία ενός ΑΦΜ.

    Στάθμιση με δυνάμεις του 2 από αριστερά (2^8 … 2^1), υπόλοιπο modulo 11, και
    modulo 10 ώστε το υπόλοιπο 10 να αποδίδει ψηφίο 0.
    """
    total = sum(int(prefix8[i]) * 2 ** (8 - i) for i in range(8))
    return (total % 11) % 10


def _ascii_digits(text: str) -> bool:
    """Μόνο τα ψηφία 0–9. Η `str.isdigit()` δέχεται και «²», αραβικά-ινδικά ή πλήρους πλάτους ψηφία: το «12345678²»
    έσκαγε με ValueError στο `int()` (σφάλμα 500) και τα αραβικά περνούσαν ως ΑΦΜ, για να τα απορρίψει αργότερα η ΑΑΔΕ."""
    return text.isascii() and text.isdigit()


def is_valid_afm(afm: str) -> bool:
    """Έλεγχος ελληνικού ΑΦΜ: εννέα ψηφία και σωστό ψηφίο ελέγχου.

    Πιάνει το συνηθισμένο λάθος πληκτρολόγησης — ένα ψηφίο αλλαγμένο ή δύο
    αντιστραμμένα — τη στιγμή της καταχώρισης, αντί να το ανακαλύψει η ΑΑΔΕ κατά τη
    διαβίβαση, όταν το τιμολόγιο έχει ήδη εκδοθεί.

    Το ΑΦΜ μένει **συμβολοσειρά**: τα αρχικά μηδενικά είναι έγκυρα και ο μετασχηματισμός
    σε ακέραιο θα τα κατέστρεφε. Το «000000000» ικανοποιεί τον αλγόριθμο αλλά δεν είναι
    ΑΦΜ, και απορρίπτεται ρητά.
    """
    afm = (afm or "").strip()
    if len(afm) != 9 or not _ascii_digits(afm) or afm == "000000000":
        return False
    return afm_check_digit(afm[:8]) == int(afm[8])


def afm_error(afm: str, label: str = "ΑΦΜ") -> str:
    """Μήνυμα που λέει τι ακριβώς φταίει — όχι απλώς «μη έγκυρο»."""
    value = (afm or "").strip()
    if not _ascii_digits(value):
        return f"Το {label} «{value}» περιέχει μη αριθμητικούς χαρακτήρες."
    if len(value) != 9:
        return (f"Το {label} «{value}» έχει {len(value)} ψηφία — το ελληνικό ΑΦΜ έχει 9. "
                "Ελέγξτε μήπως λείπει ή περισσεύει ψηφίο.")
    return (f"Το {label} «{value}» έχει λάθος ψηφίο ελέγχου. "
            "Συνήθως πρόκειται για ένα ψηφίο αλλαγμένο ή δύο αντιστραμμένα.")


# ---------------------------------------------------------- μισθοδοσία ---

# ΠΡΟΣΟΧΗ: κλίμακα και ποσοστά ισχύος 2025-2026. Αναπροσαρμόζονται με νόμο /
# υπουργική απόφαση — ελέγξτε τα στην αρχή κάθε έτους.
TAX_BRACKETS = [
    (10000, 0.09),
    (20000, 0.22),
    (30000, 0.28),
    (40000, 0.36),
    (float("inf"), 0.44),
]

TAX_CREDIT_BY_DEPENDENTS = {0: 777, 1: 810, 2: 900, 3: 1120}

EMPLOYEE_EFKA_RATE = 0.1387
EMPLOYER_EFKA_RATE = 0.2229

# Ανώτατο όριο ασφαλιστέων αποδοχών (πλαφόν ΕΦΚΑ) ανά μήνα. Πάνω από αυτό δεν
# υπολογίζονται εισφορές. Αναπροσαρμόζεται ετησίως — τιμή 2025: 7.572,62 €.
EFKA_MONTHLY_CEILING = float(os.environ.get("EFKA_MONTHLY_CEILING", "7572.62"))


def _tax_credit(dependents: int, taxable_annual: float) -> float:
    """Μείωση φόρου άρθρου 16 ΚΦΕ: 20 € ανά 1.000 € εισοδήματος πάνω από 12.000 €."""
    base = TAX_CREDIT_BY_DEPENDENTS.get(dependents, 1340 if dependents >= 4 else 777)
    if taxable_annual <= 12000:
        return base
    reduction = ((taxable_annual - 12000) // 1000) * 20
    return max(0, base - reduction)


def calculate_progressive_tax(annual_income: float) -> float:
    tax = 0.0
    lower = 0.0
    for upper, rate in TAX_BRACKETS:
        if annual_income <= lower:
            break
        taxable_in_bracket = min(annual_income, upper) - lower
        tax += taxable_in_bracket * rate
        lower = upper
    return tax


def calculate_payroll(gross_salary: float, dependents: int = 0) -> dict:
    # Οι εισφορές υπολογίζονται μέχρι το πλαφόν· ο φόρος επί όλων των αποδοχών.
    contributory_base = min(float(gross_salary), EFKA_MONTHLY_CEILING)
    employee_efka = round(contributory_base * EMPLOYEE_EFKA_RATE, 2)
    employer_efka = round(contributory_base * EMPLOYER_EFKA_RATE, 2)
    taxable_income = round(gross_salary - employee_efka, 2)
    annual_taxable = taxable_income * 14
    annual_tax = calculate_progressive_tax(annual_taxable)
    credit = _tax_credit(dependents, annual_taxable)
    annual_tax_after_credit = max(0, annual_tax - credit)
    income_tax = round(annual_tax_after_credit / 14, 2)
    net_salary = round(gross_salary - employee_efka - income_tax, 2)
    employer_total_cost = round(gross_salary + employer_efka, 2)
    return {
        "gross_salary": gross_salary,
        "employee_efka": employee_efka,
        "employer_efka": employer_efka,
        "taxable_income": taxable_income,
        "income_tax": income_tax,
        "tax_credit": round(credit / 14, 2),
        "net_salary": net_salary,
        "employer_total_cost": employer_total_cost,
    }


# ---------------------------------------------------------- προθεσμίες ---


def _last_business_day(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_months(d: date, months: int) -> date:
    """Μετακινεί κατά ``months`` και επιστρέφει την τελευταία ημέρα του μήνα."""
    total = (d.year * 12 + d.month - 1) + months
    return _end_of_month(total // 12, total % 12 + 1)


def vat_period_end(day: date, vat_period: str = "monthly") -> date:
    """Η λήξη της περιόδου ΦΠΑ που περιέχει την ``day``."""
    if vat_period == "quarterly":
        return _end_of_month(day.year, ((day.month - 1) // 3) * 3 + 3)
    return _end_of_month(day.year, day.month)


def vat_deadline_for_period_end(period_end: date, vat_period: str = "monthly") -> date:
    """Προθεσμία υποβολής & πληρωμής για την περίοδο που έληξε στο ``period_end``.

    Τελευταία εργάσιμη ημέρα του επόμενου μήνα από τη λήξη της περιόδου — ίδιος
    κανόνας για μηνιαία και τριμηνιαία δήλωση.
    """
    nxt = _shift_months(period_end, 1)
    return _last_business_day(nxt.year, nxt.month)


def next_vat_deadline(today: date, vat_period: str = "monthly") -> date:
    """Η επόμενη προθεσμία ΦΠΑ που **δεν έχει ακόμη παρέλθει**.

    Δεν είναι η προθεσμία της περιόδου που διανύουμε: στις 10/09 με μηνιαίο ΦΠΑ
    εκκρεμεί η δήλωση **Αυγούστου**, με προθεσμία την τελευταία εργάσιμη του
    Σεπτεμβρίου. Ο παλιός υπολογισμός επέστρεφε την τελευταία εργάσιμη του
    Οκτωβρίου και έκρυβε εντελώς την επικείμενη προθεσμία.
    """
    step = 3 if vat_period == "quarterly" else 1
    # Η πρώτη περίοδος που μπορεί να εκκρεμεί είναι η προηγούμενη από την τρέχουσα.
    period_end = _shift_months(vat_period_end(today, vat_period), -step)
    for _ in range(4):
        due = vat_deadline_for_period_end(period_end, vat_period)
        if due >= today:
            return due
        period_end = _shift_months(period_end, step)
    return vat_deadline_for_period_end(period_end, vat_period)


def next_efka_deadline(today: date) -> date:
    """Τελευταία εργάσιμη του μήνα· αν έχει ήδη παρέλθει, του επόμενου."""
    due = _last_business_day(today.year, today.month)
    if due >= today:
        return due
    nxt = _shift_months(today.replace(day=1), 1)
    return _last_business_day(nxt.year, nxt.month)


def next_withholding_deadline(today: date) -> date:
    """Απόδοση ΦΜΥ: τελευταία εργάσιμη του **δεύτερου** μήνα από την παρακράτηση
    (άρθρο 60 ΚΦΕ) — διακριτή προθεσμία από τις εισφορές ΕΦΚΑ."""
    due_month = _shift_months(today.replace(day=1), 2)
    due = _last_business_day(due_month.year, due_month.month)
    if due >= today:
        return due
    nxt = _shift_months(due_month, 1)
    return _last_business_day(nxt.year, nxt.month)


def generate_tax_calendar(today: date, vat_period: str = "monthly") -> list:
    vat_deadline = next_vat_deadline(today, vat_period)
    efka_deadline = next_efka_deadline(today)
    items = [
        {
            "title": "Υποβολή & Πληρωμή ΦΠΑ",
            "description": f"Δήλωση ΦΠΑ ({'μηνιαία' if vat_period == 'monthly' else 'τριμηνιαία'}) στο myDATA / ΑΑΔΕ",
            "due_date": vat_deadline.isoformat(),
            "category": "vat",
        },
        {
            "title": "Πληρωμή Εισφορών ΕΦΚΑ",
            "description": "Ασφαλιστικές εισφορές μισθωτών/απασχολούμενων προηγούμενου μήνα",
            "due_date": efka_deadline.isoformat(),
            "category": "efka",
        },
        {
            "title": "Παρακρατούμενος Φόρος Μισθωτών Υπηρεσιών",
            "description": "Απόδοση παρακρατηθέντος φόρου μισθοδοσίας",
            "due_date": next_withholding_deadline(today).isoformat(),
            "category": "income_tax",
        },
    ]
    return sorted(items, key=lambda x: x["due_date"])
