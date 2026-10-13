"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D — D-08) — η ζωντανή ενημέρωση του πινακίου.

Η οθόνη «Πινάκιο Τσέπης» είναι ένα κουμπί «+1» στο διάδρομο του δικαστηρίου. Το κουμπί έστελνε **απόλυτη** τιμή
(`current_number = αυτό που βλέπω + 1`): αν η οθόνη ήταν ξεπερασμένη (δύο συσκευές, συνεργάτης στο ακροατήριο, αποτυχημένη
ανανέωση), το «+1» έφερνε το νούμερο ΠΙΣΩ ή έσβηνε την αλλαγή του άλλου — χωρίς κανένα μήνυμα. Τώρα στέλνει και το νούμερο που
έβλεπε (`expected_current`)· ο server εφαρμόζει την αλλαγή μόνο αν είναι ακόμη αυτό (compare-and-set) και αλλιώς απαντά 409.

Η υπηρεσία `live-update` δεν είχε καθόλου tests· εδώ καλύπτεται όλη: μεταβάσεις κατάστασης, ιστορικό, ειδοποιήσεις, «Καμία
αλλαγή», όρια, απομόνωση γραφείων, και το νέο compare-and-set (μαζί με τον αγώνα δύο ταυτόχρονων «+1»).
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

import routes_hearings
from db import db

ALIEN_FIRM = "firm-" + "9" * 32


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "live@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    user = _run(db.users.find_one({"email": "live@example.gr"}))
    return SimpleNamespace(firm_id=user["firm_id"])


def _hearing(**over):
    return {"court": "Πρωτοδικείο Αθηνών", "room": "Αίθουσα 3", "date": "2026-11-20", "time": "09:00", "pinakio_number": 12,
            "notify_threshold": 3, **over}


def _create(client, **over):
    r = client.post("/api/hearings", json=_hearing(**over))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _update(client, hid, **payload):
    return client.post(f"/api/hearings/{hid}/live-update", json=payload)


def _stored(hid):
    return _run(db.hearings.find_one({"_id": ObjectId(hid)}))


def _events(hid):
    return [e["event"] for e in _stored(hid)["events"]]


# ============================================================================ κατάσταση & νούμερο ====

def test_starting_the_hearing_sets_status_and_logs_it(client, office):
    hid = _create(client)
    r = _update(client, hid, status="in_progress", current_number=1)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress" and r.json()["current_number"] == 1
    assert "Έναρξη εκδίκασης πινακίου" in _events(hid)
    assert "Εκδικάζεται τώρα: Νο 1" in _events(hid)


def test_resuming_after_an_interruption_is_logged_as_a_resumption(client, office):
    hid = _create(client)
    _update(client, hid, status="in_progress")
    _update(client, hid, status="interrupted")
    _update(client, hid, status="in_progress")
    events = _events(hid)
    assert "Διακοπή συνεδρίασης" in events
    assert "Επανέναρξη εκδίκασης" in events


@pytest.mark.parametrize("status, label", [
    ("completed", "Ολοκλήρωση — η υπόθεση εκδικάστηκε"),
    ("postponed", "Αναβολή εκδίκασης"),
    ("interrupted", "Διακοπή συνεδρίασης"),
])
def test_each_status_change_leaves_its_own_line_in_the_history(client, office, status, label):
    hid = _create(client)
    assert _update(client, hid, status=status).status_code == 200
    assert label in _events(hid)


def test_a_note_is_appended_to_the_status_line(client, office):
    hid = _create(client)
    _update(client, hid, status="postponed", note="Αναβολή λόγω απεργίας")
    assert "Αναβολή εκδίκασης — Αναβολή λόγω απεργίας" in _events(hid)


def test_a_note_alone_is_recorded_without_changing_anything(client, office):
    hid = _create(client)
    r = _update(client, hid, note="Ο εντολέας έφτασε")
    assert r.status_code == 200
    assert "Ο εντολέας έφτασε" in _events(hid)
    assert _stored(hid)["status"] == "scheduled"


def test_unknown_status_is_refused(client, office):
    hid = _create(client)
    r = _update(client, hid, status="τίποτα")
    assert r.status_code == 400
    assert _stored(hid)["status"] == "scheduled"


def test_repeating_the_same_state_is_refused_as_no_change(client, office):
    hid = _create(client)
    _update(client, hid, current_number=4)
    r = _update(client, hid, current_number=4)
    assert r.status_code == 400
    assert r.json()["detail"] == "Καμία αλλαγή"


def test_an_empty_request_changes_nothing(client, office):
    hid = _create(client)
    assert _update(client, hid).status_code == 400


# ============================================================================ ειδοποιήσεις ====

def test_approaching_turn_is_logged_when_within_the_threshold(client, office):
    hid = _create(client, pinakio_number=12, notify_threshold=3)
    _update(client, hid, status="in_progress", current_number=8)
    assert not any("Πλησιάζει" in e for e in _events(hid))
    _update(client, hid, current_number=9)
    assert any("απομένουν 3 νούμερα" in e for e in _events(hid))


def test_your_turn_is_logged_when_the_number_reaches_yours(client, office):
    hid = _create(client, pinakio_number=12)
    _update(client, hid, status="in_progress", current_number=11)
    _update(client, hid, current_number=12)
    assert any("ΣΕΙΡΑ ΣΑΣ" in e for e in _events(hid))


def test_alerts_follow_the_stored_state(client, office):
    hid = _create(client, pinakio_number=12, notify_threshold=3)
    _update(client, hid, status="in_progress", current_number=10)
    alerts = client.get("/api/hearings/alerts/list").json()
    assert [a["level"] for a in alerts if a["id"] == hid] == ["warning"]
    _update(client, hid, current_number=12)
    alerts = client.get("/api/hearings/alerts/list").json()
    assert [a["level"] for a in alerts if a["id"] == hid] == ["critical"]


# ============================================================================ όρια ====

@pytest.mark.parametrize("field", ["current_number", "expected_current"])
@pytest.mark.parametrize("value", [-1, routes_hearings.MAX_COURT_NUMBER + 1, 10 ** 30])
def test_numbers_outside_the_range_are_refused_without_touching_the_database(client, office, field, value):
    hid = _create(client)
    r = _update(client, hid, **{field: value})
    assert r.status_code == 422, r.text
    assert _stored(hid)["current_number"] == 0


def test_the_largest_allowed_number_is_accepted(client, office):
    hid = _create(client)
    r = _update(client, hid, current_number=routes_hearings.MAX_COURT_NUMBER)
    assert r.status_code == 200, r.text
    assert _stored(hid)["current_number"] == routes_hearings.MAX_COURT_NUMBER


@pytest.mark.parametrize("field, value", [
    ("pinakio_number", -1), ("pinakio_number", routes_hearings.MAX_COURT_NUMBER + 1), ("pinakio_number", 10 ** 30),
    ("current_number", -5), ("current_number", 10 ** 30),
    ("notify_threshold", -1), ("notify_threshold", 10 ** 30),
])
def test_a_hearing_cannot_be_created_with_an_impossible_number(client, office, field, value):
    r = client.post("/api/hearings", json=_hearing(**{field: value}))
    assert r.status_code == 400, r.text
    assert str(routes_hearings.MAX_COURT_NUMBER) in r.json()["detail"]
    assert _run(db.hearings.count_documents({})) == 0


def test_editing_a_hearing_cannot_set_an_impossible_number_either(client, office):
    hid = _create(client)
    r = client.put(f"/api/hearings/{hid}", json=_hearing(pinakio_number=10 ** 30))
    assert r.status_code == 400
    assert _stored(hid)["pinakio_number"] == 12


# ============================================================================ compare-and-set ====

def test_without_an_expected_number_the_update_is_unconditional(client, office):
    """Οι παλιοί πελάτες API δεν στέλνουν `expected_current`· η συμπεριφορά τους δεν αλλάζει."""
    hid = _create(client)
    _update(client, hid, current_number=7)
    assert _update(client, hid, current_number=3).status_code == 200
    assert _stored(hid)["current_number"] == 3


def test_plus_one_from_the_number_the_user_saw_is_applied(client, office):
    hid = _create(client)
    _update(client, hid, current_number=4)
    r = _update(client, hid, current_number=5, expected_current=4)
    assert r.status_code == 200, r.text
    assert _stored(hid)["current_number"] == 5


def test_plus_one_from_a_stale_screen_does_not_rewind_the_number(client, office):
    """Η οθόνη δείχνει 3, ο συνεργάτης στο ακροατήριο έχει ήδη φτάσει στο 7: το «+1» ΔΕΝ το γυρνά στο 4."""
    hid = _create(client)
    _update(client, hid, status="in_progress", current_number=7)
    before = _events(hid)

    r = _update(client, hid, current_number=4, expected_current=3)

    assert r.status_code == 409
    assert "άλλαξε στο 7" in r.json()["detail"] and "βλέπατε το 3" in r.json()["detail"]
    assert _stored(hid)["current_number"] == 7
    assert _events(hid) == before


def test_the_refusal_applies_to_the_whole_request_including_the_status(client, office):
    hid = _create(client)
    _update(client, hid, current_number=7)
    r = _update(client, hid, status="completed", current_number=8, expected_current=2)
    assert r.status_code == 409
    stored = _stored(hid)
    assert stored["status"] == "scheduled" and stored["current_number"] == 7


def test_a_double_tap_applies_only_once(client, office):
    """Το δεύτερο πάτημα στέλνει το ίδιο «βλέπω 4»: ο server έχει πια 5, άρα το αρνείται αντί να πάει στο 6 ή να μην κάνει τίποτα."""
    hid = _create(client)
    _update(client, hid, current_number=4)
    first = _update(client, hid, current_number=5, expected_current=4)
    second = _update(client, hid, current_number=5, expected_current=4)
    assert first.status_code == 200
    assert second.status_code == 409
    assert _stored(hid)["current_number"] == 5
    assert _events(hid).count("Εκδικάζεται τώρα: Νο 5") == 1


def test_expected_zero_matches_a_fresh_hearing_and_a_document_without_the_field(client, office):
    fresh = _create(client)
    assert _update(client, fresh, current_number=1, expected_current=0).status_code == 200

    legacy = _create(client)
    _run(db.hearings.update_one({"_id": ObjectId(legacy)}, {"$unset": {"current_number": ""}}))
    assert _update(client, legacy, current_number=1, expected_current=0).status_code == 200
    assert _stored(legacy)["current_number"] == 1

    again = _create(client)
    _update(client, again, current_number=2)
    assert _update(client, again, current_number=1, expected_current=0).status_code == 409


def test_a_matching_expectation_also_allows_the_status_to_change(client, office):
    hid = _create(client)
    r = _update(client, hid, status="in_progress", current_number=1, expected_current=0)
    assert r.status_code == 200
    assert _stored(hid)["status"] == "in_progress"


def test_a_refused_request_for_a_missing_hearing_is_404_not_409(client, office):
    r = _update(client, str(ObjectId()), current_number=1, expected_current=0)
    assert r.status_code == 404


def test_the_check_holds_even_if_another_request_slips_in_between_read_and_write(client, office, monkeypatch):
    """Ο έλεγχος της ανάγνωσης δεν αρκεί: άλλη συσκευή μπορεί να γράψει ανάμεσα στην ανάγνωση και στην εγγραφή. Το φίλτρο της
    εγγραφής περιέχει το αναμενόμενο νούμερο, ώστε η εγγραφή να μην ταιριάξει — και να απαντηθεί 409, όχι 200."""
    hid = _create(client)
    _update(client, hid, current_number=4)
    real = routes_hearings.db.hearings
    state = {"once": True}

    class Racing:
        def __getattr__(self, name):
            return getattr(real, name)

        async def update_one(self, flt, op, *a, **kw):
            if state["once"]:
                state["once"] = False
                await real.update_one({"_id": ObjectId(hid)}, {"$set": {"current_number": 9}})   # η άλλη συσκευή
            return await real.update_one(flt, op, *a, **kw)

    monkeypatch.setattr(routes_hearings, "db", SimpleNamespace(hearings=Racing()))

    r = _update(client, hid, current_number=5, expected_current=4)

    assert r.status_code == 409, r.text
    assert "άλλαξε στο 9" in r.json()["detail"]
    assert _stored(hid)["current_number"] == 9
    assert "Εκδικάζεται τώρα: Νο 5" not in _events(hid)


def test_the_conditional_write_is_scoped_to_the_firm_as_well(client, office, monkeypatch):
    """Η εγγραφή περιέχει και το γραφείο στο φίλτρο της (όπως κάθε ερώτημα της εφαρμογής), όχι μόνο το αναγνωριστικό."""
    hid = _create(client)
    real = routes_hearings.db.hearings
    filters = []

    class Recording:
        def __getattr__(self, name):
            return getattr(real, name)

        async def update_one(self, flt, op, *a, **kw):
            filters.append(flt)
            return await real.update_one(flt, op, *a, **kw)

    monkeypatch.setattr(routes_hearings, "db", SimpleNamespace(hearings=Recording()))

    assert _update(client, hid, current_number=1, expected_current=0).status_code == 200

    assert len(filters) == 1
    assert filters[0]["firm_id"] == office.firm_id
    assert filters[0]["_id"] == ObjectId(hid)
    assert filters[0]["current_number"] == {"$in": [0, None]}


def test_a_hearing_deleted_between_read_and_write_is_404(client, office, monkeypatch):
    hid = _create(client)
    real = routes_hearings.db.hearings
    state = {"once": True}

    class Vanishing:
        def __getattr__(self, name):
            return getattr(real, name)

        async def update_one(self, flt, op, *a, **kw):
            if state["once"]:
                state["once"] = False
                await real.delete_one({"_id": ObjectId(hid)})
            return await real.update_one(flt, op, *a, **kw)

    monkeypatch.setattr(routes_hearings, "db", SimpleNamespace(hearings=Vanishing()))
    assert _update(client, hid, current_number=5).status_code == 404


# ============================================================================ απομόνωση & αναγνωριστικά ====

def test_another_firms_hearing_cannot_be_updated(client, office):
    alien = _run(db.hearings.insert_one({"firm_id": ALIEN_FIRM, "court": "Ξένο", "date": "2026-11-20", "pinakio_number": 5,
                                         "current_number": 0, "status": "scheduled", "notify_threshold": 5, "events": []})).inserted_id
    r = _update(client, str(alien), current_number=1)
    assert r.status_code == 404
    assert _run(db.hearings.find_one({"_id": alien}))["current_number"] == 0


def test_another_firms_hearing_cannot_be_probed_through_the_expectation(client, office):
    alien = _run(db.hearings.insert_one({"firm_id": ALIEN_FIRM, "court": "Ξένο", "date": "2026-11-20", "pinakio_number": 5,
                                         "current_number": 7, "status": "scheduled", "notify_threshold": 5, "events": []})).inserted_id
    r = _update(client, str(alien), current_number=1, expected_current=0)
    assert r.status_code == 404
    assert "7" not in r.text


@pytest.mark.parametrize("method, path", [
    ("post", "/api/hearings/abc/live-update"),
    ("put", "/api/hearings/abc"),
    ("delete", "/api/hearings/abc"),
])
def test_a_malformed_identifier_is_not_found_rather_than_a_server_error(client, office, method, path):
    body = {"json": _hearing()} if method == "put" else ({"json": {"current_number": 1}} if method == "post" else {})
    r = getattr(client, method)(path, **body)
    assert r.status_code == 404, r.text
    assert "Traceback" not in r.text


def test_concurrent_plus_one_presses_from_two_devices_apply_exactly_once(client, office):
    """Δύο συσκευές βλέπουν 4 και πατούν «+1» ταυτόχρονα: το νούμερο γίνεται 5 (όχι 6), και η δεύτερη ενημερώνεται."""
    hid = _create(client)
    _update(client, hid, current_number=4)
    results = []

    def press():
        results.append(_update(client, hid, current_number=5, expected_current=4).status_code)

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409], results
    assert _stored(hid)["current_number"] == 5
    assert _events(hid).count("Εκδικάζεται τώρα: Νο 5") == 1
