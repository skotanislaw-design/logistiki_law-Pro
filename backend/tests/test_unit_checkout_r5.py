"""Γύρος 5 — το checkout της πύλης (#43, #44).

#43: το `POST /api/payments/portal-checkout` είχε το τρίτο αντίγραφο της επικύρωσης token (λήξη με σύγκριση
συμβολοσειρών, χωρίς όριο ρυθμού, χωρίς `last_used_at`), δημιουργούσε συνεδρία Stripe σε κάθε κλήση χωρίς ανώτατο όριο, και
καταγραφόταν στο ίχνος ως ανώνυμο αίτημα του `LEGACY_FIRM_ID` — αόρατο στον διαχειριστή του γραφείου.

#44: το `origin_url` του αιτήματος γινόταν `success_url`/`cancel_url` της συνεδρίας χωρίς έλεγχο: όποιος είχε έγκυρο σύνδεσμο
μπορούσε να στήσει γνήσια σελίδα πληρωμής που μετά την εξόφληση οδηγεί σε δική του διεύθυνση.
"""
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tax_utils import athens_today


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _origins(monkeypatch):
    """Ένα γραφείο με γνωστή διεύθυνση εφαρμογής."""
    monkeypatch.setenv("PUBLIC_URL", "https://nomos.example.gr")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("PREVIEW_ENDPOINT", raising=False)


@pytest.fixture()
def stripe_calls(monkeypatch):
    import routes_payments
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=f"cs_test_{len(calls)}", url=f"https://checkout.stripe.test/{len(calls)}")

    monkeypatch.setattr(routes_payments, "_stripe_ready", lambda: True)
    monkeypatch.setattr(routes_payments.stripe.checkout.Session, "create", staticmethod(fake_create))
    return calls


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register", json={"email": "pay@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "pay@example.gr"}))
    _run(db.users.update_one({"email": "pay@example.gr"}, {"$set": {"role": "admin"}}))
    cid = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                            "email": "alpha@example.gr"}).json()["id"]
    other = client.post("/api/clients", json={"name": "ΒΗΤΑ ΑΕ", "afm": "094259216", "client_type": "company",
                                              "email": "beta@example.gr"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]

    def issued(client_id):
        body = {"client_id": client_id, "date": athens_today().isoformat(),
                "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 100.0, "vat_rate": 24}]}
        draft = client.post("/api/invoices", json=body)
        assert draft.status_code == 200, draft.text
        out = client.post(f"/api/invoices/{draft.json()['id']}/issue")
        assert out.status_code == 200, out.text
        return out.json()

    mine = issued(cid)
    theirs = issued(other)
    return SimpleNamespace(firm_id=user["firm_id"], client_id=cid, token=token, invoice_id=mine["id"],
                           invoice_number=mine["number"], other_invoice_id=theirs["id"])


def _checkout(client, office, origin="https://nomos.example.gr", invoice=None, token=None):
    return client.post("/api/payments/portal-checkout", json={
        "invoice_id": invoice or office.invoice_id, "origin_url": origin,
        "client_token": office.token if token is None else token})


def _audit_rows(**flt):
    from db import db
    return _run(db.audit_logs.find({"path": "/api/payments/portal-checkout", **flt}).to_list(100))


# ====================================================== #43 Μία επικύρωση token, όριο, ίχνος ====

def test_no_other_route_validates_portal_tokens_on_its_own():
    """Το PR που ενοποίησε την επικύρωση δήλωνε «ένα `_validate_token`, όχι δύο» — ήταν τρία, γιατί το test έψαχνε
    συνάρτηση με αυτό το όνομα και δεν έβλεπε επικύρωση γραμμένη μέσα σε handler."""
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = [p.name for p in sorted(root.glob("routes_*.py"))
                 if p.name != "routes_client_portal.py" and "client_portal_tokens.find_one" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"επικύρωση token έξω από το _validate_token: {offenders}"


def test_a_valid_checkout_creates_one_session_and_marks_the_link_as_used(client, office, stripe_calls):
    r = _checkout(client, office)
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.test/")
    assert len(stripe_calls) == 1
    assert stripe_calls[0]["line_items"][0]["price_data"]["unit_amount"] == 12400
    listed = client.get(f"/api/client-portal/tokens/{office.client_id}").json()[0]
    assert listed["last_used_at"] is not None, "η χρήση του συνδέσμου για πληρωμή πρέπει να φαίνεται στο γραφείο"


def test_an_unknown_token_cannot_create_a_session(client, office, stripe_calls):
    r = _checkout(client, office, token="x" * 43)
    assert r.status_code == 404
    assert stripe_calls == []


def test_a_missing_token_is_unauthorised(client, office, stripe_calls):
    r = client.post("/api/payments/portal-checkout", json={
        "invoice_id": office.invoice_id, "origin_url": "https://nomos.example.gr", "client_token": ""})
    assert r.status_code == 401
    assert stripe_calls == []


def test_expiry_is_judged_by_instant_not_by_string(client, office, stripe_calls):
    """Λήξη πριν από μία ώρα, γραμμένη με ζώνη +03:00: ως συμβολοσειρά είναι «μεγαλύτερη» από το τώρα σε UTC, άρα η
    παλιά σύγκριση δεχόταν ληγμένο token."""
    from db import db
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(timezone(timedelta(hours=3))).isoformat()
    assert expired > datetime.now(timezone.utc).isoformat(), "η προϋπόθεση του test: η συμβολοσειρά μοιάζει μελλοντική"
    _run(db.client_portal_tokens.update_one({"token": office.token}, {"$set": {"expires_at": expired}}))
    r = _checkout(client, office)
    assert r.status_code == 401
    assert stripe_calls == []


def test_checkout_sessions_are_limited_per_token(client, office, stripe_calls):
    import routes_payments
    limit = routes_payments.PORTAL_CHECKOUT_LIMIT
    for _ in range(limit):
        assert _checkout(client, office).status_code == 200
    r = _checkout(client, office)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert len(stripe_calls) == limit, "η κλήση πάνω από το όριο δεν δημιουργεί συνεδρία Stripe"


def test_the_limit_is_per_token_not_global(client, office, stripe_calls):
    import routes_payments
    other_token = client.post("/api/client-portal/invite", json={"client_id": office.client_id}).json()["token"]
    for _ in range(routes_payments.PORTAL_CHECKOUT_LIMIT):
        assert _checkout(client, office).status_code == 200
    assert _checkout(client, office).status_code == 429
    assert _checkout(client, office, token=other_token).status_code == 200


def test_another_clients_invoice_is_refused(client, office, stripe_calls):
    """404 και όχι 403: το τιμολόγιο άλλου πελάτη δεν διακρίνεται από ανύπαρκτο (βλ. test_unit_portal_r5)."""
    r = _checkout(client, office, invoice=office.other_invoice_id)
    assert r.status_code == 404
    assert stripe_calls == []


def test_checkout_is_recorded_under_the_firm_and_the_client(client, office, stripe_calls):
    """Στο ίχνος του γραφείου, όχι στο LEGACY_FIRM_ID· ως ενέργεια του πελάτη της πύλης, χωρίς το token."""
    from tenant import LEGACY_FIRM_ID
    assert _checkout(client, office).status_code == 200
    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["firm_id"] == office.firm_id != LEGACY_FIRM_ID
    assert row["actor_type"] == "portal_client"
    assert row["entity"] == "client_portal" and row["entity_id"] == office.client_id
    assert office.invoice_number in row["details"] and "124,00" in row["details"]
    assert office.token not in str(row), "το token είναι διαπιστευτήριο: δεν μπαίνει στο ίχνος"

    trail = client.get("/api/audit", params={"actor_type": "portal_client"}).json()["items"]
    assert any(i["path"] == "/api/payments/portal-checkout" for i in trail), "ο διαχειριστής του γραφείου το βλέπει"


def test_an_expired_link_attempt_is_attributed_to_the_firm_too(client, office, stripe_calls):
    from db import db
    _run(db.client_portal_tokens.update_one(
        {"token": office.token}, {"$set": {"expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}}))
    assert _checkout(client, office).status_code == 401
    rows = _audit_rows(status_code=401)
    assert len(rows) == 1 and rows[0]["firm_id"] == office.firm_id
    assert rows[0]["actor_type"] == "portal_client" and rows[0]["entity_id"] == office.client_id


def test_staff_checkout_leaves_a_descriptive_trail_row(client, office, stripe_calls):
    r = client.post("/api/payments/checkout", json={
        "invoice_id": office.invoice_id, "origin_url": "https://nomos.example.gr"})
    assert r.status_code == 200, r.text
    from db import db
    row = _run(db.audit_logs.find_one({"path": "/api/payments/checkout"}))
    assert row["actor_type"] == "user" and office.invoice_number in row["details"]


# ====================================================== #44 Διεύθυνση επιστροφής ====

BAD_ORIGINS = [
    "https://evil.example",
    "https://nomos.example.gr.evil.example",
    "https://evil.example/https://nomos.example.gr",
    "https://nomos.example.gr@evil.example",
    "https://nomos.example.gr:8443",                    # άλλη θύρα = άλλο origin
    "http://nomos.example.gr",                          # άλλο σχήμα
    "https://nomos.example.gr?next=https://evil.example",
    "https://nomos.example.gr#https://evil.example",
    "https://nomos.example.gr\\@evil.example",
    "https://nomos.example.gr/\n.evil.example",
    "//evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>1</script>",
    "ftp://nomos.example.gr",
    "nomos.example.gr",
    "",
]


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_portal_checkout_refuses_foreign_return_addresses(client, office, stripe_calls, origin):
    r = _checkout(client, office, origin=origin)
    assert r.status_code == 400, (origin, r.text)
    assert "διεύθυνση επιστροφής" in r.json()["detail"]
    assert stripe_calls == [], "καμία συνεδρία Stripe με άγνωστη διεύθυνση επιστροφής"


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_staff_checkout_refuses_foreign_return_addresses_too(client, office, stripe_calls, origin):
    r = client.post("/api/payments/checkout", json={"invoice_id": office.invoice_id, "origin_url": origin})
    assert r.status_code == 400, (origin, r.text)
    assert stripe_calls == []


@pytest.mark.parametrize("given,normalised", [
    ("https://nomos.example.gr", "https://nomos.example.gr"),
    ("https://nomos.example.gr/", "https://nomos.example.gr"),
    ("https://NOMOS.Example.GR", "https://nomos.example.gr"),
    ("https://nomos.example.gr:443", "https://nomos.example.gr"),
    ("  https://nomos.example.gr  ", "https://nomos.example.gr"),
])
def test_the_offices_own_address_is_accepted_and_normalised(client, office, stripe_calls, given, normalised):
    r = _checkout(client, office, origin=given)
    assert r.status_code == 200, r.text
    call = stripe_calls[-1]
    page = f"{normalised}/portal/{office.token}"          # η πύλη επιστρέφει στη δική της σελίδα (P3)
    assert call["success_url"] == page + "?paid=1&session_id={CHECKOUT_SESSION_ID}"
    assert call["cancel_url"] == page + "?cancelled=1"
    staff = client.post("/api/payments/checkout", json={"invoice_id": office.invoice_id, "origin_url": given})
    assert staff.status_code == 200, staff.text
    call = stripe_calls[-1]
    assert call["success_url"] == f"{normalised}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
    assert call["cancel_url"] == f"{normalised}/payment/cancel?invoice={office.invoice_id}"


def test_the_portal_may_return_to_its_own_page_only(client, office, stripe_calls):
    """Το frontend της πύλης στέλνει `<origin>/portal/<token>` ώστε ο πελάτης να επιστρέφει στη σελίδα του."""
    own = f"https://nomos.example.gr/portal/{office.token}"
    r = _checkout(client, office, origin=own)
    assert r.status_code == 200, r.text
    assert stripe_calls[-1]["success_url"] == own + "?paid=1&session_id={CHECKOUT_SESSION_ID}"

    other_token = client.post("/api/client-portal/invite", json={"client_id": office.client_id}).json()["token"]
    for bad in (f"https://nomos.example.gr/portal/{other_token}", "https://nomos.example.gr/portal",
                "https://nomos.example.gr/anywhere", f"https://nomos.example.gr/portal/{office.token}/x"):
        assert _checkout(client, office, origin=bad).status_code == 400, bad
    assert len(stripe_calls) == 1


def test_staff_checkout_takes_the_origin_only(client, office, stripe_calls):
    ok = client.post("/api/payments/checkout", json={"invoice_id": office.invoice_id, "origin_url": "https://nomos.example.gr"})
    assert ok.status_code == 200, ok.text
    bad = client.post("/api/payments/checkout", json={
        "invoice_id": office.invoice_id, "origin_url": f"https://nomos.example.gr/portal/{office.token}"})
    assert bad.status_code == 400


def test_the_offices_public_url_setting_adds_an_allowed_address(client, office, stripe_calls):
    assert _checkout(client, office, origin="https://portal.skotanis.example").status_code == 400
    assert client.patch("/api/settings", json={"public_url": "https://portal.skotanis.example"}).status_code == 200
    r = _checkout(client, office, origin="https://portal.skotanis.example")
    assert r.status_code == 200, r.text
    assert stripe_calls[-1]["success_url"].startswith(f"https://portal.skotanis.example/portal/{office.token}?paid=1")
    assert _checkout(client, office, origin="https://evil.example").status_code == 400


def test_cors_origins_setting_also_counts_and_defaults_apply_when_nothing_is_set(monkeypatch):
    from http_utils import allowed_origins
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example/")
    assert allowed_origins() == ["https://a.example", "https://b.example"]
    monkeypatch.delenv("CORS_ORIGINS")
    monkeypatch.delenv("PUBLIC_URL")
    assert allowed_origins() == ["http://localhost:3000", "http://localhost:8080"]


def test_cors_and_return_addresses_share_one_allowlist(monkeypatch):
    """Δύο αντίγραφα της λίστας θα αποκλίνουν· η server._cors_origins() πρέπει να τη διαβάζει από το ίδιο σημείο."""
    import server
    from http_utils import allowed_origins
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")
    assert server._cors_origins() == allowed_origins()


@pytest.mark.parametrize("url,paths,expected", [
    ("https://nomos.example.gr", ("",), "https://nomos.example.gr"),
    ("https://nomos.example.gr/portal/abc", ("", "/portal/abc"), "https://nomos.example.gr/portal/abc"),
    ("https://nomos.example.gr/portal/abc/", ("", "/portal/abc"), "https://nomos.example.gr/portal/abc"),
    ("https://nomos.example.gr/portal/abc", ("",), None),
    ("https://nomos.example.gr:8443", ("",), None),
    ("https://user:pw@nomos.example.gr", ("",), None),
    ("https://nomos.example.gr/%2e%2e/", ("",), None),
    ("https://nomos.example.gr:notaport", ("",), None),
    (None, ("",), None),
    (42, ("",), None),
])
def test_checked_return_url_table(url, paths, expected):
    from http_utils import checked_return_url
    assert checked_return_url(url, ["https://nomos.example.gr"], allowed_paths=paths) == expected
