import calendar as pycal
from fastapi import APIRouter, Depends
from db import db, fetch_all
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import athens_today, next_vat_deadline, next_efka_deadline
from invoice_services import invoice_balances, open_total

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(user: dict = Depends(get_current_user)):
    today = athens_today()
    month_start = today.replace(day=1).isoformat()
    settings = await db.settings.find_one(tenant_query(user, {}))
    vat_period = settings.get("vat_period", "monthly") if settings else "monthly"

    # Τα έσοδα/έξοδα του μήνα, του έτους και του εξαμηνιαίου γραφήματος χρειάζονται μόνο το πρόσφατο παράθυρο ημερομηνιών.
    # Παλιότερα διαβάζονταν «τα πρώτα 2.000» χωρίς σειρά, και ό,τι περίσσευε έλειπε σιωπηλά από τα σύνολα.
    chart_months = []
    for i in range(5, -1, -1):
        m, y = today.month - i, today.year
        while m <= 0:
            m += 12
            y -= 1
        chart_months.append((y, m))
    window_start = min(f"{today.year}-01-01", f"{chart_months[0][0]}-{chart_months[0][1]:02d}-01")
    invoices = await fetch_all(db.invoices.find(
        tenant_query(user, {"status": {"$in": ["issued", "paid"]}, "date": {"$gte": window_start}})))
    expenses = await fetch_all(db.expenses.find(tenant_query(user, {"date": {"$gte": window_start}})))

    month_invoices = [i for i in invoices if i.get("date", "") >= month_start]
    month_expenses = [e for e in expenses if e.get("date", "") >= month_start]

    income_total = round(sum(i.get("subtotal", 0) for i in month_invoices), 2)
    output_vat = round(sum(i.get("vat_amount", 0) for i in month_invoices), 2)
    expense_total = round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in month_expenses), 2)
    input_vat = round(sum(e.get("vat_amount", 0) for e in month_expenses), 2)
    vat_due = round(output_vat - input_vat, 2)

    pending_mydata = await db.invoices.count_documents(tenant_query(user, {"mydata_status": {"$in": ["not_sent", "pending", "failed"]}}))
    draft_invoices = await db.invoices.count_documents(tenant_query(user, {"status": "draft"}))

    recent_invoices = await db.invoices.find(tenant_query(user, {})).sort("created_at", -1).limit(5).to_list(5)
    recent_expenses = await db.expenses.find(tenant_query(user, {})).sort("created_at", -1).limit(5).to_list(5)

    for d in recent_invoices + recent_expenses:
        d["id"] = str(d.pop("_id"))

    chart_data = []
    for y, m in chart_months:
        month_str = f"{y}-{m:02d}-01"
        next_m = m + 1
        next_y = y
        if next_m > 12:
            next_m = 1
            next_y += 1
        next_month_str = f"{next_y}-{next_m:02d}-01"
        m_income = round(sum(inv.get("subtotal", 0) for inv in invoices if month_str <= inv.get("date", "") < next_month_str), 2)
        m_expense = round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in expenses if month_str <= e.get("date", "") < next_month_str), 2)
        chart_data.append({"month": f"{pycal.month_abbr[m]} {y}", "income": m_income, "expense": m_expense})

    year_start = f"{today.year}-01-01"
    year_invoices = [i for i in invoices if i.get("date", "") >= year_start]
    year_income = round(sum(i.get("subtotal", 0) for i in year_invoices), 2)
    year_expense = round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in expenses if e.get("date", "") >= year_start), 2)
    # Ανείσπρακτα = ό,τι πράγματι απομένει να εισπραχθεί (μετά την παρακράτηση και ό,τι εξοφλήθηκε από πελατολογικό),
    # όχι το μεικτό σύνολο — ο ίδιος ορισμός με την πύλη του πελάτη (βλ. invoice_services.invoice_balances).
    # ΟΛΑ τα εκδοθέντα, ανεξάρτητα από ημερομηνία: ένα τιμολόγιο του περασμένου έτους που δεν εισπράχθηκε μετράει.
    unpaid = await fetch_all(db.invoices.find(tenant_query(user, {"status": "issued"})))
    balances = await invoice_balances(firm_id_for(user), unpaid, settings or {})
    unpaid_total = open_total(unpaid, balances)

    vat_deadline = next_vat_deadline(today, vat_period)
    efka_deadline = next_efka_deadline(today)

    if vat_due > 0:
        advice = (
            f"Τρέχων ΦΠΑ περιόδου: {vat_due:.2f} € πληρωτέος. Έχεις εκροές {income_total:.2f} € (ΦΠΑ {output_vat:.2f} €) "
            f"και εισροές {expense_total:.2f} € (ΦΠΑ {input_vat:.2f} €). Κάθε τιμολόγιο δαπάνης με ΦΠΑ που καταχωρείς μειώνει την οφειλή."
        )
    elif vat_due < 0:
        advice = f"Έχεις πιστωτικό υπόλοιπο ΦΠΑ {abs(vat_due):.2f} € για την τρέχουσα περίοδο — μεταφέρεται στην επόμενη δήλωση."
    else:
        advice = "Δεν προκύπτει ΦΠΑ προς πληρωμή για την τρέχουσα περίοδο."
    if unpaid_total > 0:
        advice += f" Υπενθύμιση: {len(unpaid)} ανεξόφλητα τιμολόγια συνολικού ποσού {unpaid_total:.2f} €."

    obligations = [
        {"date": vat_deadline.isoformat(), "label": "Δήλωση & πληρωμή ΦΠΑ", "amount": vat_due if vat_due > 0 else None},
        {"date": efka_deadline.isoformat(), "label": f"ΕΦΚΑ εισφορές {today.month:02d}/{today.year}", "amount": None},
    ]
    if pending_mydata:
        obligations.append({"date": today.isoformat(), "label": f"{pending_mydata} παραστατικά εκτός myDATA", "amount": None})
    upcoming_hearings = await db.hearings.find(
        tenant_query(user, {"date": {"$gte": today.isoformat()}, "status": {"$in": ["scheduled", "in_progress", "interrupted"]}})
    ).sort("date", 1).limit(2).to_list(2)
    for h in upcoming_hearings:
        obligations.append({
            "date": h.get("date", ""),
            "label": f"Δικάσιμος: {h.get('case_title') or h.get('court')} (Πινάκιο Νο {h.get('pinakio_number')})",
            "amount": None,
        })
    obligations.sort(key=lambda o: o["date"])

    return {
        "income_total": income_total,
        "expense_total": expense_total,
        "output_vat": output_vat,
        "input_vat": input_vat,
        "vat_due": vat_due,
        "pending_mydata": pending_mydata,
        "draft_invoices": draft_invoices,
        "next_vat_deadline": next_vat_deadline(today, vat_period).isoformat(),
        "next_efka_deadline": next_efka_deadline(today).isoformat(),
        "recent_invoices": recent_invoices,
        "recent_expenses": recent_expenses,
        "chart_data": chart_data,
        "year_income": year_income,
        "year_expense": year_expense,
        "year_profit": round(year_income - year_expense, 2),
        "unpaid_total": unpaid_total,
        "unpaid_count": len(unpaid),
        "advice": advice,
        "obligations": obligations,
    }
