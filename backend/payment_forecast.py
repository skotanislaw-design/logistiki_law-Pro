"""Πρόβλεψη είσπραξης από το πραγματικό ιστορικό πληρωμών του γραφείου.

Η παλιά εκτίμηση «όλοι πληρώνουν σε 30 ημέρες» αγνοούσε ότι ο κάθε πελάτης έχει δικό του
ρυθμό. Εδώ η καθυστέρηση κάθε πελάτη προκύπτει από τα δικά του εξοφλημένα τιμολόγια
(διάμεσος, ανθεκτικός σε έκτακτες τιμές), με υποχώρηση στη διάμεσο του γραφείου και τέλος
στις 30 ημέρες όταν δεν υπάρχει αρκετό δείγμα. Συναρτήσεις καθαρές, χωρίς βάση.
"""
from datetime import date, timedelta
from statistics import median

DEFAULT_DELAY_DAYS = 30
MIN_CLIENT_SAMPLES = 3
MIN_FIRM_SAMPLES = 5
LATE_RISK_DAYS = 14
MAX_DELAY_DAYS = 365


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def payment_delays(paid_invoices: list[dict]) -> tuple[dict[str, list[int]], list[int]]:
    """Ημέρες από την έκδοση ως την εξόφληση, ανά πελάτη και συνολικά."""
    by_client: dict[str, list[int]] = {}
    firm: list[int] = []
    for inv in paid_invoices:
        issued, paid = _day(inv.get("date")), _day(inv.get("paid_at"))
        if not issued or not paid:
            continue
        delay = (paid - issued).days
        if not 0 <= delay <= MAX_DELAY_DAYS:
            continue
        firm.append(delay)
        by_client.setdefault(str(inv.get("client_id") or ""), []).append(delay)
    return by_client, firm


def typical_delay(client_id: str, by_client: dict[str, list[int]], firm: list[int]) -> tuple[int, str]:
    """(ημέρες, βάση): ``client`` | ``firm`` | ``default``."""
    own = by_client.get(str(client_id or ""), [])
    if len(own) >= MIN_CLIENT_SAMPLES:
        return round(median(own)), "client"
    if len(firm) >= MIN_FIRM_SAMPLES:
        return round(median(firm)), "firm"
    return DEFAULT_DELAY_DAYS, "default"


def forecast_invoice(invoice: dict, by_client: dict[str, list[int]], firm: list[int], today: date) -> dict | None:
    """Αναμενόμενη ημερομηνία είσπραξης, βεβαιότητα και καθυστέρηση σε σχέση με το συνηθισμένο."""
    issued = _day(invoice.get("date"))
    if not issued:
        return None
    delay, basis = typical_delay(invoice.get("client_id"), by_client, firm)
    expected = issued + timedelta(days=delay)
    days_late = max(0, (today - expected).days)
    if expected < today:
        # Ήδη πιο αργά από το συνηθισμένο: δεν υπάρχει ιστορικό που να λέει πότε θα πληρώσει.
        expected = today + timedelta(days=15)
        confidence = "low"
    else:
        confidence = "high" if basis == "client" else "medium"
    return {
        "expected_date": expected, "confidence": confidence, "basis": basis,
        "typical_delay_days": delay, "days_late": days_late,
        "at_risk": days_late >= LATE_RISK_DAYS,
    }
