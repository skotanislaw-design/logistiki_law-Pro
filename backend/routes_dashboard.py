from datetime import date, datetime
import calendar as pycal
from fastapi import APIRouter, Depends
from db import db
from auth import get_current_user
from tax_utils import next_vat_deadline, next_efka_deadline

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(user: dict = Depends(get_current_user)):
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    settings = await db.settings.find_one({})
    vat_period = settings.get("vat_period", "monthly") if settings else "monthly"

    invoices = await db.invoices.find({"status": {"$in": ["issued", "paid"]}}).to_list(2000)
    expenses = await db.expenses.find({}).to_list(2000)

    month_invoices = [i for i in invoices if i.get("date", "") >= month_start]
    month_expenses = [e for e in expenses if e.get("date", "") >= month_start]

    income_total = round(sum(i.get("subtotal", 0) for i in month_invoices), 2)
    output_vat = round(sum(i.get("vat_amount", 0) for i in month_invoices), 2)
    expense_total = round(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in month_expenses), 2)
    input_vat = round(sum(e.get("vat_amount", 0) for e in month_expenses), 2)
    vat_due = round(output_vat - input_vat, 2)

    pending_mydata = await db.invoices.count_documents({"mydata_status": {"$in": ["not_sent", "pending", "failed"]}})
    draft_invoices = await db.invoices.count_documents({"status": "draft"})

    recent_invoices = await db.invoices.find({}).sort("created_at", -1).limit(5).to_list(5)
    recent_expenses = await db.expenses.find({}).sort("created_at", -1).limit(5).to_list(5)

    for d in recent_invoices + recent_expenses:
        d["_id"] = str(d["_id"])

    chart_data = []
    for i in range(5, -1, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
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
    }
