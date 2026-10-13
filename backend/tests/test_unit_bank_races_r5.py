"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C) — μία κατάθεση ↔ ένα τιμολόγιο, όταν δύο ενέργειες εναλλάσσονται.

Πριν: η `apply_bank_payment` διάβαζε την κατάθεση, έλεγχε, εξόφλησε το τιμολόγιο και στο τέλος έγραφε τη σύνδεση χωρίς
κανέναν όρο. Δύο αιτήματα (δύο καρτέλες του λογιστή, ή ο Autopilot και ένας άνθρωπος) που διάβαζαν την ίδια ασυμφώνητη
κατάθεση και διάλεγαν ΔΙΑΦΟΡΕΤΙΚΑ τιμολόγια εξόφλησαν και τα δύο — ένα ποσό, δύο εξοφλήσεις — και η κατάθεση έδειχνε στο
τελευταίο που έγραψε. Η «αγνόηση» και ο κύκλος συμφωνίας του Autopilot έγραφαν επίσης πάνω σε κατάθεση που στο μεταξύ είχε
επιβεβαιωθεί (το τιμολόγιο έμενε εξοφλημένο από κατάθεση που φαινόταν αγνοημένη/ασυμφώνητη), και το «σημείωση ως
εξοφλημένου» μετέτρεπε σε εξοφλημένο ένα τιμολόγιο που στο μεταξύ είχε ακυρωθεί ή πληρωθεί με κάρτα.

Εδώ οι εναλλαγές προσομοιώνονται ντετερμινιστικά: πριν από μια συγκεκριμένη εγγραφή της βάσης τρέχει ολόκληρη μια άλλη
ενέργεια, ακριβώς όπως θα γινόταν με δύο ταυτόχρονα αιτήματα.
"""
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import automation
import invoice_services as svc
import routes_bank
import routes_invoices
from db import db

FIRM = "firm-bank-races-r5"
USER = {"firm_id": FIRM, "email": "logistis@example.gr"}
NUMBER_A, NUMBER_B = "ΤΠΥ-2026-0001", "ΤΠΥ-2026-0002"


# ------------------------------------------------------------------------------------------------ βοηθητικά ----

def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


class _HookedCollection:
    def __init__(self, real, hooks):
        self._real, self._hooks = real, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        hook = self._hooks.get(name)
        if hook is None:
            return attr

        async def call(*args, **kwargs):
            return await hook(attr, *args, **kwargs)
        return call


class _HookedDb:
    def __init__(self, real, collection, hooks):
        self._real, self._collection, self._hooks = real, collection, hooks

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return _HookedCollection(attr, self._hooks) if name == self._collection else attr


def _race(monkeypatch, module, collection, method, before, times=1):
    """Πριν από την `times`-οστή πρώτη κλήση `collection.method` του `module`, τρέχει ολόκληρη η `before()` (με την
    πραγματική βάση, όχι την παγιδευμένη)· μετά εκτελείται κανονικά η κλήση. Επιστρέφει τον μετρητή κλήσεων."""
    state = {"calls": 0}

    async def hook(real, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] <= times:
            monkeypatch.setattr(module, "db", db)
            try:
                await before()
            finally:
                monkeypatch.setattr(module, "db", hooked)
        return await real(*args, **kwargs)

    hooked = _HookedDb(db, collection, {method: hook})
    monkeypatch.setattr(module, "db", hooked)
    return state


async def _invoice(number=NUMBER_A, total=124.0, **over):
    doc = {"firm_id": FIRM, "number": number, "status": "issued", "total": total, "subtotal": round(total / 1.24, 2),
           "client_id": "c1", "client_name": "ΑΛΦΑ ΑΕ", "date": "2026-06-01", "line_items": [], **over}
    return (await db.invoices.insert_one(doc)).inserted_id


async def _tx(amount=124.0, **over):
    doc = {"firm_id": FIRM, "date": "2026-06-05", "amount": amount, "description": "ΜΕΤΑΦΟΡΑ", "match_status": "unmatched",
           **over}
    return (await db.bank_transactions.insert_one(doc)).inserted_id


async def _suggested_tx(invoice_id, number=NUMBER_A, **over):
    """Πρόταση που περιμένει: μονοσήμαντη, με ακριβές ποσό και υψηλή βεβαιότητα — ό,τι εξοφλεί ο Autopilot."""
    return await _tx(match_status="auto_matched", matched_invoice_id=str(invoice_id), matched_invoice_number=number,
                     match_confidence=95, match_ambiguous=False, **over)


async def _doc(collection, _id):
    return await getattr(db, collection).find_one({"_id": _id})


async def _settle(tx_doc, invoice_id, *, via="bank_manual", status="manual_matched"):
    return await svc.apply_bank_payment(FIRM, dict(tx_doc), invoice_id, settings={}, paid_via=via, status_on_success=status)


def _no_claim(tx: dict):
    assert "claim" not in tx and "claim_at" not in tx, f"έμεινε κλείδωμα: {tx.get('claim')} / {tx.get('claim_at')}"


# =========================================================================== 1. η εξόφληση από κατάθεση ====

@pytest.mark.asyncio
async def test_a_request_that_loses_the_race_before_the_claim_is_refused_and_leaves_its_invoice_open(monkeypatch):
    a, b = await _invoice(NUMBER_A), await _invoice(NUMBER_B)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)                                  # ό,τι διάβασαν και τα δύο αιτήματα

    async def the_other_tab_wins():
        assert (await _settle(stale, b))["status"] == "paid"

    _race(monkeypatch, svc, "bank_transactions", "find_one_and_update", the_other_tab_wins)
    mine = await _settle(stale, a)

    assert mine["status"] == "refused" and mine["reason"] == "tx_settled", mine
    assert NUMBER_B in mine["message"]
    assert (await _doc("invoices", a))["status"] == "issued"
    assert (await _doc("invoices", b))["status"] == "paid"
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(b)
    _no_claim(tx)


@pytest.mark.asyncio
async def test_a_request_that_arrives_while_another_holds_the_deposit_is_told_it_is_busy(monkeypatch):
    a, b = await _invoice(NUMBER_A), await _invoice(NUMBER_B)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)
    other = {}

    async def second_request_arrives_mid_flight():
        other["result"] = await _settle(stale, b)

    # η πρώτη εγγραφή τιμολογίου είναι η εξόφληση του Α: τη στιγμή εκείνη η κατάθεση είναι ήδη κλειδωμένη από το πρώτο αίτημα
    _race(monkeypatch, svc, "invoices", "update_one", second_request_arrives_mid_flight)
    mine = await _settle(stale, a)

    assert mine["status"] == "paid", mine
    assert other["result"]["status"] == "refused" and other["result"]["reason"] == "tx_busy", other["result"]
    assert (await _doc("invoices", a))["status"] == "paid"
    assert (await _doc("invoices", b))["status"] == "issued"                       # το δεύτερο αίτημα δεν άγγιξε το Β
    tx = await _doc("bank_transactions", tx_id)
    assert tx["matched_invoice_id"] == str(a) and tx["match_status"] == "manual_matched"
    _no_claim(tx)


@pytest.mark.asyncio
async def test_exactly_one_of_two_invoices_is_paid_whichever_request_goes_first(monkeypatch):
    """Και στις δύο σειρές εναλλαγής, ένα ποσό εξοφλεί ένα τιμολόγιο."""
    for first_is_a in (True, False):
        await db.invoices.delete_many({"firm_id": FIRM})
        await db.bank_transactions.delete_many({"firm_id": FIRM})
        a, b = await _invoice(NUMBER_A), await _invoice(NUMBER_B)
        tx_id = await _tx()
        stale = await _doc("bank_transactions", tx_id)
        mine_target, other_target = (a, b) if first_is_a else (b, a)

        async def other():
            await _settle(stale, other_target)

        _race(monkeypatch, svc, "bank_transactions", "find_one_and_update", other)
        await _settle(stale, mine_target)
        monkeypatch.setattr(svc, "db", db)

        statuses = sorted([(await _doc("invoices", a))["status"], (await _doc("invoices", b))["status"]])
        assert statuses == ["issued", "paid"], f"first_is_a={first_is_a}: {statuses}"


@pytest.mark.asyncio
async def test_repeating_the_same_confirmation_is_harmless_and_keeps_the_first_settlement():
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)

    assert (await _settle(stale, a))["status"] == "paid"
    first = await _doc("bank_transactions", tx_id)

    # Ο Autopilot, με παλιό αντίγραφο της κατάθεσης, προσπαθεί ξανά με το ΙΔΙΟ τιμολόγιο: αδρανές, και δεν υποβαθμίζει την
    # ανθρώπινη επιβεβαίωση σε «αυτόματη».
    again = await _settle(stale, a, via="bank_auto", status="auto_confirmed")
    assert again["status"] == "linked", again
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(a)
    assert tx["confirmed_at"] == first["confirmed_at"] and tx["match_confidence"] == 100
    assert (await _doc("invoices", a))["paid_via"] == "bank_manual"
    _no_claim(tx)


@pytest.mark.asyncio
async def test_a_fresh_claim_blocks_the_deposit_and_an_expired_one_does_not():
    a = await _invoice(NUMBER_A)
    tx_id = await _tx(claim="another-action", claim_at=_ago(seconds=svc.DEPOSIT_CLAIM_TTL_SECONDS - 40))

    busy = await _settle(await _doc("bank_transactions", tx_id), a)
    assert busy["status"] == "refused" and busy["reason"] == "tx_busy"
    assert (await _doc("invoices", a))["status"] == "issued"
    assert (await _doc("bank_transactions", tx_id))["claim"] == "another-action"            # το ξένο κλείδωμα μένει ανέγγιχτο

    # Η άλλη ενέργεια κατέρρευσε: μετά το TTL το κλείδωμα δεν μπλοκάρει για πάντα την κατάθεση.
    await db.bank_transactions.update_one(
        {"_id": tx_id}, {"$set": {"claim_at": _ago(seconds=svc.DEPOSIT_CLAIM_TTL_SECONDS + 40)}})
    done = await _settle(await _doc("bank_transactions", tx_id), a)
    assert done["status"] == "paid", done
    _no_claim(await _doc("bank_transactions", tx_id))


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_payment_step_raises(monkeypatch):
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()

    async def database_down(*args, **kwargs):
        raise RuntimeError("η βάση δεν απαντά")

    monkeypatch.setattr(svc, "mark_paid", database_down)
    with pytest.raises(RuntimeError):
        await _settle(await _doc("bank_transactions", tx_id), a)

    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "unmatched" and "matched_invoice_id" not in tx
    _no_claim(tx)                                                                    # η επόμενη προσπάθεια δεν περιμένει το TTL


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_invoice_turns_out_to_be_paid_elsewhere(monkeypatch):
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()

    async def card_got_there_first(firm_id, invoice_id, paid_via="manual", *, ref=None):
        await db.invoices.update_one({"_id": invoice_id}, {"$set": {"status": "paid", "paid_via": "stripe"}})
        return False

    monkeypatch.setattr(svc, "mark_paid", card_got_there_first)
    refused = await _settle(await _doc("bank_transactions", tx_id), a)

    assert refused["status"] == "refused" and refused["reason"] == "already_paid"
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "unmatched" and "matched_invoice_id" not in tx
    _no_claim(tx)


@pytest.mark.asyncio
async def test_a_refusal_before_the_claim_never_leaves_a_claim_behind():
    a = await _invoice(NUMBER_A, status="cancelled")
    tx_id = await _tx()
    refused = await _settle(await _doc("bank_transactions", tx_id), a)
    assert refused["reason"] == "invoice_not_open"
    _no_claim(await _doc("bank_transactions", tx_id))


@pytest.mark.asyncio
async def test_every_attempt_uses_its_own_claim_token(monkeypatch):
    """Αν δύο ενέργειες είχαν το ίδιο «κλειδί», η μία θα μπορούσε να γράψει με το κλείδωμα της άλλης."""
    tokens = []
    real = svc.claim_deposit

    async def recording(firm_id, tx_id, invoice_key, token):
        tokens.append(token)
        return await real(firm_id, tx_id, invoice_key, token)

    monkeypatch.setattr(svc, "claim_deposit", recording)
    for number in (NUMBER_A, NUMBER_B):
        invoice, tx_id = await _invoice(number), await _tx()
        assert (await _settle(await _doc("bank_transactions", tx_id), invoice))["status"] == "paid"

    assert len(tokens) == 2 and len(set(tokens)) == 2 and all(len(t) >= 32 for t in tokens)


@pytest.mark.asyncio
async def test_a_dry_run_neither_claims_nor_changes_anything():
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()
    before = await _doc("bank_transactions", tx_id)
    ok = await svc.apply_bank_payment(FIRM, dict(before), a, settings={}, paid_via="bank_manual",
                                      status_on_success="manual_matched", dry_run=True)
    assert ok["status"] == "ok"
    assert await _doc("bank_transactions", tx_id) == before
    assert (await _doc("invoices", a))["status"] == "issued"


@pytest.mark.asyncio
async def test_a_deposit_that_expires_mid_operation_is_reported_and_logged_not_swallowed(monkeypatch, caplog):
    """Πολύ αργή βάση: το κλείδωμα έληξε ενώ η ενέργεια ήταν σε εξέλιξη και άλλη ενέργεια πρόλαβε με άλλο τιμολόγιο. Δεν
    μπορεί να αποτραπεί (το TTL είναι ακριβώς γι' αυτό), αλλά πρέπει να φανεί: απάντηση που το λέει και καταγραφή σφάλματος."""
    a, b = await _invoice(NUMBER_A), await _invoice(NUMBER_B)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)

    async def lease_expires_and_the_other_finishes():
        await db.bank_transactions.update_one(
            {"_id": tx_id}, {"$set": {"claim_at": _ago(seconds=svc.DEPOSIT_CLAIM_TTL_SECONDS + 60)}})
        assert (await _settle(stale, b))["status"] == "paid"

    _race(monkeypatch, svc, "bank_transactions", "update_one", lease_expires_and_the_other_finishes)
    with caplog.at_level(logging.ERROR, logger=svc.logger.name):
        mine = await _settle(stale, a)

    assert mine["status"] == "refused" and mine["reason"] == "tx_busy", mine
    assert "σημειώθηκε εξοφλημένο" in mine["message"]                                  # ο χρήστης μαθαίνει ότι το Α άλλαξε
    assert any("δεν καταγράφηκε" in record.getMessage() for record in caplog.records)
    tx = await _doc("bank_transactions", tx_id)
    assert tx["matched_invoice_id"] == str(b)                                          # η κατάθεση ανήκει σε όποιον τελείωσε
    _no_claim(tx)


@pytest.mark.asyncio
async def test_an_expired_claim_that_ends_on_the_same_invoice_loses_nothing(monkeypatch):
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)

    async def the_other_finishes_the_same_settlement():
        await db.bank_transactions.update_one(
            {"_id": tx_id}, {"$set": {"claim_at": _ago(seconds=svc.DEPOSIT_CLAIM_TTL_SECONDS + 60)}})
        await _settle(stale, a)

    _race(monkeypatch, svc, "bank_transactions", "update_one", the_other_finishes_the_same_settlement)
    mine = await _settle(stale, a)
    assert mine["status"] in ("paid", "linked"), mine
    assert (await _doc("bank_transactions", tx_id))["matched_invoice_id"] == str(a)


@pytest.mark.asyncio
async def test_a_deposit_deleted_in_the_meantime_is_reported_missing_and_nothing_is_paid():
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)
    await db.bank_transactions.delete_one({"_id": tx_id})

    refused = await _settle(stale, a)
    assert refused["status"] == "refused" and refused["reason"] == "tx_missing"
    assert (await _doc("invoices", a))["status"] == "issued"
    assert routes_bank.REFUSAL_STATUS["tx_missing"] == 404 and routes_bank.REFUSAL_STATUS.get("tx_busy", 409) == 409


@pytest.mark.asyncio
async def test_another_firms_deposit_cannot_be_claimed_even_with_its_id():
    a = await _invoice(NUMBER_A)
    foreign = (await db.bank_transactions.insert_one({
        "firm_id": "another-firm", "date": "2026-06-05", "amount": 124.0, "match_status": "unmatched"})).inserted_id
    refused = await _settle({"_id": foreign, "amount": 124.0, "match_status": "unmatched"}, a)
    assert refused["reason"] == "tx_missing"
    assert (await _doc("invoices", a))["status"] == "issued"
    assert (await _doc("bank_transactions", foreign))["match_status"] == "unmatched"


# ======================================================================== 2. η «αγνόηση» μιας κατάθεσης ====

@pytest.mark.asyncio
async def test_ignoring_does_not_overwrite_a_deposit_confirmed_in_the_meantime(monkeypatch):
    a = await _invoice(NUMBER_A)
    tx_id = await _tx()
    stale = await _doc("bank_transactions", tx_id)

    async def the_accountant_confirms_in_another_tab():
        assert (await _settle(stale, a))["status"] == "paid"

    _race(monkeypatch, svc, "bank_transactions", "update_one", the_accountant_confirms_in_another_tab)
    with pytest.raises(HTTPException) as refused:
        await routes_bank.reconcile(str(tx_id), invoice_id="", ignore=True, accept_difference=False, user=USER)

    assert refused.value.status_code == 409
    assert "ήδη συμφωνηθεί" in refused.value.detail and NUMBER_A in refused.value.detail
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(a)      # η επιβεβαίωση ισχύει
    assert (await _doc("invoices", a))["status"] == "paid"


@pytest.mark.asyncio
async def test_ignoring_a_deposit_another_action_is_processing_is_refused_and_changes_nothing():
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a, claim="the-autopilot", claim_at=_ago(seconds=5))

    with pytest.raises(HTTPException) as refused:
        await routes_bank.reconcile(str(tx_id), invoice_id="", ignore=True, accept_difference=False, user=USER)

    assert refused.value.status_code == 409 and "επεξεργάζεται" in refused.value.detail
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "auto_matched" and tx["matched_invoice_id"] == str(a)


@pytest.mark.asyncio
async def test_ignoring_an_unsettled_deposit_still_works_and_closes_its_cards():
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a)
    card = await automation.notify(FIRM, "reconcile_review", "warning", "Ταιριάζει;", entity="bank_tx", entity_id=tx_id,
                                   requires_action=True)

    out = await routes_bank.reconcile(str(tx_id), invoice_id="", ignore=True, accept_difference=False, user=USER)

    assert out == {"message": "Η κίνηση σημειώθηκε ως αγνοητέα"}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "ignored" and tx["matched_invoice_id"] is None
    assert (await _doc("notifications", card["_id"]))["resolution"] == "ignored"


@pytest.mark.asyncio
async def test_a_manual_confirmation_of_a_busy_deposit_answers_409_and_pays_nothing():
    a = await _invoice(NUMBER_A)
    tx_id = await _tx(claim="the-autopilot", claim_at=_ago(seconds=5))

    with pytest.raises(HTTPException) as refused:
        await routes_bank.reconcile(str(tx_id), invoice_id=str(a), ignore=False, accept_difference=False, user=USER)

    assert refused.value.status_code == 409 and "επεξεργάζεται" in refused.value.detail
    assert (await _doc("invoices", a))["status"] == "issued"


# ================================================================== 3. ο κύκλος συμφωνίας του Autopilot ====

@pytest.mark.asyncio
async def test_the_autopilot_does_not_unmatch_a_deposit_a_human_settled_after_it_was_read(monkeypatch):
    a, b = await _invoice(NUMBER_A), await _invoice(NUMBER_B)
    tx_id = await _suggested_tx(a)
    real = automation.apply_bank_payment

    async def human_got_there_first(firm_id, tx, invoice_id, **kwargs):
        if not kwargs.get("dry_run"):
            assert (await _settle(await _doc("bank_transactions", tx_id), b))["status"] == "paid"
        return await real(firm_id, tx, invoice_id, **kwargs)           # με το ΠΑΛΙΟ αντίγραφο που διάβασε ο κύκλος

    monkeypatch.setattr(automation, "apply_bank_payment", human_got_there_first)
    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 0}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(b)       # δεν αποδεσμεύτηκε
    assert (await _doc("invoices", a))["status"] == "issued" and (await _doc("invoices", b))["status"] == "paid"
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 0


@pytest.mark.asyncio
async def test_the_autopilot_does_not_release_a_deposit_a_human_settled_while_it_was_refusing_it(monkeypatch):
    """Η άρνηση του Autopilot (το τιμολόγιο πληρώθηκε με κάρτα) δεν είναι αγώνας για την κατάθεση — αλλά η αποδέσμευση που
    ακολουθεί πρέπει να σεβαστεί ότι, ανάμεσα στην άρνηση και την αποδέσμευση, κάποιος συμφώνησε την κατάθεση αλλού."""
    a = await _invoice(NUMBER_A, status="paid", paid_via="stripe")
    b = await _invoice(NUMBER_B)
    tx_id = await _suggested_tx(a)
    real = automation.apply_bank_payment

    async def a_human_settles_after_the_refusal(firm_id, tx, invoice_id, **kwargs):
        result = await real(firm_id, tx, invoice_id, **kwargs)
        assert result["status"] == "refused" and result["reason"] == "already_paid"
        assert (await _settle(await _doc("bank_transactions", tx_id), b))["status"] == "paid"
        return result

    monkeypatch.setattr(automation, "apply_bank_payment", a_human_settles_after_the_refusal)
    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 0}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(b)
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 0


@pytest.mark.asyncio
async def test_the_autopilot_does_not_release_a_deposit_merely_because_another_action_was_holding_it(monkeypatch):
    """«Η κατάθεση είναι απασχολημένη» δεν λέει τίποτα για το τιμολόγιο: ακόμη κι αν η άλλη ενέργεια τελείωσε χωρίς να
    συμφωνήσει την κατάθεση, ο Autopilot δεν την αποδεσμεύει — το ξαναβλέπει τον επόμενο κύκλο."""
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a, claim="a-human", claim_at=_ago(seconds=5))
    real = automation.apply_bank_payment

    async def the_other_action_gives_up(firm_id, tx, invoice_id, **kwargs):
        result = await real(firm_id, tx, invoice_id, **kwargs)
        assert result["status"] == "refused" and result["reason"] == "tx_busy"
        await db.bank_transactions.update_one({"_id": tx_id}, {"$unset": {"claim": "", "claim_at": ""}})
        return result

    monkeypatch.setattr(automation, "apply_bank_payment", the_other_action_gives_up)
    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 0}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "auto_matched" and tx["matched_invoice_id"] == str(a)
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 0


@pytest.mark.asyncio
async def test_the_autopilot_leaves_a_busy_deposit_for_the_next_cycle():
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a, claim="a-human", claim_at=_ago(seconds=5))

    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 0}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "auto_matched" and tx["matched_invoice_id"] == str(a)         # ούτε εξοφλήθηκε ούτε αποδεσμεύτηκε
    assert (await _doc("invoices", a))["status"] == "issued"
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 0

    await db.bank_transactions.update_one({"_id": tx_id}, {"$set": {"claim_at": _ago(seconds=svc.DEPOSIT_CLAIM_TTL_SECONDS + 5)}})
    assert (await automation.job_reconcile(FIRM, {}))["confirmed"] == 1                         # όταν λήξει, συνεχίζει κανονικά


@pytest.mark.asyncio
async def test_the_autopilot_opens_no_review_card_for_a_deposit_settled_after_its_check(monkeypatch):
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a)
    await db.bank_transactions.update_one({"_id": tx_id}, {"$set": {"match_confidence": 60}})    # κάτω από το όριο → έλεγχος
    real = automation.apply_bank_payment

    async def human_settles_right_after_the_check(firm_id, tx, invoice_id, **kwargs):
        result = await real(firm_id, tx, invoice_id, **kwargs)
        assert (await _settle(await _doc("bank_transactions", tx_id), a))["status"] == "paid"
        return result

    monkeypatch.setattr(automation, "apply_bank_payment", human_settles_right_after_the_check)
    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 0}
    assert await db.notifications.count_documents({"firm_id": FIRM}) == 0
    assert "review_notified" not in await _doc("bank_transactions", tx_id)


@pytest.mark.asyncio
async def test_the_autopilot_still_unmatches_a_deposit_whose_invoice_was_paid_by_card():
    a = await _invoice(NUMBER_A, status="paid", paid_via="stripe")
    tx_id = await _suggested_tx(a)

    stats = await automation.job_reconcile(FIRM, {})

    assert stats == {"confirmed": 0, "sent_for_review": 1}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "unmatched" and tx["matched_invoice_id"] is None
    conflict = await db.notifications.find_one({"firm_id": FIRM, "type": "reconcile_conflict"})
    assert conflict is not None and conflict["entity_id"] == str(tx_id)
    _no_claim(tx)


@pytest.mark.asyncio
async def test_the_autopilot_confirms_a_clean_match_and_leaves_no_claim():
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a)

    assert (await automation.job_reconcile(FIRM, {})) == {"confirmed": 1, "sent_for_review": 0}

    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "auto_confirmed" and tx["matched_invoice_id"] == str(a)
    assert (await _doc("invoices", a))["paid_via"] == "bank_auto"
    _no_claim(tx)


# ============================================================ 4. οι κάρτες του inbox και η μαζική επιβεβαίωση ====

@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["ignore", "unmatch"])
async def test_an_inbox_decision_on_a_deposit_settled_in_the_meantime_closes_the_card_as_settled(monkeypatch, decision):
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a)
    card = await automation.notify(FIRM, "reconcile_review", "warning", "Ταιριάζει;", entity="bank_tx", entity_id=tx_id,
                                   requires_action=True)
    stale = await _doc("bank_transactions", tx_id)

    async def a_human_confirms_elsewhere():
        assert (await _settle(stale, a))["status"] == "paid"

    _race(monkeypatch, svc, "bank_transactions", "update_one", a_human_confirms_elsewhere)
    out = await automation.resolve_notification(FIRM, str(card["_id"]), "logistis@example.gr", decision)

    assert out == {"resolution": "already_settled"}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == "manual_matched" and tx["matched_invoice_id"] == str(a)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["ignore", "unmatch"])
async def test_an_inbox_decision_on_a_busy_deposit_is_refused_and_the_card_stays_open(decision):
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a, claim="the-autopilot", claim_at=_ago(seconds=5))
    card = await automation.notify(FIRM, "reconcile_review", "warning", "Ταιριάζει;", entity="bank_tx", entity_id=tx_id,
                                   requires_action=True)

    with pytest.raises(svc.SettlementRefused) as refused:
        await automation.resolve_notification(FIRM, str(card["_id"]), "logistis@example.gr", decision)

    assert refused.value.reason == "tx_busy"
    assert not (await _doc("notifications", card["_id"])).get("resolved_at")
    assert (await _doc("bank_transactions", tx_id))["match_status"] == "auto_matched"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,expected", [("ignore", "ignored"), ("unmatch", "unmatched")])
async def test_an_inbox_decision_on_a_free_deposit_is_applied(decision, expected):
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a)
    card = await automation.notify(FIRM, "reconcile_review", "warning", "Ταιριάζει;", entity="bank_tx", entity_id=tx_id,
                                   requires_action=True)

    out = await automation.resolve_notification(FIRM, str(card["_id"]), "logistis@example.gr", decision)

    assert out == {"resolution": expected}
    tx = await _doc("bank_transactions", tx_id)
    assert tx["match_status"] == expected and tx["matched_invoice_id"] is None


@pytest.mark.asyncio
async def test_the_bulk_confirmation_reports_a_busy_deposit_instead_of_confirming_it():
    a = await _invoice(NUMBER_A)
    tx_id = await _suggested_tx(a, claim="the-autopilot", claim_at=_ago(seconds=5))

    out = await routes_bank.confirm_auto_matches(USER)

    assert out["confirmed"] == 0 and out["skipped"] == 1
    assert out["skipped_items"][0]["reason"] == "tx_busy" and out["skipped_items"][0]["id"] == str(tx_id)
    assert (await _doc("invoices", a))["status"] == "issued"


# ================================================================== 5. «σημείωση ως εξοφλημένου» με το χέρι ====

@pytest.mark.asyncio
async def test_marking_paid_does_not_flip_an_invoice_cancelled_in_the_meantime(monkeypatch):
    a = await _invoice(NUMBER_A)

    async def cancelled_meanwhile():
        await db.invoices.update_one({"_id": a}, {"$set": {"status": "cancelled", "cancelled_at": "2026-06-01T00:00:00+00:00"}})

    _race(monkeypatch, svc, "invoices", "update_one", cancelled_meanwhile)
    with pytest.raises(HTTPException) as refused:
        await routes_invoices.mark_invoice_paid(str(a), USER)

    assert refused.value.status_code == 409 and "άλλαξε στο μεταξύ" in refused.value.detail
    stored = await _doc("invoices", a)
    assert stored["status"] == "cancelled" and not stored.get("paid_at") and not stored.get("paid_via")


@pytest.mark.asyncio
async def test_marking_paid_does_not_overwrite_a_card_payment_made_in_the_meantime(monkeypatch):
    a = await _invoice(NUMBER_A)

    async def the_card_payment_lands():
        await db.invoices.update_one({"_id": a}, {"$set": {
            "status": "paid", "paid_via": "stripe", "paid_ref": "cs_1", "paid_at": "2026-06-02T10:00:00+00:00"}})

    _race(monkeypatch, svc, "invoices", "update_one", the_card_payment_lands)
    with pytest.raises(HTTPException) as refused:
        await routes_invoices.mark_invoice_paid(str(a), USER)

    assert refused.value.status_code == 409
    stored = await _doc("invoices", a)
    assert stored["paid_via"] == "stripe" and stored["paid_ref"] == "cs_1" and stored["paid_at"] == "2026-06-02T10:00:00+00:00"


@pytest.mark.asyncio
async def test_marking_paid_records_the_manual_channel_so_a_deposit_can_still_be_linked_to_it():
    a = await _invoice(NUMBER_A)

    out = await routes_invoices.mark_invoice_paid(str(a), USER)

    assert out.status == "paid" and out.paid_via == "manual"
    stored = await _doc("invoices", a)
    assert stored["paid_via"] == "manual" and stored["paid_at"] and svc.is_linkable_paid(stored)


@pytest.mark.asyncio
async def test_marking_paid_twice_answers_400_and_keeps_the_first_time():
    a = await _invoice(NUMBER_A)
    await routes_invoices.mark_invoice_paid(str(a), USER)
    first = (await _doc("invoices", a))["paid_at"]

    with pytest.raises(HTTPException) as refused:
        await routes_invoices.mark_invoice_paid(str(a), USER)

    assert refused.value.status_code == 400
    assert (await _doc("invoices", a))["paid_at"] == first
