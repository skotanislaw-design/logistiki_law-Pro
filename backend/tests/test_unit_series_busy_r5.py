"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C — παρατήρηση) — «ο μετρητής ήταν απασχολημένος» δεν είναι σφάλμα διακομιστή.

`numbering.next_in_series` ρίχνει `SeriesBusy` μετά από `CAS_ATTEMPTS` (50) διαδοχικούς χαμένους γύρους. Η έκδοση τιμολογίου το
μετατρέπει σε μήνυμα προς τον χρήστη· οι υποθέσεις, τα γραμμάτια και τα παράβολα όχι: το εξαίρεση έφτανε ως 500. Είναι
προσωρινή κατάσταση (δεν δόθηκε αριθμός, δεν χάθηκε κανένας) — ο σωστός κώδικας είναι 503 με `Retry-After`.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import numbering
from db import db
from numbering import BUSY_MESSAGE, SeriesBusy
from tax_utils import athens_today

ROUTES = [
    ("/api/cases", {"title": "Υπόθεση Α"}, "cases"),
    ("/api/grammatia", {"court_type": "Ειρηνοδικείο", "reference_amount": 100.0}, "grammatia"),
    ("/api/paravola", {"category": "Έφεση", "amount": 100.0}, "paravola"),
]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office():
    import server
    # raise_server_exceptions=False: το ανεπεξέργαστο σφάλμα φαίνεται ως 500, όπως θα το έβλεπε ο browser
    with TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        r = c.post("/api/auth/register", json={"email": "busy@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


@pytest.mark.parametrize("path,payload,collection", ROUTES)
def test_a_busy_counter_answers_503_with_retry_after_and_stores_nothing(office, monkeypatch, path, payload, collection):
    async def busy(*args, **kwargs):
        raise SeriesBusy("busy")

    monkeypatch.setattr(numbering, "next_in_series", busy)

    r = office.post(path, json=payload)

    assert r.status_code == 503, r.text
    assert r.json() == {"detail": BUSY_MESSAGE}
    assert r.headers["retry-after"] == "2"
    assert _run(db[collection].count_documents({})) == 0


class _LosingCounters:
    """Ο μετρητής όπως τον βλέπει ο καλών, με την εγγραφή του compare-and-set να μην ταιριάζει ποτέ."""

    class _Lost:
        matched_count = 0

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def update_one(self, *args, **kwargs):
        return self._Lost()


class _DbWithLosingCounters:
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _LosingCounters(attr) if name == "counters" else attr


@pytest.mark.parametrize("path,payload,collection", ROUTES)
def test_the_real_exhaustion_of_the_attempts_ends_the_same_way(office, monkeypatch, path, payload, collection):
    """Όχι υποκατάσταση της `next_in_series`: ο μετρητής χάνει κάθε γύρο (το compare-and-set δεν ταιριάζει ποτέ)."""
    monkeypatch.setattr(numbering, "CAS_ATTEMPTS", 3)
    # ο μετρητής πρέπει να υπάρχει, αλλιώς η πρώτη χρήση τον δημιουργεί αντί να παλέψει για αυτόν
    firm = _run(db.users.find_one({"email": "busy@example.gr"}))["firm_id"]
    year = athens_today().year                                                    # το έτος της σειράς είναι το ελληνικό, όχι της UTC
    counter = {"cases": f"case-{firm}-{year}", "grammatia": f"grammatio-{firm}-{year}", "paravola": f"paravolo-{firm}-{year}"}[collection]
    _run(db.counters.insert_one({"_id": counter, "seq": 5, "last_alloc_at": "2026-01-01T00:00:00+00:00"}))
    monkeypatch.setattr(numbering, "db", _DbWithLosingCounters(db))

    r = office.post(path, json=payload)

    assert r.status_code == 503 and r.json() == {"detail": BUSY_MESSAGE}
    assert _run(db[collection].count_documents({})) == 0
    assert _run(db.counters.find_one({"_id": counter}))["seq"] == 5               # δεν χάθηκε κανένας αριθμός


@pytest.mark.parametrize("path,payload,collection", ROUTES)
def test_once_the_counter_is_free_again_the_same_request_succeeds_with_the_next_number(office, path, payload, collection):
    first = office.post(path, json=payload)
    second = office.post(path, json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["number"] != second.json()["number"]
