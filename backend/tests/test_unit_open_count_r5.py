"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C — C-08) — «πόσα ανεξόφλητα τιμολόγια;» με τον ίδιο ορισμό που έχει το ποσό.

Πριν: το σύνολο των ανείσπρακτων (`open_total`) ήταν καθαρισμένο — ανά πελάτη, μετά την παρακράτηση, με τα πιστωτικά
συμψηφισμένα — αλλά το **πλήθος** ήταν «όσα παραστατικά έχουν κατάσταση issued»: το ταμπλό έγραφε «3 ανεξόφλητα τιμολόγια
συνολικού ποσού 248,00 €» για δύο οφειλές και ένα πιστωτικό. Το ίδιο έκαναν η πύλη του πελάτη, ο Copilot και η εβδομαδιαία
ενημέρωση προς τη λογίστρια (η λίστα «ανείσπρακτα» έδειχνε και πιστωτικά και ό,τι είχε ήδη συμψηφιστεί).
"""
import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from db import db
from invoice_services import open_invoices, open_total


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------------------- καθαρή λογική ----

def _inv(client, total, status="issued", _id=None):
    return {"_id": _id or ObjectId(), "client_id": client, "total": total, "status": status}


def _balances(invoices, owed):
    """{id: (πληρωτέο, οφειλόμενο)} όπως το επιστρέφει η `invoice_balances`."""
    return {str(i["_id"]): (i["total"], o) for i, o in zip(invoices, owed)}


def test_only_invoices_that_are_really_owed_are_counted():
    open_one, open_two = _inv("x", 124.0), _inv("x", 62.0)
    credit = _inv("y", -50.0)                              # πιστωτικό άλλου πελάτη: δεν είναι «ανεξόφλητο τιμολόγιο»
    settled = _inv("z", 124.0)                             # εκδοθέν που έχει ήδη εξοφληθεί από πελατολογικό: οφειλόμενο 0
    paid, draft, cancelled = _inv("x", 10.0, "paid"), _inv("x", 10.0, "draft"), _inv("x", 10.0, "cancelled")
    invoices = [open_one, open_two, credit, settled, paid, draft, cancelled]
    balances = _balances(invoices, [124.0, 62.0, -50.0, 0.0, 0.0, 0.0, 0.0])

    assert open_invoices(invoices, balances) == [open_one, open_two]
    assert open_total(invoices, balances) == 186.0


def test_a_credit_note_that_cancels_a_clients_debt_removes_it_from_the_count():
    invoice, credit = _inv("x", 124.0), _inv("x", -124.0)
    balances = _balances([invoice, credit], [124.0, -124.0])
    assert open_invoices([invoice, credit], balances) == []
    assert open_total([invoice, credit], balances) == 0.0


def test_a_partial_credit_leaves_the_invoice_in_the_count():
    invoice, credit = _inv("x", 124.0), _inv("x", -24.0)
    balances = _balances([invoice, credit], [124.0, -24.0])
    assert open_invoices([invoice, credit], balances) == [invoice]
    assert open_total([invoice, credit], balances) == 100.0


def test_one_clients_credit_never_hides_another_clients_debt():
    debt, other_credit = _inv("x", 124.0), _inv("y", -500.0)
    balances = _balances([debt, other_credit], [124.0, -500.0])
    assert open_invoices([debt, other_credit], balances) == [debt]


def test_a_fraction_of_a_cent_is_not_a_debt():
    invoice = _inv("x", 124.0)
    assert open_invoices([invoice], _balances([invoice], [0.004])) == []
    assert open_invoices([invoice], _balances([invoice], [0.01])) == [invoice]


def test_a_fraction_of_a_cent_does_not_count_even_beside_a_real_debt_of_the_same_client():
    dust, debt = _inv("x", 124.0), _inv("x", 100.0)
    assert open_invoices([dust, debt], _balances([dust, debt], [0.004, 100.0])) == [debt]


def test_a_document_without_a_client_is_counted_on_its_own_merit():
    no_client = {"_id": ObjectId(), "total": 50.0, "status": "issued"}
    assert open_invoices([no_client], {str(no_client["_id"]): (50.0, 50.0)}) == [no_client]


# ------------------------------------------------------------------------------- οθόνες και ενημέρωση ----

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "open-count@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    user = _run(db.users.find_one({"email": "open-count@example.gr"}))
    _run(db.users.update_one({"email": "open-count@example.gr"}, {"$set": {"role": "admin"}}))
    ids = {}
    for key, name, afm in (("x", "ΑΛΦΑ ΑΕ", "123456783"), ("y", "ΒΗΤΑ ΑΕ", "094014201")):
        ids[key] = client.post("/api/clients", json={"name": name, "afm": afm, "client_type": "company",
                                                       "email": f"{key}@example.gr"}).json()["id"]
    return SimpleNamespace(firm_id=user["firm_id"], x=ids["x"], y=ids["y"], user=user)


def _put(office, client_key, number, total, status="issued", **over):
    doc = {"firm_id": office.firm_id, "client_id": getattr(office, client_key), "client_name": client_key.upper(),
           "number": number, "status": status, "total": total, "subtotal": round(total / 1.24, 2),
           "vat_amount": round(total - total / 1.24, 2), "date": "2026-06-01", "line_items": [], **over}
    return _run(db.invoices.insert_one(doc)).inserted_id


def test_the_dashboard_counts_the_invoices_it_totals(client, office):
    _put(office, "x", "ΤΠΥ-2026-0001", 124.0)
    _put(office, "x", "ΤΠΥ-2026-0002", 124.0)
    _put(office, "y", "ΠΙΣ-2026-0001", -50.0)                                 # πιστωτικό άλλου πελάτη

    summary = client.get("/api/dashboard/summary").json()

    assert summary["unpaid_total"] == 248.0
    assert summary["unpaid_count"] == 2
    assert "2 ανεξόφλητα τιμολόγια συνολικού ποσού 248.00 €" in summary["advice"]


def test_the_dashboard_does_not_count_a_debt_that_a_credit_note_cancelled(client, office):
    _put(office, "x", "ΤΠΥ-2026-0001", 124.0)
    _put(office, "x", "ΠΙΣ-2026-0001", -124.0)

    summary = client.get("/api/dashboard/summary").json()

    assert summary["unpaid_total"] == 0.0 and summary["unpaid_count"] == 0
    assert "ανεξόφλητα τιμολόγια" not in summary["advice"]


def test_the_dashboard_does_not_count_an_invoice_the_clients_account_already_settled(client, office):
    _put(office, "x", "ΤΠΥ-2026-0001", 124.0, trust_applied=124.0)
    _put(office, "x", "ΤΠΥ-2026-0002", 124.0)

    summary = client.get("/api/dashboard/summary").json()

    assert summary["unpaid_total"] == 124.0 and summary["unpaid_count"] == 1


def test_the_client_portal_counts_what_it_totals(client, office):
    token = client.post("/api/client-portal/invite", json={"client_id": office.x}).json()["token"]
    _put(office, "x", "ΤΠΥ-2026-0001", 124.0)
    _put(office, "x", "ΤΠΥ-2026-0002", 124.0, trust_applied=124.0)           # εξοφλημένο από τον λογαριασμό του
    _put(office, "x", "ΠΙΣ-2026-0001", -24.0)                                 # μερικό πιστωτικό

    totals = client.get(f"/api/portal/{token}").json()["totals"]

    assert totals["unpaid_total"] == 100.0
    assert totals["unpaid_count"] == 1


def test_the_assistant_context_counts_and_lists_only_open_invoices(office):
    from copilot import build_context

    _put(office, "x", "ΤΠΥ-2026-0001", 124.0)
    _put(office, "x", "ΤΠΥ-2026-0002", 62.0)
    _put(office, "y", "ΠΙΣ-2026-0001", -50.0)
    _put(office, "y", "ΤΠΥ-2026-0003", 124.0, trust_applied=124.0)

    context = _run(build_context(office.user))

    assert context["ανεξόφλητα_πλήθος"] == 2
    assert sorted(i["αριθμός"] for i in context["ανεξόφλητα_τιμολόγια"]) == ["ΤΠΥ-2026-0001", "ΤΠΥ-2026-0002"]
    assert context["ανείσπρακτα_σύνολο"].startswith("186,00")


def test_the_digest_lists_and_counts_only_what_is_owed(office):
    from automation import build_digest, firm_snapshot

    _put(office, "x", "ΤΠΥ-2026-0001", 124.0)
    _put(office, "x", "ΤΠΥ-2026-0002", 124.0)
    _put(office, "y", "ΠΙΣ-2026-0001", -50.0)
    _put(office, "y", "ΤΠΥ-2026-0003", 124.0, trust_applied=124.0)

    snapshot = _run(firm_snapshot(office.firm_id, {}))
    _subject, text, html = build_digest(snapshot)

    assert sorted(i["number"] for i in snapshot["unpaid"]) == ["ΤΠΥ-2026-0001", "ΤΠΥ-2026-0002"]
    assert snapshot["unpaid_total"] == 248.0
    assert "Ανείσπρακτα: 2 τιμολόγια / 248,00 €" in text
    assert "ΠΙΣ-2026-0001" not in text and "ΤΠΥ-2026-0003" not in text and "ΤΠΥ-2026-0003" not in html
