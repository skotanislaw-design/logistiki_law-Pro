"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D — D-05) — η αποδοχή διαφοράς δεν είναι λευκή επιταγή.

Στην οθόνη «Τράπεζα», όταν η κατάθεση δεν ταιριάζει με το οφειλόμενο, ο χρήστης βλέπει «διαφορά −24,00 €» και τσεκάρει
«Αποδέχομαι τη διαφορά». Ο server δεχόταν τότε ΟΠΟΙΑΔΗΠΟΤΕ διαφορά: αν στο μεταξύ (δεύτερη καρτέλα, συνάδελφος) εφαρμοζόταν
πελατολογικό 100,00 € στο ίδιο τιμολόγιο, το οφειλόμενο έγινε 24,00 € και η εξόφληση έκλεινε με διαφορά +76,00 €. Ο χρήστης
είχε αποδεχτεί −24,00 €· τα βιβλία κατέγραφαν +76,00 €.

Τώρα η αίτηση φέρει και τη διαφορά που είδε (`expected_difference`, κατάθεση − οφειλόμενο). Αν δεν είναι αυτή που υπολογίζει ο
server τη στιγμή της εξόφλησης: 409 `amount_changed`, καμία αλλαγή, και ο χρήστης βλέπει τη νέα διαφορά.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import invoice_services as svc
import routes_bank
from db import db

FIRM = "firm-bank-consent-r5"
USER = {"firm_id": FIRM, "email": "logistis@example.gr"}
NUMBER = "ΤΠΥ-2026-0001"


async def _invoice(total=124.0, **over):
    doc = {"firm_id": FIRM, "number": NUMBER, "status": "issued", "total": total, "subtotal": round(total / 1.24, 2),
           "client_id": "c1", "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-06-01", "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _deposit(amount=100.0, **over):
    doc = {"firm_id": FIRM, "date": "2026-06-05", "amount": amount, "description": "ΜΕΤΑΦΟΡΑ", "match_status": "unmatched", **over}
    return (await db.bank_transactions.insert_one(doc)).inserted_id


async def _reconcile(tx_id, invoice_id, **over):
    kwargs = {"ignore": False, "accept_difference": True, **over}
    return await routes_bank.reconcile(str(tx_id), invoice_id=str(invoice_id), user=USER, **kwargs)


async def _state(invoice_id, tx_id):
    return await db.invoices.find_one({"_id": invoice_id}), await db.bank_transactions.find_one({"_id": tx_id})


# ============================================================================ η διαφορά που είδε ο χρήστης ====

@pytest.mark.asyncio
async def test_the_difference_the_user_saw_is_accepted_and_recorded():
    invoice, tx = await _invoice(124.0), await _deposit(100.0)

    answer = await _reconcile(tx, invoice, expected_difference=-24.0)

    assert answer["status"] == "paid" and answer["difference"] == -24.0
    paid, deposit = await _state(invoice, tx)
    assert paid["status"] == "paid" and deposit["match_status"] == "manual_matched" and deposit["amount_difference"] == -24.0


@pytest.mark.asyncio
async def test_a_difference_that_changed_while_the_window_was_open_is_refused_and_nothing_is_booked():
    invoice, tx = await _invoice(124.0, trust_applied=100.0), await _deposit(100.0)     # στο μεταξύ εφαρμόστηκαν 100 € από πελατολογικό

    with pytest.raises(HTTPException) as refused:
        await _reconcile(tx, invoice, expected_difference=-24.0)

    assert refused.value.status_code == 409
    assert "άλλαξε" in refused.value.detail and "+76,00 €" in refused.value.detail and "−24,00 €" in refused.value.detail
    still_open, deposit = await _state(invoice, tx)
    assert still_open["status"] == "issued" and deposit["match_status"] == "unmatched" and "amount_difference" not in deposit
    assert "claim" not in deposit


@pytest.mark.asyncio
async def test_the_sign_of_the_difference_matters():
    invoice, tx = await _invoice(100.0), await _deposit(124.0)                           # +24: η κατάθεση υπερκαλύπτει

    with pytest.raises(HTTPException) as refused:
        await _reconcile(tx, invoice, expected_difference=-24.0)                         # ο χρήστης είδε −24

    assert refused.value.status_code == 409 and "+24,00 €" in refused.value.detail
    assert (await _state(invoice, tx))[0]["status"] == "issued"


@pytest.mark.asyncio
async def test_a_difference_is_compared_in_cents_not_in_floating_point():
    invoice, tx = await _invoice(1240.01), await _deposit(1240.0)

    answer = await _reconcile(tx, invoice, expected_difference=-0.01)

    assert answer["status"] == "paid" and answer["difference"] == -0.01


@pytest.mark.asyncio
async def test_even_a_few_cents_of_change_is_a_change():
    invoice, tx = await _invoice(124.0), await _deposit(100.0)

    with pytest.raises(HTTPException) as refused:
        await _reconcile(tx, invoice, expected_difference=-24.05)

    assert refused.value.status_code == 409
    assert (await _state(invoice, tx))[0]["status"] == "issued"


@pytest.mark.asyncio
async def test_a_value_the_screen_rounded_to_cents_still_matches():
    invoice, tx = await _invoice(124.0), await _deposit(100.0)
    assert (await _reconcile(tx, invoice, expected_difference=-24.004))["status"] == "paid"


@pytest.mark.asyncio
async def test_without_the_expected_difference_the_old_behaviour_remains():
    """Για πελάτες API που δεν στέλνουν την παράμετρο· το frontend την στέλνει πάντα."""
    invoice, tx = await _invoice(124.0, trust_applied=100.0), await _deposit(100.0)

    answer = await _reconcile(tx, invoice)

    assert answer["status"] == "paid" and answer["difference"] == 76.0


@pytest.mark.asyncio
async def test_an_exact_amount_needs_no_consent_even_if_the_screen_showed_a_difference():
    """Αν το οφειλόμενο έγινε ακριβώς ίσο με την κατάθεση, δεν υπάρχει διαφορά να αποδεχτεί κανείς."""
    invoice, tx = await _invoice(124.0, trust_applied=24.0), await _deposit(100.0)

    answer = await _reconcile(tx, invoice, expected_difference=-24.0)

    assert answer["status"] == "paid" and answer["difference"] == 0.0
    assert "amount_difference" not in (await _state(invoice, tx))[1]


@pytest.mark.asyncio
async def test_the_expected_difference_does_not_replace_the_consent_itself():
    invoice, tx = await _invoice(124.0), await _deposit(100.0)

    with pytest.raises(HTTPException) as refused:
        await _reconcile(tx, invoice, accept_difference=False, expected_difference=-24.0)

    assert refused.value.status_code == 409 and "accept_difference" in refused.value.detail
    assert (await _state(invoice, tx))[0]["status"] == "issued"


# ============================================================================ το επίπεδο υπηρεσίας ====

@pytest.mark.asyncio
async def test_the_service_refuses_with_a_reason_the_callers_can_tell_apart():
    invoice, tx = await _invoice(124.0, trust_applied=100.0), await _deposit(100.0)
    stale = await db.bank_transactions.find_one({"_id": tx})

    result = await svc.apply_bank_payment(FIRM, stale, invoice, settings={}, paid_via="bank_manual",
                                          status_on_success="manual_matched", accept_difference=True, expected_difference=-24.0)

    assert result["status"] == "refused" and result["reason"] == "amount_changed"
    assert result["due"] == 24.0 and result["difference"] == 76.0


@pytest.mark.asyncio
async def test_a_dry_run_applies_the_same_check():
    invoice, tx = await _invoice(124.0, trust_applied=100.0), await _deposit(100.0)
    stale = await db.bank_transactions.find_one({"_id": tx})

    result = await svc.apply_bank_payment(FIRM, stale, invoice, settings={}, paid_via="bank_manual", dry_run=True,
                                          status_on_success="manual_matched", accept_difference=True, expected_difference=-24.0)

    assert result["reason"] == "amount_changed"


# ============================================================================ είσοδος από HTTP ====

@pytest.fixture()
def office():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "consent@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        firm = c.get("/api/auth/me").json().get("firm_id")
        yield c, firm


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e30", "abc"])
def test_a_nonsense_expected_difference_is_refused_at_the_door(office, bad):
    client, _firm = office
    r = client.post(f"/api/bank/reconcile/000000000000000000000000?invoice_id=000000000000000000000000&accept_difference=true"
                    f"&expected_difference={bad}")
    assert r.status_code in (400, 422), r.text


def test_the_http_route_carries_the_check_end_to_end(office):
    import asyncio
    client, firm = office
    run = lambda coro: asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)    # noqa: E731
    invoice = run(db.invoices.insert_one({"firm_id": firm, "number": NUMBER, "status": "issued", "total": 124.0, "subtotal": 100.0,
                                          "client_id": "c1", "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-06-01", "line_items": [],
                                          "trust_applied": 100.0})).inserted_id
    tx = run(db.bank_transactions.insert_one({"firm_id": firm, "date": "2026-06-05", "amount": 100.0, "description": "ΜΕΤΑΦΟΡΑ",
                                              "match_status": "unmatched"})).inserted_id

    stale = client.post(f"/api/bank/reconcile/{tx}?invoice_id={invoice}&accept_difference=true&expected_difference=-24")
    fresh = client.post(f"/api/bank/reconcile/{tx}?invoice_id={invoice}&accept_difference=true&expected_difference=76")

    assert stale.status_code == 409 and "άλλαξε" in stale.json()["detail"]
    assert fresh.status_code == 200 and fresh.json()["difference"] == 76.0
