"""Φάση 1 του «ψηφιακού λογιστή» — διορθώσεις μετά την ανεξάρτητη αναθεώρηση του κώδικα (17/10/2026).

Ένας δεύτερος αναθεωρητής, που δεν είχε δει πώς γράφτηκε ο κώδικας, εκτέλεσε πειράματα πάνω του και βρήκε 16 ευρήματα.
Κάθε test εδώ αναπαράγει ένα από αυτά (ο αριθμός Ε-n είναι της αναφοράς) και **αποτύγχανε** πριν τη διόρθωση.

Η αρχή που διατρέχει τις διορθώσεις: ό,τι δεν στάλθηκε στο αίτημα δεν αλλάζει, και μια αναφορά ελέγχεται μόνο όταν τη
θέτει ή την αλλάζει ο χρήστης — όχι όταν απλώς ξαναστέλνεται όπως ήδη ήταν αποθηκευμένη (π.χ. δικηγόρος που δεν υπάρχει
πια, γιατί οι χρήστες δεν επαναφέρονται από αντίγραφο ασφαλείας).
"""
import asyncio
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


def _update_body(invoice, **over):
    body = {"client_id": invoice["client_id"], "date": invoice["date"],
            "line_items": [{"description": "Νέα γραμμή", "quantity": 1, "unit_price": 500.0, "vat_rate": 24}]}
    body.update(over)
    return body


def _raw(collection, doc_id):
    from db import db
    return _run(db[collection].find_one({"_id": ObjectId(doc_id)}))


def _matter(e):
    return (e["scope"], e["case_id"], e["client_id"], e["lawyer_id"], e["rebillable"])


def _letters(value: str) -> bool:
    return value != value.upper()


@pytest.fixture()
def office(client):
    """Διαχειριστής, δύο δικηγόροι, ένας λογιστής, δύο πελάτες και δύο υποθέσεις με διαφορετικό υπεύθυνο."""
    me = _register(client, "owner@example.gr", "Διαχειριστής")
    _set_role("owner@example.gr", "admin")
    firm = me["firm_id"]
    lawyer = _member(firm, "lawyer@example.gr", "Δικηγόρος Α", "lawyer")
    lawyer_b = _member(firm, "lawyer2@example.gr", "Δικηγόρος Β", "lawyer")
    accountant = _member(firm, "acct@example.gr", "Λογιστής", "accountant")
    alpha = _make_client(client, "ΑΛΦΑ ΑΕ", client_type="company", afm="123456783")
    beta = _make_client(client, "Ιδιώτης Πελάτης")
    # Ένα id με γράμματα a–f, για να έχουν νόημα τα tests κεφαλαίων. (Η πιθανότητα να μην έχει κανένα είναι ~1/80.000.)
    case = _make_case(client, "Υπόθεση Α", alpha, "ΑΛΦΑ ΑΕ", lawyer_id=lawyer)
    while not _letters(case["id"]):
        case = _make_case(client, "Υπόθεση Α", alpha, "ΑΛΦΑ ΑΕ", lawyer_id=lawyer)
    other = _make_case(client, "Υπόθεση Β", beta, "Ιδιώτης Πελάτης", lawyer_id=lawyer_b)
    return SimpleNamespace(firm=firm, admin=me["id"], lawyer=lawyer, lawyer_b=lawyer_b, accountant=accountant,
                           alpha=alpha, beta=beta, case=case["id"], other=other["id"])


@pytest.fixture()
def foreign(client):
    """Ένα ΑΛΛΟ γραφείο με δικό του πελάτη, δικηγόρο και υπόθεση (ο client του μένει ανοιχτός όσο τρέχει το test)."""
    import server
    with TestClient(server.app, base_url="https://testserver") as rc:
        firm = _register(rc, "rival@example.gr", "Άλλο γραφείο")["firm_id"]
        client_id = _make_client(rc, "ΞΕΝΟΣ ΠΕΛΑΤΗΣ")
        lawyer_id = _member(firm, "rival-lawyer@example.gr", "Ξένος Δικηγόρος", "lawyer")
        case = _make_case(rc, "Ξένη Υπόθεση", client_id, "ΞΕΝΟΣ ΠΕΛΑΤΗΣ", lawyer_id=lawyer_id)
        yield SimpleNamespace(client_id=client_id, lawyer_id=lawyer_id, case_id=case["id"])


def _remove_user(user_id):
    """Ο χρήστης δεν υπάρχει πια στη βάση — όπως μετά από επαναφορά αντιγράφου (οι χρήστες δεν επαναφέρονται)."""
    from db import db
    _run(db.users.delete_one({"_id": ObjectId(user_id)}))


# ============================================ Ε2 · ΑΝΑΦΟΡΕΣ ΠΟΥ ΔΕΝ ΥΠΑΡΧΟΥΝ ΠΙΑ ====

def test_e2_expense_of_a_case_whose_lawyer_has_left_can_be_created_and_edited(client, office):
    _remove_user(office.lawyer)
    created = client.post("/api/expenses", json=_expense(scope="case", case_id=office.case))
    assert created.status_code == 200, created.text                          # ο κληρονομημένος δικηγόρος δεν ξαναελέγχεται
    assert created.json()["lawyer_id"] == office.lawyer
    edit = _expense(amount=248.0, vat_amount=48.0, scope="case", case_id=office.case, lawyer_id=office.lawyer)
    r = client.put(f"/api/expenses/{created.json()['id']}", json=edit)       # όπως το ξαναστέλνει η φόρμα
    assert r.status_code == 200, r.text
    assert _raw("expenses", created.json()["id"])["amount"] == 248.0


def test_e2_a_lawyer_that_does_not_exist_is_still_refused_when_it_is_the_one_being_set(client, office):
    missing = str(ObjectId())
    assert client.post("/api/expenses", json=_expense(scope="office", lawyer_id=missing)).status_code == 404
    e = client.post("/api/expenses", json=_expense(scope="office", lawyer_id=office.lawyer)).json()
    assert client.put(f"/api/expenses/{e['id']}", json=_expense(scope="office", lawyer_id=missing)).status_code == 404
    assert _raw("expenses", e["id"])["lawyer_id"] == office.lawyer             # η αποτυχημένη αλλαγή δεν έγραψε τίποτα
    _remove_user(office.lawyer)
    other_missing = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="office", lawyer_id=str(ObjectId())))
    assert other_missing.status_code == 404                                    # αλλαγή σε ΑΛΛΟΝ ανύπαρκτο: πάλι 404


def test_e2_expense_of_a_deleted_client_can_still_be_edited(client, office):
    from db import db
    e = client.post("/api/expenses", json=_expense(scope="client", client_id=office.beta)).json()
    _run(db.clients.delete_one({"_id": ObjectId(office.beta)}))                # δεν ελέγχεται αναφορά στη διαγραφή πελάτη
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(amount=62.0, vat_amount=12.0, scope="client", client_id=office.beta))
    assert r.status_code == 200, r.text
    assert _raw("expenses", e["id"])["amount"] == 62.0
    moved = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="client", client_id=str(ObjectId())))
    assert moved.status_code == 404                                            # αλλαγή σε άλλον ανύπαρκτο πελάτη: 404


def test_e2_draft_invoice_with_a_missing_lawyer_can_still_be_edited_and_tagged(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()            # κληρονομεί τον office.lawyer
    _remove_user(office.lawyer)
    r = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id=office.case, lawyer_id=office.lawyer))
    assert r.status_code == 200, r.text
    assert r.json()["lawyer_id"] == office.lawyer and r.json()["subtotal"] == 500.0
    again = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case, "lawyer_id": office.lawyer})
    assert again.status_code == 200, again.text
    changed = client.patch(f"/api/invoices/{inv['id']}/matter", json={"lawyer_id": str(ObjectId())})
    assert changed.status_code == 404


# ===================================================== Ε6 · ΠΙΣΤΩΤΙΚΟ ΑΚΟΛΟΥΘΕΙ ΤΟ ΑΡΧΙΚΟ ====

def _issued_with_credit(client, office, **over):
    from db import db
    original = _issued(client, office.alpha, net=200, case_id=office.case, **over)
    _run(db.invoices.update_one({"_id": ObjectId(original["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={"reason": "Λάθος"})
    assert credit.status_code == 200, credit.text
    assert client.post(f"/api/invoices/{credit.json()['id']}/issue").status_code == 200
    return original, credit.json()


def test_e6_credit_note_moves_with_the_original_to_another_case(client, office):
    original, credit = _issued_with_credit(client, office)
    r = client.patch(f"/api/invoices/{original['id']}/matter", json={"case_id": office.other})
    assert r.status_code == 200, r.text
    first = client.get(f"/api/cases/{office.case}").json()["linked"]["invoices"]
    second = client.get(f"/api/cases/{office.other}").json()["linked"]["invoices"]
    assert first == {"count": 0, "subtotal": 0.0}, first                        # το πιστωτικό δεν έμεινε πίσω
    assert second == {"count": 2, "subtotal": 0.0}, second                      # +200 και −200 μαζί: καθαρή αξία 0
    stored = _raw("invoices", credit["id"])
    assert stored["case_id"] == office.other and stored["lawyer_id"] == office.lawyer_b


def test_e6_credit_note_follows_a_lawyer_change_of_the_original(client, office):
    original, credit = _issued_with_credit(client, office)
    assert client.patch(f"/api/invoices/{original['id']}/matter", json={"lawyer_id": office.admin}).status_code == 200
    assert _raw("invoices", credit["id"])["lawyer_id"] == office.admin


def test_e6_only_credit_notes_of_that_invoice_are_touched(client, office):
    original, credit = _issued_with_credit(client, office)
    unrelated = _issued(client, office.alpha, net=50, case_id=office.case)
    other_original, other_credit = _issued_with_credit(client, office)               # άλλο τιμολόγιο, με δικό του πιστωτικό
    client.patch(f"/api/invoices/{original['id']}/matter", json={"case_id": office.other})
    assert _raw("invoices", credit["id"])["case_id"] == office.other                  # το δικό του πιστωτικό ακολουθεί
    assert _raw("invoices", unrelated["id"])["case_id"] == office.case
    assert _raw("invoices", other_original["id"])["case_id"] == office.case
    assert _raw("invoices", other_credit["id"])["case_id"] == office.case              # του άλλου τιμολογίου μένει όπου ήταν


# ============================ Ε7 / Ε8 · ΤΙΜΟΛΟΓΙΟ: PATCH ΑΛΛΑΖΕΙ ΜΟΝΟ ΟΣΑ ΣΤΑΛΘΗΚΑΝ ====

def test_e8_patch_with_only_a_lawyer_keeps_the_case(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"lawyer_id": office.admin})
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.case, office.admin)


def test_e8_patch_with_an_empty_body_is_refused_and_changes_nothing(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    before = _raw("invoices", inv["id"])
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={})
    assert r.status_code == 400, r.text
    assert _raw("invoices", inv["id"]) == before


def test_e8_detaching_the_case_keeps_the_lawyer(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": ""})
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == ("", office.lawyer)


def test_e7_an_inherited_lawyer_follows_the_invoice_to_another_case(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()             # ο office.lawyer είναι ο υπεύθυνος της Α
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.other})
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.other, office.lawyer_b)


def test_e7_an_explicitly_chosen_lawyer_stays_when_the_case_changes(client, office):
    inv = _draft(client, office.alpha, case_id=office.case, lawyer_id=office.admin).json()
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.other})
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.other, office.admin)


def test_e7_a_lawyer_sent_with_the_case_is_always_honoured(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.other, "lawyer_id": office.lawyer})
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.other, office.lawyer)
    derived = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case, "lawyer_id": ""})
    assert (derived.json()["case_id"], derived.json()["lawyer_id"]) == (office.case, office.lawyer)   # κενό = από την υπόθεση


def test_e7_draft_put_with_only_a_case_follows_the_same_rule(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id=office.other))
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.other, office.lawyer_b)
    explicit = _draft(client, office.alpha, case_id=office.case, lawyer_id=office.admin).json()
    r = client.put(f"/api/invoices/{explicit['id']}", json=_update_body(explicit, case_id=office.other))
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.other, office.admin)


def test_e8_draft_put_with_only_a_lawyer_keeps_the_case(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, lawyer_id=office.admin))
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.case, office.admin)


# ================================ Ε7 / Ε10 · ΕΞΟΔΟ: ΜΕΡΙΚΟ PUT ΣΥΓΧΩΝΕΥΕΤΑΙ ΜΕ ΤΟ ΑΠΟΘΗΚΕΥΜΕΝΟ ====

def _case_expense(client, office, **over):
    return client.post("/api/expenses", json=_expense(scope="case", case_id=office.case, rebillable=True, **over)).json()


def test_e10_put_with_only_a_lawyer_keeps_scope_case_and_flag(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(lawyer_id=office.admin))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("case", office.case, office.alpha, office.admin, True)


def test_e10_put_with_only_the_flag_keeps_the_rest(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(rebillable=False))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("case", office.case, office.alpha, office.lawyer, False)


def test_e10_put_with_only_a_case_moves_the_expense_and_rederives_the_client(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(case_id=office.other))
    assert r.status_code == 200, r.text
    # scope και rebillable μένουν· ο πελάτης προκύπτει από τη νέα υπόθεση· ο κληρονομημένος δικηγόρος ακολουθεί
    assert _matter(r.json()) == ("case", office.other, office.beta, office.lawyer_b, True)


def test_e7_an_explicit_expense_lawyer_stays_when_only_the_case_changes(client, office):
    e = _case_expense(client, office, lawyer_id=office.admin)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(case_id=office.other))
    assert _matter(r.json()) == ("case", office.other, office.beta, office.admin, True)


def test_e7_the_form_that_resends_an_inherited_lawyer_gets_the_new_cases_lawyer_only_if_it_sends_empty(client, office):
    """Η φόρμα στέλνει ρητά ό,τι έχει αποθηκευμένο· ένα ρητό δικηγόρος που ΔΕΝ αλλάζει μένει — η οθόνη είναι εκείνη που
    στέλνει κενό όταν ο χρήστης αλλάζει υπόθεση και ο δικηγόρος ήταν κληρονομημένος (βλ. frontend/lib/matters.js)."""
    e = _case_expense(client, office)
    kept = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="case", case_id=office.other, lawyer_id=office.lawyer))
    assert kept.json()["lawyer_id"] == office.lawyer
    derived = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="case", case_id=office.other, lawyer_id=""))
    assert derived.json()["lawyer_id"] == office.lawyer_b


def test_e10_a_member_that_contradicts_the_stored_scope_is_a_clean_400(client, office):
    e = client.post("/api/expenses", json=_expense(scope="office")).json()
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(case_id=office.case))
    assert r.status_code == 400, r.text
    assert _matter(_raw("expenses", e["id"]) | {}) == ("office", "", "", "", False)


def test_e10_a_put_that_sends_the_scope_still_resets_the_rest(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="client", client_id=office.beta))
    assert _matter(r.json()) == ("client", "", office.beta, "", False)


def test_e10_a_put_without_any_matter_field_changes_no_matter_field(client, office):
    e = _case_expense(client, office, lawyer_id=office.admin)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(vendor="Άλλος"))
    assert _matter(r.json()) == ("case", office.case, office.alpha, office.admin, True)


# ====================================== Ε9 · ΤΑ ids ΑΠΟΘΗΚΕΥΟΝΤΑΙ ΣΕ ΜΙΑ ΜΟΝΟ ΜΟΡΦΗ (πεζά hex) ====

def test_e9_uppercase_ids_are_stored_in_canonical_form_and_counted_everywhere(client, office):
    upper = office.case.upper()
    e = client.post("/api/expenses", json=_expense(scope="case", case_id=upper, lawyer_id=office.lawyer.upper()))
    assert e.status_code == 200, e.text
    assert e.json()["case_id"] == office.case and e.json()["lawyer_id"] == office.lawyer
    assert client.get(f"/api/cases/{office.case}").json()["linked"]["expenses"]["count"] == 1
    assert client.get(f"/api/cases/{upper}").json()["linked"]["expenses"]["count"] == 1      # και με id κεφαλαίων στο URL
    assert client.delete(f"/api/cases/{upper}").status_code == 409                           # η προστασία δεν παρακάμπτεται
    assert client.delete(f"/api/cases/{office.case}").status_code == 409


def test_e9_client_scope_expense_with_an_uppercase_client_id(client, office):
    e = client.post("/api/expenses", json=_expense(scope="client", client_id=office.alpha.upper())).json()
    assert e["client_id"] == office.alpha


def test_e9_changing_the_client_through_an_uppercase_url_still_moves_the_expenses(client, office):
    e = _case_expense(client, office)
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    r = client.put(f"/api/cases/{office.case.upper()}", json={**case, "client_id": office.beta, "client_name": "Ιδιώτης"})
    assert r.status_code == 200, r.text
    assert _raw("expenses", e["id"])["client_id"] == office.beta


def test_e9_invoice_matter_with_uppercase_ids_is_stored_canonically(client, office):
    inv = _issued(client, office.alpha)
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case.upper(), "lawyer_id": office.admin.upper()})
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (office.case, office.admin)
    assert client.get(f"/api/cases/{office.case}").json()["linked"]["invoices"]["count"] == 1


def test_e9_draft_invoice_with_uppercase_ids_is_stored_canonically(client, office):
    inv = _draft(client, office.alpha, case_id=office.case.upper()).json()
    assert inv["case_id"] == office.case


# ===================================================== Ε11 · Ο ΠΕΛΑΤΗΣ ΜΙΑΣ ΥΠΟΘΕΣΗΣ ====

def test_e11_case_client_is_checked_inside_the_firm(client, office, foreign):
    before = len(client.get("/api/cases").json())

    def create(**over):
        return client.post("/api/cases", json={"title": "Νέα υπόθεση", **over})

    assert create(client_id=foreign.client_id).status_code == 404
    assert create(client_id=str(ObjectId())).status_code == 404                # ίδιο 404 για ξένο και ανύπαρκτο
    assert create(client_id="zzz").status_code == 400
    assert create(client_id=office.alpha, client_name="ΑΛΦΑ ΑΕ").status_code == 200
    assert create().status_code == 200                                         # υπόθεση χωρίς πελάτη (όπως πριν)
    assert len(client.get("/api/cases").json()) == before + 2                  # καμία από τις αποτυχημένες δεν αποθηκεύτηκε


def test_e11_updating_a_case_to_a_foreign_client_is_refused_and_changes_nothing(client, office, foreign):
    e = _case_expense(client, office)
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    r = client.put(f"/api/cases/{office.case}", json={**case, "client_id": foreign.client_id})
    assert r.status_code == 404, r.text
    assert _raw("cases", office.case)["client_id"] == office.alpha
    assert _raw("expenses", e["id"])["client_id"] == office.alpha              # τα έξοδα δεν πήραν ξένο id


def test_e11_a_put_without_the_client_keeps_it_and_the_expenses_of_the_case(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/cases/{office.case}", json={"title": "Μόνο ο τίτλος"})
    assert r.status_code == 200, r.text
    assert (r.json()["client_id"], r.json()["client_name"]) == (office.alpha, "ΑΛΦΑ ΑΕ")
    assert _raw("expenses", e["id"])["client_id"] == office.alpha


def test_e11_an_explicit_empty_client_clears_it_and_the_expenses_follow(client, office):
    e = _case_expense(client, office)
    r = client.put(f"/api/cases/{office.case}", json={"title": "Χωρίς πελάτη", "client_id": "", "client_name": ""})
    assert r.status_code == 200, r.text
    assert r.json()["client_id"] == ""
    assert _raw("expenses", e["id"])["client_id"] == ""


def test_e11_a_case_whose_client_was_deleted_can_still_be_edited(client, office):
    from db import db
    case = client.get(f"/api/cases/{office.case}").json()["case"]
    _run(db.clients.delete_one({"_id": ObjectId(office.alpha)}))
    same = client.put(f"/api/cases/{office.case}", json={**case, "title": "Νέος τίτλος"})
    assert same.status_code == 200, same.text
    changed = client.put(f"/api/cases/{office.case}", json={**case, "client_id": str(ObjectId())})
    assert changed.status_code == 404


# ============================================================ ΡΥΘΜΙΣΕΙΣ & ΠΕΡΙΟΔΟΙ ΦΠΑ ====

SETTINGS = {"company_name": "Γραφείο", "afm": "123456783", "doy": "Α΄ Αθηνών", "vat_period": "monthly"}


def _admin(c, email="rules@example.gr"):
    r = c.post("/api/auth/register", json={"email": email, "password": PASSWORD, "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    _set_role(email, "admin")
    return r.json()["firm_id"]


def _patch(c, **body):
    return c.patch("/api/settings", json=body)


def _put(c, **over):
    """Όπως το κάνει η οθόνη Ρυθμίσεων: διαβάζει όλες τις ρυθμίσεις, αλλάζει μερικές και τις στέλνει πίσω."""
    current = c.get("/api/settings").json()
    current.update(over)
    return c.put("/api/settings", json=current)


def _last_closed_period():
    from automation import closed_vat_periods
    return closed_vat_periods(athens_today(), "monthly")[-1]


def _stored_period(firm, period):
    from db import db
    return _run(db.vat_periods.find_one({"firm_id": firm, "period": period}))


def _line(data, key):
    return next(l for l in data["lines"] if l["key"] == key)


def _run_job(firm):
    from automation import job_vat_periods
    from db import db
    _run(job_vat_periods(firm, _run(db.settings.find_one({"firm_id": firm})) or {}))


# --------------------------------------------------------------- Ε4 · επιβεβαίωση λογιστή ----

def test_e4_a_full_form_save_that_changes_the_table_but_keeps_the_name_withdraws_the_confirmation(client):
    """Η οθόνη στέλνει πάντα και το όνομα. Αν ο πίνακας αλλάζει και το όνομα είναι το ίδιο με το αποθηκευμένο, η
    επιβεβαίωση είχε δοθεί για τον ΠΡΟΗΓΟΥΜΕΝΟ πίνακα: δεν μένει πάνω σε αριθμούς που ο λογιστής δεν είδε."""
    _admin(client)
    first = _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος, 20/10/2026")
    assert first.json()["vat_rules_confirmed_by"] == "Χριστόφορος, 20/10/2026"        # νέος πίνακας + νέο όνομα: ισχύει
    changed = _put(client, vat_deductibility_rules={"Ενοίκιο": 80})                    # το όνομα μένει όπως φορτώθηκε
    assert changed.json()["vat_deductibility_rules"] == {"Ενοίκιο": 80.0}
    assert changed.json()["vat_rules_confirmed_by"] == ""
    assert client.get("/api/settings").json()["vat_rules_confirmed_by"] == ""


def test_e4_a_new_name_given_with_the_change_is_accepted(client):
    _admin(client)
    _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος, 20/10/2026")
    r = _put(client, vat_deductibility_rules={"Ενοίκιο": 80}, vat_rules_confirmed_by="Χριστόφορος, 27/10/2026")
    assert r.json()["vat_rules_confirmed_by"] == "Χριστόφορος, 27/10/2026"


def test_e4_saving_without_changing_the_table_keeps_the_confirmation(client):
    _admin(client)
    _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος, 20/10/2026")
    r = _put(client, company_name="Νέα επωνυμία")                                      # αλλάζει κάτι άλλο
    assert r.json()["vat_rules_confirmed_by"] == "Χριστόφορος, 20/10/2026"


def test_e4_after_a_withdrawal_the_name_is_accepted_again_once_the_table_is_stable(client):
    _admin(client)
    _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    assert _put(client, vat_deductibility_rules={"Ενοίκιο": 70}).json()["vat_rules_confirmed_by"] == ""
    again = _put(client, vat_rules_confirmed_by="Χριστόφορος")                         # ίδιος πίνακας, ξανά η υπογραφή
    assert again.json()["vat_rules_confirmed_by"] == "Χριστόφορος"


def test_e4_patch_follows_the_same_rule(client):
    _admin(client)
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    same_name = _patch(client, vat_deductibility_rules={"Ενοίκιο": 60}, vat_rules_confirmed_by="Χριστόφορος")
    assert same_name.json()["vat_rules_confirmed_by"] == ""
    assert _patch(client, vat_deductibility_rules={"Ενοίκιο": 60}, vat_rules_confirmed_by="Χριστόφορος").json()[
        "vat_rules_confirmed_by"] == "Χριστόφορος"                                      # ο πίνακας δεν άλλαξε: δεκτό


# ---------------------------------------------------- Ε3 · η υποβολή παγώνει και τους αριθμούς ----

def _two_expenses_in(client, period):
    for category in ("Ενοίκιο", "Φιλοξενία / Εστίαση"):
        r = client.post("/api/expenses", json=_expense(category=category, date=period["start"].isoformat()))
        assert r.status_code == 200, r.text


def test_e3_marking_a_period_submitted_stores_the_numbers_of_the_table_it_freezes(client):
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)                                                                     # το job τρέχει πριν οριστεί πίνακας
    assert _stored_period(firm, period["period"])["input_vat"] == 48.0
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0}, vat_rules_confirmed_by="Χριστόφορος")
    assert client.post(f"/api/automation/vat-periods/{period['period']}/submitted").status_code == 200

    stored = _stored_period(firm, period["period"])
    filed = client.get(f"/api/automation/vat-periods/{period['period']}/return").json()
    assert _line(filed, "inputs_vat")["amount"] == 24.0
    assert (stored["input_vat"], stored["input_vat_nondeductible"]) == (24.0, 24.0)    # ίδιοι αριθμοί με το Φ2
    assert stored["vat_due"] == round(stored["output_vat"] - 24.0, 2)
    assert stored["status"] == "submitted" and stored["vat_rules_snapshot"]["rules"] == {"Φιλοξενία / Εστίαση": 0.0}


def test_e3_a_second_marking_leaves_the_stored_numbers_alone(client):
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0})
    url = f"/api/automation/vat-periods/{period['period']}/submitted"
    assert client.post(url).status_code == 200
    first = _stored_period(firm, period["period"])
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 100, "Ενοίκιο": 0})
    client.post("/api/expenses", json=_expense(category="Λοιπά", date=period["start"].isoformat()))
    assert client.post(url).status_code == 200
    second = _stored_period(firm, period["period"])
    for key in ("input_vat", "input_vat_nondeductible", "vat_due", "expense_count", "submitted_at", "vat_rules_snapshot"):
        assert second[key] == first[key], key


def test_e3_a_job_that_read_the_period_before_it_was_submitted_cannot_overwrite_it(client):
    from automation import compute_vat_period, refresh_open_vat_period
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)
    stale = _stored_period(firm, period["period"])                                     # διάβασε το job: ακόμη «computed»
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0})
    assert client.post(f"/api/automation/vat-periods/{period['period']}/submitted").status_code == 200
    frozen = _stored_period(firm, period["period"])
    old_numbers = _run(compute_vat_period(firm, period, {}))                           # ό,τι θα έγραφε με τον παλιό πίνακα
    assert _run(refresh_open_vat_period(stale, old_numbers)) is False
    assert _stored_period(firm, period["period"]) == frozen


def test_e3_the_job_still_refreshes_an_open_period(client):
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 0})
    _run_job(firm)
    assert _stored_period(firm, period["period"])["input_vat"] == 24.0


# --------------------------------------------------------------- Ε5 · ποιος σημειώνει «Υποβλήθηκε» ----

def _login(email):
    import server
    c = TestClient(server.app, base_url="https://testserver")
    c.__enter__()
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def test_e5_only_an_admin_or_an_accountant_can_freeze_a_period(client):
    firm = _admin(client)
    _member(firm, "lawyer@example.gr", "Δικηγόρος", "lawyer")
    _member(firm, "acct@example.gr", "Λογιστής", "accountant")
    period = _last_closed_period()
    _run_job(firm)
    url = f"/api/automation/vat-periods/{period['period']}/submitted"
    lawyer = _login("lawyer@example.gr")
    try:
        denied = lawyer.post(url)
        assert denied.status_code == 403, denied.text
        assert "διαχειριστής ή ο λογιστής" in denied.json()["detail"]             # το μήνυμα που διαβάζει ο χρήστης στην οθόνη
    finally:
        lawyer.__exit__(None, None, None)
    assert _stored_period(firm, period["period"])["status"] != "submitted"
    accountant = _login("acct@example.gr")
    try:
        assert accountant.post(url).status_code == 200
    finally:
        accountant.__exit__(None, None, None)
    assert _stored_period(firm, period["period"])["status"] == "submitted"


def test_e5_the_admin_can_still_freeze_a_period(client):
    firm = _admin(client)
    period = _last_closed_period()
    _run_job(firm)
    assert client.post(f"/api/automation/vat-periods/{period['period']}/submitted").status_code == 200


# ----------------------------------------------------------------------- Ε14 · πολύ μεγάλος ακέραιος ----

def test_e14_a_huge_integer_in_the_rules_table_is_a_clean_400_not_a_500(client):
    _admin(client)
    assert _patch(client, vat_deductibility_rules={"Ενοίκιο": 10 ** 400}).status_code == 400
    assert _put(client, vat_deductibility_rules={"Ενοίκιο": -(10 ** 400)}).status_code == 400
    assert client.get("/api/settings").json()["vat_deductibility_rules"] == {}


def test_e14_a_huge_integer_is_not_a_finite_number():
    from tax_utils import is_finite_number
    assert is_finite_number(10 ** 400) is False
    assert is_finite_number(-(10 ** 400)) is False
    assert is_finite_number(10 ** 15) is True


# ------------------------------------------------------------------ Ε13 · ποσά με περισσότερα από 2 δεκαδικά ----

def test_e13_amounts_are_rounded_to_the_cent_when_they_come_in(client, office):
    created = client.post("/api/expenses", json=_expense(amount=1.006, vat_amount=0.004))
    assert created.status_code == 200, created.text
    assert (created.json()["amount"], created.json()["vat_amount"]) == (1.01, 0.0)
    half = client.post("/api/expenses", json=_expense(amount=1.005, vat_amount=0.125))
    assert (half.json()["amount"], half.json()["vat_amount"]) == (1.01, 0.13)           # μισό προς τα πάνω
    changed = client.put(f"/api/expenses/{created.json()['id']}", json=_expense(amount=2.349, vat_amount=0.455))
    assert (changed.json()["amount"], changed.json()["vat_amount"]) == (2.35, 0.46)


def test_e13_the_invariant_holds_for_every_expense_the_api_accepts(client, office):
    """`καθαρό κόστος + ΦΠΑ που εκπίπτει = ό,τι πληρώθηκε`, εξέταση ένα-ένα (όχι στο σύνολο, όπου τα λάθη του λεπτού αλληλοαναιρούνται)."""
    from vat_rules import summarize_expenses
    for amount, vat in ((1.006, 0.004), (1.005, 0.125), (0.285, 0.0714), (12.345, 2.4), (-3.337, -0.641)):
        r = client.post("/api/expenses", json=_expense(amount=amount, vat_amount=vat))
        assert r.status_code == 200, r.text
    docs = [_raw("expenses", e["id"]) for e in client.get("/api/expenses").json()]
    assert len(docs) == 5
    for settings in (None, {"vat_deductibility_rules": {"Λοιπά": 33.3}}, {"vat_deductibility_rules": {"Λοιπά": 0}}):
        for doc in docs:
            s = summarize_expenses([doc], settings)
            assert round(s["net_cost"] + s["input_vat"], 2) == s["gross"], (doc["amount"], doc["vat_amount"], settings, s)


# --------------------------------------------------------------- Ε15 · απίθανα μεγάλα αποθηκευμένα ποσά ----

def test_e15_an_absurd_stored_amount_does_not_break_the_totals():
    from vat_rules import summarize_expenses
    sane = {"amount": 124.0, "vat_amount": 24.0, "category": "Λοιπά"}
    for bad in (1e26, 1e300, 10 ** 400, float("inf")):
        s = summarize_expenses([sane, {"amount": bad, "vat_amount": bad, "category": "Λοιπά"}], None)
        assert (s["gross"], s["input_vat"], s["net_cost"]) == (124.0, 24.0, 100.0), bad    # το κατεστραμμένο μετρά 0
    with_rule = summarize_expenses([sane, {"amount": 1e26, "vat_amount": 1e25, "category": "Λοιπά"}],
                                   {"vat_deductibility_rules": {"Λοιπά": 50}})
    assert with_rule["input_vat"] == 12.0


def test_e15_one_absurd_record_does_not_take_down_the_dashboard(client, office):
    from db import db
    client.post("/api/expenses", json=_expense(date=athens_today().isoformat()))
    _run(db.expenses.insert_one({"firm_id": office.firm, "vendor": "OCR", "date": athens_today().isoformat(),
                                 "amount": 1e26, "vat_amount": 1e25, "category": "Λοιπά"}))
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200, r.text
    assert r.json()["input_vat"] == 24.0


# ----------------------------------------------- Ε12 · ενημερώσεις που δεν έλεγαν «χωρίς κανόνα» ----

def test_e12_the_digest_says_when_input_vat_rests_on_no_rule(client):
    from automation import build_digest, firm_snapshot
    from db import db
    firm = _admin(client)
    client.post("/api/expenses", json=_expense(category="Φιλοξενία / Εστίαση", date=athens_today().isoformat()))
    settings = _run(db.settings.find_one({"firm_id": firm})) or {}
    snapshot = _run(firm_snapshot(firm, settings))
    assert snapshot["input_vat_unruled"] == 24.0
    _, text, html = build_digest(snapshot)
    assert "χωρίς κανόνα" in text and "χωρίς κανόνα" in html and "24,00 €" in text
    ruled = _run(firm_snapshot(firm, {"vat_deductibility_rules": {"Φιλοξενία / Εστίαση": 50}}))
    assert ruled["input_vat_unruled"] == 0.0
    _, text, html = build_digest(ruled)
    assert "χωρίς κανόνα" not in text and "χωρίς κανόνα" not in html


def test_e12_the_period_close_notice_says_when_input_vat_rests_on_no_rule(client):
    from db import db
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)
    notice = _run(db.notifications.find_one({"firm_id": firm, "type": "vat_period", "entity_id": period["period"]}))
    assert notice and "χωρίς κανόνα" in notice["message"] and "48,00 €" in notice["message"]


def test_e12_the_period_close_notice_is_quiet_when_every_expense_has_a_rule(client):
    from automation import job_vat_periods
    from db import db
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    rules = {"Ενοίκιο": 100, "Φιλοξενία / Εστίαση": 50}
    _run(job_vat_periods(firm, {"vat_period": "monthly", "vat_deductibility_rules": rules}))
    notice = _run(db.notifications.find_one({"firm_id": firm, "type": "vat_period", "entity_id": period["period"]}))
    assert notice and "χωρίς κανόνα" not in notice["message"] and "Εισροές 48,00 €" not in notice["message"]


def test_e12_the_assistant_context_says_when_input_vat_rests_on_no_rule(client):
    from copilot import build_context
    firm = _admin(client)
    client.post("/api/expenses", json=_expense(category="Φιλοξενία / Εστίαση", date=athens_today().isoformat()))
    month = _run(build_context({"firm_id": firm}))["τρέχων_μήνας"]
    assert month["ΦΠΑ_εισροών_χωρίς_κανόνα_εκπτωσιμότητας"] == "24,00 €"
    _patch(client, vat_deductibility_rules={"Φιλοξενία / Εστίαση": 50})
    month = _run(build_context({"firm_id": firm}))["τρέχων_μήνας"]
    assert month["ΦΠΑ_εισροών_χωρίς_κανόνα_εκπτωσιμότητας"] == "0,00 €"


# ----------------------------------------------------------- Ε16 · το μήνυμα της διαγραφής υπόθεσης ----

def test_e16_the_delete_refusal_says_which_documents_it_counts(client, office):
    inv = _draft(client, office.alpha, case_id=office.case).json()
    r = client.delete(f"/api/cases/{office.case}")
    assert r.status_code == 409
    assert "1 τιμολόγια" in r.json()["detail"] and "πρόχειρα" in r.json()["detail"] and "ακυρωμένα" in r.json()["detail"]
    assert client.get(f"/api/cases/{office.case}").json()["linked"]["invoices"]["count"] == 0     # το πρόχειρο δεν μετρά στη σύνοψη
    assert inv


def _trail(firm, method, path_prefix):
    from db import db
    rows = _run(db.audit_logs.find({"firm_id": firm, "method": method}).to_list(200))
    return [r for r in rows if r["path"].startswith(path_prefix)]


def test_e16_changing_the_vat_table_leaves_a_trace_with_the_before_and_after(client):
    """Το ελεγκτικό ίχνος κρατούσε μόνο τη διαδρομή: έδειχνε ΟΤΙ άλλαξε ο πίνακας, όχι από τι σε τι."""
    firm = _admin(client)
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 50, "Μεταφορικά": 25}, vat_rules_confirmed_by="Χριστόφορος")
    _patch(client, vat_deductibility_rules={"Ενοίκιο": 80, "Μεταφορικά": 25})
    notes = [r["details"] for r in _trail(firm, "PATCH", "/api/settings")]
    last = notes[-1]
    assert "Ενοίκιο 50% → 80%" in last, notes                                  # από → σε, χωρίς περιττά δεκαδικά (όχι «50.0%»)
    assert "Μεταφορικά" not in last, notes                                     # ό,τι δεν άλλαξε δεν γράφεται
    assert "Χριστόφορος" in last and "ανακλήθηκε" in last, notes               # και ότι η επιβεβαίωση ακυρώθηκε


def test_e16_a_settings_save_that_does_not_touch_the_table_leaves_no_table_note(client):
    firm = _admin(client)
    _put(client, vat_deductibility_rules={"Ενοίκιο": 50}, vat_rules_confirmed_by="Χριστόφορος")
    _put(client, company_name="Νέα επωνυμία")
    notes = [r["details"] for r in _trail(firm, "PUT", "/api/settings")]
    assert "Ενοίκιο" in notes[0] and notes[-1] == "", notes


def test_e16_retagging_an_invoice_leaves_a_trace_with_the_before_and_after(client, office):
    inv = _issued(client, office.alpha, case_id=office.case)
    client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.other})
    notes = [r["details"] for r in _trail(office.firm, "PATCH", "/api/invoices")]
    assert notes and "Υπόθεση Α" in notes[-1] and "Υπόθεση Β" in notes[-1], notes
    assert notes[-1].index("Υπόθεση Α") < notes[-1].index("Υπόθεση Β"), notes     # πρώτα το πριν, μετά το μετά


# ================================================== ΚΕΝΑ ΠΟΥ ΒΡΗΚΑΝ ΤΑ ΣΚΟΠΙΜΑ ΛΑΘΗ ====
#
# Μετά τις διορθώσεις χαλάστηκε σκόπιμα ο κώδικας σε 71 σημεία (ένα-ένα) για να φανεί αν τα tests θα το πρόσεχαν. Τα 15 που
# πέρασαν απαρατήρητα αφορούσαν συμπεριφορές που δεν είχε ελέγξει κανένα test· τα παρακάτω τις κλείνουν.

def test_an_invoice_whose_case_has_gone_can_still_be_retagged_and_edited(client, office):
    """Η υπόθεση δεν υπάρχει πια (π.χ. επαναφορά από αντίγραφο ασφαλείας που δεν την περιείχε): το τιμολόγιο που τη φέρει
    πρέπει να διορθώνεται όσο η αναφορά δεν αλλάζει — και το ελεγκτικό ίχνος δεν αποτυγχάνει."""
    from db import db
    inv = _draft(client, office.alpha, case_id=office.case, lawyer_id=office.admin).json()
    _run(db.cases.delete_one({"_id": ObjectId(office.case)}))
    edited = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id=office.case, lawyer_id=office.admin))
    assert edited.status_code == 200, edited.text
    assert (edited.json()["case_id"], edited.json()["lawyer_id"], edited.json()["subtotal"]) == (office.case, office.admin, 500.0)
    again = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": office.case})
    assert again.status_code == 200, again.text
    assert (again.json()["case_id"], again.json()["lawyer_id"]) == (office.case, office.admin)
    assert "(άγνωστη υπόθεση)" in _trail(office.firm, "PATCH", "/api/invoices")[-1]["details"]
    elsewhere = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": str(ObjectId())})
    assert elsewhere.status_code == 404                                        # μια ΑΛΛΗ ανύπαρκτη υπόθεση απορρίπτεται


def test_resending_the_same_case_does_not_change_the_invoice_lawyer(client, office):
    """Η ίδια υπόθεση που ξαναστέλνεται δεν είναι αλλαγή υπόθεσης: ο δικηγόρος του τιμολογίου δεν ξαναπροκύπτει από αυτήν."""
    free = _make_case(client, "Υπόθεση χωρίς δικηγόρο", office.alpha, "ΑΛΦΑ ΑΕ")
    inv = _draft(client, office.alpha, case_id=free["id"]).json()
    assert inv["lawyer_id"] == ""                                              # η υπόθεση δεν είχε υπεύθυνο όταν εκδόθηκε
    case = client.get(f"/api/cases/{free['id']}").json()["case"]
    assert client.put(f"/api/cases/{free['id']}", json={**case, "lawyer_id": office.lawyer_b}).status_code == 200
    patched = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": free["id"]})
    assert patched.status_code == 200, patched.text
    assert (patched.json()["case_id"], patched.json()["lawyer_id"]) == (free["id"], "")
    put = client.put(f"/api/invoices/{inv['id']}", json=_update_body(inv, case_id=free["id"]))
    assert put.status_code == 200, put.text
    assert (put.json()["case_id"], put.json()["lawyer_id"]) == (free["id"], "")


def test_an_expense_whose_case_has_gone_can_still_be_edited_and_keeps_its_client(client, office):
    from db import db
    e = _case_expense(client, office)
    _run(db.cases.delete_one({"_id": ObjectId(office.case)}))
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(
        amount=62.0, vat_amount=12.0, scope="case", case_id=office.case, lawyer_id=office.lawyer, rebillable=True))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("case", office.case, office.alpha, office.lawyer, True)     # ο πελάτης δεν χάθηκε μαζί με την υπόθεση
    assert _raw("expenses", e["id"])["amount"] == 62.0
    elsewhere = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="case", case_id=str(ObjectId())))
    assert elsewhere.status_code == 404                                        # μια ΑΛΛΗ ανύπαρκτη υπόθεση απορρίπτεται


def test_resending_the_same_case_does_not_revalidate_the_lawyer_the_expense_inherits(client, office):
    """Η ίδια υπόθεση που ξαναστέλνεται δεν είναι αλλαγή υπόθεσης: ο δικηγόρος που κληρονομείται από αυτήν δεν ελέγχεται ξανά
    (εδώ έχει φύγει από το γραφείο), αφού δεν τον όρισε ο χρήστης."""
    from db import db
    e = _case_expense(client, office)
    _run(db.expenses.update_one({"_id": ObjectId(e["id"])}, {"$set": {"lawyer_id": ""}}))    # καταχωρήθηκε πριν οριστεί υπεύθυνος
    _remove_user(office.lawyer)
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(case_id=office.case))
    assert r.status_code == 200, r.text
    assert _matter(r.json())[:3] == ("case", office.case, office.alpha)


def test_a_put_that_sends_a_case_and_a_lawyer_keeps_the_lawyer_it_sent(client, office):
    e = _case_expense(client, office)                                          # κληρονόμησε τον office.lawyer
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(case_id=office.other, lawyer_id=office.lawyer))
    assert r.status_code == 200, r.text
    assert _matter(r.json()) == ("case", office.other, office.beta, office.lawyer, True)      # όχι ο υπεύθυνος της νέας υπόθεσης


def test_a_put_without_matter_fields_is_not_blocked_by_an_odd_stored_matter(client, office):
    """Ένα αίτημα που δεν στέλνει κανένα από τα πεδία «σε ποιον ανήκει» δεν κρίνει όσα είναι αποθηκευμένα — ούτε τα αλλάζει."""
    from db import db
    e = client.post("/api/expenses", json=_expense()).json()
    _run(db.expenses.update_one({"_id": ObjectId(e["id"])},
                                {"$set": {"scope": "client", "client_id": office.alpha, "case_id": office.case}}))   # αντιφατικό, από παλιά δεδομένα
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(vendor="Νέος προμηθευτής", amount=62.0, vat_amount=12.0))
    assert r.status_code == 200, r.text
    stored = _raw("expenses", e["id"])
    assert (stored["vendor"], stored["amount"]) == ("Νέος προμηθευτής", 62.0)
    assert _matter(stored) == ("client", office.case, office.alpha, "", False)


def test_a_stored_reference_into_another_firm_is_treated_as_unknown(client, office, foreign):
    """Μια αποθηκευμένη υπόθεση που δείχνει σε ΑΛΛΟ γραφείο (κατεστραμμένα δεδομένα) δεν διαβάζεται ποτέ: ούτε ο πελάτης ούτε
    ο δικηγόρος της περνούν στο έξοδο."""
    from db import db
    e = _case_expense(client, office)
    _run(db.expenses.update_one({"_id": ObjectId(e["id"])}, {"$set": {"case_id": foreign.case_id}}))
    r = client.put(f"/api/expenses/{e['id']}", json=_expense(scope="case", case_id=foreign.case_id))
    assert r.status_code == 200, r.text
    assert r.json()["client_id"] == office.alpha and r.json()["lawyer_id"] == ""


def test_an_invoice_stored_against_another_firms_case_does_not_inherit_from_it(client, office, foreign):
    from db import db
    inv = _issued(client, office.alpha)
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])}, {"$set": {"case_id": foreign.case_id, "lawyer_id": ""}}))
    r = client.patch(f"/api/invoices/{inv['id']}/matter", json={"case_id": foreign.case_id, "lawyer_id": ""})
    assert r.status_code == 200, r.text
    assert (r.json()["case_id"], r.json()["lawyer_id"]) == (foreign.case_id, "")            # ο δικηγόρος της ξένης υπόθεσης δεν κληρονομείται
    note = _trail(office.firm, "PATCH", "/api/invoices")[-1]["details"]
    assert "(άγνωστη υπόθεση)" in note and "Ξένη Υπόθεση" not in note, note              # ούτε ο τίτλος της διαρρέει στο ίχνος


def test_e11_the_client_name_follows_the_client(client, office):
    """Αλλάζει ο πελάτης της υπόθεσης χωρίς να σταλεί όνομα: το όνομα που φαίνεται είναι του νέου πελάτη, όχι του παλιού."""
    r = client.put(f"/api/cases/{office.case}", json={"title": "Υπόθεση Α", "client_id": office.beta})
    assert r.status_code == 200, r.text
    assert (r.json()["client_id"], r.json()["client_name"]) == (office.beta, "Ιδιώτης Πελάτης")
    cleared = client.put(f"/api/cases/{office.case}", json={"title": "Υπόθεση Α", "client_id": ""})
    assert (cleared.json()["client_id"], cleared.json()["client_name"]) == ("", "")


def test_the_limit_on_amounts_is_the_same_for_the_api_and_the_totals(client, office):
    """Ό,τι δέχεται το API ως έξοδο (έως και ακριβώς το όριο) μετρά και στα σύνολα· ό,τι το ξεπερνά, όχι."""
    from tax_utils import MAX_LINE_VALUE
    from vat_rules import summarize_expenses
    created = client.post("/api/expenses", json=_expense(amount=MAX_LINE_VALUE, vat_amount=MAX_LINE_VALUE / 10))
    assert created.status_code == 200, created.text
    assert client.post("/api/expenses", json=_expense(amount=MAX_LINE_VALUE + 1, vat_amount=0)).status_code == 400
    at_limit = summarize_expenses([_raw("expenses", created.json()["id"])], None)
    assert (at_limit["gross"], at_limit["input_vat"]) == (float(MAX_LINE_VALUE), MAX_LINE_VALUE / 10)
    over = summarize_expenses([{"amount": MAX_LINE_VALUE + 0.01, "vat_amount": 0.0, "category": "Λοιπά"}], None)
    assert over["gross"] == 0.0


def test_the_note_about_input_vat_without_a_rule_appears_only_for_a_positive_amount():
    from automation import _unruled_sentence
    assert _unruled_sentence(0) == "" and _unruled_sentence(0.0) == "" and _unruled_sentence(None) == ""
    assert _unruled_sentence(-24.0) == ""                     # καθαρό ΦΠΑ πιστωτικών: δεν «εκπίπτει 100%» τίποτα
    positive = _unruled_sentence(24.0)
    assert "24,00 €" in positive and "χωρίς κανόνα" in positive


def test_e3_two_simultaneous_markings_cannot_overwrite_each_other(client, monkeypatch):
    """Δύο κλικ «Υποβλήθηκε» μαζί: ό,τι έγραψε η πρώτη εγγραφή δεν ξαναγράφεται από τη δεύτερη, που είχε διαβάσει την
    περίοδο πριν υποβληθεί (προσομοίωση: ενώ υπολογίζονται οι αριθμοί του δεύτερου αιτήματος, ολοκληρώνεται το πρώτο)."""
    import routes_automation
    from db import db
    firm = _admin(client)
    period = _last_closed_period()
    _two_expenses_in(client, period)
    _run_job(firm)
    real = routes_automation.compute_vat_period
    first_at = "2026-01-01T00:00:00+00:00"

    async def with_a_competing_request(firm_id, period_data, settings):
        await db.vat_periods.update_one(
            {"firm_id": firm, "period": period["period"]},
            {"$set": {"status": "submitted", "submitted_at": first_at, "input_vat": 111.0,
                      "vat_rules_snapshot": {"rules": {}, "confirmed_by": "πρώτο αίτημα", "frozen_at": first_at}}})
        return await real(firm_id, period_data, settings)

    monkeypatch.setattr(routes_automation, "compute_vat_period", with_a_competing_request)
    assert client.post(f"/api/automation/vat-periods/{period['period']}/submitted").status_code == 200
    stored = _stored_period(firm, period["period"])
    assert (stored["input_vat"], stored["submitted_at"]) == (111.0, first_at)
    assert stored["vat_rules_snapshot"]["confirmed_by"] == "πρώτο αίτημα"
