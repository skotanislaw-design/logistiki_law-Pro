"""Γύρος 5 (έλεγχος μεταλλάξεων, περιοχή πληρωμών) — κάθε σφάλμα του Stripe στη δημιουργία συνεδρίας γινόταν 500.

`routes_payments._create_session` καλεί `stripe.checkout.Session.create(...)` χωρίς να πιάνει κανένα `stripe.error.StripeError`
(ενώ το polling της σελίδας κατάστασης το πιάνει ρητά). Αποτέλεσμα, και στο προσωπικό (`/api/payments/checkout`) και στην πύλη του
πελάτη (`/api/payments/portal-checkout`):

* υπόλοιπο μικρότερο από το ελάχιστο του Stripe (0,50 €) — π.χ. 0,30 € που απομένουν αφού ο πελατολογικός λογαριασμός εξόφλησε
  σχεδόν όλο το τιμολόγιο — δίνει `InvalidRequestError` → «Internal Server Error», χωρίς να λέει στον πελάτη ούτε στο γραφείο τι φταίει·
* πτώση ή χρονική υπέρβαση του Stripe (`APIConnectionError`) δίνει το ίδιο 500, ενώ είναι προσωρινό και θα έπρεπε να αναφέρεται ως
  «δοκιμάστε ξανά» (502/503).

Συμπεριφορά που ελέγχεται: ελεγχόμενη απάντηση 4xx/5xx (400 για ποσό εκτός ορίων, 502/503 για διακοπή) με `detail` στα
ελληνικά. Τα tests αυτού του αρχείου αποτυγχάνουν στον κώδικα πριν τη διόρθωση (500 «Internal Server Error»).
"""
import asyncio
from datetime import datetime, timezone

import pytest
import stripe
from fastapi.testclient import TestClient

from tax_utils import athens_today


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _origins(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("PREVIEW_ENDPOINT", raising=False)


@pytest.fixture()
def client():
    import server
    # raise_server_exceptions=False: το 500 του server φαίνεται ως απάντηση, όπως θα το έβλεπε ο browser.
    with TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        r = c.post("/api/auth/register", json={"email": "stripe@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def _issued_invoice(owed_total: float) -> str:
    from db import db
    user = _run(db.users.find_one({"email": "stripe@example.gr"}))
    doc = {"firm_id": user["firm_id"], "client_id": "client-a", "client_name": "ΠΕΛΑΤΗΣ", "number": "ΤΠΥ-2026-0001",
           "date": athens_today().isoformat(), "line_items": [], "subtotal": owed_total, "vat_amount": 0.0, "total": owed_total,
           "vat_breakdown": {}, "status": "issued", "mydata_status": "synced", "invoice_type": "2.1",
           "created_at": datetime.now(timezone.utc).isoformat()}
    return str(_run(db.invoices.insert_one(doc)).inserted_id)


@pytest.mark.parametrize("error", [
    stripe.error.InvalidRequestError("Amount must convert to at least 50 cents. €0.30 converts to approximately €0.30.", "amount"),
    stripe.error.APIConnectionError("Δεν ήταν δυνατή η σύνδεση με το Stripe"),
], ids=["amount-below-the-stripe-minimum", "stripe-unreachable"])
def test_a_stripe_error_at_checkout_is_reported_with_a_message_and_not_as_a_500(client, monkeypatch, error):
    import routes_payments

    calls = []

    def refuse(**_kwargs):
        calls.append(_kwargs)
        raise error

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "create", staticmethod(refuse))
    invoice_id = _issued_invoice(0.30)
    r = client.post("/api/payments/checkout", json={"invoice_id": invoice_id, "origin_url": "https://nomos.example.gr"})
    assert len(calls) == 1, f"το αίτημα δεν έφτασε στο Stripe (άλλο σφάλμα πριν): {r.status_code} {r.text[:120]}"
    assert r.status_code in (400, 502, 503), f"{r.status_code}: {r.text[:120]}"
    assert r.json().get("detail"), "καμία εξήγηση προς τον χρήστη"
