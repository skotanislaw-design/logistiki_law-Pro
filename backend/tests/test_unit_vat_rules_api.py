"""Φάση 1: ο πίνακας εκπτωσιμότητας ΦΠΑ στις Ρυθμίσεις και η επίδρασή του στο Φ2, στις περιόδους ΦΠΑ και στα σύνολα.

Η λογική υπολογισμού ζει στο `vat_rules.py` (ελέγχεται στο test_unit_vat_rules.py)· εδώ ελέγχεται το σύνορο προς τον χρήστη:

* Ο πίνακας αποθηκεύεται μόνο έγκυρος και ομαλοποιημένος, και μόνο από διαχειριστή.
* Μια καρτέλα που δεν τον ξέρει (ανοιγμένη πριν την ενημέρωση) δεν τον σβήνει όταν αποθηκεύει άλλες ρυθμίσεις.
* Ό,τι υπολογίζει «ΦΠΑ εισροών» — Φ2, κλείσιμο περιόδου, dashboard, πρόβλεψη ταμείου, βοηθός — βλέπει τον ίδιο πίνακα.
* Η περίοδος που υποβλήθηκε δεν αλλάζει όταν αργότερα αλλάξει ο πίνακας.
* Οι κατηγορίες και οι τιμές που ξέρει το frontend είναι αυτές που ξέρει το backend.
"""
import asyncio
import re
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tax_utils import athens_today
from vat_return import build_vat_return, build_vat_return_pdf

PASSWORD = "Str0ngPass!"
SETTINGS = {"company_name": "Γραφείο", "afm": "123456783", "doy": "Α΄ Αθηνών", "vat_period": "monthly"}
PERIOD = {"period": "2026-08", "period_start": "2026-08-01", "period_end": "2026-08-31",
          "due_date": "2026-09-30", "status": "computed"}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _admin(c, email="rules@example.gr"):
    r = c.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    _run(db.users.update_one({"email": email}, {"$set": {"role": "admin"}}))
    return r.json()["firm_id"]


def _expense(**over):
    body = {"vendor": "Προμηθευτής", "date": "2026-08-10", "amount": 124.0, "vat_amount": 24.0, "vat_rate": 24,
            "category": "Λοιπά"}
    body.update(over)
    return body


def _line(data, key):
    return next(l for l in data["lines"] if l["key"] == key)


def _period(firm, period="2026-08", start="2026-08-01", end="2026-08-31", **extra):
    from db import db
    doc = {"firm_id": firm, "period": period, "period_start": start, "period_end": end, "due_date": "2026-09-30",
           "status": "computed", "output_vat": 0, "input_vat": 0, "vat_due": 0, **extra}
    _run(db.vat_periods.insert_one(doc))


def _stored_period(firm, period):
    from db import db
    return _run(db.vat_periods.find_one({"firm_id": firm, "period": period}))


def _patch(c, **body):
    return c.patch("/api/settings", json=body)


def _put(c, **over):
    """Όπως το κάνει η οθόνη Ρυθμίσεων: διαβάζει όλες τις ρυθμίσεις, αλλάζει μερικές και τις στέλνει πίσω."""
    current = c.get("/api/settings").json()
    current.update(over)
    return c.put("/api/settings", json=current)


# =================================================================== ΡΥΘΜΙΣΕΙΣ ====

def test_new_settings_have_no_rules_and_no_confirmation(client):
    _admin(client)
    s = client.get("/api/settings").json()
    assert s["vat_deductibility_rules"] == {} and s["vat_rules_confirmed_by"] == ""


def test_rules_are_stored_normalised(client):
    _admin(client)
    r = _put(client, vat_deductibility_rules={" Ενοίκιο ": "50,5", "Τηλεπικοινωνίες": 100, "Φιλοξενία / Εστίαση": 0})
    assert r.status_code == 200, r.text
    expected = {"Ενοίκιο": 50.5, "Τηλεπικοινωνίες": 100.0, "Φιλοξενία / Εστίαση": 0.0}
    assert r.json()["vat_deductibility_rules"] == expected
    assert client.get("/api/settings").json()["vat_deductibility_rules"] == expected
    patched = _patch(client, vat_deductibility_rules={" Μεταφορικά ": "25"})
    assert patched.status_code == 200 and patched.json()["vat_deductibility_rules"] == {"Μεταφορικά": 25.0}


BAD_TABLES = {
    "above 100": {"Ενοίκιο": 100.1},
    "negative": {"Ενοίκιο": -1},
    "text": {"Ενοίκιο": "πολύ"},
    "bool": {"Ενοίκιο": True},
    "null value": {"Ενοίκιο": None},
    "NaN as text": {"Ενοίκιο": "NaN"},
    "infinity as text": {"Ενοίκιο": "Infinity"},
    "empty category": {"": 50},
    "blank category": {"   ": 50},
    "duplicate after trimming": {"Ενοίκιο": 50, " Ενοίκιο ": 60},
    "dot in category": {"Λογ. ΔΕΗ": 50},
    "dollar in category": {"$gt": 50},
    "category too long": {"Κ" * 81: 50},
    "too many categories": {f"Κατηγορία {i}": 10 for i in range(101)},
}


@pytest.mark.parametrize("name", list(BAD_TABLES))
def test_invalid_tables_are_refused_and_the_stored_table_is_untouched(client, name):
    _admin(client)
    assert _put(client, vat_deductibility_rules={"Ενοίκιο": 50}).status_code == 200
    for call in (lambda t: _patch(client, vat_deductibility_rules=t), lambda t: _put(client, vat_deductibility_rules=t)):
        r = call(BAD_TABLES[name])
        assert r.status_code in (400, 422), (name, r.status_code, r.text)
    assert client.get("/api/settings").json()["vat_deductibility_rules"] == {"Ενοίκιο": 50.0}


@pytest.mark.parametrize("not_a_table", [[{"Ενοίκιο": 50}], "Ενοίκιο", 5, True])
def test_a_patch_that_is_not_a_table_is_a_clean_400(client, not_a_table):
    _admin(client)
    assert _put(client, vat_deductibility_rules={"Ενοίκιο": 50}).status_code == 200
    assert _patch(client, vat_deductibility_rules=not_a_table).status_code == 400
    assert client.get("/api/settings").json()["vat_deductibility_rules"] == {"Ενοίκιο": 50.0}


def test_every_standard_category_can_have_a_rule(client):
    from models import EXPENSE_CATEGORIES
    _admin(client)
    r = _patch(client, vat_deductibility_rules={c: 50 for c in EXPENSE_CATEGORIES})
    assert r.status_code == 200, r.text
    assert set(r.json()["vat_deductibility_rules"]) == set(EXPENSE_CATEGORIES)


def test_a_patch_with_null_clears_the_table(client):
    _admin(client)
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    r = _patch(client, vat_deductibility_rules=None)
    assert r.status_code == 200, r.text
    assert r.json()["vat_deductibility_rules"] == {} and r.json()["vat_rules_confirmed_by"] == ""


def test_only_an_admin_can_change_the_table(client):
    r = client.post("/api/auth/register", json={"email": "lawyer@example.gr", "password": PASSWORD, "name": "Δικηγόρος"})
    assert r.status_code == 200
    assert _patch(client, vat_deductibility_rules={"Ενοίκιο": 0}).status_code == 403
    assert client.get("/api/settings").json()["vat_deductibility_rules"] == {}       # ο πίνακας διαβάζεται από όλους


def test_a_settings_save_from_a_stale_form_does_not_erase_the_table(client):
    """Καρτέλα ανοιγμένη πριν την ενημέρωση: στέλνει όλες τις ρυθμίσεις, εκτός από τα νέα πεδία."""
    _admin(client)
    assert _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος").status_code == 200
    stale = client.get("/api/settings").json()
    del stale["vat_deductibility_rules"], stale["vat_rules_confirmed_by"]
    stale["company_name"] = "Νέο όνομα"
    r = client.put("/api/settings", json=stale)
    assert r.status_code == 200, r.text
    out = client.get("/api/settings").json()
    assert out["company_name"] == "Νέο όνομα"
    assert out["vat_deductibility_rules"] == {"Ενοίκιο": 50.0}
    assert out["vat_rules_confirmed_by"] == "Χριστόφορος"


def test_changing_the_table_withdraws_a_confirmation_that_was_not_restated(client):
    _admin(client)
    base = _patch(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    assert base.json()["vat_rules_confirmed_by"] == "Χριστόφορος"
    # ίδιος πίνακας, χωρίς όνομα στο αίτημα: η επιβεβαίωση μένει
    assert _patch(client, vat_deductibility_rules={"Ενοίκιο": 50}).json()["vat_rules_confirmed_by"] == "Χριστόφορος"
    # άλλος πίνακας, χωρίς νέα δήλωση επιβεβαίωσης: η παλιά δεν ισχύει για αριθμούς που δεν είδε
    assert _patch(client, vat_deductibility_rules={"Ενοίκιο": 60}).json()["vat_rules_confirmed_by"] == ""
    # άλλος πίνακας, με ρητή δήλωση: ισχύει αυτή
    restated = _patch(client, vat_deductibility_rules={"Ενοίκιο": 70}, vat_rules_confirmed_by="Χριστόφορος")
    assert restated.json()["vat_rules_confirmed_by"] == "Χριστόφορος"
    # αλλαγή μόνο του ονόματος: ο πίνακας μένει
    renamed = _patch(client, vat_rules_confirmed_by="Άλλος λογιστής")
    assert renamed.json()["vat_rules_confirmed_by"] == "Άλλος λογιστής"
    assert renamed.json()["vat_deductibility_rules"] == {"Ενοίκιο": 70.0}


def test_a_full_form_save_that_changes_the_table_does_not_keep_the_name_left_in_the_field(client):
    """Η οθόνη στέλνει πάντα και το όνομα: αν ο πίνακας αλλάζει και το όνομα είναι το ίδιο με το αποθηκευμένο, δεν το
    ξαναδήλωσε κανείς — η επιβεβαίωση είχε δοθεί για τον προηγούμενο πίνακα και ανακαλείται (βλ. Ε4 στο AUDIT)."""
    _admin(client)
    _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    r = _put(client, vat_deductibility_rules={"Ενοίκιο": 80}, vat_rules_confirmed_by="Χριστόφορος")
    assert r.json()["vat_deductibility_rules"] == {"Ενοίκιο": 80.0}
    assert r.json()["vat_rules_confirmed_by"] == ""
    # Ίδιος πίνακας με τη φόρμα όπως φορτώθηκε: τίποτα δεν αλλάζει, η επιβεβαίωση μένει.
    _put(client, vat_deductibility_rules={"Ενοίκιο": 80}, vat_rules_confirmed_by="Χριστόφορος, 20/10/2026")
    again = _put(client, vat_deductibility_rules={"Ενοίκιο": 80}, vat_rules_confirmed_by="Χριστόφορος, 20/10/2026")
    assert again.json()["vat_rules_confirmed_by"] == "Χριστόφορος, 20/10/2026"


def test_confirmation_name_is_trimmed_limited_and_must_be_text(client):
    _admin(client)
    assert _patch(client, vat_rules_confirmed_by="  Χριστόφορος  ").json()["vat_rules_confirmed_by"] == "Χριστόφορος"
    assert _patch(client, vat_rules_confirmed_by="Χ" * 120).status_code == 200
    assert _patch(client, vat_rules_confirmed_by="Χ" * 121).status_code == 400
    assert _patch(client, vat_rules_confirmed_by=5).status_code == 400
    assert _patch(client, vat_rules_confirmed_by=["Χριστόφορος"]).status_code == 400
    assert _patch(client, vat_rules_confirmed_by=None).json()["vat_rules_confirmed_by"] == ""


# ========================================================================== Φ2 ====

def test_the_return_deducts_only_the_deductible_part_and_shows_the_rest():
    settings = {**SETTINGS, "vat_deductibility_rules": {"Φιλοξενία / Εστίαση": 0, "Τηλεπικοινωνίες": 50},
                "vat_rules_confirmed_by": "Χριστόφορος"}
    invoices = [{"subtotal": 1000, "vat_amount": 240, "vat_breakdown": {"24": 240},
                 "line_items": [{"quantity": 1, "unit_price": 1000, "vat_rate": 24}]}]
    expenses = [
        {"amount": 124, "vat_amount": 24, "category": "Ενοίκιο"},                                  # χωρίς κανόνα: 100%
        {"amount": 124, "vat_amount": 24, "category": "Φιλοξενία / Εστίαση"},                     # 0%
        {"amount": 124, "vat_amount": 24, "category": "Τηλεπικοινωνίες"},                         # 50%
        {"amount": 124, "vat_amount": 24, "category": "Λοιπά", "vat_deductible_pct": 25},         # ρητό 25%
    ]
    data = build_vat_return(PERIOD, invoices, expenses, settings)

    assert _line(data, "inputs_vat")["amount"] == 42                       # 24 + 0 + 12 + 6
    assert _line(data, "inputs_base")["amount"] == 400                     # η βάση μένει η καθαρή αξία: 4 × (124 − 24)
    assert _line(data, "vat_payable")["amount"] == 198                     # 240 − 42
    assert data["totals"]["input_vat"] == 42 and data["totals"]["input_vat_nondeductible"] == 54
    rules = data["vat_rules"]
    assert rules["confirmed_by"] == "Χριστόφορος" and rules["rule_count"] == 2
    assert rules["unruled_vat"] == 24                                      # μόνο το Ενοίκιο εκπίπτει «από προεπιλογή»
    assert rules["unruled_categories"] == [{"category": "Ενοίκιο", "count": 1, "vat_amount": 24.0}]


def test_without_rules_the_return_is_what_it_always_was():
    expenses = [{"amount": 124, "vat_amount": 24, "category": "Φιλοξενία / Εστίαση"},
                {"amount": 226, "vat_amount": 26, "category": "Λοιπά"}]
    data = build_vat_return(PERIOD, [], expenses, SETTINGS)
    assert _line(data, "inputs_vat")["amount"] == 50 and data["totals"]["input_vat_nondeductible"] == 0
    assert data["vat_rules"]["rule_count"] == 0 and data["vat_rules"]["confirmed_by"] == ""
    assert data["vat_rules"]["unruled_vat"] == 50


def test_the_pdf_renders_with_hostile_category_and_confirmation_text():
    settings = {**SETTINGS, "vat_deductibility_rules": {"Άλλη": 50}, "vat_rules_confirmed_by": "<b>Χ</b> & Σία"}
    expenses = [{"amount": 124, "vat_amount": 24, "category": "Κ<script>&</i>"},
                {"amount": 124, "vat_amount": 24, "category": "Άλλη"}]
    pdf = build_vat_return_pdf(build_vat_return(PERIOD, [], expenses, settings))
    assert pdf.startswith(b"%PDF")


# =============================================================== ΠΕΡΙΟΔΟΙ ΦΠΑ ====

def test_the_return_endpoint_follows_the_current_table_for_an_open_period(client):
    firm = _admin(client)
    _period(firm)
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        assert client.post("/api/expenses", json=_expense(category=category)).status_code == 200
    plain = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert _line(plain, "inputs_vat")["amount"] == 48
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0}, vat_rules_confirmed_by="Χριστόφορος")
    ruled = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert _line(ruled, "inputs_vat")["amount"] == 24
    assert ruled["vat_rules"]["confirmed_by"] == "Χριστόφορος"
    pdf = client.get("/api/automation/vat-periods/2026-08/return.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_a_submitted_period_keeps_the_table_it_was_filed_with(client):
    firm = _admin(client)
    _period(firm)
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        client.post("/api/expenses", json=_expense(category=category))
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0, "Ενοίκιο": 100}, vat_rules_confirmed_by="Χριστόφορος")
    filed = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert _line(filed, "inputs_vat")["amount"] == 24
    assert client.post("/api/automation/vat-periods/2026-08/submitted").status_code == 200
    snapshot = _stored_period(firm, "2026-08")["vat_rules_snapshot"]
    assert snapshot["rules"] == {"Φιλοξενία / Εστίαση": 0.0, "Ενοίκιο": 100.0} and snapshot["confirmed_by"] == "Χριστόφορος"

    # ο πίνακας αλλάζει ΜΕΤΑ την υποβολή
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 50, "Ενοίκιο": 0}, vat_rules_confirmed_by="Άλλος")
    after = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert after["totals"] == filed["totals"] and after["vat_rules"] == filed["vat_rules"]
    assert _line(after, "inputs_vat")["amount"] == 24 and after["status"] == "submitted"

    # μια ΑΝΟΙΧΤΗ περίοδος ακολουθεί τον τρέχοντα πίνακα
    _period(firm, period="2026-09", start="2026-09-01", end="2026-09-30")
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        client.post("/api/expenses", json=_expense(category=category, date="2026-09-10"))
    live = client.get("/api/automation/vat-periods/2026-09/return").json()
    assert _line(live, "inputs_vat")["amount"] == 12                      # 0% του Ενοικίου + 50% της Εστίασης


def test_marking_a_period_submitted_twice_changes_neither_the_time_nor_the_snapshot(client):
    firm = _admin(client)
    _period(firm)
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 10}, vat_rules_confirmed_by="Χριστόφορος")
    assert client.post("/api/automation/vat-periods/2026-08/submitted").status_code == 200
    first = _stored_period(firm, "2026-08")
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 90}, vat_rules_confirmed_by="Άλλος")
    assert client.post("/api/automation/vat-periods/2026-08/submitted").status_code == 200
    second = _stored_period(firm, "2026-08")
    assert second["vat_rules_snapshot"] == first["vat_rules_snapshot"] and second["submitted_at"] == first["submitted_at"]


def test_a_period_submitted_before_the_table_existed_is_computed_without_rules(client):
    firm = _admin(client)
    _period(firm, status="submitted", submitted_at="2026-09-05T10:00:00+00:00")          # χωρίς στιγμιότυπο
    client.post("/api/expenses", json=_expense(category="Φιλοξενία / Εστίαση"))
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0}, vat_rules_confirmed_by="Χριστόφορος")
    data = client.get("/api/automation/vat-periods/2026-08/return").json()
    assert _line(data, "inputs_vat")["amount"] == 24                                     # όπως υπολογιζόταν τότε
    assert data["vat_rules"]["rule_count"] == 0 and data["vat_rules"]["confirmed_by"] == ""


def test_marking_a_missing_or_foreign_period_is_a_404(client):
    firm = _admin(client)
    from db import db
    _run(db.vat_periods.insert_one({"firm_id": "another-firm", "period": "2026-08", "period_start": "2026-08-01",
                                    "period_end": "2026-08-31", "status": "computed"}))
    assert client.post("/api/automation/vat-periods/2026-08/submitted").status_code == 404
    assert client.post("/api/automation/vat-periods/2026-99/submitted").status_code == 404
    assert _run(db.vat_periods.find_one({"firm_id": "another-firm"}))["status"] == "computed"
    assert firm


def _last_closed_period():
    from automation import closed_vat_periods
    return closed_vat_periods(athens_today(), "monthly")[-1]


def test_period_close_stores_only_the_deductible_vat(client):
    from automation import compute_vat_period
    firm = _admin(client)
    period = _last_closed_period()
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        client.post("/api/expenses", json=_expense(category=category, date=period["start"].isoformat()))
    ruled = _run(compute_vat_period(firm, period, {"vat_deductibility_rules": {"Φιλοξενία / Εστίαση": 0}}))
    assert (ruled.input_vat, ruled.input_vat_nondeductible, ruled.input_vat_unruled, ruled.expense_net) == (24.0, 24.0, 24.0, 224.0)
    plain = _run(compute_vat_period(firm, period))                       # χωρίς ρυθμίσεις: όπως πριν τη Φάση 1
    assert (plain.input_vat, plain.input_vat_nondeductible, plain.input_vat_unruled, plain.expense_net) == (48.0, 0.0, 48.0, 200.0)


def test_the_period_job_follows_the_table_for_open_periods_but_never_for_a_submitted_one(client):
    from automation import job_vat_periods
    from db import db
    firm = _admin(client)
    period = _last_closed_period()
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        client.post("/api/expenses", json=_expense(category=category, date=period["start"].isoformat()))

    def run_job():
        _run(job_vat_periods(firm, _run(db.settings.find_one({"firm_id": firm})) or {}))
        stored = _stored_period(firm, period["period"])
        return stored["input_vat"], stored["input_vat_nondeductible"], stored["input_vat_unruled"]

    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0})
    assert run_job() == (24.0, 24.0, 24.0)
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 100, "Ενοίκιο": 100})
    assert run_job() == (48.0, 0.0, 0.0)                                  # η ανοιχτή περίοδος ακολουθεί τον νέο πίνακα
    assert client.post(f"/api/automation/vat-periods/{period['period']}/submitted").status_code == 200
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0, "Ενοίκιο": 0})
    assert run_job() == (48.0, 0.0, 0.0)                                  # η υποβληθείσα δεν ξαναϋπολογίζεται


# ======================================================== ΑΘΡΟΙΣΜΑΤΑ & ΒΟΗΘΟΣ ====

def _spend_today(client):
    today = athens_today().isoformat()
    for category, amount, vat in (("Ενοίκιο", 124.0, 24.0), ("Φιλοξενία / Εστίαση", 62.0, 12.0),
                                  ("Τηλεπικοινωνίες", 62.0, 12.0)):
        r = client.post("/api/expenses", json=_expense(category=category, amount=amount, vat_amount=vat, date=today))
        assert r.status_code == 200, r.text
    r = _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0, "Τηλεπικοινωνίες": 50})
    assert r.status_code == 200, r.text


def test_dashboard_expense_and_input_vat_add_up_to_what_was_paid(client):
    _admin(client)
    _spend_today(client)
    d = client.get("/api/dashboard/summary").json()
    assert d["input_vat"] == 30.0                                         # 24 (Ενοίκιο, χωρίς κανόνα) + 0 + 6
    assert d["input_vat_nondeductible"] == 18.0                           # 12 + 6
    assert d["input_vat_unruled"] == 24.0
    assert d["expense_total"] == 218.0                                    # ό,τι δεν εκπίπτει είναι κόστος
    assert round(d["expense_total"] + d["input_vat"], 2) == 248.0         # = όσα πληρώθηκαν συνολικά
    assert d["vat_due"] == round(d["output_vat"] - 30.0, 2)


def test_cash_flow_forecast_uses_the_deductible_vat(client):
    _admin(client)
    _spend_today(client)
    cl = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company"}).json()["id"]
    draft = client.post("/api/invoices", json={
        "client_id": cl, "date": athens_today().isoformat(),
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}]}).json()
    assert client.post(f"/api/invoices/{draft['id']}/issue").status_code == 200
    events = client.get("/api/analytics/cashflow").json()["events"]
    vat = [e for e in events if e["label"] == "Δήλωση ΦΠΑ"]
    assert vat and vat[0]["amount"] == 210.0                              # 240 εκροές − 30 εκπιπτόμενο (όχι − 48)


def test_the_assistant_context_uses_the_deductible_vat(client):
    from copilot import build_context
    firm = _admin(client)
    _spend_today(client)
    context = _run(build_context({"firm_id": firm}))
    month = context["τρέχων_μήνας"]
    assert month["ΦΠΑ_εισροών"] == "30,00 €" and month["καθαρά_έξοδα"] == "218,00 €"


def test_the_digest_sent_to_the_accountant_uses_the_deductible_vat(client):
    """Η εβδομαδιαία σύνοψη email είναι αυτό που διαβάζει η λογίστρια· δεν επιτρέπεται να δείχνει άλλο ΦΠΑ από την οθόνη."""
    from automation import build_digest, firm_snapshot
    from db import db
    firm = _admin(client)
    _spend_today(client)
    settings = _run(db.settings.find_one({"firm_id": firm})) or {}
    snapshot = _run(firm_snapshot(firm, settings))
    assert (snapshot["input_vat"], snapshot["expense_net"]) == (30.0, 218.0)
    assert snapshot["vat_due"] == round(snapshot["output_vat"] - 30.0, 2)
    text = build_digest(snapshot)[1]
    assert "εισροές 30,00 €" in text and "Καθαρά έξοδα: 218,00 €" in text


# ============================================================ FRONTEND ↔ BACKEND ====

ROOT = Path(__file__).resolve().parents[2]


def _js(name):
    return (ROOT / "frontend" / "src" / "lib" / name).read_text(encoding="utf-8")


def _string_list(source, name):
    match = re.search(rf"export const {name} = \[(.*?)\];", source, re.S)
    assert match, f"δεν βρέθηκε το {name}"
    return re.findall(r'"([^"]*)"', match.group(1))


def test_frontend_expense_categories_are_the_backends():
    from models import EXPENSE_CATEGORIES
    assert _string_list(_js("vatRules.js"), "EXPENSE_CATEGORIES") == EXPENSE_CATEGORIES


def test_frontend_lawyer_roles_are_the_backends():
    from dimensions import LAWYER_ROLES
    assert tuple(_string_list(_js("matters.js"), "LAWYER_ROLES")) == LAWYER_ROLES


def test_frontend_expense_scopes_are_the_backends():
    from dimensions import EXPENSE_SCOPES
    from models import Expense
    block = re.search(r"export const SCOPE_LABEL = \{(.*?)\};", _js("matters.js"), re.S)
    assert block, "δεν βρέθηκε το SCOPE_LABEL"
    assert tuple(re.findall(r"^\s*(\w+):", block.group(1), re.M)) == EXPENSE_SCOPES
    assert typing.get_args(Expense.model_fields["scope"].annotation) == EXPENSE_SCOPES
