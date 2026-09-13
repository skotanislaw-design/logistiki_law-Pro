"""Έλεγχος ΑΦΜ: να πιάνεται το τυπογραφικό στην καταχώριση, όχι στην ΑΑΔΕ.

Χωρίς αυτόν, ένα λάθος ψηφίο γινόταν αντιληπτό μόνο τη στιγμή της διαβίβασης — αφού το
τιμολόγιο είχε ήδη εκδοθεί, αριθμηθεί και σταλεί στον πελάτη.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from tax_utils import afm_check_digit, is_valid_afm

VALID = "123456783"
INVALID_DIGIT = "123456789"      # ίδια οκτώ ψηφία, λάθος ψηφίο ελέγχου


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        c.post("/api/auth/register",
               json={"email": "d@example.gr", "password": "Str0ngPass!", "name": "Δικηγόρος"})
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------- αλγόριθμος ---

def test_exactly_one_check_digit_is_valid_per_prefix():
    """Η ιδιότητα που κάνει τον έλεγχο χρήσιμο: εννέα στα δέκα λάθη απορρίπτονται."""
    prefix = "12345678"
    accepted = [d for d in range(10) if is_valid_afm(f"{prefix}{d}")]
    assert accepted == [afm_check_digit(prefix)]


def test_transposed_digits_are_caught():
    """Το πιο συνηθισμένο τυπογραφικό: δύο ψηφία αντεστραμμένα."""
    assert is_valid_afm(VALID)
    assert not is_valid_afm("213456783")


def test_all_zeros_is_rejected_although_the_algorithm_accepts_it():
    assert afm_check_digit("00000000") == 0
    assert not is_valid_afm("000000000")


def test_leading_zero_afm_is_valid_and_stays_a_string():
    prefix = "01234567"
    assert is_valid_afm(f"{prefix}{afm_check_digit(prefix)}")


@pytest.mark.parametrize("value", ["", "12345678", "1234567890", "12345678α", "  "])
def test_malformed_values_are_rejected(value):
    assert not is_valid_afm(value)


# ---------------------------------------------------------------- πελάτες ---

def test_client_with_valid_afm_is_accepted(client):
    r = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": VALID, "client_type": "company"})
    assert r.status_code == 200, r.text
    assert r.json()["country"] == "GR"


def test_client_with_bad_check_digit_is_refused_with_a_useful_message(client):
    r = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": INVALID_DIGIT,
                                          "client_type": "company"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert INVALID_DIGIT in detail and "ψηφίο ελέγχου" in detail


def test_client_with_eight_digits_is_told_what_is_wrong(client):
    r = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "12345678"})
    assert r.status_code == 400
    assert "8 ψηφία" in r.json()["detail"]


def test_client_without_afm_is_still_accepted(client):
    """Ιδιώτης χωρίς ΑΦΜ είναι νόμιμος — εκδίδεται ΑΠΥ λιανικής."""
    assert client.post("/api/clients", json={"name": "Ιωάννης Παπαδόπουλος"}).status_code == 200


def test_foreign_client_is_not_checked_against_the_greek_rule(client):
    r = client.post("/api/clients", json={"name": "MÜLLER GMBH", "afm": "DE123456789",
                                          "country": "DE", "client_type": "company"})
    assert r.status_code == 200, r.text


def test_existing_client_with_a_bad_afm_remains_readable(client):
    """Ο έλεγχος είναι στη διαδρομή, όχι στο μοντέλο: μια παλιά εγγραφή με λάθος ΑΦΜ
    πρέπει να φαίνεται στη λίστα για να μπορεί να διορθωθεί — όχι να εξαφανίζεται."""
    from bson import ObjectId
    from db import db

    # Το γραφείο του συνδεδεμένου χρήστη, όπως το βλέπει η ίδια η διαδρομή.
    cid = client.post("/api/clients", json={"name": "ΝΕΑ ΑΕ", "afm": VALID,
                                            "client_type": "company"}).json()["id"]
    firm_id = _run(db.clients.find_one({"_id": ObjectId(cid)}))["firm_id"]

    # Εγγραφή γραμμένη πριν υπάρξει ο έλεγχος, απευθείας στη βάση.
    _run(db.clients.insert_one({"name": "ΠΑΛΙΑ ΑΕ", "afm": INVALID_DIGIT,
                                "firm_id": firm_id, "client_type": "company"}))

    listed = {c["name"]: c["afm"] for c in client.get("/api/clients").json()}
    assert listed.get("ΠΑΛΙΑ ΑΕ") == INVALID_DIGIT, "η παλιά εγγραφή πρέπει να παραμένει ορατή"


def test_updating_a_client_also_validates(client):
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": VALID,
                                            "client_type": "company"}).json()["id"]
    r = client.put(f"/api/clients/{cid}", json={"name": "ΑΛΦΑ ΑΕ", "afm": INVALID_DIGIT,
                                                "client_type": "company"})
    assert r.status_code == 400


# --------------------------------------------------------------- ρυθμίσεις ---

def test_firm_afm_is_validated(client):
    """Λάθος εδώ δεν χαλάει ένα τιμολόγιο — τα απορρίπτει όλα."""
    from db import db

    _run(db.users.update_one({"email": "d@example.gr"}, {"$set": {"role": "admin"}}))
    assert client.patch("/api/settings", json={"afm": INVALID_DIGIT}).status_code == 400
    assert client.patch("/api/settings", json={"afm": VALID}).status_code == 200


# ------------------------------------------------------------------ myDATA ---

def test_foreign_client_fails_loudly_at_transmission():
    """Ο κώδικας δήλωνε <country>GR</country> για κάθε πελάτη: ένα γερμανικό ΑΦΜ έφευγε
    ως ελληνικό και επέστρεφε δυσνόητη απόρριψη από την ΑΑΔΕ."""
    from mydata_client import MyDataError, build_invoices_doc_xml

    invoice = {"number": "ΤΠΥ-2026-0001", "date": "2026-09-10",
               "line_items": [{"description": "Αμοιβή", "quantity": 1,
                               "unit_price": 1000.0, "vat_rate": 24}]}
    foreign = {"name": "MÜLLER GMBH", "afm": "DE123456789", "country": "DE",
               "client_type": "company"}
    with pytest.raises(MyDataError, match="2.2/2.3"):
        build_invoices_doc_xml(invoice, foreign, {"afm": VALID})
