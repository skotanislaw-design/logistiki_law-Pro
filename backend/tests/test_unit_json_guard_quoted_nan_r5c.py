"""ΔΙΟΡΘΩΘΗΚΕ (εύρημα του ελέγχου μεταλλάξεων) (έλεγχος μεταλλάξεων, περιοχή payments_caps) — ο φραγμός NaN/άπειρων παρακάμπτεται με εισαγωγικά.

`json_guard.RejectNonFiniteJson` ψάχνει στο σώμα JSON τις σταθερές `NaN` / `Infinity` και τους αριθμούς που
υπερχειλίζουν (`1e999`). Ένα **κείμενο** JSON — `"amount": "NaN"` — δεν το βλέπει (σωστά: το «NaN» επιτρέπεται μέσα σε
κείμενο). Όμως η Pydantic, σε χαλαρή λειτουργία, μετατρέπει το κείμενο `"NaN"` / `"Infinity"` / `"1e999"` σε `float`
(`float('nan')`, `inf`) για κάθε πεδίο `float` των μοντέλων — και τα μοντέλα δεν έχουν `allow_inf_nan=False`.
Τα routes των εξόδων, της μισθοδοσίας, των παραβόλων και των γραμματίων δεν ελέγχουν την τιμή (ο έλεγχος που υπάρχει στις
γραμμές τιμολογίου, `is_finite_number`, δεν υπάρχει εκεί). Αποτέλεσμα: το NaN/inf αποθηκεύεται και

* το ίδιο το αίτημα απαντά 500 (η απάντηση δεν μπορεί να γίνει JSON),
* από εκεί και πέρα κάθε `GET /api/expenses`, `GET /api/dashboard/summary`, `GET /api/analytics/cashflow`,
  `GET /api/payroll/employees`, `GET /api/paravola`, `GET /api/grammatia` απαντά 500 για ΟΛΟ το γραφείο,

δηλαδή ακριβώς η βλάβη που περιγράφει η τεκμηρίωση του φραγμού και που ο φραγμός υποτίθεται ότι αποκλείει
(«ανεπιτρέπεται άκυρος αριθμός να φτάσει στη βάση»).

Αναμενόμενη συμπεριφορά: 400/422 και τίποτα αποθηκευμένο. (Η διόρθωση ανήκει στα μοντέλα — `allow_inf_nan=False` στις
ρυθμίσεις του `BaseDocument` — ή σε έλεγχο ανά route· όχι στον φραγμό, που δεν πρέπει να απορρίπτει κείμενο.)

"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from db import db

JSON = {"content-type": "application/json"}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    # raise_server_exceptions=False: το 500 του server φαίνεται ως απάντηση, όπως θα το έβλεπε ο browser.
    with TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        r = c.post("/api/auth/register", json={"email": "quoted@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def _stored_non_finite(collection: str, field: str) -> list:
    import math
    docs = _run(db[collection].find({}).to_list(100))
    return [d[field] for d in docs if isinstance(d.get(field), float) and not math.isfinite(d[field])]


CASES = [
    ("/api/expenses", {"vendor": "Προμηθευτής", "date": "2026-10-01", "amount": "@@", "vat_amount": 0}, "expenses", "amount"),
    ("/api/payroll/employees", {"name": "Εργαζόμενος", "gross_salary": "@@"}, "payroll_employees", "gross_salary"),
    ("/api/paravola", {"category": "Παράβολο", "amount": "@@"}, "paravola", "amount"),
    ("/api/grammatia", {"court_type": "Ειρηνοδικείο", "reference_amount": "@@"}, "grammatia", "reference_amount"),
]


# Η γραμμή (grammatia, -Infinity) περνά ήδη: το route απορρίπτει κάθε αρνητική αξία (`value < 0`)· οι υπόλοιπες 15 αποτυγχάνουν.
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
@pytest.mark.parametrize("path, body, collection, field", CASES, ids=[c[0] for c in CASES])
def test_a_quoted_non_finite_number_is_refused_and_never_stored(client, path, body, collection, field, token):
    raw = json.dumps(body, ensure_ascii=False).replace('"@@"', json.dumps(token))       # ΚΕΙΜΕΝΟ JSON: "NaN", όχι NaN
    r = client.post(path, content=raw.encode(), headers=JSON)
    assert r.status_code in (400, 422), f"{path} δέχτηκε {token!r}: {r.status_code}"
    assert _stored_non_finite(collection, field) == [], f"αποθηκεύτηκε {token!r} στο {collection}.{field}"


def test_one_quoted_nan_does_not_break_the_expense_list_the_dashboard_and_the_forecast_of_the_whole_office(client):
    client.post("/api/expenses", json={"vendor": "Κανονικό", "date": "2026-10-01", "amount": 124.0, "vat_amount": 24.0})
    raw = json.dumps({"vendor": "Προμηθευτής", "date": "2026-10-01", "amount": "NaN", "vat_amount": 0})
    client.post("/api/expenses", content=raw.encode(), headers=JSON)
    for path in ("/api/expenses", "/api/dashboard/summary", "/api/analytics/cashflow"):
        assert client.get(path).status_code == 200, f"{path} σπάει για όλο το γραφείο μετά από ένα «NaN» σε εισαγωγικά"
