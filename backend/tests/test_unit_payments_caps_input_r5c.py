"""Γύρος 5 (έλεγχος μεταλλάξεων) — είσοδοι: ο φραγμός NaN/άπειρων και τα όρια των γραμμών.

Κάθε test εδώ υπάρχει επειδή μια μικρή, εύλογη αλλαγή στον κώδικα δεν έριχνε κανένα υπάρχον test:

* ο φραγμός δεν ελέγχει πια τα PUT, ή τα PATCH, ή τα DELETE (τα υπάρχοντα tests έστελναν NaN μόνο με POST, ή σε πεδία που
  ελέγχει και το ίδιο το route)· ένα NaN σε ελεύθερο πεδίο ρυθμίσεων ή σε ποσό εξόδου αποθηκευόταν και χάλαγε τις αναφορές·
* ένα πολύ βαθιά φωλιασμένο σώμα (`[[[[…`) έριχνε `RecursionError` μέσα στον φραγμό αντί να περάσει στη FastAPI·
* το όριο της τιμής μονάδας στη γραμμή (τιμή 2 δισ. × ποσότητα 0,001 περνούσε, αφού το γινόμενο είναι μικρό) και το όριο
  της ποσότητας (ποσότητα 2 δισ. × τιμή 0,0000001), καθώς και η μηδενική ποσότητα (το `quantity <= 0` έγινε `< 0`)·
* το ανώτατο ποσό του Copilot δεν ταυτίζεται με της φόρμας (το ακριβές όριο απορρίπτεται, ή το επόμενο ευρώ γίνεται δεκτό).
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tax_utils import MAX_LINE_VALUE, athens_today

JSON = {"content-type": "application/json"}


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register",
                    json={"email": "input@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "input@example.gr"}))
    _run(db.users.update_one({"email": "input@example.gr"}, {"$set": {"role": "admin"}}))
    return SimpleNamespace(firm_id=user["firm_id"], user={"email": "input@example.gr", "firm_id": user["firm_id"]})


async def _drive(scope, chunks):
    """Περνά ένα αίτημα από τον φραγμό προς μια εφαρμογή που καταγράφει όσα λαμβάνει."""
    from json_guard import RejectNonFiniteJson
    seen, sent = [], []
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def inner(scope_, receive_, send_):
        seen.append(await receive_())
        await send_({"type": "http.response.start", "status": 204, "headers": []})
        await send_({"type": "http.response.body", "body": b""})

    await RejectNonFiniteJson(inner)(scope, receive, send)
    return seen, sent


def _scope(method="POST", content_type=b"application/json"):
    return {"type": "http", "method": method, "path": "/x", "headers": [(b"content-type", content_type)]}


# ====================================================== 1. Ο φραγμός JSON ====

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_guard_refuses_non_finite_numbers_for_every_method_that_may_carry_a_body(method):
    seen, sent = _run(_drive(_scope(method), [b'{"amount": NaN}']))
    assert seen == [] and sent[0]["status"] == 400
    seen, sent = _run(_drive(_scope(method), [b'{"amount": 12.5, "note": "NaN"}']))
    assert len(seen) == 1 and sent[0]["status"] == 204                       # ο έγκυρος αριθμός περνά αυτούσιος


def test_a_very_deeply_nested_body_is_passed_on_to_the_framework_not_crashed_on():
    """Το `json.loads` ρίχνει `RecursionError` (όχι ValueError) σε βάθος χιλιάδων επιπέδων. Το σώμα δεν έχει NaN: ο φραγμός
    το αφήνει να περάσει και το απαντά η FastAPI, αντί να σκάσει το ίδιο το middleware."""
    from json_guard import has_non_finite_number
    deep = b"[" * 100_000 + b"]" * 100_000
    assert has_non_finite_number(deep) is False
    seen, sent = _run(_drive(_scope("POST"), [deep]))
    assert len(seen) == 1 and seen[0]["body"] == deep and sent[0]["status"] == 204


def test_editing_an_expense_to_nan_is_refused_and_the_expense_is_unchanged(client, office):
    created = client.post("/api/expenses", json={"vendor": "Προμηθευτής", "date": athens_today().isoformat(),
                                                 "amount": 124.0, "vat_amount": 24.0})
    assert created.status_code == 200, created.text
    expense_id = created.json()["id"]
    raw = '{"vendor": "Προμηθευτής", "date": "%s", "amount": NaN, "vat_amount": 0}' % athens_today().isoformat()
    r = client.put(f"/api/expenses/{expense_id}", content=raw.encode(), headers=JSON)
    assert r.status_code == 400, r.text
    from db import db
    stored = _run(db.expenses.find_one({"_id": ObjectId(expense_id)}))
    assert stored["amount"] == 124.0 and stored["vat_amount"] == 24.0


def test_editing_a_salary_to_infinity_is_refused_and_the_employee_is_unchanged(client, office):
    created = client.post("/api/payroll/employees", json={"name": "Εργαζόμενος", "gross_salary": 1000})
    assert created.status_code == 200, created.text
    employee_id = created.json()["id"]
    r = client.put(f"/api/payroll/employees/{employee_id}", content='{"name": "Εργαζόμενος", "gross_salary": Infinity}'.encode(),
                   headers=JSON)
    assert r.status_code == 400, r.text
    from db import db
    assert _run(db.payroll_employees.find_one({"_id": ObjectId(employee_id)}))["gross_salary"] == 1000


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_a_nan_inside_a_free_form_settings_field_is_refused_and_never_stored(client, office, method):
    """Το πεδίο `vat_form_codes` είναι ελεύθερο λεξικό: κανένας έλεγχος του route δεν κοιτά μέσα του, και το NaN θα
    αποθηκευόταν στα ΡΥΘΜΙΣΕΙΣ ολόκληρου του γραφείου. Μόνο ο φραγμός το κόβει — και στα δύο PUT και PATCH."""
    from db import db
    from json_guard import contains_non_finite
    form = client.get("/api/settings").json()                                  # δημιουργεί το έγγραφο ρυθμίσεων του γραφείου
    if method == "PUT":
        form["vat_form_codes"] = {"F2": "@@"}
        raw = json.dumps(form, ensure_ascii=False).replace('"@@"', "NaN").encode()
    else:
        raw = b'{"vat_form_codes": {"F2": NaN}}'
    r = client.request(method, "/api/settings", content=raw, headers=JSON)
    assert r.status_code == 400, r.text
    stored = _run(db.settings.find_one({"firm_id": office.firm_id}))
    assert not contains_non_finite(stored)
    assert (stored.get("vat_form_codes") or {}) == {}


# ============================================= 2. Όρια γραμμής τιμολογίου και Copilot ====

def _invoice(quantity: float, unit_price: float):
    from models import Invoice, InvoiceLineItem
    return Invoice(client_id="c", date=athens_today().isoformat(),
                   line_items=[InvoiceLineItem(description="x", quantity=quantity, unit_price=unit_price, vat_rate=24)])


@pytest.mark.parametrize("quantity, unit_price", [
    (0.001, 2_000_000_000.0),                 # γινόμενο 2.000.000: μικρό, αλλά η τιμή μονάδας είναι παράλογη
    (0.000001, 1_000_000_001.0),              # ένα ευρώ πάνω από το όριο
    (0.5, -3_000_000_000.0),                  # αρνητική τιμή (πιστωτική γραμμή) πάνω από το όριο
])
def test_an_absurd_unit_price_is_refused_even_when_the_quantity_makes_the_line_small(quantity, unit_price):
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(quantity, unit_price))
    assert e.value.status_code == 400 and "υπερβολικά μεγάλο" in e.value.detail


@pytest.mark.parametrize("quantity", [0.0, -1.0, -0.001])
def test_a_line_without_a_positive_quantity_is_refused(quantity):
    """Η ποσότητα μηδέν δεν είναι «μικρή ποσότητα»: είναι γραμμή που δεν χρεώνει τίποτα και δεν διαβιβάζεται στην ΑΑΔΕ."""
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(quantity, 10.0))
    assert e.value.status_code == 400 and "θετική" in e.value.detail


@pytest.mark.parametrize("quantity, unit_price", [
    (2_000_000_000.0, 0.0000001),             # γινόμενο 200: μικρό, αλλά η ποσότητα είναι παράλογη
    (1_000_000_001.0, 0.000001),              # ένα τεμάχιο πάνω από το όριο
])
def test_an_absurd_quantity_is_refused_even_when_the_price_makes_the_line_small(quantity, unit_price):
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(quantity, unit_price))
    assert e.value.status_code == 400 and "υπερβολικά μεγάλο" in e.value.detail


@pytest.mark.parametrize("quantity, unit_price", [
    (1.0, 1_000_000_000.0), (0.001, 1_000_000_000.0), (1_000_000_000.0, 1.0), (1_000.0, 1_000_000.0), (0.5, -1_000_000_000.0)])
def test_a_line_exactly_at_the_limits_is_accepted(quantity, unit_price):
    from routes_invoices import _validate_invoice_payload
    _validate_invoice_payload(_invoice(quantity, unit_price))


def test_the_copilot_accepts_exactly_the_amount_the_invoice_form_accepts_and_not_a_euro_more(office):
    from db import db
    from routes_copilot import _execute_create_invoice
    _run(db.clients.insert_one({"firm_id": office.firm_id, "name": "ΑΛΦΑ ΑΕ", "afm": "", "client_type": "company"}))
    created, reply = _run(_execute_create_invoice({"client_name": "ΑΛΦΑ", "amount": MAX_LINE_VALUE, "vat_rate": 24}, office.user))
    assert created is not None, reply
    assert created.subtotal == float(MAX_LINE_VALUE)
    over, reply = _run(_execute_create_invoice({"client_name": "ΑΛΦΑ", "amount": MAX_LINE_VALUE + 1, "vat_rate": 24}, office.user))
    assert over is None and "ποσό" in reply
    assert _run(db.invoices.count_documents({"firm_id": office.firm_id})) == 1
