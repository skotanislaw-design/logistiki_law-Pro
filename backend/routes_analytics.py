"""Business Analytics — Cash Flow forecast + Profitability."""
from datetime import date, timedelta
from calendar import monthrange
from fastapi import APIRouter, Depends
from db import db
from auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


@router.get("/cashflow")
async def cash_flow_forecast(user: dict = Depends(get_current_user)):
    """Πρόβλεψη ταμείου 3 μηνών βάσει ανείσπρακτων + επαναλαμβανόμενων εξόδων + φορολογικών προθεσμιών."""
    today = date.today()
    horizon_end = _add_months(today, 3)

    # 1) Αναμενόμενες εισπράξεις = ανεξόφλητα τιμολόγια, projected στις 30 μέρες μετά την ημερομηνία τους
    unpaid = await db.invoices.find({"status": "issued"}).to_list(2000)
    expected_income = []
    for inv in unpaid:
        try:
            issue = date.fromisoformat((inv.get("date") or "")[:10])
        except Exception:
            continue
        expected_pay = issue + timedelta(days=30)
        if expected_pay < today:
            # ήδη ληξιπρόθεσμο — θεωρώ ότι θα πληρωθεί εντός 15 ημερών από σήμερα
            expected_pay = today + timedelta(days=15)
        if expected_pay <= horizon_end:
            expected_income.append({
                "date": expected_pay.isoformat(),
                "label": f"Είσπραξη {inv.get('number')} — {inv.get('client_name', '')}",
                "amount": float(inv.get("total", 0)),
                "type": "income",
                "confidence": "medium",
            })

    # 2) Επαναλαμβανόμενα έξοδα = μέσος όρος τελευταίων 3 μηνών ανά κατηγορία
    three_months_ago = _add_months(today, -3).isoformat()
    recent_expenses = await db.expenses.find({"date": {"$gte": three_months_ago}}).to_list(3000)
    by_cat = {}
    for e in recent_expenses:
        c = e.get("category") or "Λοιπά"
        by_cat.setdefault(c, []).append(float(e.get("amount", 0)))
    monthly_expense_avg = 0
    expense_by_cat = {}
    for c, amounts in by_cat.items():
        avg = sum(amounts) / max(1, len(set(e.get("date", "")[:7] for e in recent_expenses if e.get("category") == c)))
        expense_by_cat[c] = round(avg, 2)
        monthly_expense_avg += avg

    expected_expense = []
    for m in range(1, 4):
        expected_month = _add_months(today.replace(day=1), m).replace(day=5)  # π.χ. 5η κάθε μήνα
        if expected_month <= horizon_end:
            expected_expense.append({
                "date": expected_month.isoformat(),
                "label": f"Εκτιμώμενα λειτουργικά έξοδα {expected_month.strftime('%m/%Y')}",
                "amount": round(monthly_expense_avg, 2),
                "type": "expense",
                "confidence": "high",
            })

    # 3) Φορολογικές προθεσμίες (VAT/EFKA εντός horizon)
    from tax_utils import next_vat_deadline, next_efka_deadline
    settings = await db.settings.find_one({}) or {}
    vat_period = settings.get("vat_period", "monthly")

    tax_deadlines = []
    ref_day = today
    for _ in range(4):
        vd = next_vat_deadline(ref_day, vat_period)
        if vd <= horizon_end:
            # Υπολογίζω το ποσό ΦΠΑ βάσει τρέχοντος πραγματικού
            month_start = ref_day.replace(day=1).isoformat()
            invs = await db.invoices.find({"date": {"$gte": month_start}, "status": {"$in": ["issued", "paid"]}}).to_list(500)
            exps = await db.expenses.find({"date": {"$gte": month_start}}).to_list(500)
            out_vat = sum(float(i.get("vat_amount", 0)) for i in invs)
            in_vat = sum(float(e.get("vat_amount", 0)) for e in exps)
            vat_due = max(0, out_vat - in_vat)
            tax_deadlines.append({
                "date": vd.isoformat(),
                "label": "Δήλωση ΦΠΑ",
                "amount": round(vat_due, 2),
                "type": "expense",
                "confidence": "high",
            })
            ref_day = vd + timedelta(days=1)
        else:
            break
    ed = next_efka_deadline(today)
    if ed <= horizon_end:
        tax_deadlines.append({
            "date": ed.isoformat(),
            "label": "Εισφορές ΕΦΚΑ",
            "amount": 300.0,
            "type": "expense",
            "confidence": "medium",
        })

    # Combine & sort
    events = expected_income + expected_expense + tax_deadlines
    events.sort(key=lambda e: e["date"])

    # Running balance
    total_income = sum(e["amount"] for e in events if e["type"] == "income")
    total_expense = sum(e["amount"] for e in events if e["type"] == "expense")
    running = 0
    projected = []
    for e in events:
        running += e["amount"] if e["type"] == "income" else -e["amount"]
        projected.append({**e, "running_balance": round(running, 2)})

    # Monthly buckets για chart
    buckets = {}
    for e in events:
        m = e["date"][:7]
        buckets.setdefault(m, {"month": m, "income": 0, "expense": 0})
        if e["type"] == "income":
            buckets[m]["income"] += e["amount"]
        else:
            buckets[m]["expense"] += e["amount"]
    monthly = sorted(buckets.values(), key=lambda x: x["month"])
    for m in monthly:
        m["net"] = round(m["income"] - m["expense"], 2)
        m["income"] = round(m["income"], 2)
        m["expense"] = round(m["expense"], 2)

    return {
        "horizon": {"start": today.isoformat(), "end": horizon_end.isoformat()},
        "total_expected_income": round(total_income, 2),
        "total_expected_expense": round(total_expense, 2),
        "net": round(total_income - total_expense, 2),
        "events": projected[:50],
        "monthly": monthly,
        "expense_by_category": expense_by_cat,
    }


@router.get("/profitability")
async def profitability(user: dict = Depends(get_current_user)):
    """Κερδοφορία ανά πελάτη, ανά τύπο υπόθεσης, top clients."""
    today = date.today()
    year_start = f"{today.year}-01-01"

    invoices = await db.invoices.find({"date": {"$gte": year_start}, "status": {"$in": ["issued", "paid"]}}).to_list(5000)
    clients = await db.clients.find({}).to_list(5000)
    cases = await db.cases.find({}).to_list(5000)

    client_map = {str(c["_id"]): c for c in clients}
    case_map = {str(c["_id"]): c for c in cases}

    # Ανά πελάτη
    per_client = {}
    for inv in invoices:
        cid = inv.get("client_id", "")
        if not cid:
            continue
        per_client.setdefault(cid, {
            "client_id": cid,
            "client_name": client_map.get(cid, {}).get("name", inv.get("client_name", "—")),
            "invoice_count": 0,
            "total_revenue": 0.0,
            "paid_revenue": 0.0,
            "unpaid_revenue": 0.0,
        })
        p = per_client[cid]
        p["invoice_count"] += 1
        p["total_revenue"] += float(inv.get("subtotal", 0))
        if inv.get("status") == "paid":
            p["paid_revenue"] += float(inv.get("total", 0))
        else:
            p["unpaid_revenue"] += float(inv.get("total", 0))
    top_clients = sorted(per_client.values(), key=lambda p: p["total_revenue"], reverse=True)[:10]
    for c in top_clients:
        c["total_revenue"] = round(c["total_revenue"], 2)
        c["paid_revenue"] = round(c["paid_revenue"], 2)
        c["unpaid_revenue"] = round(c["unpaid_revenue"], 2)

    # Ανά τύπο υπόθεσης (case_type αν συνδέεται με invoice μέσω client)
    per_case_type = {}
    for case in cases:
        ct = case.get("case_type", "Λοιπά")
        per_case_type.setdefault(ct, {"case_type": ct, "case_count": 0, "revenue": 0.0})
        per_case_type[ct]["case_count"] += 1
    for inv in invoices:
        cid = inv.get("client_id", "")
        # find first case for this client
        for c in cases:
            if c.get("client_id") == cid:
                ct = c.get("case_type", "Λοιπά")
                if ct in per_case_type:
                    per_case_type[ct]["revenue"] += float(inv.get("subtotal", 0))
                break
    case_types = sorted(per_case_type.values(), key=lambda x: x["revenue"], reverse=True)
    for c in case_types:
        c["revenue"] = round(c["revenue"], 2)
        c["avg_per_case"] = round(c["revenue"] / max(1, c["case_count"]), 2)

    # Overall metrics
    total_revenue = sum(float(i.get("subtotal", 0)) for i in invoices)
    paid_revenue = sum(float(i.get("total", 0)) for i in invoices if i.get("status") == "paid")
    unpaid_revenue = sum(float(i.get("total", 0)) for i in invoices if i.get("status") == "issued")
    collection_rate = round(paid_revenue / max(0.01, paid_revenue + unpaid_revenue) * 100, 1)
    avg_invoice = round(total_revenue / max(1, len(invoices)), 2)

    return {
        "period": {"start": year_start, "end": today.isoformat()},
        "totals": {
            "revenue": round(total_revenue, 2),
            "paid": round(paid_revenue, 2),
            "unpaid": round(unpaid_revenue, 2),
            "invoice_count": len(invoices),
            "avg_invoice": avg_invoice,
            "collection_rate_pct": collection_rate,
        },
        "top_clients": top_clients,
        "case_types": case_types,
    }
