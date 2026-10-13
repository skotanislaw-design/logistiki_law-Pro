"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C — C-07) — η ημερομηνία δικασίμου.

Όλες οι ημερομηνίες της εφαρμογής συγκρίνονται ως κείμενα. Τιμολόγια και έξοδα το είχαν ήδη (`valid_iso_date`)· το πινάκιο
όχι: `POST /api/hearings` αποθήκευε ό,τι έρθει στο πεδίο `date`, ενώ οι ειδοποιήσεις «Σήμερα: πινάκιο Νο …» συγκρίνουν
`date == σήμερα`. Μια δικάσιμος `20261015` ή `15/10/2026` (πελάτης API, εισαγωγή) δεν εμφανιζόταν ποτέ την ημέρα της, και η
λίστα των πινακίων (ταξινόμηση κατά `date`) την έβαζε σε λάθος σειρά — δίχως κανένα σφάλμα.

Ελέγχθηκε και τι ΔΕΝ χρειάζεται: τα γραμμάτια και τα παράβολα δεν έχουν ημερομηνία που πληκτρολογεί ο χρήστης (μόνο σφραγίδες
του διακομιστή), η ημερομηνία πρόσληψης της μισθοδοσίας εμφανίζεται αλλά δεν συγκρίνεται πουθενά, η τράπεζα κανονικοποιεί κάθε
ημερομηνία CSV σε ISO ή την απορρίπτει (`_parse_date`), οι κινήσεις πελατολογίου έχουν δική τους επικύρωση.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from tax_utils import athens_today

BAD_DATES = [
    "20261015",                # βασική μορφή ISO 8601 — η fromisoformat της Python 3.11 την δέχεται
    "2026-W42-4",              # ημερομηνία εβδομάδας ISO
    "15/10/2026", "15-10-2026",
    "2026-10-15T09:00:00",     # με ώρα: δεν είναι ίση με το «σήμερα»
    "2026-02-30", "2026-13-01",
    "2026-1-5",                # χωρίς μηδενικά
    "٢٠٢٦-١٠-١٥",              # αραβικά-ινδικά ψηφία
    "２０２６-１０-１５",         # ψηφία πλήρους πλάτους
    "", " 2026-10-15",
]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "hearing-dates@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    return client


def _hearing(date, **over):
    return {"court": "Πρωτοδικείο Αθηνών", "room": "Αίθουσα 3", "date": date, "time": "09:00", "pinakio_number": 12,
            "notify_threshold": 5, **over}


def test_a_real_iso_date_is_accepted_and_stored_as_typed(office):
    r = office.post("/api/hearings", json=_hearing("2026-11-20"))
    assert r.status_code == 200, r.text
    assert r.json()["date"] == "2026-11-20"


@pytest.mark.parametrize("bad", BAD_DATES)
def test_a_date_the_system_cannot_compare_is_refused_and_nothing_is_stored(office, bad):
    r = office.post("/api/hearings", json=_hearing(bad))

    assert r.status_code == 400, r.text
    assert "ΕΕΕΕ-ΜΜ-ΗΗ" in r.json()["detail"]
    assert office.get("/api/hearings").json() == []


@pytest.mark.parametrize("bad", ["20261015", "15/10/2026", "2026-02-30", ""])
def test_an_edit_cannot_replace_a_good_date_with_a_bad_one(office, bad):
    hearing = office.post("/api/hearings", json=_hearing("2026-11-20")).json()

    r = office.put(f"/api/hearings/{hearing['id']}", json=_hearing(bad))

    assert r.status_code == 400, r.text
    assert office.get("/api/hearings").json()[0]["date"] == "2026-11-20"


def test_an_edit_with_a_good_date_still_works(office):
    hearing = office.post("/api/hearings", json=_hearing("2026-11-20")).json()

    r = office.put(f"/api/hearings/{hearing['id']}", json=_hearing("2026-12-01", room="Αίθουσα 5"))

    assert r.status_code == 200, r.text
    stored = office.get("/api/hearings").json()[0]
    assert stored["date"] == "2026-12-01" and stored["room"] == "Αίθουσα 5"


def test_a_hearing_dated_today_is_announced_as_today(office):
    office.post("/api/hearings", json=_hearing(athens_today().isoformat(), pinakio_number=7))

    alerts = office.get("/api/hearings/alerts/list").json()

    assert [a["message"] for a in alerts if a["level"] == "info"] == ["📅 Σήμερα: πινάκιο Νο 7 — Πρωτοδικείο Αθηνών 09:00"]
