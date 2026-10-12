"""Γύρος 5 — δύο μικρά ευρήματα που έκλειναν «μισά»: το ΑΦΜ εργαζομένου (#45) και η κατηγορία παρακράτησης (#60).

#45: τα `POST`/`PUT /api/payroll/employees` έγραφαν οποιοδήποτε ΑΦΜ. Ο έλεγχος του ΑΦΜ πελάτη (#39) υπήρχε, αλλά δεν
εφαρμόστηκε εδώ — το PR που τον έφερε δήλωνε ότι «δεν υπάρχει μοντέλο Employee», επειδή η αναζήτηση έγινε με λάθος όνομα.

#60: το προεπιλεγμένο του μοντέλου έγινε «3», όμως η κατηγορία (που διαβιβάζεται στο myDATA ως `taxCategory`) δεν
ελεγχόταν πουθενά: κάθε τιμή περνούσε και αποθηκευόταν· και δεν υπήρχε πεδίο στην οθόνη.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _admin(client, email="settings@example.gr"):
    r = client.post("/api/auth/register", json={"email": email, "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    _run(db.users.update_one({"email": email}, {"$set": {"role": "admin"}}))


# ==================================================================== #45 ΑΦΜ εργαζομένου ====

def _employee(**over):
    body = {"name": "Μαρία Παπαδοπούλου", "afm": "123456783", "gross_salary": 1500.0}
    body.update(over)
    return body


def _stored_employees():
    from db import db
    return _run(db.payroll_employees.find({}).to_list(100))


def test_employee_with_a_wrong_check_digit_is_rejected(client):
    _admin(client)
    r = client.post("/api/payroll/employees", json=_employee(afm="123456789"))
    assert r.status_code == 400
    assert "ΑΦΜ εργαζομένου" in r.json()["detail"]
    assert "ψηφίο ελέγχου" in r.json()["detail"]
    assert _stored_employees() == []


@pytest.mark.parametrize("afm,fragment", [
    ("12345678", "8 ψηφία"),
    ("1234567890", "10 ψηφία"),
    ("12345678A", "μη αριθμητικούς"),
])
def test_employee_afm_errors_say_what_is_wrong(client, afm, fragment):
    _admin(client)
    r = client.post("/api/payroll/employees", json=_employee(afm=afm))
    assert r.status_code == 400
    assert fragment in r.json()["detail"]
    assert _stored_employees() == []


def test_employee_with_a_valid_afm_is_stored_trimmed(client):
    _admin(client)
    r = client.post("/api/payroll/employees", json=_employee(afm=" 123456783 "))
    assert r.status_code == 200, r.text
    assert r.json()["afm"] == "123456783"
    assert _stored_employees()[0]["afm"] == "123456783"


def test_employee_without_afm_is_still_accepted(client):
    """Όπως στους πελάτες: το κενό δεν είναι λάθος ΑΦΜ (ο εργαζόμενος μπορεί να μην έχει καταχωρηθεί ακόμη)."""
    _admin(client)
    r = client.post("/api/payroll/employees", json=_employee(afm=""))
    assert r.status_code == 200, r.text


def test_updating_an_employee_validates_the_afm_too(client):
    _admin(client)
    eid = client.post("/api/payroll/employees", json=_employee()).json()["id"]
    r = client.put(f"/api/payroll/employees/{eid}", json=_employee(afm="123456789", gross_salary=1800.0))
    assert r.status_code == 400
    assert "ΑΦΜ εργαζομένου" in r.json()["detail"]
    stored = _stored_employees()[0]
    assert stored["afm"] == "123456783" and stored["gross_salary"] == 1500.0, "ο έλεγχος προηγείται της εγγραφής"

    ok = client.put(f"/api/payroll/employees/{eid}", json=_employee(afm="123456783", gross_salary=1800.0))
    assert ok.status_code == 200, ok.text
    assert _stored_employees()[0]["gross_salary"] == 1800.0


def test_a_payroll_run_still_works_for_a_validated_employee(client):
    _admin(client)
    eid = client.post("/api/payroll/employees", json=_employee()).json()["id"]
    r = client.post("/api/payroll/run", json={"employee_id": eid, "period": "2026-09"})
    assert r.status_code == 200, r.text
    assert r.json()["employee_name"] == "Μαρία Παπαδοπούλου"


# ==================================================================== #60 Κατηγορία παρακράτησης ====

def test_a_new_office_defaults_to_category_three(client):
    _admin(client)
    assert client.get("/api/settings").json()["withholding_category"] == "3"


@pytest.mark.parametrize("category", ["2", "3", "10"])
def test_the_three_mydata_categories_are_accepted(client, category):
    _admin(client)
    r = client.patch("/api/settings", json={"withholding_category": category})
    assert r.status_code == 200, r.text
    assert r.json()["withholding_category"] == category
    form = client.get("/api/settings").json()
    form["withholding_category"] = category
    assert client.put("/api/settings", json=form).status_code == 200


@pytest.mark.parametrize("category", ["7", "", "abc", "03", " 3", "2.0"])
def test_any_other_category_is_rejected_on_patch(client, category):
    _admin(client)
    before = client.get("/api/settings").json()["withholding_category"]
    r = client.patch("/api/settings", json={"withholding_category": category})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "κατηγορία παρακράτησης" in detail.lower() or "Κατηγορία παρακράτησης" in detail
    assert "2, 3 ή 10" in detail
    assert client.get("/api/settings").json()["withholding_category"] == before


def test_an_invalid_category_is_rejected_on_put_too(client):
    _admin(client)
    form = client.get("/api/settings").json()
    form["withholding_category"] = "7"
    r = client.put("/api/settings", json=form)
    assert r.status_code == 400
    assert "2, 3 ή 10" in r.json()["detail"]
    assert client.get("/api/settings").json()["withholding_category"] == "3"


def test_an_existing_office_keeps_category_two_until_it_decides(client):
    """Δεν γίνεται σιωπηλή μετατροπή: η κατηγορία είναι νομική απόφαση του γραφείου."""
    _admin(client)
    assert client.patch("/api/settings", json={"withholding_category": "2"}).status_code == 200
    # Ασχετη αλλαγή ρύθμισης (όπως από τη σελίδα Autopilot) δεν αγγίζει την κατηγορία
    assert client.patch("/api/settings", json={"withholding_enabled": True}).status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["withholding_category"] == "2" and settings["withholding_enabled"] is True
    assert client.put("/api/settings", json=settings).status_code == 200      # η οθόνη στέλνει πίσω ό,τι φόρτωσε
    assert client.get("/api/settings").json()["withholding_category"] == "2"


def test_a_stale_invalid_category_in_the_database_does_not_block_unrelated_patches(client):
    """Παλιά εγγραφή με τιμή εκτός λίστας (πριν τον έλεγχο): οι ασχετες αλλαγές από το Autopilot δεν σπάνε."""
    _admin(client)
    client.get("/api/settings")
    from db import db
    _run(db.settings.update_many({}, {"$set": {"withholding_category": "7"}}))
    r = client.patch("/api/settings", json={"auto_reminders": False})
    assert r.status_code == 200, r.text
    # …αλλά η σωστή τιμή πρέπει να μπορεί να οριστεί
    assert client.patch("/api/settings", json={"withholding_category": "3"}).status_code == 200


def test_the_rate_must_be_a_fraction_not_a_percentage(client):
    """Το 20 (αντί για 0,20) θα έβγαζε παρακράτηση 20 φορές την καθαρή αξία."""
    _admin(client)
    r = client.patch("/api/settings", json={"withholding_rate": 20})
    assert r.status_code == 400
    assert "ποσοστό" in r.json()["detail"]
    assert client.get("/api/settings").json()["withholding_rate"] == 0.2
    assert client.patch("/api/settings", json={"withholding_rate": 0.15}).status_code == 200
    assert client.get("/api/settings").json()["withholding_rate"] == 0.15


def test_the_threshold_cannot_be_negative(client):
    _admin(client)
    r = client.patch("/api/settings", json={"withholding_min_net": -1})
    assert r.status_code == 400
    assert client.patch("/api/settings", json={"withholding_min_net": 0}).status_code == 200


def test_allowed_categories_are_defined_next_to_the_mydata_mapping():
    """Μία πηγή αλήθειας: ό,τι δέχονται οι Ρυθμίσεις είναι ό,τι τεκμηριώνει ο κώδικας διαβίβασης (η οθόνη το αντιγράφει
    — βλ. frontend/src/pages/__tests__/Settings.test.jsx)· και η προεπιλογή ανήκει στη λίστα."""
    from mydata_client import WITHHOLDING_CATEGORIES, WITHHOLDING_CATEGORY_SERVICES
    from models import Settings
    assert set(WITHHOLDING_CATEGORIES) == {"2", "3", "10"}
    assert WITHHOLDING_CATEGORY_SERVICES in WITHHOLDING_CATEGORIES
    assert Settings().withholding_category == WITHHOLDING_CATEGORY_SERVICES


def test_the_screen_offers_exactly_the_categories_the_server_accepts():
    """Η οθόνη (frontend/src/lib/withholding.js) αντιγράφει τη λίστα του server· αν αποκλίνουν, μια επιλογή θα
    αποθηκευόταν με σφάλμα — ή μια έγκυρη κατηγορία δεν θα προσφερόταν ποτέ."""
    import pathlib
    import re
    from mydata_client import WITHHOLDING_CATEGORIES
    text = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "withholding.js").read_text(encoding="utf-8")
    values = re.findall(r'value:\s*"(\d+)"', text)
    assert sorted(values, key=int) == sorted(WITHHOLDING_CATEGORIES, key=int)
    proposed = {v: float(r) for v, r in re.findall(r'value:\s*"(\d+)"[^}]*?rate:\s*([0-9.]+)', text)}
    for value, label in WITHHOLDING_CATEGORIES.items():
        assert proposed[value] == int(re.search(r"(\d+)%", label).group(1)) / 100, (value, label)
