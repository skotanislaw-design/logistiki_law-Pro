"""Έλεγχος γύρου 4 — Πακέτο 2: ακεραιότητα της σειράς παραστατικών και των ποσών τους.

Κάθε test αντιστοιχεί σε συμπεριφορά που ο έλεγχος βρήκε ανοιχτή:

* ο αριθμός δινόταν στο πρόχειρο, οπότε κάθε διαγραφή πρόχειρου άφηνε κενό στη σειρά·
* ΤΠΥ και ΑΠΥ μοιράζονταν μία σειρά (`ΤΠΥ-`), και κάθε είδος — ακόμη και η ΑΠΥ και το πιστωτικό —
  έφερε στο PDF τον τίτλο «Τιμολόγιο Παροχής Υπηρεσιών»·
* δεν υπήρχε έλεγχος ημερομηνίας (μελλοντική, ή προγενέστερη του προηγούμενου αριθμού)·
* το PDF και το email έγραφαν ως πληρωτέο το ακαθάριστο, ενώ η ΑΑΔΕ έπαιρνε το καθαρό μετά την παρακράτηση·
* το PDF που στελνόταν στον πελάτη κατά την έκδοση δεν είχε MARK·
* η διαγραφή πρόχειρου πιστωτικού/τιμολογίου άφηνε «κρεμασμένους» συνδέσμους που έμπλεκαν την επανάληψη.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
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
                    json={"email": "lifecycle@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    _run(db.users.update_one({"email": "lifecycle@example.gr"}, {"$set": {"role": "admin"}}))
    company = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                                 "email": "alpha@example.gr"}).json()["id"]
    person = client.post("/api/clients", json={"name": "Ιδιώτης Πελάτης", "client_type": "individual"}).json()["id"]
    return SimpleNamespace(company=company, person=person)


def _today() -> str:
    return athens_today().isoformat()


def _draft(client, client_id, when=None, net=1000.0, **over):
    body = {"client_id": client_id, "date": when or _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _issue(client, invoice_id):
    return client.post(f"/api/invoices/{invoice_id}/issue")


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


# --------------------------------------------------------------- αρίθμηση ----

def test_draft_has_no_number_and_deleting_it_leaves_no_gap(client, office):
    a = _draft(client, office.company)
    b = _draft(client, office.company)
    c = _draft(client, office.company)
    assert a["number"] == b["number"] == c["number"] == ""
    assert client.delete(f"/api/invoices/{b['id']}").status_code == 200

    year = athens_today().year
    first = _issue(client, a["id"]).json()
    third = _issue(client, c["id"]).json()
    assert first["number"] == f"ΤΠΥ-{year}-0001"
    assert third["number"] == f"ΤΠΥ-{year}-0002"            # καμία τρύπα από το διαγραμμένο πρόχειρο


def test_each_document_type_has_its_own_series(client, office):
    year = athens_today().year
    b2b = _issue(client, _draft(client, office.company)["id"]).json()
    retail = _issue(client, _draft(client, office.person)["id"]).json()
    assert b2b["number"] == f"ΤΠΥ-{year}-0001" and b2b["invoice_type"] == "2.1"
    assert retail["number"] == f"ΑΠΥ-{year}-0001" and retail["invoice_type"] == "11.2"

    second_retail = _issue(client, _draft(client, office.person)["id"]).json()
    assert second_retail["number"] == f"ΑΠΥ-{year}-0002"

    # πιστωτικό: δική του σειρά ΠΙΣ
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(b2b["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{b2b['id']}/credit-note", json={"reason": "Λάθος"}).json()
    assert credit["number"] == "" and credit["invoice_type"] == "5.1"
    credit = _issue(client, credit["id"]).json()
    assert credit["number"] == f"ΠΙΣ-{year}-0001"


def test_issue_freezes_the_document_type(client, office):
    """Αν ο πελάτης αλλάξει αργότερα, η ΑΠΥ που ήδη εκδόθηκε δεν γίνεται ξαφνικά ΤΠΥ."""
    retail = _issue(client, _draft(client, office.person)["id"]).json()
    assert retail["invoice_type"] == "11.2"
    client.put(f"/api/clients/{office.person}", json={"name": "Ιδιώτης Πελάτης", "client_type": "company",
                                                      "afm": "123456783"})
    import mydata_client
    assert mydata_client.resolve_invoice_type(_doc(retail["id"]), {"client_type": "company", "afm": "123456783"}) == "11.2"


def test_number_year_follows_the_invoice_date(client, office):
    last_year = athens_today().year - 1
    out = _issue(client, _draft(client, office.company, when=f"{last_year}-12-15")["id"]).json()
    assert out["number"] == f"ΤΠΥ-{last_year}-0001"


def test_double_issue_is_rejected_and_consumes_no_number(client, office):
    year = athens_today().year
    first = _draft(client, office.company)
    assert _issue(client, first["id"]).status_code == 200
    again = _issue(client, first["id"])
    assert again.status_code == 400
    assert _doc(first["id"])["number"] == f"ΤΠΥ-{year}-0001"
    nxt = _issue(client, _draft(client, office.company)["id"]).json()
    assert nxt["number"] == f"ΤΠΥ-{year}-0002"


def test_concurrent_claim_loses_cleanly_and_returns_its_number():
    """Δύο αιτήματα έκδοσης για το ίδιο πρόχειρο: το δεύτερο δεν αφήνει κενό."""
    import invoice_services as svc
    from db import db

    async def scenario():
        firm = "firm-race"
        inserted = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        stale = await db.invoices.find_one({"_id": inserted.inserted_id})   # το «άλλο» αίτημα κρατά παλιά εικόνα
        winner = await svc.issue_draft(firm, dict(stale), {})
        assert winner["number"].endswith("-0001")
        with pytest.raises(svc.InvoiceWorkflowError):
            await svc.issue_draft(firm, dict(stale), {})                     # ίδιο πρόχειρο, ήδη εκδοθέν
        # ο αριθμός που πήρε ο χαμένος επέστρεψε στη σειρά
        other = await db.invoices.insert_one({
            "firm_id": firm, "number": "", "status": "draft", "client_id": "x", "date": _today(),
            "subtotal": 100.0, "total": 124.0, "line_items": []})
        nxt = await svc.issue_draft(firm, await db.invoices.find_one({"_id": other.inserted_id}), {})
        assert nxt["number"].endswith("-0002")

    _run(scenario())


def test_legacy_draft_keeps_the_number_it_already_had(client, office):
    from db import db
    year = athens_today().year
    inserted = _run(db.invoices.insert_one({
        "firm_id": _doc(_draft(client, office.company)["id"])["firm_id"], "number": f"ΤΠΥ-{year}-0042",
        "status": "draft", "client_id": office.company, "client_name": "ΑΛΦΑ ΑΕ", "date": _today(),
        "subtotal": 100.0, "vat_amount": 24.0, "total": 124.0, "vat_breakdown": {"24": 24.0},
        "line_items": [{"description": "x", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}))
    out = _issue(client, str(inserted.inserted_id)).json()
    assert out["number"] == f"ΤΠΥ-{year}-0042"


# ---------------------------------------------------------- ημερομηνίες ----

def test_future_date_cannot_be_issued_and_burns_no_number(client, office):
    year = athens_today().year
    future = _draft(client, office.company, when=(athens_today() + timedelta(days=3)).isoformat())
    r = _issue(client, future["id"])
    assert r.status_code == 400 and "μελλοντική" in r.json()["detail"]
    assert _doc(future["id"])["status"] == "draft" and _doc(future["id"])["number"] == ""
    ok = _issue(client, _draft(client, office.company)["id"]).json()
    assert ok["number"] == f"ΤΠΥ-{year}-0001"


def test_date_before_the_previous_number_is_refused_until_corrected(client, office):
    today = athens_today()
    if today.day <= 5:           # χρειάζεται ημερομηνία «πριν» μέσα στον ίδιο μήνα/έτος για απλότητα
        pytest.skip("το σενάριο θέλει ημέρα > 5 του μήνα για ημερομηνία στο παρελθόν του ίδιου έτους")
    first = _issue(client, _draft(client, office.company)["id"]).json()
    older = _draft(client, office.company, when=(today - timedelta(days=5)).isoformat())
    r = _issue(client, older["id"])
    assert r.status_code == 400
    assert first["number"] in r.json()["detail"] and "χρονολογική" in r.json()["detail"]
    assert _doc(older["id"])["status"] == "draft"

    fixed = client.put(f"/api/invoices/{older['id']}", json={
        "client_id": office.company, "date": today.isoformat(),
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}]})
    assert fixed.status_code == 200, fixed.text
    assert _issue(client, older["id"]).status_code == 200


def test_a_future_dated_legacy_invoice_does_not_block_the_series_forever(client, office):
    from db import db
    year = athens_today().year
    firm = _doc(_draft(client, office.company)["id"])["firm_id"]
    _run(db.invoices.insert_one({
        "firm_id": firm, "number": f"ΤΠΥ-{year}-0005", "status": "issued", "client_id": office.company,
        "date": (athens_today() + timedelta(days=30)).isoformat(), "total": 1.0, "line_items": []}))
    out = _issue(client, _draft(client, office.company)["id"])
    assert out.status_code == 200, out.text
    assert out.json()["number"] == f"ΤΠΥ-{year}-0006"        # συνεχίζει πάνω από το υπάρχον μέγιστο


def test_athens_today_is_not_the_utc_date(monkeypatch):
    """Στις 00:30 ώρα Ελλάδας (22:30 UTC) το έτος και η ημέρα είναι ήδη τα νέα."""
    import tax_utils

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(tax_utils, "datetime", FakeDateTime)
    assert tax_utils.athens_today() == date(2027, 1, 1)


# --------------------------------------------- παρακράτηση, PDF και email ----

@pytest.fixture()
def pdf_texts(monkeypatch):
    import pdf_utils
    seen = []
    real = pdf_utils._p

    def spy(text, *a, **k):
        seen.append(text)
        return real(text, *a, **k)

    monkeypatch.setattr(pdf_utils, "_p", spy)
    return seen


def _enable_withholding(client):
    r = client.patch("/api/settings", json={"withholding_enabled": True, "withholding_min_net": 300,
                                            "withholding_rate": 0.20})
    assert r.status_code == 200, r.text


def test_withholding_is_frozen_at_issue_and_used_everywhere(client, office, pdf_texts):
    import invoice_services as svc
    _enable_withholding(client)
    out = _issue(client, _draft(client, office.company, net=1000.0)["id"]).json()
    assert out["withholding_amount"] == 200.0 and out["total"] == 1240.0

    # Αλλαγή ρυθμίσεων μετά την έκδοση δεν αγγίζει το εκδοθέν παραστατικό.
    client.patch("/api/settings", json={"withholding_enabled": False})
    doc = _doc(out["id"])
    assert _run(svc.payable_amount(doc["firm_id"], doc, {"withholding_enabled": False})) == (1040.0, 200.0)

    assert client.get(f"/api/invoices/{out['id']}/pdf").status_code == 200
    assert "Παρακράτηση φόρου" in pdf_texts
    assert "1.040,00 €" in pdf_texts                     # ΠΛΗΡΩΤΕΟ ΠΟΣΟ = σύνολο − παρακράτηση
    assert "1.240,00 €" in pdf_texts                     # και το σύνολο με ΦΠΑ φαίνεται


def test_no_withholding_row_when_none_applies(client, office, pdf_texts):
    _enable_withholding(client)
    retail = _issue(client, _draft(client, office.person, net=1000.0)["id"]).json()
    assert retail["withholding_amount"] == 0.0                  # η λιανική δεν έχει παρακράτηση
    small = _issue(client, _draft(client, office.company, net=100.0)["id"]).json()
    assert small["withholding_amount"] == 0.0                   # κάτω από το όριο των 300 €
    for inv in (retail, small):
        client.get(f"/api/invoices/{inv['id']}/pdf")
    assert "Παρακράτηση φόρου" not in pdf_texts


def test_email_and_stripe_ask_for_the_payable_amount_not_the_gross(client, office):
    from email_utils import invoice_email_text
    _enable_withholding(client)
    out = _issue(client, _draft(client, office.company, net=1000.0)["id"]).json()
    assert "1.040,00 €" in invoice_email_text(_doc(out["id"]), {})

    from routes_payments import _validate_invoice_and_get_amount
    _inv, amount = _run(_validate_invoice_and_get_amount(out["id"], _doc(out["id"])["firm_id"]))
    assert amount == 1040.0


def test_stripe_checkout_refuses_a_draft(client, office):
    from fastapi import HTTPException
    from routes_payments import _validate_invoice_and_get_amount
    draft = _draft(client, office.company)
    with pytest.raises(HTTPException) as exc:
        _run(_validate_invoice_and_get_amount(draft["id"], _doc(draft["id"])["firm_id"]))
    assert exc.value.status_code == 400


def test_default_withholding_category_is_not_royalties():
    from models import Settings
    assert Settings(firm_id="x").withholding_category == "3"


def test_pdf_title_follows_the_document_type(client, office, pdf_texts):
    from db import db
    b2b = _issue(client, _draft(client, office.company)["id"]).json()
    retail = _issue(client, _draft(client, office.person)["id"]).json()
    _run(db.invoices.update_one({"_id": ObjectId(b2b["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{b2b['id']}/credit-note", json={}).json()
    for inv in (b2b, retail, credit):
        assert client.get(f"/api/invoices/{inv['id']}/pdf").status_code == 200
    assert pdf_texts.count("ΤΙΜΟΛΟΓΙΟ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ") == 1
    assert pdf_texts.count("ΑΠΟΔΕΙΞΗ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ") == 1
    assert pdf_texts.count("ΠΙΣΤΩΤΙΚΟ ΤΙΜΟΛΟΓΙΟ") == 1
    assert "Συσχετιζόμενο παραστατικό (MARK): 400001111111111" in pdf_texts


def test_attachment_names_are_ascii_for_every_series():
    from invoice_services import safe_pdf_name
    assert safe_pdf_name({"number": "ΤΠΥ-2026-0001"}) == "TPY-2026-0001.pdf"
    assert safe_pdf_name({"number": "ΑΠΥ-2026-0001"}) == "APY-2026-0001.pdf"
    assert safe_pdf_name({"number": "ΠΙΣ-2026-0001"}) == "PIS-2026-0001.pdf"
    assert safe_pdf_name({}) == "invoice.pdf"


def test_the_pdf_emailed_at_issue_already_carries_the_mark(client, office, monkeypatch):
    """Το email έφευγε με αντίγραφο του παραστατικού από πριν τη διαβίβαση, άρα χωρίς MARK."""
    import invoice_services
    import pdf_utils
    monkeypatch.setenv("MYDATA_MOCK", "1")
    marks = []
    real = pdf_utils.build_invoice_pdf

    def spy(invoice, client_doc, settings):
        marks.append(invoice.get("mydata_mark"))
        return real(invoice, client_doc, settings)

    async def fake_send(*args, **kwargs):
        return None

    monkeypatch.setattr(pdf_utils, "build_invoice_pdf", spy)
    monkeypatch.setattr(invoice_services, "send_email_with_pdf", fake_send)
    r = client.patch("/api/settings", json={"smtp_user": "office@example.gr", "smtp_password": "x",
                                            "smtp_host": "smtp.example.gr"})
    assert r.status_code == 200, r.text

    out = _issue(client, _draft(client, office.company)["id"])
    assert out.status_code == 200, out.text
    assert out.json()["email_sent_to"] == "alpha@example.gr"
    assert len(marks) == 1 and marks[0] and marks[0].startswith("MOCK-")


# ------------------------------------------- διαγραφή πρόχειρου και σύνδεσμοι ----

def test_deleting_a_draft_credit_note_frees_the_original_for_a_new_one(client, office):
    from db import db
    original = _issue(client, _draft(client, office.company)["id"]).json()
    _run(db.invoices.update_one({"_id": ObjectId(original["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={"reason": "Λάθος"}).json()
    assert _doc(original["id"])["credit_note_id"] == credit["id"]
    assert client.post(f"/api/invoices/{original['id']}/credit-note", json={}).status_code == 400

    assert client.delete(f"/api/invoices/{credit['id']}").status_code == 200
    assert "credit_note_id" not in _doc(original["id"])
    again = client.post(f"/api/invoices/{original['id']}/credit-note", json={"reason": "Σωστό"})
    assert again.status_code == 200, again.text


def test_deleting_a_draft_invoice_releases_its_grammatio(client, office):
    from db import db
    draft = _draft(client, office.company)
    firm = _doc(draft["id"])["firm_id"]
    g = _run(db.grammatia.insert_one({"firm_id": firm, "number": "ΓΡΠ-1", "client_id": office.company,
                                      "invoice_id": draft["id"], "total": 10.0, "status": "issued"}))
    assert client.delete(f"/api/invoices/{draft['id']}").status_code == 200
    assert "invoice_id" not in _run(db.grammatia.find_one({"_id": g.inserted_id}))


# ------------------------------------------------- πεδία που ανήκουν στον server ----

def test_client_cannot_forge_credit_note_fields(client, office):
    body = {"client_id": office.company, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
    forged = client.post("/api/invoices", json={**body, "credit_note_for": "abc", "correlated_mark": "123",
                                                "credit_note_id": "zzz", "paid_via": "bank_auto",
                                                "email_sent_at": "2026-01-01", "reminder_count": 9})
    assert forged.status_code == 200, forged.text
    stored = _doc(forged.json()["id"])
    for field in ("credit_note_for", "correlated_mark", "credit_note_id", "paid_via", "email_sent_at"):
        assert stored.get(field) is None, field
    assert stored["reminder_count"] == 0

    # και στην τροποποίηση
    upd = client.put(f"/api/invoices/{stored['_id']}", json={**body, "correlated_mark": "999"})
    assert upd.status_code == 200
    assert _doc(str(stored["_id"])).get("correlated_mark") is None


def test_only_user_document_types_are_accepted(client, office):
    body = {"client_id": office.company, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
    assert client.post("/api/invoices", json={**body, "invoice_type": "5.1"}).status_code == 400
    assert client.post("/api/invoices", json={**body, "invoice_type": "9.9"}).status_code == 400
    assert client.post("/api/invoices", json={**body, "invoice_type": "2.1"}).status_code == 200
    assert client.post("/api/invoices", json={**body, "invoice_type": "11.2"}).status_code == 200


def test_explicit_withholding_must_stay_within_the_net_value(client, office):
    body = {"client_id": office.company, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
    assert client.post("/api/invoices", json={**body, "withholding_amount": -1}).status_code == 400
    assert client.post("/api/invoices", json={**body, "withholding_amount": 100.01}).status_code == 400
    assert client.post("/api/invoices", json={**body, "withholding_amount": 20.0}).status_code == 200


# ------------------------------------------------------------ πιστωτικά ----

def _synced(client, office):
    from db import db
    original = _issue(client, _draft(client, office.company, net=1000.0)["id"]).json()
    _run(db.invoices.update_one({"_id": ObjectId(original["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400001111111111"}}))
    return original


def test_credit_cannot_exceed_the_original_net_value(client, office):
    original = _synced(client, office)
    too_much = client.post(f"/api/invoices/{original['id']}/credit-note", json={"amount": 1000.01})
    assert too_much.status_code == 400 and "υπερβαίνει" in too_much.json()["detail"]
    exact = client.post(f"/api/invoices/{original['id']}/credit-note", json={"amount": 1000.0})
    assert exact.status_code == 200, exact.text


def test_credit_note_of_a_credit_note_is_refused(client, office):
    original = _synced(client, office)
    credit = client.post(f"/api/invoices/{original['id']}/credit-note", json={}).json()
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(credit["id"])},
                                {"$set": {"status": "issued", "mydata_status": "synced", "mydata_mark": "400002222222222"}}))
    r = client.post(f"/api/invoices/{credit['id']}/credit-note", json={})
    assert r.status_code == 400 and "δεν πιστώνεται" in r.json()["detail"]
