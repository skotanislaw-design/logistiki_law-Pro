"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D) — αναγνωριστικό που δεν είναι έγκυρο ObjectId.

Πάνω από εκατό σημεία του backend κάνουν `ObjectId(<κείμενο του αιτήματος>)`. Για `/api/hearings/abc` η `bson` ρίχνει
`InvalidId`, που δεν το πιάνει κανείς: ο server απαντούσε 500 («Internal Server Error», ίχνος στο log) αντί για «δεν βρέθηκε»
— όπως θα απαντούσε για ένα έγκυρο αναγνωριστικό που δεν υπάρχει. Η εφαρμογή δεν στέλνει ποτέ τέτοια αναγνωριστικά· συμβαίνει
με κομμένο σύνδεσμο, παλιό σελιδοδείκτη ή πελάτη API. Διορθώνεται κεντρικά (ένας χειριστής στο `server.py`), και το test εδώ
περνά **κάθε** διαδρομή της εφαρμογής που έχει παράμετρο στο URL, με κάθε μέθοδο, ώστε να πιαστεί και μελλοντική διαδρομή.
"""
import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from db import db

MALFORMED = ["abc", "not-an-object-id", "123", "z" * 24, "%00", "6" * 23, "6" * 25]
BODY_METHODS = {"POST", "PUT", "PATCH"}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    # raise_server_exceptions=False: ένα 500 να φανεί ως απάντηση (όπως στην παραγωγή), όχι ως εξαίρεση μέσα στο test
    with TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/register", json={"email": "ids@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    _run(db.users.update_one({"email": "ids@example.gr"}, {"$set": {"role": "admin"}}))
    return client


def _routes_with_parameters():
    import server
    found = []
    for route in server.app.routes:
        path = getattr(route, "path", "")
        methods = (getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}
        if "{" in path:
            found.extend((method, path) for method in sorted(methods))
    return found


def test_the_sweep_actually_covers_the_application():
    """Φρούρηση του ίδιου του test: αν αλλάξει η δομή του router και η λίστα αδειάσει, το test δεν πρέπει να περνά σιωπηλά."""
    routes = _routes_with_parameters()
    assert len(routes) >= 60, len(routes)
    assert ("DELETE", "/api/hearings/{hid}") in routes
    assert ("POST", "/api/hearings/{hid}/live-update") in routes


@pytest.mark.parametrize("bad", MALFORMED)
def test_no_route_answers_with_a_server_error_for_a_malformed_identifier(admin, bad):
    failures = []
    for method, template in _routes_with_parameters():
        url = re.sub(r"\{[^}]+\}", bad, template)
        kwargs = {"json": {}} if method in BODY_METHODS else {}
        response = admin.request(method, url, **kwargs)
        if response.status_code >= 500:
            failures.append(f"{method} {template} -> {response.status_code}")
    assert not failures, "\n".join(failures)


def test_the_answer_is_the_same_not_found_as_for_a_valid_identifier_that_does_not_exist(admin):
    valid_but_missing = admin.delete("/api/hearings/" + "a" * 24)
    malformed = admin.delete("/api/hearings/abc")
    assert valid_but_missing.status_code == malformed.status_code == 404
    assert "Traceback" not in malformed.text and "ObjectId" not in malformed.text
