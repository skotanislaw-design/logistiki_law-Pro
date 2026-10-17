"""Γύρος 5 (ανασκόπηση 5, WP15α) — ποσά και ημερομηνίες που η είσοδος δεν έπρεπε να δεχτεί.

Ο φραγμός JSON (`json_guard`) βλέπει μόνο τις **σταθερές** `NaN`/`Infinity` και τους αριθμούς που υπερχειλίζουν. Ένα
κείμενο `"NaN"`, `"Infinity"` ή `"1e999"` είναι για αυτόν απλό κείμενο — και η Pydantic το μετατρέπει σε πραγματικό
`nan`/`inf` σε οποιοδήποτε πεδίο `float`. Έτσι έξοδα, γραμμάτια, παράβολα και μισθοδοσία αποθήκευαν μη πραγματικά ποσά,
και ένα πιστωτικό με `amount: "NaN"` έμενε ως πρόχειρο με NaN σύνολο: από εκεί η λίστα τιμολογίων απαντούσε 500 για
ΟΛΟ το γραφείο μέχρι να διορθωθεί το έγγραφο στη βάση.

Εδώ ελέγχεται ό,τι δεν πρέπει να μπαίνει:

1. κάθε αριθμητικό πεδίο εισόδου, με κάθε γραφή μη πεπερασμένου αριθμού ως κείμενο — και ένας έλεγχος πληρότητας από το
   OpenAPI, ώστε ένα νέο πεδίο να μην ξεχαστεί·
2. όρια και πρόσημα (έξοδα, παράβολα, μισθοδοσία, γραμμάτια): δύο πεπερασμένα `1e308` δεν πρέπει να ρίχνουν το dashboard·
3. ημερομηνίες μόνο της μορφής ΕΕΕΕ-ΜΜ-ΗΗ — η `date.fromisoformat` της Python 3.11 δέχεται και `20260612`, `2026-W24-5`·
4. στρογγυλοποίηση της παρακράτησης μισού προς τα πάνω στο λεπτό (η `round()` των float έδινε 45,07 αντί 45,08)·
5. το ΑΦΜ αποθηκεύεται χωρίς κενά (ελεγχόταν μετά το `strip()` αλλά αποθηκευόταν όπως πληκτρολογήθηκε).
"""
import asyncio
import re
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today

# Όλες οι γραφές που η Python (και άρα η Pydantic) διαβάζει ως μη πεπερασμένο αριθμό.
SPELLINGS = ["NaN", "nan", "NAN", "Infinity", "-Infinity", "inf", "-inf", "+inf", "1e999", "-1e999", "1E400"]


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
                    json={"email": "money@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "money@example.gr"}))
    _run(db.users.update_one({"email": "money@example.gr"}, {"$set": {"role": "admin"}}))
    company = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                                "email": "alpha@example.gr"}).json()["id"]
    return SimpleNamespace(company=company, firm_id=user["firm_id"])


def _today() -> str:
    return athens_today().isoformat()


def _count(collection: str) -> int:
    from db import db
    return _run(db[collection].count_documents({}))


def _stored(collection: str, doc_id: str) -> dict:
    from db import db
    return _run(db[collection].find_one({"_id": ObjectId(doc_id)}))


def _assert_refused(response, message_part: str | None = None):
    assert response.status_code == 400, response.text
    if message_part:
        assert message_part in str(response.json()["detail"]), response.text


def _assert_non_finite_refused(response):
    """Άρνηση του αριθμού: 400 με το ίδιο μήνυμα που δίνει ο φραγμός JSON για τις σταθερές `NaN`/`Infinity`."""
    from json_guard import MESSAGE
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == MESSAGE, response.text


def _expense(**over):
    body = {"vendor": "Προμηθευτής", "date": _today(), "amount": 124.0, "vat_amount": 24.0}
    body.update(over)
    return body


def _grammatio(**over):
    body = {"court_type": "Ειρηνοδικείο", "procedure": "Μικροδιαφορές", "reference_amount": 64.0}
    body.update(over)
    return body


def _paravolo(**over):
    body = {"category": "Λοιπά παράβολα", "amount": 50.0}
    body.update(over)
    return body


def _employee(**over):
    body = {"name": "Εργαζόμενος", "gross_salary": 1500.0}
    body.update(over)
    return body


def _invoice(client_id, **over):
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
    body.update(over)
    return body


def _line(client_id, **over):
    body = _invoice(client_id)
    body["line_items"][0].update(over)
    return body


# ===================================== 1. Μη πεπερασμένοι αριθμοί γραμμένοι ως κείμενο ====

# (όνομα, αριθμητικό πεδίο όπως το δηλώνει το OpenAPI, συλλογή, σώμα με την τιμή στο πεδίο)
FIELDS = [
    ("expense.amount", "amount", "expenses", lambda o, v: _expense(amount=v)),
    ("expense.vat_amount", "vat_amount", "expenses", lambda o, v: _expense(vat_amount=v)),
    ("expense.vat_deductible_pct", "vat_deductible_pct", "expenses", lambda o, v: _expense(vat_deductible_pct=v)),
    ("grammatio.reference_amount", "reference_amount", "grammatia", lambda o, v: _grammatio(reference_amount=v)),
    ("grammatio.contributions_amount", "contributions_amount", "grammatia",
     lambda o, v: _grammatio(contributions_amount=v)),
    ("grammatio.vat_amount", "vat_amount", "grammatia", lambda o, v: _grammatio(vat_amount=v)),
    ("grammatio.total", "total", "grammatia", lambda o, v: _grammatio(total=v)),
    ("paravolo.amount", "amount", "paravola", lambda o, v: _paravolo(amount=v)),
    ("employee.gross_salary", "gross_salary", "payroll_employees", lambda o, v: _employee(gross_salary=v)),
    ("invoice.line.quantity", "line_items[].quantity", "invoices", lambda o, v: _line(o.company, quantity=v)),
    ("invoice.line.unit_price", "line_items[].unit_price", "invoices", lambda o, v: _line(o.company, unit_price=v)),
    ("invoice.subtotal", "subtotal", "invoices", lambda o, v: _invoice(o.company, subtotal=v)),
    ("invoice.vat_amount", "vat_amount", "invoices", lambda o, v: _invoice(o.company, vat_amount=v)),
    ("invoice.total", "total", "invoices", lambda o, v: _invoice(o.company, total=v)),
    ("invoice.trust_applied", "trust_applied", "invoices", lambda o, v: _invoice(o.company, trust_applied=v)),
    ("invoice.withholding_amount", "withholding_amount", "invoices",
     lambda o, v: _invoice(o.company, withholding_amount=v)),
]

# Η διαδρομή δημιουργίας, η διαδρομή ενημέρωσης (όπως τη γράφει το OpenAPI) και ο δημιουργός ενός έγκυρου εγγράφου.
RESOURCES = {
    "expense": ("/api/expenses", "/api/expenses/{expense_id}", lambda o: _expense()),
    "grammatio": ("/api/grammatia", "/api/grammatia/{gid}", lambda o: _grammatio()),
    "paravolo": ("/api/paravola", "/api/paravola/{pid}", lambda o: _paravolo()),
    "employee": ("/api/payroll/employees", "/api/payroll/employees/{employee_id}", lambda o: _employee()),
    "invoice": ("/api/invoices", "/api/invoices/{invoice_id}", lambda o: _invoice(o.company)),
}

# Πεδία που ελέγχονται σε δικές τους δοκιμές παρακάτω (χρειάζονται προετοιμασία): πιστωτικό, πελατολογικός, ρυθμίσεις.
SEPARATELY_COVERED = {
    ("POST", "/api/invoices/{invoice_id}/credit-note", "amount"),
    ("POST", "/api/trust/transactions", "amount"),
    ("PUT", "/api/settings", "withholding_rate"),
    ("PUT", "/api/settings", "withholding_min_net"),
}


def _resource_of(name: str) -> str:
    return name.split(".", 1)[0]


@pytest.mark.parametrize("value", SPELLINGS)
@pytest.mark.parametrize("name,field,collection,build", FIELDS, ids=[f[0] for f in FIELDS])
def test_a_non_finite_number_written_as_text_is_refused_on_create(client, office, name, field, collection, build, value):
    create_path, _update_path, _valid = RESOURCES[_resource_of(name)]
    response = client.post(create_path, json=build(office, value))
    assert response.status_code in (400, 422), response.text            # 422 = δεν διαβάζεται καν ως αριθμός
    if response.status_code == 400:
        _assert_non_finite_refused(response)
    assert _count(collection) == 0


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf", "1e999"])
@pytest.mark.parametrize("name,field,collection,build", FIELDS, ids=[f[0] for f in FIELDS])
def test_a_non_finite_number_written_as_text_is_refused_on_update_and_the_stored_document_is_untouched(
        client, office, name, field, collection, build, value):
    create_path, update_path, valid = RESOURCES[_resource_of(name)]
    created = client.post(create_path, json=valid(office))
    assert created.status_code == 200, created.text
    doc_id = created.json()["id"]
    before = _stored(collection, doc_id)
    response = client.put(re.sub(r"\{[a-z_]+\}", doc_id, update_path), json=build(office, value))
    _assert_non_finite_refused(response)
    assert _stored(collection, doc_id) == before


def _issued_and_transmitted(client, office, net=1000.0) -> str:
    created = client.post("/api/invoices", json=_line(office.company, unit_price=net)).json()
    assert client.post(f"/api/invoices/{created['id']}/issue").status_code == 200
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(created["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400000000000123"}}))
    return created["id"]


@pytest.mark.parametrize("value", ["NaN", "nan", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_a_credit_note_with_a_non_finite_amount_is_refused_and_leaves_no_draft_behind(client, office, value):
    """Το πιστωτικό με `amount: "NaN"` έμενε ως πρόχειρο με NaN σύνολο, δεμένο στο αρχικό, και η λίστα τιμολογίων του
    γραφείου απαντούσε 500."""
    invoice_id = _issued_and_transmitted(client, office)
    before = _count("invoices")
    response = client.post(f"/api/invoices/{invoice_id}/credit-note", json={"amount": value, "reason": "Λάθος"})
    _assert_non_finite_refused(response)
    assert _count("invoices") == before                        # κανένα πρόχειρο πιστωτικό
    assert _stored("invoices", invoice_id).get("credit_note_id") is None
    assert client.get("/api/invoices").status_code == 200       # η λίστα του γραφείου ζει
    assert client.get("/api/dashboard/summary").status_code == 200
    ok = client.post(f"/api/invoices/{invoice_id}/credit-note", json={"amount": 100.0, "reason": "Μερική"})
    assert ok.status_code == 200, ok.text                       # και το σωστό ποσό εξακολουθεί να εκδίδεται


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf", "1e999"])
def test_a_trust_movement_with_a_non_finite_amount_is_refused(client, office, value):
    body = {"client_id": office.company, "type": "deposit", "amount": value, "date": _today()}
    _assert_non_finite_refused(client.post("/api/trust/transactions", json=body))
    assert _count("trust_transactions") == 0


@pytest.mark.parametrize("field", ["withholding_rate", "withholding_min_net"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-inf", "1e999"])
def test_a_non_finite_withholding_setting_written_as_text_is_refused_by_put(client, office, field, value):
    form = client.get("/api/settings").json()
    form[field] = value
    _assert_non_finite_refused(client.put("/api/settings", json=form))
    assert client.get("/api/settings").json()[field] == (0.2 if field == "withholding_rate" else 300.0)


def _numeric_request_fields() -> set[tuple[str, str, str]]:
    """(μέθοδος, διαδρομή, πεδίο) για κάθε αριθμητικό πεδίο σώματος JSON που δηλώνει το OpenAPI της εφαρμογής."""
    import server
    spec = server.app.openapi()
    schemas = spec["components"]["schemas"]
    found: set[tuple[str, str, str]] = set()

    def walk(schema, prefix, out, stack=()):
        if "$ref" in schema:
            name = schema["$ref"].split("/")[-1]
            if name not in stack:
                walk(schemas[name], prefix, out, stack + (name,))
            return
        if schema.get("type") == "number":
            out.add(prefix)
            return
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in schema.get(key, []):
                walk(sub, prefix, out, stack)
        if schema.get("type") == "array":
            walk(schema.get("items", {}), prefix + "[]", out, stack)
        for name, sub in (schema.get("properties") or {}).items():
            walk(sub, (prefix + "." if prefix else "") + name, out, stack)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            walk(extra, prefix + "{}", out, stack)

    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in ("post", "put", "patch", "delete"):
                continue
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
            if not body:
                continue
            fields: set[str] = set()
            walk(body["schema"], "", fields)
            found.update((method.upper(), path, f) for f in fields)
    return found


def test_every_numeric_input_field_of_the_api_is_covered_by_the_non_finite_checks():
    """Αν προστεθεί νέο αριθμητικό πεδίο εισόδου (ή νέο endpoint με αριθμό), αυτός ο έλεγχος αποτυγχάνει μέχρι να μπει
    στον πίνακα παραπάνω — αλλιώς ένα πεδίο που ξεχάστηκε θα δεχόταν ξανά `"NaN"`."""
    covered = set(SEPARATELY_COVERED)
    for name, field, _collection, _build in FIELDS:
        create_path, update_path, _valid = RESOURCES[_resource_of(name)]
        covered.add(("POST", create_path, field))
        covered.add(("PUT", update_path, field))
    assert _numeric_request_fields() == covered


# ======================================================== 2. Όρια και πρόσημα ====

@pytest.mark.parametrize("amount,vat", [(1e308, 0.0), (-1e308, 0.0), (1_000_000_000.01, 0.0), (124.0, 1e308)])
def test_an_absurdly_large_expense_is_refused(client, office, amount, vat):
    _assert_refused(client.post("/api/expenses", json=_expense(amount=amount, vat_amount=vat)))
    assert _count("expenses") == 0


def test_two_expenses_at_the_limit_cannot_overflow_the_dashboard(client, office):
    """Δύο πεπερασμένες τιμές 1e308 αθροίζονταν σε `inf` και το dashboard απαντούσε 500."""
    assert client.post("/api/expenses", json=_expense(amount=1e308, vat_amount=0)).status_code == 400
    for _ in range(2):
        assert client.post("/api/expenses", json=_expense(amount=1_000_000_000, vat_amount=0)).status_code == 200
    assert client.get("/api/dashboard/summary").status_code == 200
    assert client.get("/api/expenses").status_code == 200


@pytest.mark.parametrize("amount,vat", [(100.0, 124.0), (100.0, -24.0), (-100.0, 24.0), (100.0, 100.01), (-124.0, -124.01)])
def test_an_expense_whose_vat_is_larger_than_the_amount_or_of_the_opposite_sign_is_refused(client, office, amount, vat):
    """ΦΠΑ μεγαλύτερο από το ποσό δίνει αρνητική καθαρή δαπάνη και φουσκώνει το ΦΠΑ εισροών στο Φ2."""
    _assert_refused(client.post("/api/expenses", json=_expense(amount=amount, vat_amount=vat)))
    assert _count("expenses") == 0


@pytest.mark.parametrize("amount,vat", [(124.0, 24.0), (124.0, 0.0), (0.0, 0.0), (100.0, 100.0), (-124.0, -24.0)])
def test_ordinary_expenses_and_supplier_credits_are_accepted(client, office, amount, vat):
    """Το αρνητικό ποσό με αρνητικό ΦΠΑ είναι πιστωτικό προμηθευτή: έγκυρο."""
    assert client.post("/api/expenses", json=_expense(amount=amount, vat_amount=vat)).status_code == 200


def test_an_expense_update_obeys_the_same_limits(client, office):
    created = client.post("/api/expenses", json=_expense()).json()
    before = _stored("expenses", created["id"])
    _assert_refused(client.put(f"/api/expenses/{created['id']}", json=_expense(amount=100.0, vat_amount=124.0)))
    _assert_refused(client.put(f"/api/expenses/{created['id']}", json=_expense(amount=1e308)))
    assert _stored("expenses", created["id"]) == before


@pytest.mark.parametrize("amount", [-0.01, -50.0, 1_000_000_000.01, 1e308])
def test_a_paravolo_needs_an_amount_between_zero_and_the_limit(client, office, amount):
    _assert_refused(client.post("/api/paravola", json=_paravolo(amount=amount)))
    assert _count("paravola") == 0


def test_a_paravolo_update_obeys_the_same_limits(client, office):
    created = client.post("/api/paravola", json=_paravolo()).json()
    _assert_refused(client.put(f"/api/paravola/{created['id']}", json=_paravolo(amount=-5.0)))
    assert _stored("paravola", created["id"])["amount"] == 50.0


@pytest.mark.parametrize("salary", [-0.01, -1500.0, 1_000_000_000.01, 1e308])
def test_an_employee_needs_a_salary_between_zero_and_the_limit(client, office, salary):
    _assert_refused(client.post("/api/payroll/employees", json=_employee(gross_salary=salary)))
    assert _count("payroll_employees") == 0


def test_an_employee_update_obeys_the_same_limits(client, office):
    created = client.post("/api/payroll/employees", json=_employee()).json()
    _assert_refused(client.put(f"/api/payroll/employees/{created['id']}", json=_employee(gross_salary=-1.0)))
    assert _stored("payroll_employees", created["id"])["gross_salary"] == 1500.0


@pytest.mark.parametrize("field", ["reference_amount", "contributions_amount", "vat_amount"])
def test_a_grammatio_amount_over_the_limit_is_refused(client, office, field):
    _assert_refused(client.post("/api/grammatia", json=_grammatio(**{field: 1_000_000_000.01})))
    assert _count("grammatia") == 0


def test_the_amounts_at_the_limits_are_stored_as_sent(client, office):
    """Οι νέοι κανόνες δεν αλλοιώνουν τα έγκυρα ποσά: ακριβώς στο όριο και το μηδέν περνούν."""
    expense = client.post("/api/expenses", json=_expense(amount=1_000_000_000, vat_amount=0)).json()
    assert expense["amount"] == 1_000_000_000
    employee = client.post("/api/payroll/employees", json=_employee(gross_salary=0)).json()
    assert employee["gross_salary"] == 0
    assert client.post("/api/paravola", json=_paravolo(amount=0)).status_code == 200


# ================================================================ 3. Ημερομηνίες ====

BAD_DATES = [
    "20260612",                  # η fromisoformat της Python 3.11 τη δέχεται
    "2026-W24-5",                # εβδομαδιαία μορφή ISO
    "2026-06-12T10:00:00",       # ημερομηνία-ώρα: τα φίλτρα του ΦΠΑ συγκρίνουν κείμενα
    " 2026-06-12",
    "2026-06-12 ",
    "2026-06-12\n",
    "2026-6-12",
    "2026/06/12",
    "12-06-2026",
    "2026-02-30",
    "2026-13-01",
    "２０２６-０６-１２",          # ψηφία πλήρους πλάτους (Unicode)
    "٢٠٢٦-٠٦-١٢",               # αραβικά-ινδικά ψηφία
    "",
    "σήμερα",
]


def test_the_date_check_accepts_only_real_iso_calendar_dates():
    from tax_utils import valid_iso_date
    for good in ("2026-06-12", "2024-02-29", "2026-12-31", "0001-01-01", "9999-12-31"):
        assert valid_iso_date(good) is True, good
    for bad in BAD_DATES + ["2025-02-29", None, 20260612, 12.5, b"2026-06-12", ["2026-06-12"]]:
        assert valid_iso_date(bad) is False, repr(bad)


@pytest.mark.parametrize("bad", BAD_DATES)
def test_an_invoice_with_a_date_that_is_not_plain_iso_is_refused_on_create_and_update(client, office, bad):
    """Το `20260612` αποθηκευόταν όπως πληκτρολογήθηκε· οι συγκρίσεις κειμένου των περιόδων ΦΠΑ και του dashboard
    το έβαζαν μετά από κάθε «2026-…» και το τιμολόγιο έλειπε από τη δήλωση."""
    _assert_refused(client.post("/api/invoices", json=_invoice(office.company, date=bad)), "ημερομηνία")
    assert _count("invoices") == 0
    draft = client.post("/api/invoices", json=_invoice(office.company)).json()
    _assert_refused(client.put(f"/api/invoices/{draft['id']}", json=_invoice(office.company, date=bad)), "ημερομηνία")
    assert _stored("invoices", draft["id"])["date"] == _today()


@pytest.mark.parametrize("bad", BAD_DATES)
def test_an_expense_with_a_date_that_is_not_plain_iso_is_refused_on_create_and_update(client, office, bad):
    _assert_refused(client.post("/api/expenses", json=_expense(date=bad)), "ημερομηνία")
    assert _count("expenses") == 0
    created = client.post("/api/expenses", json=_expense()).json()
    _assert_refused(client.put(f"/api/expenses/{created['id']}", json=_expense(date=bad)), "ημερομηνία")
    assert _stored("expenses", created["id"])["date"] == _today()


def test_a_plain_iso_date_is_stored_exactly_as_sent(client, office):
    assert client.post("/api/invoices", json=_invoice(office.company, date="2026-06-12")).json()["date"] == "2026-06-12"
    assert client.post("/api/expenses", json=_expense(date="2026-06-12")).json()["date"] == "2026-06-12"


# ============================================== 4. Στρογγυλοποίηση παρακράτησης ====

@pytest.mark.parametrize("net,rate,expected", [
    (300.50, 0.15, 45.08),       # 45,075 → 45,08 (η round() των float έδινε 45,07)
    (300.70, 0.15, 45.11),       # 45,105
    (300.90, 0.15, 45.14),       # 45,135
    (1000.0, 0.20, 200.0),
    (333.33, 0.20, 66.67),       # 66,666
    (300.50, 0.125, 37.56),      # 37,5625
    (301.01, 0.03, 9.03),        # 9,0303
    (2000.0, 0.15, 300.0),
    (1234.56, 0.20, 246.91),     # 246,912
])
def test_withholding_is_rounded_half_up_to_the_cent(net, rate, expected):
    from mydata_client import withholding_for
    settings = {"withholding_enabled": True, "withholding_rate": rate, "withholding_min_net": 0}
    assert withholding_for({}, settings, "2.1", net) == expected


def test_an_explicit_withholding_is_rounded_half_up_too():
    from mydata_client import withholding_for
    assert withholding_for({"withholding_amount": 45.075}, {}, "2.1", 300.5) == 45.08


def test_the_payable_amount_uses_the_half_up_withholding():
    """Το ποσό που περιμένει ο λογιστής: 300,50 € + ΦΠΑ 24 % = 372,62 € μείον παρακράτηση 15 % επί 300,50 = 45,08 €."""
    from invoice_services import payable_amount
    invoice = {"total": 372.62, "subtotal": 300.5, "invoice_type": "2.1", "status": "issued"}
    settings = {"withholding_enabled": True, "withholding_rate": 0.15, "withholding_min_net": 0}
    payable, withheld = _run(payable_amount("f", invoice, settings))
    assert (payable, withheld) == (327.54, 45.08)


# ================================================================ 5. ΑΦΜ χωρίς κενά ====

def test_an_afm_with_surrounding_spaces_is_stored_trimmed_for_clients(client, office):
    created = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": " 094259216 ", "client_type": "company"})
    assert created.status_code == 200, created.text
    assert created.json()["afm"] == "094259216"
    assert _stored("clients", created.json()["id"])["afm"] == "094259216"
    updated = client.put(f"/api/clients/{created.json()['id']}",
                         json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "\t094259216\n", "client_type": "company"})
    assert updated.status_code == 200 and updated.json()["afm"] == "094259216"
    assert _stored("clients", created.json()["id"])["afm"] == "094259216"


def test_a_foreign_client_afm_is_trimmed_too(client, office):
    created = client.post("/api/clients", json={"name": "Foreign Ltd", "afm": "  DE123456789 ", "country": "DE",
                                                "client_type": "company"})
    assert created.status_code == 200 and created.json()["afm"] == "DE123456789"


def test_the_firm_afm_is_stored_trimmed_by_put_and_patch(client, office):
    form = client.get("/api/settings").json()
    form["afm"] = " 094259216 "
    assert client.put("/api/settings", json=form).status_code == 200
    assert client.get("/api/settings").json()["afm"] == "094259216"
    assert client.patch("/api/settings", json={"afm": "  123456783\t"}).status_code == 200
    assert client.get("/api/settings").json()["afm"] == "123456783"
