"""Έλεγχος γύρου 4 — Πακέτο 1: το unique index των εξόδων.

Το παλιό index (`firm_id + mydata_mark`, unique + sparse) θεωρούσε ότι ένα έξοδο χωρίς MARK
δεν ευρετηριάζεται. Σε σύνθετο κλειδί αυτό δεν ισχύει (το `firm_id` υπάρχει πάντα), οπότε
το πρώτο χειροκίνητο έξοδο κάθε γραφείου περνούσε και το δεύτερο έπεφτε με HTTP 500.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def session(client):
    r = client.post("/api/auth/register",
                    json={"email": "exp@example.gr", "password": "Str0ngPass!", "name": "Λογιστής"})
    assert r.status_code == 200, r.text
    return client


def _expense(**over):
    body = {"vendor": "Προμηθευτής", "date": "2026-09-20", "amount": 124.0,
            "vat_amount": 24.0, "vat_rate": 24, "category": "Λοιπά"}
    body.update(over)
    return body


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_several_manual_expenses_without_mark_are_accepted(session):
    """Η αρχική αστοχία: μόνο το πρώτο έξοδο περνούσε, τα επόμενα έδιναν 500."""
    codes = [session.post("/api/expenses", json=_expense(vendor=f"Προμηθευτής {i}")).status_code
             for i in range(5)]
    assert codes == [200] * 5, codes
    assert len(session.get("/api/expenses").json()) == 5


def test_blank_mark_is_stored_as_no_mark(session):
    """Ένα κενό string δεν είναι MARK· αλλιώς το δεύτερο κενό θα συγκρουόταν με το πρώτο."""
    for blank in ("", "   "):
        r = session.post("/api/expenses", json=_expense(mydata_mark=blank))
        assert r.status_code == 200, r.text
        assert r.json()["mydata_mark"] is None
    assert all(e["mydata_mark"] is None for e in session.get("/api/expenses").json())


def test_same_mark_twice_in_one_firm_is_a_clean_409(session):
    first = session.post("/api/expenses", json=_expense(mydata_mark="400000000001"))
    assert first.status_code == 200, first.text
    dup = session.post("/api/expenses", json=_expense(vendor="Άλλος", mydata_mark="400000000001"))
    assert dup.status_code == 409
    assert "MARK" in dup.json()["detail"]
    assert len(session.get("/api/expenses").json()) == 1


def test_editing_an_expense_into_a_duplicate_mark_is_409_not_500(session):
    session.post("/api/expenses", json=_expense(mydata_mark="400000000001"))
    other = session.post("/api/expenses", json=_expense(vendor="Β")).json()
    r = session.put(f"/api/expenses/{other['id']}", json=_expense(vendor="Β", mydata_mark="400000000001"))
    assert r.status_code == 409


def test_same_mark_is_allowed_in_a_different_firm(session):
    import server
    assert session.post("/api/expenses", json=_expense(mydata_mark="400000000001")).status_code == 200
    with TestClient(server.app, base_url="https://testserver") as other:
        r = other.post("/api/auth/register",
                       json={"email": "other@example.gr", "password": "Str0ngPass!", "name": "Άλλο γραφείο"})
        assert r.status_code == 200, r.text
        again = other.post("/api/expenses", json=_expense(mydata_mark="400000000001"))
        assert again.status_code == 200, again.text


def test_startup_replaces_the_legacy_sparse_index():
    """Υπάρχουσα εγκατάσταση: το παλιό index αντικαθίσταται χωρίς χειροκίνητη ενέργεια."""
    import server
    from db import db

    async def scenario():
        await db.expenses.drop()
        await db.expenses.create_index([("firm_id", 1), ("mydata_mark", 1)], unique=True, sparse=True)
        await db.expenses.insert_one({"firm_id": "F", "vendor": "α"})
        # με το παλιό index το δεύτερο έξοδο χωρίς MARK αποτυγχάνει
        with pytest.raises(Exception):
            await db.expenses.insert_one({"firm_id": "F", "vendor": "β"})

        await server.ensure_expense_mark_index()

        info = await db.expenses.index_information()
        legacy = [n for n, i in info.items()
                  if list(i["key"]) == [("firm_id", 1), ("mydata_mark", 1)] and not i.get("partialFilterExpression")]
        assert legacy == []
        assert info[server.EXPENSE_MARK_INDEX]["unique"] is True

        await db.expenses.insert_one({"firm_id": "F", "vendor": "β"})
        await db.expenses.insert_one({"firm_id": "F", "vendor": "γ"})
        await db.expenses.insert_one({"firm_id": "F", "vendor": "δ", "mydata_mark": "7"})
        with pytest.raises(Exception):
            await db.expenses.insert_one({"firm_id": "F", "vendor": "ε", "mydata_mark": "7"})

        # δεύτερη εκκίνηση: idempotent
        await server.ensure_expense_mark_index()
        assert list((await db.expenses.index_information()).keys()).count(server.EXPENSE_MARK_INDEX) == 1

    _run(scenario())


def test_index_failure_does_not_block_startup(caplog):
    """Αν υπάρχουν ήδη διπλά MARK στα δεδομένα, η εφαρμογή ξεκινά και καταγράφει σφάλμα.

    Το mongomock_motor δίνει νέο αντικείμενο συλλογής σε κάθε `db.expenses`, οπότε monkeypatch
    πάνω του δεν έπιανε τίποτα (το test περνούσε χωρίς να ελέγχει τίποτα)· εδώ δίνουμε στη
    συνάρτηση μια συλλογή που αποτυγχάνει πραγματικά.
    """
    import server

    class FailingCollection:
        async def index_information(self):
            return {}

        async def create_index(self, *args, **kwargs):
            raise RuntimeError("E11000 duplicate key")

    with caplog.at_level("ERROR"):
        _run(server.ensure_expense_mark_index(FailingCollection()))   # δεν πρέπει να σηκώσει εξαίρεση
    assert "αποτυχία δημιουργίας του unique index" in caplog.text and "E11000" in caplog.text
