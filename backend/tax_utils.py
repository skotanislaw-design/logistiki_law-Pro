from datetime import date, timedelta
import calendar

TAX_BRACKETS = [
    (10000, 0.09),
    (20000, 0.22),
    (30000, 0.28),
    (40000, 0.36),
    (float("inf"), 0.44),
]

TAX_CREDIT_BY_DEPENDENTS = {0: 777, 1: 810, 2: 900, 3: 1120}


def _tax_credit(dependents: int, taxable_annual: float) -> float:
    base = TAX_CREDIT_BY_DEPENDENTS.get(dependents, 1340 if dependents >= 4 else 777)
    if taxable_annual <= 12000:
        return base
    if taxable_annual >= 20000:
        reduction = ((taxable_annual - 12000) // 1000) * 20
        return max(0, base - reduction)
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


EMPLOYEE_EFKA_RATE = 0.1387
EMPLOYER_EFKA_RATE = 0.2229


def calculate_payroll(gross_salary: float, dependents: int = 0) -> dict:
    employee_efka = round(gross_salary * EMPLOYEE_EFKA_RATE, 2)
    employer_efka = round(gross_salary * EMPLOYER_EFKA_RATE, 2)
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


def calculate_invoice_totals(line_items: list) -> dict:
    subtotal = 0.0
    vat_breakdown = {}
    for item in line_items:
        line_total = item.quantity * item.unit_price if hasattr(item, "quantity") else item["quantity"] * item["unit_price"]
        rate = item.vat_rate if hasattr(item, "vat_rate") else item["vat_rate"]
        subtotal += line_total
        vat_amount = round(line_total * rate / 100, 2)
        vat_breakdown[str(rate)] = round(vat_breakdown.get(str(rate), 0) + vat_amount, 2)
    vat_total = round(sum(vat_breakdown.values()), 2)
    total = round(subtotal + vat_total, 2)
    return {"subtotal": round(subtotal, 2), "vat_amount": vat_total, "total": total, "vat_breakdown": vat_breakdown}


def _last_business_day(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_vat_deadline(today: date, vat_period: str = "monthly") -> date:
    if vat_period == "quarterly":
        quarter_end_month = ((today.month - 1) // 3) * 3 + 3
        year = today.year
        if quarter_end_month < today.month:
            quarter_end_month += 3
        target_month = quarter_end_month + 1
        target_year = year
        if target_month > 12:
            target_month -= 12
            target_year += 1
        return _last_business_day(target_year, target_month)
    target_month = today.month + 1
    target_year = today.year
    if target_month > 12:
        target_month = 1
        target_year += 1
    return _last_business_day(target_year, target_month)


def next_efka_deadline(today: date) -> date:
    return _last_business_day(today.year, today.month)


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
            "due_date": _last_business_day(efka_deadline.year, efka_deadline.month).isoformat(),
            "category": "income_tax",
        },
    ]
    return sorted(items, key=lambda x: x["due_date"])
