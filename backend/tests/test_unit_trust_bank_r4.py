"""Έλεγχος γύρου 4 — Πακέτο 4: πελατολογικός λογαριασμός και τραπεζική συμφωνία.

Πελατολογικός λογαριασμός (χρήματα πελατών):
* το υπόλοιπο διαβαζόταν και ξαναγραφόταν — δύο ταυτόχρονες αναλήψεις το άφηναν αρνητικό·
* τα χρήματα ενός πελάτη μπορούσαν να εξοφλήσουν τιμολόγιο άλλου, ή να ξεπεράσουν το οφειλόμενο·
* η μερική εξόφληση δεν άφηνε ίχνος στο τιμολόγιο.

Τραπεζική συμφωνία:
* η σύγκριση γινόταν με το σύνολο του τιμολογίου — ο πελάτης με παρακράτηση πλήρωνε λιγότερο, άρα
  καμία είσπραξή του δεν ταίριαζε·
* σκορ ≥ 90 από όνομα και ημερομηνία αρκούσε για αυτόματη εξόφληση ακόμη κι όταν το ποσό διέφερε·
* με δύο ίδια ποσά δεν υπήρχε μονοσήμαντη αντιστοίχιση, αλλά η εξόφληση γινόταν ούτως ή άλλως·
* μια δεύτερη κατάθεση για ήδη εξοφλημένο τιμολόγιο «συμφωνιόταν» σιωπηλά (κρυμμένη διπλοπληρωμή)·
* δύο πανομοιότυπες γραμμές στο ίδιο αρχείο θεωρούνταν διπλότυπο και η δεύτερη χανόταν.
"""
import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

import automation
import routes_trust
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
                    json={"email": "trustbank@example.gr", "password": "Str0ngPass!", "name": "Λογιστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "trustbank@example.gr"}))
    _run(db.users.update_one({"email": "trustbank@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    beta = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "987654324", "client_type": "company",
                                             "email": "beta@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, beta=beta, firm_id=user["firm_id"])


def _today() -> str:
    return athens_today().isoformat()


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


def _issued(client, client_id, net=100.0, **over):
    """Εκδοθέν τιμολόγιο: καθαρή αξία `net` + ΦΠΑ 24%."""
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    draft = client.post("/api/invoices", json=body)
    assert draft.status_code == 200, draft.text
    out = client.post(f"/api/invoices/{draft.json()['id']}/issue")
    assert out.status_code == 200, out.text
    return out.json()


def _enable_withholding(client):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text


# ============================================================ πελατολογικός ====

def _tx(client, client_id, kind, amount, **over):
    body = {"client_id": client_id, "type": kind, "amount": amount, "date": _today(), "description": "δοκιμή"}
    body.update(over)
    return client.post("/api/trust/transactions", json=body)


def _account(client, client_id):
    return client.get(f"/api/trust/accounts/{client_id}").json()


def test_amounts_are_kept_to_the_cent(client, office):
    """0,70 + 0,10 = 0,7999999999999999 σε float· η πλήρης ανάληψη των 0,80 έπρεπε να περνά."""
    assert _tx(client, office.alpha, "deposit", 0.70).status_code == 200
    assert _tx(client, office.alpha, "deposit", 0.10).status_code == 200
    assert _account(client, office.alpha)["account"]["balance"] == 0.8
    out = _tx(client, office.alpha, "withdrawal", 0.80)
    assert out.status_code == 200, out.text
    assert out.json()["account"]["balance"] == 0.0


def test_overdraft_is_refused_and_leaves_no_trace(client, office):
    _tx(client, office.alpha, "deposit", 50.0)
    out = _tx(client, office.alpha, "withdrawal", 50.01)
    assert out.status_code == 400 and "Ανεπαρκές" in out.json()["detail"]
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 50.0 and len(data["transactions"]) == 1


@pytest.mark.parametrize("amount", [0, -5, 0.001, 10.005, 1e12])
def test_invalid_amounts_are_refused(client, office, amount):
    out = _tx(client, office.alpha, "deposit", amount)
    assert out.status_code == 400, out.text


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_amounts_are_refused(client, office, literal):
    """Το JSON της Python δέχεται NaN/Infinity· αν περνούσαν, το υπόλοιπο γινόταν NaN για πάντα."""
    body = ('{"client_id": "%s", "type": "deposit", "amount": %s, "date": "%s"}' % (office.alpha, literal, _today()))
    out = client.post("/api/trust/transactions", content=body, headers={"Content-Type": "application/json"})
    assert out.status_code == 400, out.text
    assert _account(client, office.alpha)["account"]["balance"] == 0.0


def test_dates_must_be_real_and_not_in_the_future(client, office):
    for bad in ("", "12/10/2026", "2026-13-45", "2026-02-30", "σήμερα"):
        assert _tx(client, office.alpha, "deposit", 10.0, date=bad).status_code == 400, bad
    tomorrow = (athens_today() + timedelta(days=1)).isoformat()
    out = _tx(client, office.alpha, "deposit", 10.0, date=tomorrow)
    assert out.status_code == 400 and "μελλοντική" in out.json()["detail"]
    assert _tx(client, office.alpha, "deposit", 10.0, date=_today()).status_code == 200
    assert _tx(client, office.alpha, "deposit", 10.0, date="2026-01-05").status_code == 200   # παλαιότερη: δεκτή


def test_unknown_case_is_refused(client, office):
    assert _tx(client, office.alpha, "deposit", 10.0, case_id=str(ObjectId())).status_code == 404
    case = client.post("/api/cases", json={"title": "Υπόθεση", "client_id": office.alpha}).json()
    assert _tx(client, office.alpha, "deposit", 10.0, case_id=case["id"]).status_code == 200


# ---- ατομικότητα ---------------------------------------------------------

class _RacyCollection:
    """Συλλογή που, αμέσως μετά την πρώτη ανάγνωση, αφήνει «άλλη αίτηση» να αλλάξει τον λογαριασμό."""

    def __init__(self, collection, concurrent_change):
        self.collection, self.change, self.fired = collection, concurrent_change, False

    async def find_one(self, query):
        doc = await self.collection.find_one(query)
        if not self.fired:
            self.fired = True
            await self.collection.update_one({"_id": doc["_id"]}, {"$set": self.change})
        return doc

    async def find_one_and_update(self, *args, **kwargs):
        return await self.collection.find_one_and_update(*args, **kwargs)


def _account_id(client, client_id):
    return ObjectId(_account(client, client_id)["account"]["id"])


def test_a_concurrent_withdrawal_cannot_be_overwritten(client, office):
    """Η δεύτερη ανάληψη διάβασε 100 €, αλλά στο μεταξύ η πρώτη (30 €) άφησε 70 €. Η δεύτερη (60 €)
    δεν επιτρέπεται να γράψει 40 € (=100−60) και να σβήσει την πρώτη: ξαναϋπολογίζεται στα 70 €."""
    from db import db
    _tx(client, office.alpha, "deposit", 100.0)
    acc_id = _account_id(client, office.alpha)
    racy = _RacyCollection(db.trust_accounts, {"balance": 70.0, "total_withdrawals": 30.0})
    updated = _run(routes_trust._cas_update(racy, {"_id": acc_id}, routes_trust._account_delta("withdrawal", Decimal("60"))))
    assert updated["balance"] == 10.0                       # 70 − 60, όχι 100 − 60
    assert updated["total_withdrawals"] == 90.0             # 30 της άλλης αίτησης + 60 αυτής
    assert updated["total_deposits"] == 100.0


def test_a_concurrent_withdrawal_cannot_cause_an_overdraft(client, office):
    from db import db
    _tx(client, office.alpha, "deposit", 100.0)
    acc_id = _account_id(client, office.alpha)
    racy = _RacyCollection(db.trust_accounts, {"balance": 40.0, "total_withdrawals": 60.0})
    with pytest.raises(HTTPException) as refused:
        _run(routes_trust._cas_update(racy, {"_id": acc_id}, routes_trust._account_delta("withdrawal", Decimal("60"))))
    assert refused.value.status_code == 400 and "Ανεπαρκές" in refused.value.detail
    assert _run(db.trust_accounts.find_one({"_id": acc_id}))["balance"] == 40.0     # ούτε ένα λεπτό κάτω από το 0


def test_a_concurrent_deposit_is_not_lost(client, office):
    from db import db
    _tx(client, office.alpha, "deposit", 100.0)
    acc_id = _account_id(client, office.alpha)
    racy = _RacyCollection(db.trust_accounts, {"balance": 110.0, "total_deposits": 110.0})
    updated = _run(routes_trust._cas_update(racy, {"_id": acc_id}, routes_trust._account_delta("deposit", Decimal("5"))))
    assert updated["balance"] == 115.0 and updated["total_deposits"] == 115.0


def test_balance_always_equals_the_sum_of_the_ledger(client, office):
    for kind, amount in (("deposit", 300.0), ("withdrawal", 20.5), ("refund", 10.25), ("deposit", 0.3)):
        assert _tx(client, office.alpha, kind, amount).status_code == 200
    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 269.55
    assert data["ledger_check"] == {"ledger_balance": 269.55, "difference": 0.0, "consistent": True}
    assert [t["balance_after"] for t in reversed(data["transactions"])] == [300.0, 279.5, 269.25, 269.55]


def test_ledger_check_flags_a_balance_that_no_longer_matches_the_movements(client, office):
    from db import db
    _tx(client, office.alpha, "deposit", 100.0)
    _run(db.trust_accounts.update_one({"_id": _account_id(client, office.alpha)}, {"$set": {"balance": 65.0}}))
    check = _account(client, office.alpha)["ledger_check"]
    assert check["consistent"] is False and check["difference"] == -35.0


def test_one_account_per_client_is_enforced_by_the_database(client, office):
    from db import db
    acc = _account(client, office.alpha)["account"]
    with pytest.raises(DuplicateKeyError):
        _run(db.trust_accounts.insert_one({"firm_id": office.firm_id, "client_id": office.alpha, "balance": 0.0}))
    assert len(client.get("/api/trust/accounts").json()) == 1 and acc["balance"] == 0.0


# ---- εξόφληση τιμολογίου από πελατολογικό ---------------------------------

def test_apply_pays_the_invoice_in_full(client, office):
    inv = _issued(client, office.alpha)                      # 124 €
    _tx(client, office.alpha, "deposit", 200.0)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["invoice_paid"] is True and "εξοφλήθηκε" in body["message"]
    assert body["account"]["balance"] == 76.0
    assert body["transaction"]["invoice_number"] == inv["number"]
    doc = _doc(inv["id"])
    assert doc["status"] == "paid" and doc["paid_via"] == "trust" and doc["trust_applied"] == 124.0


def test_partial_application_is_tracked_on_the_invoice(client, office):
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 500.0)
    first = _tx(client, office.alpha, "apply_to_invoice", 100.0, invoice_id=inv["id"]).json()
    assert first["invoice_paid"] is False and first["invoice_outstanding"] == 24.0
    doc = _doc(inv["id"])
    assert doc["status"] == "issued" and doc["trust_applied"] == 100.0

    options = client.get(f"/api/trust/accounts/{office.alpha}/open-invoices").json()
    assert [(o["id"], o["payable"], o["outstanding"]) for o in options] == [(inv["id"], 124.0, 24.0)]

    # Ό,τι απομένει είναι το ποσό που ζητούν η κάρτα και οι υπενθυμίσεις — όχι ξανά τα 124 €.
    import routes_payments
    _invoice, amount = _run(routes_payments._validate_invoice_and_get_amount(inv["id"], office.firm_id))
    assert amount == 24.0

    over = _tx(client, office.alpha, "apply_to_invoice", 24.01, invoice_id=inv["id"])
    assert over.status_code == 400 and "υπερβαίνει" in over.json()["detail"]
    done = _tx(client, office.alpha, "apply_to_invoice", 24.0, invoice_id=inv["id"]).json()
    assert done["invoice_paid"] is True
    assert _doc(inv["id"])["status"] == "paid"
    assert client.get(f"/api/trust/accounts/{office.alpha}/open-invoices").json() == []


def test_money_above_what_is_owed_is_not_absorbed(client, office):
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 500.0)
    out = _tx(client, office.alpha, "apply_to_invoice", 200.0, invoice_id=inv["id"])
    assert out.status_code == 400 and "υπερβαίνει το οφειλόμενο" in out.json()["detail"]
    assert _account(client, office.alpha)["account"]["balance"] == 500.0
    assert _doc(inv["id"])["status"] == "issued"


def test_one_clients_money_does_not_pay_another_clients_invoice(client, office):
    foreign = _issued(client, office.beta)
    _tx(client, office.alpha, "deposit", 500.0)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=foreign["id"])
    assert out.status_code == 400 and "άλλον πελάτη" in out.json()["detail"]
    assert _account(client, office.alpha)["account"]["balance"] == 500.0
    assert _doc(foreign["id"])["status"] == "issued"


def test_apply_needs_an_open_invoice(client, office):
    _tx(client, office.alpha, "deposit", 500.0)
    assert _tx(client, office.alpha, "apply_to_invoice", 10.0).status_code == 400            # χωρίς τιμολόγιο

    draft = client.post("/api/invoices", json={
        "client_id": office.alpha, "date": _today(),
        "line_items": [{"description": "x", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()
    assert _tx(client, office.alpha, "apply_to_invoice", 10.0, invoice_id=draft["id"]).status_code == 400

    paid = _issued(client, office.alpha)
    assert _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=paid["id"]).status_code == 200
    again = _tx(client, office.alpha, "apply_to_invoice", 1.0, invoice_id=paid["id"])
    assert again.status_code == 400
    assert _account(client, office.alpha)["account"]["balance"] == 376.0
    assert _tx(client, office.alpha, "apply_to_invoice", 1.0, invoice_id="δεν-είναι-id").status_code == 400
    assert _tx(client, office.alpha, "apply_to_invoice", 1.0, invoice_id=str(ObjectId())).status_code == 404


def test_withholding_reduces_what_the_trust_account_must_cover(client, office):
    _enable_withholding(client)
    inv = _issued(client, office.alpha, net=1000.0)          # σύνολο 1.240, παρακράτηση 200 ⇒ πληρωτέο 1.040
    _tx(client, office.alpha, "deposit", 2000.0)
    rows = client.get(f"/api/trust/accounts/{office.alpha}/open-invoices").json()
    assert rows[0]["total"] == 1240.0 and rows[0]["payable"] == 1040.0 and rows[0]["outstanding"] == 1040.0
    out = _tx(client, office.alpha, "apply_to_invoice", 1040.0, invoice_id=inv["id"])
    assert out.status_code == 200 and out.json()["invoice_paid"] is True
    assert _account(client, office.alpha)["account"]["balance"] == 960.0


def test_failed_account_update_does_not_leave_the_invoice_claimed(client, office):
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 50.0)
    out = _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])
    assert out.status_code == 400 and "Ανεπαρκές" in out.json()["detail"]
    doc = _doc(inv["id"])
    assert doc["status"] == "issued" and not doc.get("trust_applied")


def test_failure_after_the_money_moved_is_rolled_back(client, office, monkeypatch):
    """Αν η εγγραφή στο ledger αποτύχει, ούτε το τιμολόγιο ούτε ο λογαριασμός μένουν μισοπειραγμένα."""
    from models import TrustTransaction
    inv = _issued(client, office.alpha)
    _tx(client, office.alpha, "deposit", 200.0)

    def boom(self, *args, **kwargs):
        raise RuntimeError("σφάλμα εγγραφής")

    with monkeypatch.context() as patched:
        patched.setattr(TrustTransaction, "to_mongo", boom)
        with pytest.raises(RuntimeError):
            _tx(client, office.alpha, "apply_to_invoice", 124.0, invoice_id=inv["id"])

    data = _account(client, office.alpha)
    assert data["account"]["balance"] == 200.0 and data["account"]["total_withdrawals"] == 0.0
    assert len(data["transactions"]) == 1 and data["ledger_check"]["consistent"] is True
    doc = _doc(inv["id"])
    assert doc["status"] == "issued" and not doc.get("trust_applied")


def test_trust_applied_cannot_be_set_through_the_invoice_api(client, office):
    body = {"client_id": office.alpha, "date": _today(), "trust_applied": 100.0,
            "line_items": [{"description": "x", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
    draft = client.post("/api/invoices", json=body).json()
    assert not draft.get("trust_applied")
    edited = client.put(f"/api/invoices/{draft['id']}", json={**body, "trust_applied": 50.0}).json()
    assert not edited.get("trust_applied")


def test_reminder_asks_for_what_is_still_owed():
    from email_utils import reminder_email_text, reminder_email_html
    invoice = {"number": "ΤΠΥ-2026-0001", "client_name": "ΑΛΦΑ ΑΕ", "total": 124.0, "withholding_amount": 0.0,
               "trust_applied": 100.0, "payment_method": "Τραπεζική κατάθεση"}
    text = reminder_email_text(invoice, {}, 10, 0)
    assert "Οφειλόμενο ποσό: 24,00 €" in text and "124,00" not in text
    html = reminder_email_html(invoice, {}, 10, 0)
    assert ">24,00 €<" in html and "124,00" not in html


# ========================================================= τραπεζική συμφωνία ====

def _csv(*rows):
    return ("Ημερομηνία;Αιτιολογία;Χρέωση;Πίστωση;Υπόλοιπο;Αναφορά\n" + "\n".join(rows) + "\n").encode("utf-8")


def _row(description, credit="", debit="", balance="", reference="", day=None):
    day = day or athens_today()
    return f"{day.strftime('%d/%m/%Y')};{description};{debit};{credit};{balance};{reference}"


def _import(client, content):
    r = client.post("/api/bank/import?bank=Δοκιμή", files={"file": ("extrait.csv", content, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def _bank_txs():
    from db import db
    return _run(db.bank_transactions.find({}).sort("_id", 1).to_list(100))


def _reconcile_job(firm_id, threshold=90):
    return _run(automation.job_reconcile(firm_id, {"auto_reconcile_min_confidence": threshold}))


def _notifications(kind):
    from db import db
    return _run(db.notifications.find({"type": kind}).to_list(50))


def test_client_that_withholds_tax_is_matched_on_the_payable_amount(client, office):
    _enable_withholding(client)
    inv = _issued(client, office.alpha, net=1000.0)         # σύνολο 1.240, πληρωτέο 1.040
    # Η κατάθεση του πελάτη είναι 1.040· με σύγκριση με το σύνολο (1.240) δεν θα ταίριαζε ποτέ.
    out = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="1.040,00")))
    assert out["created"] == 1 and out["auto_matched"] == 1 and out["needs_review"] == 0
    tx = _bank_txs()[0]
    assert tx["matched_invoice_id"] == inv["id"] and tx["match_confidence"] >= 90 and not tx["match_ambiguous"]

    assert _reconcile_job(office.firm_id) == {"confirmed": 1, "sent_for_review": 0}
    doc = _doc(inv["id"])
    assert doc["status"] == "paid" and doc["paid_via"] == "bank_auto"

    # ... και το ακαθάριστο (1.240) δεν είναι είσπραξη αυτού του τιμολογίου.
    other = _issued(client, office.beta, net=1000.0)
    miss = _import(client, _csv(_row("ΒΗΤΑ ΕΠΕ", credit="1.240,00")))
    assert miss["auto_matched"] == 0
    assert other["id"] != inv["id"]


def test_a_payment_that_differs_in_amount_is_never_paid_automatically(client, office):
    inv = _issued(client, office.alpha)                      # 124,00 €
    out = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="123,50")))
    assert out["auto_matched"] == 1 and out["needs_review"] == 1
    tx = _bank_txs()[0]
    assert tx["match_confidence"] <= 75 and tx["matched_invoice_id"] == inv["id"]

    # Ακόμη και με πολύ χαμηλό όριο, η αυτόματη εξόφληση χρειάζεται ακριβές ποσό.
    assert _reconcile_job(office.firm_id, threshold=30) == {"confirmed": 0, "sent_for_review": 1}
    assert _doc(inv["id"])["status"] == "issued"
    note = _notifications("reconcile_review")[0]
    assert note["requires_action"] and "διαφορά" in note["message"] and "0,50" in note["message"]

    # Χειροκίνητα: χρειάζεται ρητή αποδοχή της διαφοράς, και η διαφορά καταγράφεται.
    refused = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert refused.status_code == 409 and "accept_difference" in refused.json()["detail"]
    assert _doc(inv["id"])["status"] == "issued"
    accepted = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}&accept_difference=true")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["difference"] == -0.5
    settled = _bank_txs()[0]
    assert settled["match_status"] == "manual_matched" and settled["amount_difference"] == -0.5
    assert _doc(inv["id"])["status"] == "paid" and _doc(inv["id"])["paid_via"] == "bank_manual"


def test_high_confidence_alone_is_not_enough(client, office):
    """Εγγραφές από την παλιά έκδοση: σκορ 100 με ποσό 123,50 για τιμολόγιο 124,00."""
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.bank_transactions.insert_one({
        "firm_id": office.firm_id, "date": _today(), "description": "ΑΛΦΑ", "amount": 123.5, "external_id": "legacy-1",
        "match_status": "auto_matched", "match_confidence": 100, "matched_invoice_id": inv["id"],
        "matched_invoice_number": inv["number"]}))
    assert _reconcile_job(office.firm_id) == {"confirmed": 0, "sent_for_review": 1}
    assert _doc(inv["id"])["status"] == "issued"


def test_two_open_invoices_with_the_same_amount_need_a_human_choice(client, office):
    first = _issued(client, office.alpha)
    second = _issued(client, office.alpha)                   # ίδιος πελάτης, ίδιο ποσό
    out = _import(client, _csv(_row("ΑΛΦΑ ΑΕ ΑΜΟΙΒΗ", credit="124,00")))
    assert out["auto_matched"] == 1 and out["needs_review"] == 1
    tx = _bank_txs()[0]
    assert tx["match_ambiguous"] is True and tx["match_confidence"] <= 75

    assert _reconcile_job(office.firm_id, threshold=40) == {"confirmed": 0, "sent_for_review": 1}
    assert "άλλα τιμολόγια" in _notifications("reconcile_review")[0]["message"]
    bulk = client.post("/api/bank/confirm-auto-matches").json()
    assert bulk["confirmed"] == 0 and bulk["skipped_items"][0]["reason"] == "ambiguous"
    assert _doc(first["id"])["status"] == "issued" and _doc(second["id"])["status"] == "issued"

    # Ο άνθρωπος διαλέγει το δεύτερο: εξοφλείται εκείνο και μόνο.
    assert client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={second['id']}").status_code == 200
    assert _doc(second["id"])["status"] == "paid" and _doc(first["id"])["status"] == "issued"


def test_naming_the_invoice_number_resolves_the_ambiguity(client, office):
    first = _issued(client, office.alpha)
    second = _issued(client, office.alpha)
    out = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {second['number']}", credit="124,00")))
    assert out["auto_matched"] == 1 and out["needs_review"] == 0
    tx = _bank_txs()[0]
    assert tx["matched_invoice_id"] == second["id"] and not tx["match_ambiguous"]
    assert _reconcile_job(office.firm_id) == {"confirmed": 1, "sent_for_review": 0}
    assert _doc(second["id"])["status"] == "paid" and _doc(first["id"])["status"] == "issued"


def test_one_deposit_pays_one_invoice(client, office):
    inv = _issued(client, office.alpha)
    line = _row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="124,00")
    out = _import(client, _csv(line, line))                  # δύο πανομοιότυπες καταθέσεις
    assert out["created"] == 2 and out["skipped"] == 0       # και οι δύο καταχωρούνται (πριν: η δεύτερη χανόταν)
    first, second = _bank_txs()
    assert first["matched_invoice_id"] == inv["id"]
    assert second["match_status"] == "unmatched" and not second.get("matched_invoice_id")

    assert _reconcile_job(office.firm_id) == {"confirmed": 1, "sent_for_review": 0}
    assert _bank_txs()[1]["match_status"] == "unmatched"      # η δεύτερη μένει ανοιχτή για τον λογιστή


def test_reimporting_a_statement_adds_nothing(client, office):
    content = _csv(_row("ΕΝΟΙΚΙΟ", credit="500,00"), _row("ΕΝΟΙΚΙΟ", credit="500,00"), _row("ΔΕΗ", debit="80,00"))
    assert _import(client, content)["created"] == 3
    again = _import(client, content)
    assert again["created"] == 0 and again["skipped"] == 3
    assert len(_bank_txs()) == 3


def test_lines_with_different_references_are_different_movements(client, office):
    content = _csv(_row("ΕΝΤΟΛΗ ΜΕΤΑΦΟΡΑΣ", credit="200,00", reference="REF-1"),
                   _row("ΕΝΤΟΛΗ ΜΕΤΑΦΟΡΑΣ", credit="200,00", reference="REF-2"))
    assert _import(client, content)["created"] == 2


def test_overlapping_statements_neither_lose_nor_duplicate_lines(client, office):
    """Το νεότερο extrait έχει τις ίδιες γραμμές με άλλη σειρά και μία καινούργια."""
    line1 = _row("ΜΕΤΑΦΟΡΑ", credit="200,00", reference="REF-1")
    line2 = _row("ΜΕΤΑΦΟΡΑ", credit="200,00", reference="REF-2")
    assert _import(client, _csv(line1))["created"] == 1
    out = _import(client, _csv(line2, line1))
    assert out["created"] == 1 and out["skipped"] == 1
    assert sorted(t["reference"] for t in _bank_txs()) == ["REF-1", "REF-2"]


def test_an_invoice_already_proposed_for_one_deposit_is_not_proposed_again(client, office):
    inv = _issued(client, office.alpha)
    first = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="124,00", reference="Π1")))
    second = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="124,00", reference="Π2")))
    assert first["auto_matched"] == 1 and second["auto_matched"] == 0
    held, other = _bank_txs()
    assert held["matched_invoice_id"] == inv["id"]
    assert other["match_status"] == "unmatched" and not other.get("matched_invoice_id")


def test_lines_imported_before_the_change_are_still_recognised(client, office):
    from db import db
    legacy_key = f"{_today()}|500.00|ΕΝΟΙΚΙΟ"
    _run(db.bank_transactions.insert_one({"firm_id": office.firm_id, "date": _today(), "description": "ΕΝΟΙΚΙΟ",
                                          "amount": 500.0, "external_id": legacy_key, "match_status": "unmatched"}))
    out = _import(client, _csv(_row("ΕΝΟΙΚΙΟ", credit="500,00", reference="Α1")))
    assert out["created"] == 0 and out["skipped"] == 1


def test_manual_reconcile_rules(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    _import(client, _csv(_row("ΑΛΦΑ ΑΕ", credit="124,00"), _row("ΔΕΗ", debit="124,00")))
    credit_tx, debit_tx = _bank_txs()

    # χρέωση: δεν είναι είσπραξη
    assert client.post(f"/api/bank/reconcile/{debit_tx['_id']}?invoice_id={inv['id']}").status_code == 400
    # άγνωστο τιμολόγιο
    assert client.post(f"/api/bank/reconcile/{credit_tx['_id']}?invoice_id={ObjectId()}").status_code == 404

    ok = client.post(f"/api/bank/reconcile/{credit_tx['_id']}?invoice_id={inv['id']}")
    assert ok.status_code == 200 and _doc(inv["id"])["paid_via"] == "bank_manual"

    # η ίδια κατάθεση δεν εξοφλεί και δεύτερο τιμολόγιο, ούτε αγνοείται αφού συμφωνήθηκε
    other = _issued(client, office.alpha)
    assert client.post(f"/api/bank/reconcile/{credit_tx['_id']}?invoice_id={other['id']}").status_code == 409
    assert client.post(f"/api/bank/reconcile/{credit_tx['_id']}?ignore=true").status_code == 409
    assert _doc(other["id"])["status"] == "issued"
    assert _run(db.bank_transactions.find_one({"_id": credit_tx["_id"]}))["match_status"] == "manual_matched"


def test_a_second_deposit_cannot_settle_an_invoice_that_is_already_paid(client, office):
    inv = _issued(client, office.alpha)
    _import(client, _csv(_row("ΑΛΦΑ ΑΕ", credit="124,00", reference="Α"), _row("ΑΛΦΑ ΑΕ", credit="124,00", reference="Β")))
    first, second = _bank_txs()
    assert client.post(f"/api/bank/reconcile/{first['_id']}?invoice_id={inv['id']}").status_code == 200

    dup = client.post(f"/api/bank/reconcile/{second['_id']}?invoice_id={inv['id']}")
    assert dup.status_code == 409 and "έχει ήδη εξοφληθεί" in dup.json()["detail"]
    assert _bank_txs()[1]["match_status"] == "unmatched"


def test_deposit_can_be_linked_to_an_invoice_the_user_already_marked_paid(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"status": "paid", "paid_via": "manual"}}))
    _import(client, _csv(_row("ΑΛΦΑ ΑΕ", credit="124,00")))
    tx = _bank_txs()[0]
    ok = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert ok.status_code == 200 and ok.json()["status"] == "linked"
    assert _bank_txs()[0]["match_status"] == "manual_matched"


@pytest.mark.parametrize("paid_via", ["stripe", "trust"])
def test_deposit_for_an_invoice_paid_by_another_channel_is_a_conflict(client, office, paid_via):
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"status": "paid", "paid_via": paid_via}}))
    _import(client, _csv(_row("ΑΛΦΑ ΑΕ", credit="124,00")))
    tx = _bank_txs()[0]
    out = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={inv['id']}")
    assert out.status_code == 409 and "διπλοπληρωμή" in out.json()["detail"]


def test_cancelled_and_draft_invoices_cannot_be_settled(client, office):
    from db import db
    cancelled = _issued(client, office.alpha)
    _run(db.invoices.update_one({"_id": ObjectId(cancelled["id"])}, {"$set": {"status": "cancelled"}}))
    draft = client.post("/api/invoices", json={
        "client_id": office.alpha, "date": _today(),
        "line_items": [{"description": "x", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}).json()
    _import(client, _csv(_row("ΑΛΦΑ ΑΕ", credit="124,00")))
    tx = _bank_txs()[0]
    for target in (cancelled["id"], draft["id"]):
        out = client.post(f"/api/bank/reconcile/{tx['_id']}?invoice_id={target}")
        assert out.status_code == 409 and "δεν είναι ανοιχτό" in out.json()["detail"]


def test_duplicate_deposits_are_sent_to_the_inbox_not_absorbed(client, office):
    """Δύο προτάσεις για το ίδιο τιμολόγιο (π.χ. από παλιά έκδοση): η πρώτη εξοφλεί, η δεύτερη
    είναι πιθανή διπλοπληρωμή και πρέπει να φτάσει σε άνθρωπο — πριν «συμφωνιόταν» και χανόταν."""
    from db import db
    inv = _issued(client, office.alpha)
    for n in (1, 2):
        _run(db.bank_transactions.insert_one({
            "firm_id": office.firm_id, "date": _today(), "description": f"ΑΛΦΑ {n}", "amount": 124.0,
            "external_id": f"dup-{n}", "match_status": "auto_matched", "match_confidence": 95,
            "matched_invoice_id": inv["id"], "matched_invoice_number": inv["number"]}))
    assert _reconcile_job(office.firm_id) == {"confirmed": 1, "sent_for_review": 1}
    first, second = _bank_txs()
    assert first["match_status"] == "auto_confirmed"
    assert second["match_status"] == "unmatched" and not second.get("matched_invoice_id")
    conflict = _notifications("reconcile_conflict")[0]
    assert conflict["requires_action"] and "έχει ήδη εξοφληθεί" in conflict["message"]
    assert [a["key"] for a in conflict["actions"]] == ["ignore", "done"]
    # δεύτερος κύκλος: τίποτα νέο
    assert _reconcile_job(office.firm_id) == {"confirmed": 0, "sent_for_review": 0}

    # Η απόφαση του ανθρώπου («αγνόησε») εφαρμόζεται στην κίνηση.
    out = _run(automation.resolve_notification(office.firm_id, str(conflict["_id"]), "lawyer@example.gr", "ignore"))
    assert out["resolution"] == "ignored" and _bank_txs()[1]["match_status"] == "ignored"


def test_inbox_confirmation_follows_the_same_rules(client, office):
    from db import db
    from invoice_services import SettlementRefused
    inv = _issued(client, office.alpha)
    _run(db.bank_transactions.insert_one({
        "firm_id": office.firm_id, "date": _today(), "description": "ΑΛΦΑ", "amount": 124.0, "external_id": "inbox-1",
        "match_status": "auto_matched", "match_confidence": 70, "matched_invoice_id": inv["id"],
        "matched_invoice_number": inv["number"]}))
    assert _reconcile_job(office.firm_id) == {"confirmed": 0, "sent_for_review": 1}
    note = _notifications("reconcile_review")[0]
    assert "κάτω από το όριο" in note["message"]

    # Στο μεταξύ το τιμολόγιο εξοφλήθηκε με κάρτα: το «Ναι, εξοφλήθηκε» δεν περνά.
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"status": "paid", "paid_via": "stripe"}}))
    with pytest.raises(SettlementRefused) as refused:
        _run(automation.resolve_notification(office.firm_id, str(note["_id"]), "lawyer@example.gr", "confirm"))
    assert refused.value.reason == "already_paid"
    still_open = _run(db.notifications.find_one({"_id": note["_id"]}))
    assert still_open.get("resolved_at") is None             # η απόφαση παραμένει εκκρεμής
    assert _bank_txs()[0]["match_status"] == "auto_matched"


def test_inbox_confirmation_accepts_a_difference_and_records_it(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.bank_transactions.insert_one({
        "firm_id": office.firm_id, "date": _today(), "description": "ΑΛΦΑ", "amount": 123.5, "external_id": "inbox-2",
        "match_status": "auto_matched", "match_confidence": 80, "matched_invoice_id": inv["id"],
        "matched_invoice_number": inv["number"]}))
    _reconcile_job(office.firm_id)
    note = _notifications("reconcile_review")[0]
    assert "διαφορά" in note["message"]                      # η διαφορά αναγράφεται πριν την απόφαση
    out = _run(automation.resolve_notification(office.firm_id, str(note["_id"]), "lawyer@example.gr", "confirm"))
    assert out["resolution"] == "confirmed"
    assert _bank_txs()[0]["amount_difference"] == -0.5 and _doc(inv["id"])["status"] == "paid"


def test_stale_notification_does_not_touch_an_already_settled_movement(client, office):
    from db import db
    inv = _issued(client, office.alpha)
    tx_id = _run(db.bank_transactions.insert_one({
        "firm_id": office.firm_id, "date": _today(), "description": "ΑΛΦΑ", "amount": 124.0, "external_id": "stale-1",
        "match_status": "auto_matched", "match_confidence": 70, "matched_invoice_id": inv["id"],
        "matched_invoice_number": inv["number"]})).inserted_id
    _reconcile_job(office.firm_id)
    note = _notifications("reconcile_review")[0]
    # Ο χρήστης συμφωνεί την κίνηση από αλλού (μαζική επιβεβαίωση)· η ειδοποίηση μένει πίσω.
    assert client.post("/api/bank/confirm-auto-matches").json()["confirmed"] == 1
    out = _run(automation.resolve_notification(office.firm_id, str(note["_id"]), "lawyer@example.gr", "ignore"))
    assert out["resolution"] == "already_settled"
    assert _run(db.bank_transactions.find_one({"_id": tx_id}))["match_status"] == "manual_matched"


def test_bulk_confirmation_takes_only_the_safe_ones(client, office):
    from db import db
    exact = _issued(client, office.alpha)
    off = _issued(client, office.beta)
    ambiguous = _issued(client, office.alpha, net=200.0)
    base = {"firm_id": office.firm_id, "date": _today(), "match_status": "auto_matched", "match_confidence": 70}
    _run(db.bank_transactions.insert_many([
        {**base, "description": "α", "amount": 124.0, "external_id": "b1", "matched_invoice_id": exact["id"], "matched_invoice_number": exact["number"]},
        {**base, "description": "β", "amount": 123.0, "external_id": "b2", "matched_invoice_id": off["id"], "matched_invoice_number": off["number"]},
        {**base, "description": "γ", "amount": 248.0, "external_id": "b3", "matched_invoice_id": ambiguous["id"], "matched_invoice_number": ambiguous["number"], "match_ambiguous": True},
    ]))
    out = client.post("/api/bank/confirm-auto-matches").json()
    assert out["confirmed"] == 1 and out["skipped"] == 2
    assert {item["reason"] for item in out["skipped_items"]} == {"amount_mismatch", "ambiguous"}
    assert _doc(exact["id"])["status"] == "paid" and _doc(exact["id"])["paid_via"] == "bank_manual"
    assert _doc(off["id"])["status"] == "issued" and _doc(ambiguous["id"])["status"] == "issued"
    statuses = {t["external_id"]: t["match_status"] for t in _bank_txs()}
    assert statuses == {"b1": "manual_matched", "b2": "auto_matched", "b3": "auto_matched"}


def test_ignoring_a_movement_clears_its_suggestion(client, office):
    inv = _issued(client, office.alpha)
    _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="124,00")))
    tx = _bank_txs()[0]
    assert client.post(f"/api/bank/reconcile/{tx['_id']}?ignore=true").status_code == 200
    ignored = _bank_txs()[0]
    assert ignored["match_status"] == "ignored" and not ignored["matched_invoice_id"]
    # το τιμολόγιο γίνεται ξανά διαθέσιμο για επόμενη κατάθεση
    out = _import(client, _csv(_row(f"ΑΛΦΑ ΑΕ {inv['number']}", credit="124,00", reference="ΝΕΑ")))
    assert out["auto_matched"] == 1


# ---- PSD2 και κάρτα ---------------------------------------------------------

@pytest.mark.asyncio
async def test_psd2_sync_also_binds_one_invoice_to_one_deposit(monkeypatch):
    import openbanking
    from db import db
    firm = "firm-ob-r4"
    await db.settings.insert_one({
        "firm_id": firm, "company_name": "Γραφείο", "openbanking_enabled": True, "gocardless_secret_id": "sid",
        "gocardless_secret_key": "skey", "gocardless_institution_name": "Πειραιώς", "gocardless_account_ids": ["acc-1"]})
    client_doc = await db.clients.insert_one({"firm_id": firm, "name": "Παπαδόπουλος"})
    inv = await db.invoices.insert_one({
        "firm_id": firm, "number": "ΤΠΥ-2026-0001", "client_id": str(client_doc.inserted_id), "client_name": "Παπαδόπουλος",
        "status": "issued", "total": 620.0, "date": _today(), "line_items": []})

    async def fake_fetch(settings, account_id, date_from=None):
        return [{"date": _today(), "amount": 620.0, "description": "ΠΑΠΑΔΟΠΟΥΛΟΣ ΤΠΥ-2026-0001",
                 "reference": "", "external_id": f"TX-{n}"} for n in (1, 2)]

    monkeypatch.setattr(openbanking, "fetch_transactions", fake_fetch)
    out = (await automation.run_firm(firm, ["bank_sync"]))[0]
    assert out["status"] != "error", out.get("error")
    assert out["stats"]["created"] == 2 and out["stats"]["auto_matched"] == 1
    first = await db.bank_transactions.find_one({"firm_id": firm, "external_id": "psd2:acc-1:TX-1"})
    second = await db.bank_transactions.find_one({"firm_id": firm, "external_id": "psd2:acc-1:TX-2"})
    assert first["matched_invoice_id"] == str(inv.inserted_id) and second["match_status"] == "unmatched"


@pytest.mark.asyncio
async def test_card_payment_does_not_resurrect_a_cancelled_invoice():
    import routes_payments
    from db import db
    firm = "firm-card-r4"
    cancelled = await db.invoices.insert_one({"firm_id": firm, "number": "ΤΠΥ-2026-0007", "status": "cancelled", "total": 124.0})
    await db.payment_transactions.insert_one({
        "session_id": "cs_1", "firm_id": firm, "invoice_id": str(cancelled.inserted_id), "amount": 124.0,
        "status": "initiated", "payment_status": "unpaid"})
    await routes_payments._mark_paid("cs_1")
    assert (await db.invoices.find_one({"_id": cancelled.inserted_id}))["status"] == "cancelled"
    note = await db.notifications.find_one({"type": "card_payment_unapplied"})
    assert note and note["requires_action"] and "cancelled" in note["message"]

    # Σε ανοιχτό τιμολόγιο η πληρωμή εφαρμόζεται κανονικά και δηλώνει το κανάλι.
    open_inv = await db.invoices.insert_one({"firm_id": firm, "number": "ΤΠΥ-2026-0008", "status": "issued", "total": 124.0})
    await db.payment_transactions.insert_one({
        "session_id": "cs_2", "firm_id": firm, "invoice_id": str(open_inv.inserted_id), "amount": 124.0,
        "status": "initiated", "payment_status": "unpaid"})
    await routes_payments._mark_paid("cs_2")
    paid = await db.invoices.find_one({"_id": open_inv.inserted_id})
    assert paid["status"] == "paid" and paid["paid_via"] == "stripe"
    # επανάληψη του webhook: καμία νέα ειδοποίηση, καμία αλλαγή
    await routes_payments._mark_paid("cs_2")
    assert await db.notifications.count_documents({"type": "card_payment_unapplied"}) == 1
