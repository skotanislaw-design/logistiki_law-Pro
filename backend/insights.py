"""Προληπτικά αναλυτικά: σημάδια που φαίνονται στα δεδομένα πριν γίνουν πρόβλημα.

Καθαρές συναρτήσεις (χωρίς βάση) — το job και το endpoint τις τροφοδοτούν με τα έγγραφα.
"""
from collections import defaultdict
from datetime import date

SPIKE_FACTOR = 2.0
SPIKE_MIN_DELTA = 100.0
CONCENTRATION_SHARE = 0.4
CONCENTRATION_MIN_REVENUE = 1000.0


def _month(value) -> str | None:
    text = str(value or "")[:7]
    return text if len(text) == 7 and text[4] == "-" else None


def _shift(month: str, delta: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def expense_spikes(expenses: list[dict], today: date) -> list[dict]:
    """Κατηγορίες με δαπάνη τρέχοντος μήνα ≥2× του μέσου όρου των 3 προηγούμενων μηνών."""
    current = today.strftime("%Y-%m")
    history = {_shift(current, -i) for i in (1, 2, 3)}
    now_total: dict[str, float] = defaultdict(float)
    past_total: dict[str, float] = defaultdict(float)
    for e in expenses:
        m, cat = _month(e.get("date")), e.get("category") or "Λοιπά"
        amount = float(e.get("amount") or 0)
        if m == current:
            now_total[cat] += amount
        elif m in history:
            past_total[cat] += amount
    out = []
    for cat, now in now_total.items():
        avg = past_total.get(cat, 0.0) / 3
        # Χωρίς ιστορικό δεν υπάρχει βάση σύγκρισης — δεν ειδοποιούμε.
        if avg > 0 and now >= avg * SPIKE_FACTOR and now - avg >= SPIKE_MIN_DELTA:
            out.append({"category": cat, "current": round(now, 2), "average": round(avg, 2), "ratio": round(now / avg, 1)})
    return sorted(out, key=lambda x: x["current"] - x["average"], reverse=True)


def client_concentration(invoices: list[dict], today: date) -> dict | None:
    """Πελάτης με >40% του τζίρου των τελευταίων 12 μηνών — κίνδυνος εξάρτησης."""
    cutoff = _shift(today.strftime("%Y-%m"), -11)
    totals: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for inv in invoices:
        m = _month(inv.get("date"))
        if inv.get("status") not in ("issued", "paid") or not m or m < cutoff or inv.get("credit_note_for"):
            continue
        key = inv.get("client_id") or inv.get("client_name") or "?"
        totals[key] += float(inv.get("total") or 0)
        names[key] = inv.get("client_name") or key
    revenue = sum(totals.values())
    if revenue < CONCENTRATION_MIN_REVENUE:
        return None
    key, top = max(totals.items(), key=lambda kv: kv[1])
    share = top / revenue
    if share < CONCENTRATION_SHARE:
        return None
    return {"client_id": key, "client_name": names[key], "share": round(share * 100), "revenue": round(top, 2), "total_revenue": round(revenue, 2)}
