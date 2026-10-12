"""Γύρος 5 — «Ανείσπρακτα» = ό,τι πράγματι απομένει να εισπραχθεί.

Το dashboard, η εβδομαδιαία ενημέρωση του Autopilot, οι αναλύσεις, ο Copilot και η σελίδα Τιμολόγια
αθροίζαν το **μεικτό σύνολο** των εκδοθέντων. Για τιμολόγιο 1.240 € με παρακράτηση 200 € και 400 € ήδη
εξοφλημένα από πελατολογικό, ο πελάτης οφείλει 640 € — η πύλη του και το email υπενθύμισης το έλεγαν, ενώ το
γραφείο έβλεπε «1.240 € ανείσπρακτα». Εδώ κλειδώνεται ένας μόνος ορισμός για όλες τις οθόνες:

* εκδοθέν τιμολόγιο: πληρωτέο (σύνολο − παρακράτηση) μείον ό,τι εξοφλήθηκε από πελατολογικό, όχι κάτω από 0·
* πιστωτικό (αρνητικό σύνολο): το αρνητικό πληρωτέο του, που συμψηφίζεται με τα υπόλοιπα του ΙΔΙΟΥ πελάτη·
* ανά πελάτη το υπόλοιπο δεν πέφτει κάτω από το μηδέν (πιστωτικό ενός πελάτη δεν «χαρίζει» οφειλές άλλου)·
* ό,τι δεν είναι εκδοθέν (πρόχειρο, εξοφλημένο, ακυρωμένο) δεν οφείλεται.
"""
import asyncio
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today


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
                    json={"email": "owed@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "owed@example.gr"}))
    _run(db.users.update_one({"email": "owed@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    beta = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "987654324", "client_type": "company",
                                             "email": "beta@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, beta=beta, firm_id=user["firm_id"],
                           user={"email": "owed@example.gr", "firm_id": user["firm_id"]})


def _today() -> str:
    return athens_today().isoformat()


def _draft(client, client_id, net=100.0, **over):
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _issued(client, client_id, net=100.0, **over):
    out = client.post(f"/api/invoices/{_draft(client, client_id, net, **over)['id']}/issue")
    assert out.status_code == 200, out.text
    return out.json()


def _set(invoice_id, **fields):
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": fields}))


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


def _settings(firm_id):
    from db import db
    return _run(db.settings.find_one({"firm_id": firm_id})) or {}


def _enable_withholding(client):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text


def _credit(client, invoice, **body):
    """Διαβιβασμένο τιμολόγιο → εκδοθέν πιστωτικό."""
    _set(invoice["id"], mydata_status="synced", mydata_mark="4000" + str(abs(hash(invoice["id"])))[:11].ljust(11, "0"))
    draft = client.post(f"/api/invoices/{invoice['id']}/credit-note", json=body)
    assert draft.status_code == 200, draft.text
    issued = client.post(f"/api/invoices/{draft.json()['id']}/issue")
    assert issued.status_code == 200, issued.text
    return issued.json()


def _portal(client, client_id):
    token = client.post("/api/client-portal/invite", json={"client_id": client_id}).json()["token"]
    return client.get(f"/api/portal/{token}").json()


def _seed(client, office):
    """Γραφείο με παρακράτηση: ένα τιμολόγιο που οφείλεται μερικώς, ένα απλό, ένα εξοφλημένο, ένα πρόχειρο."""
    _enable_withholding(client)
    owed = _issued(client, office.alpha, net=1000.0)            # 1.240, παρακράτηση 200 → πληρωτέο 1.040
    _set(owed["id"], trust_applied=400.0)                        # 400 έχουν εξοφληθεί από πελατολογικό → οφείλονται 640
    plain = _issued(client, office.beta, net=100.0)              # 124, κάτω από το όριο παρακράτησης → οφείλονται 124
    paid = _issued(client, office.alpha, net=500.0)              # 620, παρακράτηση 100 → πληρωτέο 520
    _set(paid["id"], status="paid")
    draft = _draft(client, office.beta, net=900.0)
    return SimpleNamespace(owed=owed, plain=plain, paid=paid, draft=draft)


# ============================================================ 1. Dashboard ====

def test_dashboard_unpaid_is_what_is_still_owed_not_the_gross_total(client, office):
    """Πριν: 1.240 + 124 = 1.364 €. Ο πελάτης χρωστά 640 + 124."""
    _seed(client, office)
    summary = client.get("/api/dashboard/summary").json()
    assert summary["unpaid_total"] == 764.0
    assert summary["unpaid_count"] == 2
    assert "764.00" in summary["advice"] and "1364" not in summary["advice"]


def test_dashboard_without_unpaid_invoices_says_nothing_about_them(client, office):
    _enable_withholding(client)
    paid = _issued(client, office.alpha, net=500.0)
    _set(paid["id"], status="paid")
    summary = client.get("/api/dashboard/summary").json()
    assert (summary["unpaid_total"], summary["unpaid_count"]) == (0.0, 0)
    assert "ανεξόφλητα" not in summary["advice"]


def test_dashboard_and_the_client_portal_agree_for_the_same_client(client, office):
    """Ο ορισμός είναι ένας: ό,τι βλέπει το γραφείο είναι ό,τι βλέπει ο πελάτης στην πύλη του."""
    _enable_withholding(client)
    big = _issued(client, office.alpha, net=1000.0)
    _set(big["id"], trust_applied=400.0)                          # 640
    small = _issued(client, office.alpha, net=200.0)              # 248
    _credit(client, small, amount=50.0)                           # −62 (50 + ΦΠΑ 12)
    summary = client.get("/api/dashboard/summary").json()
    portal = _portal(client, office.alpha)["totals"]
    assert summary["unpaid_total"] == portal["unpaid_total"] == 826.0
    assert summary["unpaid_count"] == portal["unpaid_count"] == 3


def test_a_credit_note_reduces_what_its_own_client_owes(client, office):
    inv = _issued(client, office.alpha, net=1000.0)               # χωρίς παρακράτηση: 1.240
    _credit(client, inv, amount=400.0)                            # −496
    assert client.get("/api/dashboard/summary").json()["unpaid_total"] == 744.0


def test_the_unpaid_balance_of_one_client_never_goes_below_zero(client, office):
    """Πιστωτικό μεγαλύτερο από ό,τι οφείλει ο πελάτης δεν εμφανίζεται ως αρνητικά ανείσπρακτα."""
    paid = _issued(client, office.beta, net=200.0)
    _credit(client, paid)                                         # −248 για εξοφλημένο τιμολόγιο
    _set(paid["id"], status="paid")
    assert client.get("/api/dashboard/summary").json()["unpaid_total"] == 0.0


def test_a_credit_note_of_one_client_does_not_cancel_the_debt_of_another(client, office):
    """Η ουσία του ανά-πελάτη συμψηφισμού: το πιστωτικό της ΒΗΤΑ δεν μειώνει ό,τι χρωστά η ΑΛΦΑ."""
    _issued(client, office.alpha, net=1000.0)                     # ΑΛΦΑ οφείλει 1.240
    refunded = _issued(client, office.beta, net=200.0)
    _credit(client, refunded)                                     # ΒΗΤΑ: πιστωτικό −248 για εξοφλημένο τιμολόγιο
    _set(refunded["id"], status="paid")
    assert client.get("/api/dashboard/summary").json()["unpaid_total"] == 1240.0


def test_cancelled_and_draft_invoices_are_not_owed(client, office):
    _enable_withholding(client)
    gone = _issued(client, office.alpha, net=1000.0)
    _set(gone["id"], status="cancelled")
    _draft(client, office.beta, net=900.0)
    summary = client.get("/api/dashboard/summary").json()
    assert (summary["unpaid_total"], summary["unpaid_count"]) == (0.0, 0)


# ====================================================== 2. Λίστα τιμολογίων ====

def test_invoice_list_carries_the_amount_still_owed(client, office):
    seeded = _seed(client, office)
    credit = _credit(client, _issued(client, office.beta, net=200.0), amount=50.0)      # −62
    rows = {r["id"]: r for r in client.get("/api/invoices").json()}
    assert rows[seeded.owed["id"]]["outstanding"] == 640.0 and rows[seeded.owed["id"]]["total"] == 1240.0
    assert rows[seeded.plain["id"]]["outstanding"] == 124.0
    assert rows[seeded.paid["id"]]["outstanding"] == 0.0
    assert rows[seeded.draft["id"]]["outstanding"] == 0.0
    assert rows[credit["id"]]["outstanding"] == -62.0
    for row in rows.values():                                                            # το υπόλοιπο έγγραφο μένει όπως ήταν
        assert {"id", "number", "status", "total", "client_name", "date"} <= set(row)


def test_the_client_cannot_store_an_outstanding_amount(client, office):
    """Το `outstanding` υπολογίζεται· δεν είναι πεδίο που μπορεί να στείλει ο client."""
    forged = client.post("/api/invoices", json={
        "client_id": office.alpha, "date": _today(), "outstanding": 5.0,
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]})
    assert forged.status_code == 200
    assert "outstanding" not in _doc(forged.json()["id"])
    assert client.get("/api/invoices").json()[0]["outstanding"] == 0.0                    # πρόχειρο


# ======================================================= 3. Ενημέρωση Autopilot ====

def test_the_digest_lists_and_totals_the_amount_still_owed(client, office):
    import automation
    seeded = _seed(client, office)
    snapshot = _run(automation.firm_snapshot(office.firm_id, _settings(office.firm_id)))
    assert snapshot["unpaid_total"] == 764.0 and len(snapshot["unpaid"]) == 2
    assert {i["number"]: i["open_amount"] for i in snapshot["unpaid"]} == {
        seeded.owed["number"]: 640.0, seeded.plain["number"]: 124.0}

    _subject, text, html = automation.build_digest(snapshot, "weekly")
    assert "Ανείσπρακτα: 2 τιμολόγια / 764,00 €" in text
    unpaid_lines = [ln for ln in text.splitlines() if ln.strip().startswith(f"• {seeded.owed['number']} ")]
    assert len(unpaid_lines) == 1 and "640,00 €" in unpaid_lines[0] and "1.240,00 €" not in unpaid_lines[0]
    assert "<strong>640,00 €</strong>" in html and "764,00 €" in html


def test_the_digest_still_works_with_a_snapshot_that_has_no_open_amount():
    """Παλιά δεδομένα/tests χωρίς `open_amount`: εμφανίζεται το σύνολο, όχι σφάλμα."""
    import automation
    from datetime import date
    snap = {
        "today": date(2026, 9, 4), "company": "Γραφείο", "income_net": 0, "expense_net": 0, "output_vat": 0,
        "input_vat": 0, "vat_due": 0, "unpaid": [{"number": "ΤΠΥ-2026-0001", "client_name": "Π", "total": 124,
                                                    "date": "2026-07-01", "reminder_count": 0}],
        "unpaid_total": 124, "deadlines": [], "activity": [], "open_items": [], "mydata_pending": 0,
        "vat_periods": [], "public_url": "",
    }
    _subject, text, _html = automation.build_digest(snap, "weekly")
    assert "ΤΠΥ-2026-0001" in text and "124,00 €" in text


# ============================================================ 4. Αναλύσεις ====

def test_profitability_counts_the_owed_amount_and_collects_the_payable(client, office):
    _seed(client, office)
    data = client.get("/api/analytics/profitability").json()
    totals = data["totals"]
    # Εισπραγμένα = πληρωτέο των εξοφλημένων (620 − 100 παρακράτηση = 520)· ανείσπρακτα = 640 + 124
    assert totals["paid"] == 520.0 and totals["unpaid"] == 764.0
    assert totals["collection_rate_pct"] == round(520.0 / (520.0 + 764.0) * 100, 1)
    per = {c["client_name"]: c for c in data["top_clients"]}
    assert (per["ΑΛΦΑ ΑΕ"]["paid_revenue"], per["ΑΛΦΑ ΑΕ"]["unpaid_revenue"]) == (520.0, 640.0)
    assert (per["ΒΗΤΑ ΕΠΕ"]["paid_revenue"], per["ΒΗΤΑ ΕΠΕ"]["unpaid_revenue"]) == (0.0, 124.0)
    assert per["ΑΛΦΑ ΑΕ"]["total_revenue"] == 1500.0                  # τα καθαρά έσοδα δεν αλλάζουν


def test_profitability_does_not_show_negative_unpaid_for_a_client_with_a_surplus_credit(client, office):
    paid = _issued(client, office.beta, net=200.0)
    _credit(client, paid)
    _set(paid["id"], status="paid")
    data = client.get("/api/analytics/profitability").json()
    assert data["totals"]["unpaid"] == 0.0
    assert all(c["unpaid_revenue"] >= 0 for c in data["top_clients"])


def test_cashflow_forecast_expects_the_owed_amount_not_the_gross(client, office):
    seeded = _seed(client, office)
    covered = _issued(client, office.alpha, net=100.0)                # καλύφθηκε εξ ολοκλήρου από πελατολογικό
    _set(covered["id"], trust_applied=124.0)
    data = client.get("/api/analytics/cashflow").json()
    income = [e for e in data["events"] if e["type"] == "income"]
    assert data["total_expected_income"] == 764.0
    assert sorted(e["amount"] for e in income) == [124.0, 640.0]
    assert not any(covered["number"] in e["label"] for e in income)   # τίποτα για να εισπραχθεί → κανένα γεγονός
    assert any(seeded.owed["number"] in e["label"] for e in income)


# ================================================================ 5. Copilot ====

def test_the_copilot_is_told_the_amount_still_owed(client, office):
    import copilot
    seeded = _seed(client, office)
    context = _run(copilot.build_context(office.user))
    rows = {r["αριθμός"]: r for r in context["ανεξόφλητα_τιμολόγια"]}
    assert rows[seeded.owed["number"]]["οφειλόμενο"] == "640,00 €" and rows[seeded.owed["number"]]["σύνολο"] == "1.240,00 €"
    assert rows[seeded.plain["number"]]["οφειλόμενο"] == "124,00 €"
    assert context["ανείσπρακτα_σύνολο"] == "764,00 €"


# ======================================================= 6. Ο κοινός υπολογισμός ====

class _NoDb:
    """Βάση που αρνείται κάθε πρόσβαση: η απόδειξη ότι ο υπολογισμός δεν ρωτά τίποτα όταν δεν χρειάζεται."""

    def __getattr__(self, name):
        raise AssertionError(f"άσκοπη ανάγνωση της βάσης: {name}")


class _CountingClients:
    def __init__(self, real):
        self._real, self.finds, self.find_ones = real, 0, 0

    def find(self, *args, **kwargs):
        self.finds += 1
        return self._real.find(*args, **kwargs)

    def find_one(self, *args, **kwargs):
        self.find_ones += 1
        return self._real.find_one(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _DbWith:
    def __init__(self, real, **replaced):
        self._real, self._replaced = real, replaced

    def __getattr__(self, name):
        return self._replaced[name] if name in self._replaced else getattr(self._real, name)


def _frozen(client_id, net, *, withheld, status="issued", **over):
    vat = round(net * 0.24, 2)
    return {"_id": ObjectId(), "firm_id": "F", "client_id": client_id, "status": status, "subtotal": net,
            "vat_amount": vat, "total": round(net + vat, 2), "withholding_amount": withheld, "date": "2026-10-01", **over}


def test_balances_read_nothing_when_the_withholding_is_already_frozen(monkeypatch):
    import invoice_services as svc
    monkeypatch.setattr(svc, "db", _NoDb())
    settings = {"withholding_enabled": True}
    invoices = [_frozen("c1", 1000.0, withheld=200.0), _frozen("c2", 100.0, withheld=0.0, trust_applied=24.0)]
    balances = _run(svc.invoice_balances("F", invoices, settings))
    assert [balances[str(i["_id"])] for i in invoices] == [(1040.0, 1040.0), (124.0, 100.0)]


def test_balances_read_nothing_when_withholding_is_off(monkeypatch):
    import invoice_services as svc
    monkeypatch.setattr(svc, "db", _NoDb())
    legacy = {"_id": ObjectId(), "client_id": "c1", "status": "issued", "subtotal": 1000.0, "total": 1240.0}
    assert _run(svc.invoice_balances("F", [legacy], {})) == {str(legacy["_id"]): (1240.0, 1240.0)}


def test_legacy_invoices_need_one_client_query_in_total_not_one_per_invoice(client, office, monkeypatch):
    """Τιμολόγια πριν την παγωμένη παρακράτηση: το είδος (ΤΠΥ/ΑΠΥ) βγαίνει από τον πελάτη — με ΕΝΑ ερώτημα."""
    import invoice_services as svc
    from db import db
    person = client.post("/api/clients", json={"name": "ΙΔΙΩΤΗΣ", "client_type": "individual"}).json()["id"]

    def legacy(client_id, net):
        return {"_id": ObjectId(), "firm_id": office.firm_id, "client_id": client_id, "status": "issued",
                "subtotal": net, "vat_amount": round(net * 0.24, 2), "total": round(net * 1.24, 2), "date": "2026-10-01"}

    invoices = [legacy(office.alpha, 1000.0), legacy(person, 1000.0), legacy(office.alpha, 100.0),
                legacy(office.alpha, 1000.0), legacy(person, 500.0)]
    counting = _CountingClients(db.clients)
    monkeypatch.setattr(svc, "db", _DbWith(db, clients=counting))

    async def no_single_reads(*args, **kwargs):
        raise AssertionError("ανάγνωση πελάτη ανά τιμολόγιο")

    monkeypatch.setattr(svc, "load_client", no_single_reads)
    settings = {"withholding_enabled": True, "withholding_min_net": 300, "withholding_rate": 0.20}
    balances = _run(svc.invoice_balances(office.firm_id, invoices, settings))
    payable = [balances[str(i["_id"])][0] for i in invoices]
    # εταιρεία (ΤΠΥ) με καθαρά 1000 → παρακράτηση 200· ιδιώτης (ΑΠΥ) δεν έχει· κάτω από το όριο δεν έχει
    assert payable == [1040.0, 1240.0, 124.0, 1040.0, 620.0]
    assert counting.finds == 1 and counting.find_ones == 0


def test_a_credit_note_keeps_its_negative_amount_and_only_issued_invoices_are_owed():
    import invoice_services as svc
    credit = _frozen("c1", -200.0, withheld=0.0)
    paid = _frozen("c1", 100.0, withheld=0.0, status="paid")
    draft = _frozen("c1", 100.0, withheld=0.0, status="draft")
    cancelled = _frozen("c1", 100.0, withheld=0.0, status="cancelled")
    balances = _run(svc.invoice_balances("F", [credit, paid, draft, cancelled], {}))
    assert balances[str(credit["_id"])] == (-248.0, -248.0)
    assert [balances[str(i["_id"])] for i in (paid, draft, cancelled)] == [(124.0, 0.0)] * 3


def test_open_total_nets_per_client_and_floors_each_client_at_zero():
    import invoice_services as svc
    debt = _frozen("a", 100.0, withheld=0.0)                          # 124
    big_credit = _frozen("b", -500.0, withheld=0.0)                   # −620 (του b, που δεν χρωστά τίποτα)
    own_credit = _frozen("a", -25.0, withheld=0.0)                    # −31 (συμψηφίζεται με το χρέος του a)
    paid = _frozen("a", 100.0, withheld=0.0, status="paid")
    invoices = [debt, big_credit, own_credit, paid]
    balances = _run(svc.invoice_balances("F", invoices, {}))
    assert svc.open_total(invoices, balances) == 93.0                 # 124 − 31, ο b δεν μετράει
    assert svc.open_total([], {}) == 0.0
    assert svc.open_total([paid], balances) == 0.0


def test_the_portal_uses_the_shared_calculation(client, office):
    """Η πύλη και το γραφείο δεν μπορούν να αποκλίνουν: το αποτέλεσμα της πύλης είναι αυτό της κοινής συνάρτησης."""
    import invoice_services as svc
    seeded = _seed(client, office)
    invoices = _run(_alpha_invoices(office))
    balances = _run(svc.invoice_balances(office.firm_id, invoices, _settings(office.firm_id)))
    portal = _portal(client, office.alpha)
    assert portal["totals"]["unpaid_total"] == svc.open_total(invoices, balances) == 640.0
    rows = {r["id"]: r for r in portal["invoices"]}
    assert rows[seeded.owed["id"]]["outstanding"] == balances[seeded.owed["id"]][1] == 640.0
    assert rows[seeded.owed["id"]]["payable"] == balances[seeded.owed["id"]][0] == 1040.0


async def _alpha_invoices(office):
    from db import db
    return await db.invoices.find({"firm_id": office.firm_id, "client_id": office.alpha,
                                   "status": {"$in": ["issued", "paid"]}}).to_list(50)
