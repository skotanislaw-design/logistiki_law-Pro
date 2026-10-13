"""ΔΙΟΡΘΩΘΗΚΕ (εύρημα του ελέγχου μεταλλάξεων) (χαμηλής σοβαρότητας) — το ΑΦΜ ελέγχεται μετά το `strip()` αλλά αποθηκεύεται όπως στάλθηκε.

`routes_clients._validate_client_payload` και `routes_settings._validate_firm_afm` κάνουν `(afm or "").strip()` πριν τον έλεγχο,
δηλαδή δέχονται «123456783 » ή «\\u00a0123456783» ως έγκυρο — αλλά αποθηκεύουν το πεδίο ΑΥΤΟΥΣΙΟ. Η μισθοδοσία
(`routes_payroll._validate_employee_afm`, commit ca0ff02 του ίδιου γύρου) κάνει σωστά `payload.afm = afm`.

Συνέπειες: το ΑΦΜ του πελάτη με κενό δεν ταιριάζει στο ΑΦΜ του εκδότη που έρχεται από το myDATA
(`automation.job_mydata_expenses`: `suppliers = {c.get("afm"): c.get("name")}` → ταίριασμα με ακριβή ισότητα) ούτε στο
`routes_import` (`find_one({"afm": data["afm"]})` → διπλότυπος πελάτης)· το ΑΦΜ του γραφείου με κενό εκτυπώνεται όπως
πληκτρολογήθηκε. Το frontend δεν κάνει trim.

Αναμενόμενο: αποθηκεύεται το κομμένο ΑΦΜ (όπως στη μισθοδοσία).
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from db import db

RAW = ["123456783 ", " 123456783", " 123456783", "\t123456783\n"]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "afm@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        _run(db.users.update_one({"email": "afm@example.gr"}, {"$set": {"role": "admin"}}))
        yield c


@pytest.mark.parametrize("raw", RAW)
def test_a_client_afm_is_stored_trimmed_on_create_and_update(client, raw):
    created = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": raw, "client_type": "company"})
    assert created.status_code == 200, created.text
    stored = _run(db.clients.find_one({"name": "ΑΛΦΑ ΑΕ"}))
    assert stored["afm"] == "123456783", repr(stored["afm"])
    updated = client.put(f"/api/clients/{created.json()['id']}", json={"name": "ΑΛΦΑ ΑΕ", "afm": raw, "client_type": "company"})
    assert updated.status_code == 200, updated.text
    assert _run(db.clients.find_one({"name": "ΑΛΦΑ ΑΕ"}))["afm"] == "123456783"


@pytest.mark.parametrize("raw", RAW)
def test_the_firm_afm_is_stored_trimmed_by_put_and_patch(client, raw):
    assert client.patch("/api/settings", json={"afm": raw}).status_code == 200
    assert _run(db.settings.find_one({}))["afm"] == "123456783", repr(_run(db.settings.find_one({}))["afm"])
    form = client.get("/api/settings").json()
    form["afm"] = raw
    assert client.put("/api/settings", json=form).status_code == 200
    assert _run(db.settings.find_one({}))["afm"] == "123456783"
