"""Γύρος 5 (ανασκόπηση 2) — είσοδοι που «περνούσαν» από τους ελέγχους: NaN/άπειρα, ΑΦΜ με μη-ASCII ψηφία, ελεύθερο JSON
στο PATCH ρυθμίσεων, και τα όρια της παρακράτησης.

Η `json` της Python δέχεται `NaN`, `Infinity` και `1e999`. Ένα `NaN` σε ποσό περνά από κάθε έλεγχο ορίων (κάθε σύγκριση
είναι ψευδής), από τον υπολογισμό ΦΠΑ (`Decimal('NaN')` δεν σκάει στο `quantize`) και αποθηκεύεται ως σύνολο· από εκεί
κάθε άθροισμα γίνεται `NaN` και η απάντηση JSON αποτυγχάνει με 500 για ΟΛΟ το γραφείο. Το «άπειρο» έσκαγε με 500 στον
υπολογισμό. Το «12345678²» έσκαγε με ValueError στο `int()` (η `str.isdigit()` δέχεται τα εκθετικά ψηφία) και τα
αραβικά-ινδικά ψηφία περνούσαν ως ΑΦΜ.
"""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tax_utils import athens_today

JSON = {"content-type": "application/json"}


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
                    json={"email": "valid@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "valid@example.gr"}))
    _run(db.users.update_one({"email": "valid@example.gr"}, {"$set": {"role": "admin"}}))
    company = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                                "email": "alpha@example.gr"}).json()["id"]
    person = client.post("/api/clients", json={"name": "Ιδιώτης Πελάτης", "client_type": "individual"}).json()["id"]
    return SimpleNamespace(company=company, person=person, firm_id=user["firm_id"],
                           user={"email": "valid@example.gr", "firm_id": user["firm_id"]})


def _today() -> str:
    return athens_today().isoformat()


def _body(client_id, net=100.0, **over):
    body = {"client_id": client_id, "date": _today(),
            "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": net, "vat_rate": 24}]}
    body.update(over)
    return body


def _count(collection: str) -> int:
    from db import db
    return _run(db[collection].count_documents({}))


def _doc(invoice_id):
    from db import db
    return _run(db.invoices.find_one({"_id": ObjectId(invoice_id)}))


# ======================================================= 1. Ο φραγμός JSON ====

@pytest.mark.parametrize("body", [
    b'{"a": NaN}', b'[Infinity]', b'{"a": {"b": [1, -Infinity]}}', b'{"a": 1e999}', b'{"a": -1e999}', b'NaN',
    b'{"a": 1E400}',
])
def test_the_guard_finds_non_finite_numbers_anywhere_in_a_json_body(body):
    from json_guard import has_non_finite_number
    assert has_non_finite_number(body) is True


@pytest.mark.parametrize("body", [
    b'{"a": "NaN"}', b'{"a": 1e308}', b'{"a": 1.5, "b": [1, 2, 3]}', b'null', b'{"text": "Infinity and 1e999 and NaN"}',
    b'{"a": ', b'', b'\xff\xfe\x00',
])
def test_the_guard_leaves_valid_numbers_strings_and_broken_json_alone(body):
    from json_guard import has_non_finite_number
    assert has_non_finite_number(body) is False         # το μη έγκυρο JSON το απαντά η FastAPI (422), όχι ο φραγμός


async def _drive(scope, chunks):
    """Περνά ένα αίτημα από τον φραγμό προς μια εφαρμογή που καταγράφει όσα λαμβάνει."""
    from json_guard import RejectNonFiniteJson
    seen, sent = [], []
    messages = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def inner(scope_, receive_, send_):
        seen.append((await receive_(), await receive_()))
        await send_({"type": "http.response.start", "status": 204, "headers": []})
        await send_({"type": "http.response.body", "body": b""})

    await RejectNonFiniteJson(inner)(scope, receive, send)
    return seen, sent


def _scope(content_type=b"application/json", method="POST", type_="http"):
    headers = [(b"content-type", content_type)] if content_type is not None else []
    return {"type": type_, "method": method, "path": "/x", "headers": headers}


def test_the_guard_replays_a_chunked_body_unchanged_to_the_application():
    seen, sent = _run(_drive(_scope(), [b'{"a":', b' 1,', b' "b": "\xce\xa9"}']))
    first, second = seen[0]
    assert first == {"type": "http.request", "body": b'{"a": 1, "b": "\xce\xa9"}', "more_body": False}
    assert second == {"type": "http.disconnect"}         # μετά το σώμα, το receive() προωθείται στο αρχικό
    assert sent[0]["status"] == 204


@pytest.mark.parametrize("content_type", [b"application/json", b"application/json; charset=utf-8",
                                           b"application/problem+json", b"APPLICATION/JSON", None])
def test_the_guard_rejects_non_finite_numbers_for_every_content_type_fastapi_reads_as_json(content_type):
    seen, sent = _run(_drive(_scope(content_type), [b'{"amount": NaN}']))
    assert seen == []                                     # η εφαρμογή δεν κλήθηκε
    assert sent[0]["status"] == 400


@pytest.mark.parametrize("scope", [
    _scope(b"multipart/form-data; boundary=x"), _scope(b"application/zip"), _scope(b"text/plain"),
    _scope(b"application/x-www-form-urlencoded"), _scope(method="GET"), _scope(method="OPTIONS"),
    _scope(type_="websocket"), _scope(type_="lifespan"),
])
def test_the_guard_does_not_touch_other_content_types_methods_or_protocols(scope):
    seen, sent = _run(_drive(scope, [b'NaN']))
    assert len(seen) == 1                                 # πέρασε αυτούσιο


def test_the_guard_ignores_a_client_that_disconnects_before_the_body_is_complete():
    from json_guard import RejectNonFiniteJson
    called = []

    async def inner(*_):
        called.append(True)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_):
        raise AssertionError("δεν πρέπει να σταλεί απάντηση")

    _run(RejectNonFiniteJson(inner)(_scope(), receive, send))
    assert called == []


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize("field", ["unit_price", "quantity"])
def test_a_non_finite_line_value_is_refused_and_nothing_is_stored(client, office, token, field):
    line = {"description": "Αμοιβή", "quantity": 1, "unit_price": 100, "vat_rate": 24, field: "@@"}
    raw = json.dumps({"client_id": office.company, "date": _today(), "line_items": [line]}, ensure_ascii=False)
    raw = raw.replace('"@@"', token)                      # το JSON της Python δεν παράγει NaN· το βάζουμε στο κείμενο
    r = client.post("/api/invoices", content=raw.encode(), headers=JSON)
    assert r.status_code == 400, r.text
    assert "NaN" in r.json()["detail"]
    assert _count("invoices") == 0


@pytest.mark.parametrize("token", ["NaN", "Infinity"])
def test_a_non_finite_withholding_amount_is_refused_by_post_and_put(client, office, token):
    draft = client.post("/api/invoices", json=_body(office.company)).json()
    raw = ('{"client_id": "%s", "date": "%s", "withholding_amount": %s, "line_items": '
           '[{"description": "Αμοιβή", "quantity": 1, "unit_price": 100, "vat_rate": 24}]}' % (office.company, _today(), token))
    assert client.post("/api/invoices", content=raw.encode(), headers=JSON).status_code == 400
    assert client.put(f"/api/invoices/{draft['id']}", content=raw.encode(), headers=JSON).status_code == 400
    assert _count("invoices") == 1
    assert "withholding_amount" not in _doc(draft["id"])


def test_a_non_finite_expense_amount_is_refused(client, office):
    for token in ("NaN", "Infinity"):
        raw = '{"vendor": "Προμηθευτής", "date": "%s", "amount": %s, "vat_amount": 0}' % (_today(), token)
        assert client.post("/api/expenses", content=raw.encode(), headers=JSON).status_code == 400
    assert _count("expenses") == 0


def test_a_non_finite_salary_is_refused(client, office):
    raw = '{"name": "Εργαζόμενος", "gross_salary": Infinity}'
    assert client.post("/api/payroll/employees", content=raw.encode(), headers=JSON).status_code == 400
    assert _count("payroll_employees") == 0


def test_the_words_nan_and_infinity_are_fine_inside_text(client, office):
    body = _body(office.company)
    body["line_items"][0]["description"] = "NaN Infinity 1e999"
    body["notes"] = "-Infinity"
    r = client.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["line_items"][0]["description"] == "NaN Infinity 1e999"


def test_broken_json_still_gets_the_frameworks_own_422(client, office):
    assert client.post("/api/invoices", content=b'{"client_id": ', headers=JSON).status_code == 422


def test_a_valid_request_with_greek_text_still_reaches_the_route_intact(client, office):
    r = client.post("/api/clients", json={"name": "Ωμέγα — Ελληνικά ✓", "client_type": "individual"})
    assert r.status_code == 200 and r.json()["name"] == "Ωμέγα — Ελληνικά ✓"


# ====================================== 2. Έλεγχος γραμμών και παρακράτησης ====

def _invoice(quantity=1.0, unit_price=100.0, withholding=None):
    """Χωρίς επικύρωση του μοντέλου (`model_construct`): από το WP15α το ίδιο το μοντέλο αρνείται NaN και άπειρο, οπότε
    εδώ ελέγχεται η δεύτερη γραμμή άμυνας — ό,τι φτάσει στο route χωρίς να έχει περάσει από την Pydantic."""
    from models import Invoice, InvoiceLineItem
    line = InvoiceLineItem.model_construct(description="x", quantity=quantity, unit_price=unit_price, vat_rate=24)
    return Invoice.model_construct(client_id="c", date=_today(), withholding_amount=withholding, line_items=[line])


@pytest.mark.parametrize("quantity,unit_price", [
    (float("nan"), 100.0), (1.0, float("nan")), (float("inf"), 100.0), (1.0, float("inf")), (1.0, float("-inf")),
])
def test_the_line_check_refuses_nan_and_infinity_even_without_json(quantity, unit_price):
    """Ο φραγμός JSON δεν καλύπτει ό,τι δεν έρχεται ως JSON (π.χ. τιμή που φτιάχνει κώδικας): η γραμμή ελέγχεται κι εδώ."""
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(quantity, unit_price))
    assert e.value.status_code == 400 and "έγκυροι αριθμοί" in e.value.detail


@pytest.mark.parametrize("quantity,unit_price", [(1.0, 1_000_000_001.0), (1_000_000_001.0, 1.0), (1.0, -1_000_000_001.0),
                                                 (100_000.0, 100_000.0)])
def test_an_absurdly_large_line_is_refused_with_a_clear_message(quantity, unit_price):
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(quantity, unit_price))
    assert e.value.status_code == 400 and "υπερβολικά μεγάλο" in e.value.detail


def test_a_line_exactly_at_the_limit_is_accepted():
    from routes_invoices import _validate_invoice_payload
    _validate_invoice_payload(_invoice(1.0, 1_000_000_000.0))
    _validate_invoice_payload(_invoice(1.0, -1_000_000_000.0))        # πιστωτικό


@pytest.mark.parametrize("withholding", [float("nan"), float("inf"), float("-inf")])
def test_the_payload_check_refuses_a_non_finite_withholding(withholding):
    from routes_invoices import _validate_invoice_payload
    with pytest.raises(HTTPException) as e:
        _validate_invoice_payload(_invoice(withholding=withholding))
    assert e.value.status_code == 400 and "έγκυρος αριθμός" in e.value.detail


@pytest.mark.parametrize("invoice_type", ["2.1", "11.2", "5.1"])
@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
def test_the_issue_time_check_refuses_a_non_finite_withholding_for_every_invoice_type(invoice_type, amount):
    """Ένα πρόχειρο που αποθηκεύτηκε πριν τον έλεγχο μπορεί να φέρει NaN· η έκδοση δεν το «παγώνει» ως παρακράτηση."""
    from invoice_services import InvoiceWorkflowError, check_explicit_withholding
    with pytest.raises(InvoiceWorkflowError) as e:
        check_explicit_withholding({"withholding_amount": amount, "subtotal": 100.0}, invoice_type)
    assert "έγκυρος αριθμός" in str(e.value)


def test_withholding_is_bounded_by_the_net_value_not_the_gross(client, office):
    """Καθαρή αξία 100 € + ΦΠΑ 24 € = 124 €. Η παρακράτηση 110 € ξεπερνά την καθαρή, ακόμη κι αν χωρά στο μεικτό."""
    over = client.post("/api/invoices", json=_body(office.company, net=100.0, withholding_amount=110.0))
    assert over.status_code == 400 and "καθαρή αξία" in over.json()["detail"]
    at_limit = client.post("/api/invoices", json=_body(office.company, net=100.0, withholding_amount=100.0))
    assert at_limit.status_code == 200
    assert client.post("/api/invoices", json=_body(office.company, net=100.0, withholding_amount=0.0)).status_code == 200
    assert client.post("/api/invoices", json=_body(office.company, net=100.0, withholding_amount=-0.01)).status_code == 400


def test_updating_a_credit_note_draft_never_stores_an_explicit_withholding(client, office):
    """Ακόμη και το «0» δεν γράφεται στο πρόχειρο πιστωτικό (το 0 μπαίνει στην έκδοση)."""
    inv = client.post("/api/invoices", json=_body(office.company, net=1000.0)).json()
    assert client.post(f"/api/invoices/{inv['id']}/issue").status_code == 200
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(inv["id"])},
                                {"$set": {"mydata_status": "synced", "mydata_mark": "400000000000123"}}))
    credit = client.post(f"/api/invoices/{inv['id']}/credit-note", json={}).json()
    body = _body(office.company, net=-1000.0, withholding_amount=0.0)
    r = client.put(f"/api/invoices/{credit['id']}", json=body)
    assert r.status_code == 200, r.text
    assert _doc(credit["id"]).get("withholding_amount") is None


# ====================================================== 3. Ρυθμίσεις ====

def _form(client):
    return client.get("/api/settings").json()


@pytest.mark.parametrize("rate", [0, 0.2, 0.5])
def test_the_rate_bounds_are_inclusive_on_put_and_patch(client, office, rate):
    assert client.patch("/api/settings", json={"withholding_rate": rate}).status_code == 200
    form = _form(client)
    form["withholding_rate"] = rate
    assert client.put("/api/settings", json=form).status_code == 200
    assert client.get("/api/settings").json()["withholding_rate"] == rate


@pytest.mark.parametrize("rate", [0.5001, 0.51, 1.0, 20, -0.01, -1])
def test_a_rate_outside_the_bounds_is_refused_on_put_and_patch(client, office, rate):
    before = _form(client)["withholding_rate"]
    assert client.patch("/api/settings", json={"withholding_rate": rate}).status_code == 400
    form = _form(client)
    form["withholding_rate"] = rate
    assert client.put("/api/settings", json=form).status_code == 400
    assert client.get("/api/settings").json()["withholding_rate"] == before


@pytest.mark.parametrize("min_net", [0, 0.0, 300, 1_000_000])
def test_the_minimum_net_accepts_zero_and_positive_values(client, office, min_net):
    assert client.patch("/api/settings", json={"withholding_min_net": min_net}).status_code == 200
    form = _form(client)
    form["withholding_min_net"] = min_net
    assert client.put("/api/settings", json=form).status_code == 200


@pytest.mark.parametrize("min_net", [-0.01, -1, -300])
def test_a_negative_minimum_net_is_refused_on_put_and_patch(client, office, min_net):
    assert client.patch("/api/settings", json={"withholding_min_net": min_net}).status_code == 400
    form = _form(client)
    form["withholding_min_net"] = min_net
    assert client.put("/api/settings", json=form).status_code == 400


@pytest.mark.parametrize("field,value", [
    ("withholding_rate", "abc"), ("withholding_rate", [0.2]), ("withholding_rate", True), ("withholding_rate", {"x": 1}),
    ("withholding_rate", "0,2"), ("withholding_rate", "nan"), ("withholding_rate", "inf"),
    ("withholding_min_net", "abc"), ("withholding_min_net", [300]), ("withholding_min_net", False),
    ("withholding_min_net", "nan"), ("withholding_min_net", "inf"), ("withholding_min_net", "-inf"),
    ("afm", 123456783), ("afm", ["123456783"]), ("afm", {"a": 1}),
    ("autopilot_enabled", "όχι"), ("reminder_after_days", "πολλές"), ("withholding_enabled", [1]),
])
def test_patch_settings_answers_400_for_hostile_value_types_and_never_500(client, office, field, value):
    before = _form(client)
    r = client.patch("/api/settings", json={field: value})
    assert r.status_code == 400, f"{field}={value!r}: {r.status_code} {r.text}"
    assert _form(client)[field] == before[field]


def test_patch_settings_accepts_a_numeric_string_the_model_can_read(client, office):
    assert client.patch("/api/settings", json={"withholding_rate": "0.2"}).status_code == 200
    assert client.get("/api/settings").json()["withholding_rate"] == 0.2


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_minimum_net_is_refused_and_the_setting_is_unchanged(client, office, token):
    before = _form(client)["withholding_min_net"]
    assert client.patch("/api/settings", content=('{"withholding_min_net": %s}' % token).encode(), headers=JSON).status_code == 400
    raw = re.sub(r'"withholding_min_net":\s*[-0-9.eE+]+', '"withholding_min_net": %s' % token,
                 json.dumps(_form(client), ensure_ascii=False))
    assert token in raw
    assert client.put("/api/settings", content=raw.encode(), headers=JSON).status_code == 400
    assert _form(client)["withholding_min_net"] == before


def test_the_validator_accepts_exactly_the_documented_values():
    from routes_settings import _validate_withholding
    _validate_withholding(None, None, None)
    _validate_withholding("2", 0, 0)
    _validate_withholding("10", 0.5, 12345.67)
    for rate in (float("nan"), float("inf"), 0.51, -0.0001, "x", {}):
        with pytest.raises(HTTPException):
            _validate_withholding(None, rate, None)
    for min_net in (float("nan"), float("inf"), -0.0001, "x", [1]):
        with pytest.raises(HTTPException):
            _validate_withholding(None, None, min_net)


def test_only_an_admin_may_change_the_settings(client):
    r = client.post("/api/auth/register",
                    json={"email": "lawyer@example.gr", "password": "Str0ngPass!", "name": "Δικηγόρος"})
    assert r.status_code == 200, r.text                                  # ρόλος «lawyer»: όχι διαχειριστής
    assert client.patch("/api/settings", json={"withholding_rate": 0.2}).status_code == 403
    form = client.get("/api/settings").json()
    assert client.put("/api/settings", json=form).status_code == 403


# ========================================================== 4. ΑΦΜ ====

VALID_AFM = "123456783"
ARABIC_INDIC = "١٢٣٤٥٦٧٨٣"          # τα ίδια ψηφία με το VALID_AFM
FULLWIDTH = "１２３４５６７８３"
SUPERSCRIPT = "12345678²"
DEVANAGARI = "१२३४५६७८३"


@pytest.mark.parametrize("afm", [ARABIC_INDIC, FULLWIDTH, SUPERSCRIPT, DEVANAGARI, "12345678٣", "12345678³"])
def test_an_afm_with_non_ascii_digits_is_invalid_and_never_raises(afm):
    from tax_utils import is_valid_afm
    assert is_valid_afm(afm) is False                    # πριν: ValueError για το «²», αποδοχή για τα αραβικά-ινδικά


def test_the_error_message_names_the_non_numeric_characters():
    from tax_utils import afm_error
    assert "μη αριθμητικούς χαρακτήρες" in afm_error(SUPERSCRIPT, "ΑΦΜ πελάτη")
    assert "μη αριθμητικούς χαρακτήρες" in afm_error(ARABIC_INDIC, "ΑΦΜ πελάτη")


def test_the_ascii_afm_is_still_valid():
    from tax_utils import is_valid_afm
    assert is_valid_afm(VALID_AFM) and is_valid_afm(" 123456783 ")
    assert not is_valid_afm("000000000") and not is_valid_afm("12345678") and not is_valid_afm("")


@pytest.mark.parametrize("afm", [SUPERSCRIPT, ARABIC_INDIC, FULLWIDTH])
def test_every_afm_entry_point_answers_400_and_stores_nothing(client, office, afm):
    assert client.post("/api/clients", json={"name": "Νέος", "afm": afm, "client_type": "company"}).status_code == 400
    assert client.post("/api/payroll/employees", json={"name": "Εργαζόμενος", "afm": afm, "gross_salary": 1000}).status_code == 400
    assert client.patch("/api/settings", json={"afm": afm}).status_code == 400
    form = _form(client)
    form["afm"] = afm
    assert client.put("/api/settings", json=form).status_code == 400
    assert _count("payroll_employees") == 0


# ================================== 5. Ποσό που παράγει το μοντέλο (Copilot) ====

@pytest.mark.parametrize("amount", ["NaN", "nan", "inf", "-inf", "abc", "1e12", "", None, 0, -5, float("nan"), float("inf")])
def test_the_copilot_never_creates_an_invoice_from_a_garbage_amount(office, amount):
    from routes_copilot import _execute_create_invoice
    created, reply = _run(_execute_create_invoice({"client_name": "ΑΛΦΑ", "amount": amount, "vat_rate": 24}, office.user))
    assert created is None and "ποσό" in reply
    assert _count("invoices") == 0


def test_the_copilot_falls_back_to_24_percent_for_an_unreadable_vat_rate(office):
    from routes_copilot import _execute_create_invoice
    created, _reply = _run(_execute_create_invoice({"client_name": "ΑΛΦΑ", "amount": 100, "vat_rate": "πολύ"}, office.user))
    assert created is not None and (created.subtotal, created.vat_amount, created.total) == (100.0, 24.0, 124.0)


def test_the_copilot_finds_a_client_beyond_the_first_200(office):
    from routes_copilot import _execute_create_invoice
    from db import db
    _run(db.clients.insert_many([{"firm_id": office.firm_id, "name": f"Πελάτης {i:04d}", "afm": "", "client_type": "individual"}
                                 for i in range(250)]))
    _run(db.clients.insert_one({"firm_id": office.firm_id, "name": "Ζήνων Τελευταίος", "afm": "", "client_type": "individual"}))
    created, reply = _run(_execute_create_invoice({"client_name": "Ζήνων", "amount": 100}, office.user))
    assert created is not None, reply
    assert created.client_name == "Ζήνων Τελευταίος"


# ============================================ 6. Υπόλοιπα (survivors O06, O19) ====

def test_the_outstanding_amount_never_goes_below_zero_when_more_was_applied_than_payable():
    from invoice_services import outstanding_amount
    invoice = {"total": 124.0, "subtotal": 100.0, "trust_applied": 500.0}
    assert _run(outstanding_amount("f", invoice, {}, {})) == (0.0, 0.0)


def test_the_outstanding_amount_is_payable_minus_applied():
    from invoice_services import outstanding_amount
    invoice = {"total": 124.0, "subtotal": 100.0, "trust_applied": 24.0}
    assert _run(outstanding_amount("f", invoice, {}, {})) == (100.0, 0.0)


def test_balances_never_use_a_client_of_another_firm(office):
    """Παλιά τιμολόγια χωρίς παγωμένο είδος βρίσκουν το είδος (ΤΠΥ/ΑΠΥ) από τον πελάτη. Ο πελάτης ΑΛΛΟΥ γραφείου
    με το ίδιο id δεν πρέπει ποτέ να χρησιμοποιείται: θα έβαζε παρακράτηση σε τιμολόγιο που δεν την έχει."""
    from invoice_services import invoice_balances
    from db import db
    foreign = _run(db.clients.insert_one({"firm_id": "another-firm", "name": "ΞΕΝΟΣ ΑΕ", "afm": "123456783",
                                          "client_type": "company"})).inserted_id
    own = _run(db.clients.insert_one({"firm_id": office.firm_id, "name": "ΔΙΚΟΣ ΜΑΣ ΑΕ", "afm": "987654324",
                                      "client_type": "company"})).inserted_id
    settings = {"withholding_enabled": True, "withholding_rate": 0.2, "withholding_min_net": 0}

    def legacy(client_id):
        return {"_id": ObjectId(), "client_id": str(client_id), "status": "issued", "total": 124.0, "subtotal": 100.0,
                "invoice_type": ""}

    stranger_invoice, own_invoice = legacy(foreign), legacy(own)
    balances = _run(invoice_balances(office.firm_id, [stranger_invoice, own_invoice], settings))
    assert balances[str(stranger_invoice["_id"])] == (124.0, 124.0)     # άγνωστος πελάτης → λιανική → καμία παρακράτηση
    assert balances[str(own_invoice["_id"])] == (104.0, 104.0)          # δικός μας πελάτης-εταιρεία → παρακράτηση 20 €
