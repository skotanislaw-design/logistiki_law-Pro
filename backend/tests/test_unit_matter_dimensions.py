"""Φάση 1 του «ψηφιακού λογιστή»: σε ποιον ανήκει ένα έξοδο ή ένα τιμολόγιο (πελάτης, υπόθεση, δικηγόρος).

Τι ελέγχεται εδώ, και γιατί:

* **Απομόνωση γραφείων.** Υπόθεση, πελάτης ή δικηγόρος άλλου γραφείου δεν δένεται σε έγγραφα του χρήστη, και η απάντηση
  δεν τον ξεχωρίζει από ανύπαρκτο (ίδιο 404) — αλλιώς διαρρέει η ύπαρξη ξένων εγγράφων.
* **Συνέπεια.** Ο πελάτης ενός εξόδου υπόθεσης προκύπτει πάντα από την υπόθεση· αντιφατικοί συνδυασμοί απορρίπτονται αντί
  να «διορθωθούν» σιωπηλά.
* **Δεν χάνονται δεδομένα.** Ένα PUT από καρτέλα που δεν ξέρει τα νέα πεδία δεν τα σβήνει.
* **Ακεραιότητα παραστατικών.** Η σύνδεση ενός τιμολογίου με υπόθεση/δικηγόρο, ακόμη και μετά την έκδοση, δεν αγγίζει
  κανένα άλλο πεδίο του εγγράφου.
"""
import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today

PASSWORD = "Str0ngPass!"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


# ------------------------------------------------------------------ βοηθητικά ----

def _register(c, email, name="Χρήστης"):
    r = c.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _member(firm_id, email, name, role):
    """Δεύτερος χρήστης του ίδιου γραφείου (η δημόσια εγγραφή ανοίγει πάντα νέο γραφείο)."""
    from auth import hash_password
    from db import db
    from models import User
    doc = User(email=email, password_hash=hash_password(PASSWORD), name=name, role=role, firm_id=firm_id).to_mongo()
    return str(_run(db.users.insert_one(doc)).inserted_id)


def _set_role(email, role):
    from db import db
    _run(db.users.update_one({"email": email}, {"$set": {"role": role}}))


def _make_client(c, name, **over):
    r = c.post("/api/clients", json={"name": name, "client_type": "individual", **over})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _make_case(c, title, client_id, client_name="", lawyer_id=""):
    body = {"title": title, "client_id": client_id, "client_name": client_name}
    if lawyer_id:
        body["lawyer_id"] = lawyer_id
    r = c.post("/api/cases", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _expense(**over):
    body = {"vendor": "Προμηθευτής", "date": "2026-09-20", "amount": 124.0, "vat_amount": 24.0, "vat_rate": 24,
            "category": "Λοιπά"}
    body.update(over)
    return body


def _draft(c, client_id, net=1000.0, **over):
    """Η απάντηση του POST (όχι το JSON), ώστε τα tests να ελέγχουν και τους κωδικούς σφάλματος."""
    body = {"client_id": client_id, "date": athens_today().isoformat(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    return c.post("/api/invoices", json=body)


def _issued(c, client_id, **over):
    draft = _draft(c, client_id, **over)
    assert draft.status_code == 200, draft.text
    r = c.post(f"/api/invoices/{draft.json()['id']}/issue")
    assert r.status_code == 200, r.text
    return r.json()


def _raw(collection, doc_id):
    from db import db
    return _run(db[collection].find_one({"_id": ObjectId(doc_id)}))


def _matter(e):
    return (e["scope"], e["case_id"], e["client_id"], e["lawyer_id"], e["rebillable"])


@pytest.fixture()
def office(client):
    """Το γραφείο του χρήστη: διαχειριστής, δύο δικηγόροι, ένας λογιστής, δύο πελάτες και μια υπόθεση με υπεύθυνο."""
    me = _register(client, "owner@example.gr", "Διαχειριστής")
    _set_role("owner@example.gr", "admin")
    firm = me["firm_id"]
    lawyer = _member(firm, "lawyer@example.gr", "Δικηγόρος Α", "lawyer")
    lawyer_b = _member(firm, "lawyer2@example.gr", "Δικηγόρος Β", "lawyer")
    accountant = _member(firm, "acct@example.gr", "Λογιστής", "accountant")
    alpha = _make_client(client, "ΑΛΦΑ ΑΕ", client_type="company", afm="123456783")
    beta = _make_client(client, "Ιδιώτης Πελάτης")
    case = _make_case(client, "Υπόθεση Α", alpha, "ΑΛΦΑ ΑΕ", lawyer_id=lawyer)
    return SimpleNamespace(firm=firm, admin=me["id"], lawyer=lawyer, lawyer_b=lawyer_b, accountant=accountant,
                           alpha=alpha, beta=beta, case=case["id"])


@pytest.fixture()
def rival():
    """Ένα ΑΛΛΟ γραφείο, με δικό του πελάτη, υπόθεση, δικηγόρο, έξοδο και τιμολόγιο."""
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        me = _register(c, "rival@example.gr", "Άλλο γραφείο")
        lawyer = _member(me["firm_id"], "rlawyer@example.gr", "Ξένος Δικηγόρος", "lawyer")
        cl = _make_client(c, "ΞΕΝΟΣ ΠΕΛΑΤΗΣ")
        case = _make_case(c, "Ξένη υπόθεση", cl, "ΞΕΝΟΣ ΠΕΛΑΤΗΣ", lawyer_id=lawyer)
        expense = c.post("/api/expenses", json=_expense(vendor="ΞΕΝΟ ΕΞΟΔΟ", scope="case", case_id=case["id"]))
        assert expense.status_code == 200, expense.text
        invoice = _draft(c, cl, case_id=case["id"])
        assert invoice.status_code == 200, invoice.text
        yield SimpleNamespace(client=c, firm=me["firm_id"], lawyer=lawyer, case=case["id"], customer=cl,
                              expense=expense.json()["id"], invoice=invoice.json()["id"])


# ======================================================================== ΕΞΟΔΑ ====

def test_a_plain_expense_stays_unassigned(client, office):
    r = client.post("/api/expenses", json=_expense())
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("unassigned", "", "", "", False)
    assert r.json()["vat_deductible_pct"] is None


def test_case_expense_takes_its_client_from_the_case_and_ignores_the_one_sent(client, office):
    r = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case, client_id=office.beta))
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == office.case
    assert r.json()["client_id"] == office.alpha            # ο πελάτης της υπόθεσης, όχι αυτός που στάλθηκε


def test_case_expense_inherits_the_responsible_lawyer_unless_another_is_chosen(client, office):
    inherited = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    assert inherited["lawyer_id"] == office.lawyer
    chosen = client.post("/api/expenses",
                         json=_expense(scope="case", case_id=office.case, lawyer_id=office.lawyer_b)).json()
    assert chosen["lawyer_id"] == office.lawyer_b


def test_client_expense_keeps_the_client_and_has_no_case(client, office):
    r = client.post("/api/expenses", json=_expense(scope="client", client_id=office.beta, lawyer_id=office.lawyer))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("client", "", office.beta, office.lawyer, False)


CONTRADICTIONS = {
    "client scope without a client": lambda o: {"scope": "client"},
    "case scope without a case": lambda o: {"scope": "case"},
    "office expense with a client": lambda o: {"scope": "office", "client_id": o.alpha},
    "office expense with a case": lambda o: {"scope": "office", "case_id": o.case},
    "unassigned expense with a case": lambda o: {"scope": "unassigned", "case_id": o.case},
    "client scope with a case": lambda o: {"scope": "client", "client_id": o.alpha, "case_id": o.case},
    "office expense that is rebillable": lambda o: {"scope": "office", "rebillable": True},
    "unassigned expense that is rebillable": lambda o: {"scope": "unassigned", "rebillable": True},
}


@pytest.mark.parametrize("name", list(CONTRADICTIONS))
def test_contradictory_combinations_are_refused_not_silently_fixed(client, office, name):
    r = client.post("/api/expenses", json=_expense(**CONTRADICTIONS[name](office)))
    assert r.status_code == 400, (name, r.text)
    assert client.get("/api/expenses").json() == []            # τίποτα δεν αποθηκεύτηκε


def test_rebillable_is_kept_for_client_and_case_expenses(client, office):
    on_client = client.post("/api/expenses", json=_expense(scope="client", client_id=office.alpha, rebillable=True)).json()
    on_case = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case, rebillable=True)).json()
    assert on_client["rebillable"] is True and on_case["rebillable"] is True


def test_unknown_scope_is_refused(client, office):
    assert client.post("/api/expenses", json=_expense(scope="everyone")).status_code in (400, 422)


def test_the_resolver_refuses_an_unknown_scope_even_when_called_without_the_model(client, office):
    """Το API απορρίπτει άγνωστο εύρος ήδη στο μοντέλο (422)· ο έλεγχος του resolver είναι η δεύτερη γραμμή άμυνας για
    κάθε διαδρομή που τον καλεί χωρίς το μοντέλο — π.χ. ο bot Telegram της Φάσης 2 ή μια εισαγωγή από αρχείο."""
    from fastapi import HTTPException
    from dimensions import resolve_expense_matter

    with pytest.raises(HTTPException) as refused:
        _run(resolve_expense_matter({"firm_id": office.firm}, scope="everyone", case_id=office.case,
                                    client_id="", lawyer_id="", rebillable=False))
    assert refused.value.status_code == 400


def test_references_of_another_firm_look_exactly_like_missing_ones(client, office, rival):
    """Η ίδια απάντηση για ξένο και για ανύπαρκτο: αλλιώς ο χρήστης μπορεί να ανακαλύψει ξένα έγγραφα."""
    missing = str(ObjectId())
    pairs = [
        (dict(scope="case", case_id=rival.case), dict(scope="case", case_id=missing)),
        (dict(scope="client", client_id=rival.customer), dict(scope="client", client_id=missing)),
        (dict(scope="office", lawyer_id=rival.lawyer), dict(scope="office", lawyer_id=missing)),
    ]
    for foreign, absent in pairs:
        a = client.post("/api/expenses", json=_expense(**foreign))
        b = client.post("/api/expenses", json=_expense(**absent))
        assert a.status_code == b.status_code == 404, (a.text, b.text)
        assert a.json() == b.json()
    assert client.get("/api/expenses").json() == []


@pytest.mark.parametrize("over", [dict(scope="case", case_id="not-an-id"), dict(scope="client", client_id="xyz"),
                                  dict(scope="office", lawyer_id="123")])
def test_malformed_references_are_a_clean_400(client, office, over):
    assert client.post("/api/expenses", json=_expense(**over)).status_code == 400


def test_only_lawyers_of_the_firm_can_be_responsible(client, office):
    accountant = client.post("/api/expenses", json=_expense(scope="office", lawyer_id=office.accountant))
    assert accountant.status_code == 400
    admin = client.post("/api/expenses", json=_expense(scope="office", lawyer_id=office.admin))
    assert admin.status_code == 200 and admin.json()["lawyer_id"] == office.admin


def test_put_replaces_the_group_and_clears_what_no_longer_applies(client, office):
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case, rebillable=True)).json()
    assert _matter(e) == ("case", office.case, office.alpha, office.lawyer, True)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="office"))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("office", "", "", "", False)
    assert _matter(client.get("/api/expenses").json()[0]) == ("office", "", "", "", False)


def test_put_to_another_case_rederives_the_client(client, office):
    other = _make_case(client, "Υπόθεση Β", office.beta, "Ιδιώτης Πελάτης")
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="case", case_id=other["id"]))
    assert r.status_code == 200, r.text
    assert r.json()["client_id"] == office.beta
    assert r.json()["lawyer_id"] == ""                          # η νέα υπόθεση δεν έχει υπεύθυνο


def test_put_with_a_foreign_reference_is_refused_and_changes_nothing(client, office, rival):
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(vendor="ΑΛΛΑΓΗ", scope="case", case_id=rival.case))
    assert r.status_code == 404
    stored = _raw("expenses", e["id"])
    assert stored["vendor"] == "Προμηθευτής" and stored["case_id"] == office.case


def test_put_from_a_stale_form_keeps_the_classification(client, office):
    """Καρτέλα browser ανοιγμένη πριν την ενημέρωση: στέλνει σώμα χωρίς κανένα από τα νέα πεδία."""
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case, vat_deductible_pct=50,
                                                   rebillable=True)).json()
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(vendor="Νέος προμηθευτής"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["vendor"] == "Νέος προμηθευτής"
    assert _matter(out) == ("case", office.case, office.alpha, office.lawyer, True)
    assert out["vat_deductible_pct"] == 50.0


def test_explicit_vat_percentage_can_be_set_zeroed_and_removed(client, office):
    e = client.post("/api/expenses", json=_expense(vat_deductible_pct=50)).json()
    assert e["vat_deductible_pct"] == 50.0
    zero = client.put(f"/api/expenses/{e['id']}", json=_expense(vat_deductible_pct=0)).json()
    assert zero["vat_deductible_pct"] == 0.0                    # το 0 είναι τιμή («δεν εκπίπτει»), όχι «κενό»
    assert _raw("expenses", e["id"])["vat_deductible_pct"] == 0.0
    cleared = client.put(f"/api/expenses/{e['id']}", json=_expense(vat_deductible_pct=None)).json()
    assert cleared["vat_deductible_pct"] is None
    assert "vat_deductible_pct" not in _raw("expenses", e["id"])      # αφαιρέθηκε, δεν έμεινε null


@pytest.mark.parametrize("bad", [-0.01, 100.01, 1000, -50])
def test_vat_percentage_outside_0_to_100_is_refused(client, office, bad):
    r = client.post("/api/expenses", json=_expense(vat_deductible_pct=bad))
    assert r.status_code == 400, r.text
    assert client.get("/api/expenses").json() == []
    e = client.post("/api/expenses", json=_expense(vat_deductible_pct=40)).json()
    assert client.put(f"/api/expenses/{e['id']}", json=_expense(vat_deductible_pct=bad)).status_code == 400
    assert _raw("expenses", e["id"])["vat_deductible_pct"] == 40.0       # η αποτυχημένη αλλαγή δεν έγραψε τίποτα


@pytest.mark.parametrize("good", [0, 100, 33.33, "50"])
def test_vat_percentage_within_range_is_accepted(client, office, good):
    r = client.post("/api/expenses", json=_expense(vat_deductible_pct=good))
    assert r.status_code == 200, r.text
    assert r.json()["vat_deductible_pct"] == float(good)


def test_vat_percentage_that_is_not_a_number_is_refused(client, office):
    assert client.post("/api/expenses", json=_expense(vat_deductible_pct="πολύ")).status_code in (400, 422)


# ================================================================== ΥΠΟΘΕΣΕΙΣ ====

def test_case_lawyer_is_checked_against_the_firm(client, office, rival):
    def create(**over):
        return client.post("/api/cases", json={"title": "Νέα υπόθεση", "client_id": office.alpha, **over})

    assert create(lawyer_id=office.lawyer).status_code == 200
    assert create(lawyer_id="").json()["lawyer_id"] == ""
    assert create().json()["lawyer_id"] == ""
    assert create(lawyer_id=rival.lawyer).status_code == 404
    assert create(lawyer_id=str(ObjectId())).status_code == 404
    assert create(lawyer_id=office.accountant).status_code == 400
    assert create(lawyer_id="zzz").status_code == 400


def test_case_update_checks_the_lawyer_only_when_it_changed(client, office):
    """Δικηγόρος που έφυγε από το γραφείο δεν εμποδίζει την επεξεργασία της υπόθεσής του."""
    from db import db
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    _run(db.users.delete_one({"_id": ObjectId(office.lawyer)}))
    same = client.put(f"/api/cases/{office.case}", json={**case, "title": "Νέος τίτλος"})
    assert same.status_code == 200, same.text
    assert same.json()["lawyer_id"] == office.lawyer and same.json()["title"] == "Νέος τίτλος"
    changed = client.put(f"/api/cases/{office.case}", json={**case, "lawyer_id": str(ObjectId())})
    assert changed.status_code == 404
    assert client.get(f"/api/cases/{office.case}").json()["case"]["lawyer_id"] == office.lawyer


def test_case_update_checks_a_changed_lawyer_against_the_firm(client, office, rival):
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    assert client.put(f"/api/cases/{office.case}", json={**case, "lawyer_id": rival.lawyer}).status_code == 404
    assert client.put(f"/api/cases/{office.case}", json={**case, "lawyer_id": office.accountant}).status_code == 400
    ok = client.put(f"/api/cases/{office.case}", json={**case, "lawyer_id": office.lawyer_b})
    assert ok.status_code == 200 and ok.json()["lawyer_id"] == office.lawyer_b


def test_case_update_from_a_stale_form_keeps_the_lawyer(client, office):
    stale = {"title": "Μετονομασία", "client_id": office.alpha, "client_name": "ΑΛΦΑ ΑΕ"}      # χωρίς lawyer_id
    r = client.put(f"/api/cases/{office.case}", json=stale)
    assert r.status_code == 200, r.text
    assert r.json()["lawyer_id"] == office.lawyer
    cleared = client.put(f"/api/cases/{office.case}", json={**stale, "lawyer_id": ""})
    assert cleared.json()["lawyer_id"] == ""                    # το ρητό κενό σημαίνει «χωρίς υπεύθυνο»


def test_changing_the_client_of_a_case_moves_its_expenses_with_it(client, office, rival):
    from db import db
    on_case = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    other_case = _make_case(client, "Άλλη υπόθεση", office.alpha, "ΑΛΦΑ ΑΕ")
    on_other = client.post("/api/expenses", json=_expense(scope="case", case_id=other_case["id"])).json()
    on_client = client.post("/api/expenses", json=_expense(scope="client", client_id=office.alpha)).json()
    # έγγραφο ΑΛΛΟΥ γραφείου που τυχαίνει να έχει το ίδιο case_id: δεν πρέπει να αλλάξει
    foreign = _run(db.expenses.insert_one({"firm_id": rival.firm, "vendor": "ΞΕΝΟ", "date": "2026-09-01", "amount": 1.0,
                                          "scope": "case", "case_id": office.case, "client_id": "ξένος-πελάτης"}))

    case = client.get(f"/api/cases/{office.case}").json()["case"]
    r = client.put(f"/api/cases/{office.case}", json={**case, "client_id": office.beta, "client_name": "Ιδιώτης"})
    assert r.status_code == 200, r.text

    by_id = {e["id"]: e for e in client.get("/api/expenses").json()}
    assert by_id[on_case["id"]]["client_id"] == office.beta
    assert by_id[on_other["id"]]["client_id"] == office.alpha        # άλλη υπόθεση: αμετάβλητο
    assert by_id[on_client["id"]]["client_id"] == office.alpha       # έξοδο πελάτη (όχι υπόθεσης): αμετάβλητο
    assert _raw("expenses", str(foreign.inserted_id))["client_id"] == "ξένος-πελάτης"


def test_editing_a_case_without_changing_its_client_leaves_expenses_alone(client, office):
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    assert client.put(f"/api/cases/{office.case}", json={**case, "title": "Άλλος τίτλος"}).status_code == 200
    assert _raw("expenses", e["id"])["client_id"] == office.alpha


def test_case_shows_the_issued_and_paid_invoices_and_all_expenses_linked_to_it(client, office, rival):
    from db import db
    _draft(client, office.alpha, net=100, case_id=office.case)                       # πρόχειρο: δεν μετρά
    _issued(client, office.alpha, net=200, case_id=office.case)
    paid = _issued(client, office.alpha, net=300, case_id=office.case)
    cancelled = _issued(client, office.alpha, net=400, case_id=office.case)
    _run(db.invoices.update_one({"_id": ObjectId(paid["id"])}, {"$set": {"status": "paid"}}))
    _run(db.invoices.update_one({"_id": ObjectId(cancelled["id"])}, {"$set": {"status": "cancelled"}}))
    _issued(client, office.alpha, net=999)                                           # χωρίς υπόθεση
    client.post("/api/expenses", json=_expense(amount=124.0, vat_amount=24.0, scope="case", case_id=office.case))
    client.post("/api/expenses", json=_expense(amount=62.0, vat_amount=12.0, scope="case", case_id=office.case))
    client.post("/api/expenses", json=_expense(amount=500.0, vat_amount=0, scope="office"))
    # έγγραφα ΑΛΛΟΥ γραφείου με το ίδιο case_id δεν μετρούν
    _run(db.expenses.insert_one({"firm_id": rival.firm, "vendor": "ΞΕΝΟ", "date": "2026-09-01", "amount": 7777.0,
                                 "case_id": office.case, "scope": "case"}))
    _run(db.invoices.insert_one({"firm_id": rival.firm, "status": "paid", "subtotal": 5555.0, "case_id": office.case}))

    linked = client.get(f"/api/cases/{office.case}").json()["linked"]
    assert linked == {"invoices": {"count": 2, "subtotal": 500.0}, "expenses": {"count": 2, "total": 186.0}}


def test_credit_note_reduces_the_revenue_linked_to_the_case(client, office):
    from db import db
    original = _issued(client, office.alpha, net=200, case_id=office.case)
    _run(db.invoices.update_one({"_id": ObjectId(original["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={"reason": "Λάθος"}).json()
    assert client.post(f"/api/invoices/{credit['id']}/issue").status_code == 200
    linked = client.get(f"/api/cases/{office.case}").json()["linked"]
    assert linked["invoices"] == {"count": 2, "subtotal": 0.0}


def test_a_case_without_links_reports_zero(client, office):
    assert client.get(f"/api/cases/{office.case}").json()["linked"] == {
        "invoices": {"count": 0, "subtotal": 0.0}, "expenses": {"count": 0, "total": 0.0}}


def test_a_case_with_linked_documents_cannot_be_deleted(client, office):
    expense = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case)).json()
    r = client.delete(f"/api/cases/{office.case}")
    assert r.status_code == 409
    assert "1 έξοδα" in r.json()["detail"] and "0 τιμολόγια" in r.json()["detail"]
    assert client.get(f"/api/cases/{office.case}").status_code == 200

    assert client.delete(f"/api/expenses/{expense['id']}").status_code == 200
    invoice = _draft(client, office.alpha, case_id=office.case).json()
    blocked = client.delete(f"/api/cases/{office.case}")
    assert blocked.status_code == 409 and "1 τιμολόγια" in blocked.json()["detail"]

    assert client.delete(f"/api/invoices/{invoice['id']}").status_code == 200
    assert client.delete(f"/api/cases/{office.case}").status_code == 200
    assert client.get(f"/api/cases/{office.case}").status_code == 404


def test_an_unlinked_case_is_still_deleted_as_before(client, office):
    spare = _make_case(client, "Χωρίς έγγραφα", office.alpha, "ΑΛΦΑ ΑΕ")
    assert client.delete(f"/api/cases/{spare['id']}").status_code == 200


def test_another_firms_case_is_a_404_even_when_it_has_documents(client, office, rival):
    assert client.delete(f"/api/cases/{rival.case}").status_code == 404
    assert rival.client.get(f"/api/cases/{rival.case}").status_code == 200


# ================================================================= ΤΙΜΟΛΟΓΙΑ ====

def test_invoice_inherits_the_case_lawyer_and_may_name_another(client, office):
    inherited = _draft(client, office.alpha, case_id=office.case)
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["case_id"] == office.case and inherited.json()["lawyer_id"] == office.lawyer
    chosen = _draft(client, office.alpha, case_id=office.case, lawyer_id=office.lawyer_b).json()
    assert chosen["lawyer_id"] == office.lawyer_b
    plain = _draft(client, office.alpha).json()
    assert plain["case_id"] == "" and plain["lawyer_id"] == ""


def test_invoice_lawyer_without_a_case_is_accepted(client, office):
    r = _draft(client, office.alpha, lawyer_id=office.lawyer_b)
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == "" and r.json()["lawyer_id"] == office.lawyer_b


def test_invoice_may_be_billed_to_someone_other_than_the_case_client(client, office):
    r = _draft(client, office.beta, case_id=office.case)                     # τρίτος πληρώνει
    assert r.status_code == 200, r.text
    assert r.json()["client_id"] == office.beta and r.json()["case_id"] == office.case


def test_invoice_references_are_checked_inside_the_firm(client, office, rival):
    missing = str(ObjectId())
    for over in (dict(case_id=rival.case), dict(case_id=missing), dict(lawyer_id=rival.lawyer),
                 dict(lawyer_id=missing)):
        assert _draft(client, office.alpha, **over).status_code == 404, over
    assert _draft(client, office.alpha, case_id="bad").status_code == 400
    assert _draft(client, office.alpha, lawyer_id=office.accountant).status_code == 400
    assert client.get("/api/invoices").json() == []                           # τίποτα δεν αποθηκεύτηκε


def _update_body(invoice, **over):
    body = {"client_id": invoice["client_id"], "date": invoice["date"],
            "line_items": [{"description": "Νέα γραμμή", "quantity": 1, "unit_price": 500.0, "vat_rate": 24}]}
    body.update(over)
    return body


def test_draft_update_without_matter_fields_keeps_the_link(client, office):
    inv = _draft(client, office.alpha, case_id=office.case, lawyer_id=office.lawyer_b).json()
    r = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv))
    assert r.status_code == 200, r.text
    assert r.json()["subtotal"] == 500.0
    assert r.json()["case_id"] == office.case and r.json()["lawyer_id"] == office.lawyer_b


def test_draft_update_with_matter_fields_replaces_them(client, office, rival):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id="", lawyer_id=office.lawyer_b))
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == "" and r.json()["lawyer_id"] == office.lawyer_b
    foreign = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id=rival.case))
    assert foreign.status_code == 404
    assert _raw("invoices", inv["id"])["lawyer_id"] == office.lawyer_b


@pytest.mark.parametrize("status", ["draft", "issued", "paid", "cancelled"])
def test_matter_can_be_set_in_any_status_without_touching_anything_else(client, office, status):
    from db import db
    inv = _draft(client, office.alpha).json() if status == "draft" else _issued(client, office.alpha)
    if status in ("paid", "cancelled"):
        _run(db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"status": status}}))
    before = _raw("invoices", inv["id"])

    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case})
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == office.case and r.json()["lawyer_id"] == office.lawyer     # κληρονομεί τον υπεύθυνο

    after = _raw("invoices", inv["id"])
    untouched = lambda doc: {k: v for k, v in doc.items() if k not in ("case_id", "lawyer_id")}  # noqa: E731
    assert untouched(after) == untouched(before)
    assert after["status"] == status


def test_matter_patch_refuses_every_other_field(client, office):
    inv = _issued(client, office.alpha)
    before = _raw("invoices", inv["id"])
    for body in ({"case_id": office.case, "total": 1.0}, {"status": "draft"}, {"mydata_mark": "1"}, {"number": "X"},
                 {"client_id": office.beta}, {"line_items": []}):
        assert client.patch(f"/api/invoices/{inv['id']}/matter", json=body).status_code == 422, body
    assert _raw("invoices", inv["id"]) == before


def test_matter_patch_is_checked_inside_the_firm(client, office, rival):
    inv = _issued(client, office.alpha)
    url = f"/api/invoices/{inv['id']}/matter"
    assert client.patch(url, json={"case_id": rival.case}).status_code == 404
    assert client.patch(url, json={"lawyer_id": rival.lawyer}).status_code == 404
    assert client.patch(url, json={"lawyer_id": office.accountant}).status_code == 400
    assert client.patch(url, json={"case_id": "bad"}).status_code == 400
    assert _raw("invoices", inv["id"])["case_id"] == ""                       # οι αποτυχημένες κλήσεις δεν έγραψαν τίποτα
    # ξένο, ανύπαρκτο και κακοσχηματισμένο τιμολόγιο
    assert client.patch(f"/api/invoices/{rival.invoice}/matter", json={}).status_code == 404
    assert client.patch(f"/api/invoices/{ObjectId()}/matter", json={}).status_code == 404
    assert client.patch("/api/invoices/not-an-id/matter", json={}).status_code == 404
    assert _raw("invoices", rival.invoice)["case_id"] == rival.case           # το ξένο τιμολόγιο έμεινε ως είχε


def test_matter_patch_can_clear_the_link(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": "", "lawyer_id": ""})
    assert r.status_code == 200
    assert r.json()["case_id"] == "" and r.json()["lawyer_id"] == ""


def test_matter_patch_is_in_the_audit_trail(client, office):
    inv = _issued(client, office.alpha)
    client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case})
    history = client.get(f"/api/audit/entity/invoices/{inv['id']}").json()
    assert any(h["action"].startswith("patch:invoices") for h in history), history


def test_credit_note_follows_the_original_to_the_same_case_and_lawyer(client, office):
    from db import db
    original = _issued(client, office.alpha, case_id=office.case, lawyer_id=office.lawyer_b)
    _run(db.invoices.update_one({"_id": ObjectId(original["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={"reason": "Λάθος"})
    assert credit.status_code == 200, credit.text
    assert credit.json()["case_id"] == office.case and credit.json()["lawyer_id"] == office.lawyer_b


def _issued_grammatio(c, client_id, **over):
    body = {"court_type": "Μονομελές Πρωτοδικείο", "procedure": "Τακτική διαδικασία", "client_id": client_id,
            "client_name": "ΑΛΦΑ ΑΕ", "reference_amount": 100.0, "contributions_amount": 20.0, "vat_amount": 24.0}
    body.update(over)
    g = c.post("/api/grammatia", json=body)
    assert g.status_code == 200, g.text
    issued = c.post(f"/api/grammatia/{g.json()['id']}/issue", json={"portal_ref": "OLO-1"})
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_invoice_from_a_grammatio_belongs_to_its_case_and_lawyer(client, office):
    g = _issued_grammatio(client, office.alpha, case_id=office.case)
    inv = client.post(f"/api/grammatia/{g['id']}/to-invoice")
    assert inv.status_code == 200, inv.text
    assert inv.json()["case_id"] == office.case and inv.json()["lawyer_id"] == office.lawyer


def test_grammatio_pointing_to_a_foreign_missing_or_malformed_case_gives_an_unlinked_invoice(client, office, rival):
    for case_id in (rival.case, str(ObjectId()), "garbage", ""):
        g = _issued_grammatio(client, office.alpha, case_id=case_id)
        inv = client.post(f"/api/grammatia/{g['id']}/to-invoice")
        assert inv.status_code == 200, (case_id, inv.text)
        assert inv.json()["case_id"] == "" and inv.json()["lawyer_id"] == "", case_id


def test_inherited_matter_ignores_foreign_missing_and_malformed_cases(client, office, rival):
    from dimensions import inherited_matter
    assert _run(inherited_matter(office.firm, office.case)) == (office.case, office.lawyer)
    assert _run(inherited_matter(office.firm, rival.case)) == ("", "")
    assert _run(inherited_matter(office.firm, str(ObjectId()))) == ("", "")
    assert _run(inherited_matter(office.firm, "garbage")) == ("", "")
    assert _run(inherited_matter(office.firm, None)) == ("", "")


# ===================================================================== GDPR ====

def test_gdpr_export_contains_the_expenses_of_the_client_and_of_its_cases(client, office, rival):
    from db import db
    client.post("/api/expenses", json=_expense(vendor="ΕΞΟΔΟ-ΠΕΛΑΤΗ", scope="client", client_id=office.alpha))
    client.post("/api/expenses", json=_expense(vendor="ΕΞΟΔΟ-ΥΠΟΘΕΣΗΣ", scope="case", case_id=office.case))
    client.post("/api/expenses", json=_expense(vendor="ΓΕΝΙΚΟ", scope="office"))
    client.post("/api/expenses", json=_expense(vendor="ΑΛΛΟΣ-ΠΕΛΑΤΗΣ", scope="client", client_id=office.beta))
    # ΞΕΝΟ γραφείο με τα ίδια αναγνωριστικά: δεν πρέπει να διαρρεύσει
    _run(db.expenses.insert_many([
        {"firm_id": rival.firm, "vendor": "ΞΕΝΟ-1", "date": "2026-09-01", "amount": 1.0, "client_id": office.alpha},
        {"firm_id": rival.firm, "vendor": "ΞΕΝΟ-2", "date": "2026-09-01", "amount": 1.0, "case_id": office.case},
    ]))
    r = client.get(f"/api/backup/gdpr/client/{office.alpha}")
    assert r.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(r.content))
    data = json.loads(archive.read("data.json"))
    assert sorted(e["vendor"] for e in data["expenses"]) == ["ΕΞΟΔΟ-ΠΕΛΑΤΗ", "ΕΞΟΔΟ-ΥΠΟΘΕΣΗΣ"]
    assert "έξοδα" in archive.read("_readme.txt").decode("utf-8")


def test_gdpr_export_finds_a_case_expense_whose_own_client_field_is_blank(client, office, rival):
    """Ο πελάτης ενός εξόδου υπόθεσης προκύπτει από την υπόθεση, αλλά το ίδιο το έγγραφο μπορεί να έχει μείνει με κενό
    πελάτη (εγγραφή από άλλη διαδρομή ή παλιότερη κατάσταση). Η εξαγωγή ΓΚΠΔ πρέπει να είναι πλήρης: ό,τι δείχνει σε
    υπόθεση του πελάτη εξάγεται, ό,τι κι αν γράφει το δικό του πεδίο — και πάλι μόνο μέσα στο ίδιο γραφείο."""
    from db import db
    _run(db.expenses.insert_many([
        {"firm_id": office.firm, "vendor": "ΥΠΟΘΕΣΗ-ΧΩΡΙΣ-ΠΕΛΑΤΗ", "date": "2026-09-01", "amount": 1.0,
         "scope": "case", "case_id": office.case, "client_id": ""},
        {"firm_id": rival.firm, "vendor": "ΞΕΝΟ-ΧΩΡΙΣ-ΠΕΛΑΤΗ", "date": "2026-09-01", "amount": 1.0,
         "scope": "case", "case_id": office.case, "client_id": ""},
    ]))
    r = client.get(f"/api/backup/gdpr/client/{office.alpha}")
    assert r.status_code == 200
    data = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("data.json"))
    assert [e["vendor"] for e in data["expenses"]] == ["ΥΠΟΘΕΣΗ-ΧΩΡΙΣ-ΠΕΛΑΤΗ"]
