"""Γύρος 5 (ανασκόπηση 4) — η πύλη πελάτη: ομοιόμορφο 404, επιστροφή από την πληρωμή, πρόσκληση, όρια, συνομιλία.

* **P2** — το checkout της πύλης έλεγχε την ιδιοκτησία του τιμολογίου *μετά* την επικύρωσή του: για τιμολόγιο ΑΛΛΟΥ πελάτη
  του ίδιου γραφείου η απάντηση ήταν «ήδη εξοφλημένο» / «πρόχειρο» / «υπάρχει πιστωτικό» / 403 ανάλογα με την κατάστασή του.
  Όποιος είχε έγκυρο σύνδεσμο μπορούσε να δοκιμάζει αναγνωριστικά (οι ObjectId της ίδιας διεργασίας είναι διαδοχικοί) και να
  μαθαίνει ποια τιμολόγια υπάρχουν και σε ποια κατάσταση είναι. Τώρα: ένα τιμολόγιο που δεν ανήκει στον πελάτη του συνδέσμου
  είναι ακριβώς το ίδιο με ένα που δεν υπάρχει — ίδιος κωδικός, ίδιο σώμα.
* **P3** — μετά την πληρωμή ο πελάτης επέστρεφε στο `<origin>/portal/<token>/payment/success`, διαδρομή που δεν υπάρχει στο
  SPA (και, πίσω από τον παλιό nginx, κατέληγε σε JSON). Τώρα επιστρέφει στη σελίδα της πύλης του, που επαληθεύει την πληρωμή.
* **P4** — το `origin_url` της πρόσκλησης μπαινόταν αυτούσιο στον σύνδεσμο του email, και ονόματα/σύνδεσμοι στο HTML χωρίς escape.
* **T3** — τα όρια ρυθμού της πύλης (ανά token, ανά IP) δεν είχαν καμία δοκιμή.
* Η συνομιλία πύλης επέστρεφε τα *παλαιότερα* 500 μηνύματα· τα νεότερα δεν φαίνονταν ποτέ (και σημαίνονταν ως διαβασμένα).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


@pytest.fixture(autouse=True)
def _origins(monkeypatch):
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
    """Ένα γραφείο, δύο πελάτες (ο ΑΛΦΑ με σύνδεσμο πύλης), ένα εκδοθέν τιμολόγιο ο καθένας."""
    r = client.post("/api/auth/register", json={"email": "portal@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "portal@example.gr"}))
    _run(db.users.update_one({"email": "portal@example.gr"}, {"$set": {"role": "admin"}}))
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

    mine, theirs = issued(cid), issued(other)
    return SimpleNamespace(firm_id=user["firm_id"], client_id=cid, other_client_id=other, token=token,
                           invoice_id=mine["id"], other_invoice_id=theirs["id"])


def _checkout(client, office, invoice, origin="https://nomos.example.gr", token=None):
    return client.post("/api/payments/portal-checkout", json={
        "invoice_id": invoice, "origin_url": origin, "client_token": office.token if token is None else token})


def _pdf(client, office, invoice):
    return client.get(f"/api/portal/{office.token}/invoice/{invoice}/pdf")


def _set_invoice(invoice_id, **fields):
    from db import db
    _run(db.invoices.update_one({"_id": ObjectId(invoice_id)}, {"$set": fields}))


# ============================================================ P2 — ομοιόμορφο 404 ====

FOREIGN_STATES = [
    pytest.param({}, id="εκδοθέν"),
    pytest.param({"status": "paid"}, id="εξοφλημένο"),
    pytest.param({"status": "draft"}, id="πρόχειρο"),
    pytest.param({"status": "cancelled"}, id="ακυρωμένο"),
    pytest.param({"credit_note_id": "ΠΙΣ-1"}, id="με πιστωτικό"),
]


def test_a_missing_invoice_is_a_plain_404(client, office, stripe_calls):
    r = _checkout(client, office, str(ObjectId()))
    assert r.status_code == 404
    assert stripe_calls == []


@pytest.mark.parametrize("state", FOREIGN_STATES)
def test_another_clients_invoice_looks_exactly_like_a_missing_one(client, office, stripe_calls, state):
    """Όποια κι αν είναι η κατάσταση του ξένου τιμολογίου, η απάντηση δεν μαρτυρά ούτε ότι υπάρχει ούτε ποια είναι."""
    _set_invoice(office.other_invoice_id, **state)
    missing = _checkout(client, office, str(ObjectId()))
    foreign = _checkout(client, office, office.other_invoice_id)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, missing.json())
    assert stripe_calls == []


def test_another_firms_invoice_looks_the_same_too(client, office, stripe_calls):
    from db import db
    mine = _run(db.invoices.find_one({"_id": ObjectId(office.invoice_id)}))
    mine.pop("_id")
    mine["firm_id"] = "firm-" + "0" * 32
    foreign_id = str(_run(db.invoices.insert_one(mine)).inserted_id)
    missing = _checkout(client, office, str(ObjectId()))
    foreign = _checkout(client, office, foreign_id)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
    assert stripe_calls == []


def test_the_clients_own_invoices_keep_their_specific_messages(client, office, stripe_calls):
    """Για τα δικά του τιμολόγια ο πελάτης πρέπει να μαθαίνει γιατί δεν πληρώνεται."""
    _set_invoice(office.invoice_id, status="paid")
    assert "ήδη εξοφλημένο" in _checkout(client, office, office.invoice_id).json()["detail"]
    _set_invoice(office.invoice_id, status="draft")
    assert "εκδοθέντα" in _checkout(client, office, office.invoice_id).json()["detail"]
    _set_invoice(office.invoice_id, status="issued", credit_note_id="ΠΙΣ-1")
    assert "πιστωτικό" in _checkout(client, office, office.invoice_id).json()["detail"]
    assert stripe_calls == []


def test_a_malformed_invoice_id_is_still_a_400_that_reveals_nothing(client, office, stripe_calls):
    r = _checkout(client, office, "not-an-object-id")
    assert r.status_code == 400
    assert stripe_calls == []


def test_staff_checkout_is_not_restricted_to_one_client(client, office, stripe_calls):
    """Το προσωπικό του γραφείου μπορεί να ξεκινήσει πληρωμή για οποιονδήποτε πελάτη του γραφείου."""
    for invoice in (office.invoice_id, office.other_invoice_id):
        r = client.post("/api/payments/checkout", json={"invoice_id": invoice, "origin_url": "https://nomos.example.gr"})
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("state", FOREIGN_STATES)
def test_the_pdf_of_another_clients_invoice_is_a_plain_404(client, office, state):
    _set_invoice(office.other_invoice_id, **state)
    missing = _pdf(client, office, str(ObjectId()))
    foreign = _pdf(client, office, office.other_invoice_id)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, missing.json())


def test_the_clients_own_pdf_is_served(client, office):
    r = _pdf(client, office, office.invoice_id)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")


# ====================================================== P3 — επιστροφή από την πληρωμή ====

def test_the_portal_returns_to_its_own_page_after_payment(client, office, stripe_calls):
    """`<origin>/portal/<token>/payment/success` δεν είναι διαδρομή του SPA· ο πελάτης επιστρέφει στη σελίδα της πύλης
    του με `?paid=1&session_id=…`, όπου η σελίδα επαληθεύει την πληρωμή, και με `?cancelled=1` αν την ακύρωσε."""
    for origin in ("https://nomos.example.gr", f"https://nomos.example.gr/portal/{office.token}"):
        assert _checkout(client, office, office.invoice_id, origin=origin).status_code == 200
        call = stripe_calls[-1]
        page = f"https://nomos.example.gr/portal/{office.token}"
        assert call["success_url"] == page + "?paid=1&session_id={CHECKOUT_SESSION_ID}"
        assert call["cancel_url"] == page + "?cancelled=1"


def test_staff_checkout_still_returns_to_the_payment_result_page(client, office, stripe_calls):
    r = client.post("/api/payments/checkout", json={"invoice_id": office.invoice_id, "origin_url": "https://nomos.example.gr/"})
    assert r.status_code == 200, r.text
    call = stripe_calls[-1]
    assert call["success_url"] == "https://nomos.example.gr/payment/success?session_id={CHECKOUT_SESSION_ID}"
    assert call["cancel_url"] == f"https://nomos.example.gr/payment/cancel?invoice={office.invoice_id}"


def test_every_return_path_the_backend_builds_exists_in_the_spa():
    """Συμβόλαιο backend ↔ frontend: οι διαδρομές επιστροφής πρέπει να είναι δηλωμένες στο App.jsx, και η σελίδα της
    πύλης να διαβάζει τις παραμέτρους που στέλνει το backend."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
    app = (src / "App.jsx").read_text(encoding="utf-8")
    for route in ('path="/portal/:token"', 'path="/payment/success"', 'path="/payment/cancel"'):
        assert route in app, route
    portal = (src / "pages" / "ClientPortal.jsx").read_text(encoding="utf-8")
    for param in ('"paid"', '"session_id"', '"cancelled"'):
        assert param in portal, f"η πύλη δεν διαβάζει την παράμετρο {param}"


# ============================================================ P4 — η πρόσκληση ====

@pytest.fixture()
def sent_emails(monkeypatch):
    import email_utils
    sent = []

    async def fake_send(settings, to_email, subject, body_text, body_html, *a, **kw):
        sent.append(SimpleNamespace(to=to_email, subject=subject, text=body_text, html=body_html))

    monkeypatch.setattr(email_utils, "smtp_ready", lambda settings: True)
    monkeypatch.setattr(email_utils, "send_email_with_pdf", fake_send)
    return sent


def _tokens():
    from db import db
    return _run(db.client_portal_tokens.count_documents({}))


def test_the_invite_link_is_built_from_a_checked_address(client, office):
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": "https://nomos.example.gr/"})
    assert r.status_code == 200, r.text
    assert r.json()["portal_link"] == f"https://nomos.example.gr/portal/{r.json()['token']}"


@pytest.mark.parametrize("origin", [
    "https://evil.example", "https://nomos.example.gr.evil.example", "https://nomos.example.gr@evil.example",
    "http://nomos.example.gr", "https://nomos.example.gr:8443", "https://nomos.example.gr/portal/x",
    'https://nomos.example.gr"><script>alert(1)</script>', "javascript:alert(1)", "//evil.example",
    "https://nomos.example.gr?x=1", "https://nomos.example.gr#x",
])
def test_the_invite_refuses_a_foreign_or_malformed_address_and_creates_nothing(client, office, sent_emails, origin):
    """Η διεύθυνση καταλήγει σε email που φτάνει στον πελάτη από το γραφείο: ένας σύνδεσμος προς ξένο ιστότοπο θα ήταν
    ψεύτικο «ασφαλές» μήνυμα του γραφείου. Και δεν δημιουργείται token για αίτημα που απορρίπτεται."""
    before = _tokens()
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": origin})
    assert r.status_code == 400, (origin, r.text)
    assert "διεύθυνση" in r.json()["detail"]
    assert _tokens() == before
    assert sent_emails == []


def test_the_offices_own_public_url_is_accepted_but_not_another_offices(client, office):
    from db import db
    ok = "https://portal.skotanis.example"
    assert client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": ok}).status_code == 400
    assert client.patch("/api/settings", json={"public_url": ok}).status_code == 200
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": ok})
    assert r.status_code == 200 and r.json()["portal_link"].startswith(ok + "/portal/")
    # η ρύθμιση ενός ΑΛΛΟΥ γραφείου δεν δίνει δικαίωμα σε αυτό
    _run(db.settings.insert_one({"firm_id": "firm-" + "1" * 32, "public_url": "https://other-firm.example"}))
    assert client.post("/api/client-portal/invite", json={
        "client_id": office.client_id, "origin_url": "https://other-firm.example"}).status_code == 400


def test_without_an_address_from_the_browser_the_offices_configured_address_is_used(client, office):
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id})
    assert r.json()["portal_link"] == f"https://nomos.example.gr/portal/{r.json()['token']}", "PUBLIC_URL"
    assert client.patch("/api/settings", json={"public_url": "https://portal.skotanis.example/"}).status_code == 200
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": ""})
    assert r.json()["portal_link"] == f"https://portal.skotanis.example/portal/{r.json()['token']}", "οι Ρυθμίσεις προηγούνται"


def test_with_nothing_configured_the_link_stays_relative(client, office, monkeypatch):
    monkeypatch.delenv("PUBLIC_URL")
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id})
    assert r.status_code == 200 and r.json()["portal_link"] == f"/portal/{r.json()['token']}"


def test_a_junk_public_url_setting_is_ignored_rather_than_put_in_the_email(client, office):
    from db import db
    _run(db.settings.update_one({"firm_id": office.firm_id}, {"$set": {"public_url": 'javascript:alert(1)"'}}, upsert=True))
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id})
    assert r.json()["portal_link"] == f"https://nomos.example.gr/portal/{r.json()['token']}"


def test_the_invite_email_escapes_what_it_interpolates(client, office, sent_emails):
    from db import db
    nasty = 'ΑΛΦΑ <img src=x onerror=alert(1)> & "Σια"'
    _run(db.clients.update_one({"_id": ObjectId(office.client_id)}, {"$set": {"name": nasty}}))
    r = client.post("/api/client-portal/invite", json={"client_id": office.client_id, "origin_url": "https://nomos.example.gr"})
    assert r.status_code == 200 and r.json()["email_sent"] is True
    mail = sent_emails[-1]
    assert "<img" not in mail.html and "&lt;img src=x onerror=alert(1)&gt; &amp; &quot;Σια&quot;" in mail.html
    assert f'href="https://nomos.example.gr/portal/{r.json()["token"]}"' in mail.html
    assert nasty in mail.text, "το απλό κείμενο μένει ως έχει"


# ------------------------------------------- ο σύνδεσμος πληρωμής στην υπενθύμιση: πού επιστρέφει ο πελάτης ---

@pytest.fixture()
def link_for(office, stripe_calls, monkeypatch):
    """`link_for(settings)` → (σύνδεσμος, κλήσεις Stripe) για το τιμολόγιο του γραφείου."""
    import invoice_services as svc
    from db import db
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_realistic_key")

    def build(settings=None):
        invoice = _run(db.invoices.find_one({"_id": ObjectId(office.invoice_id)}))
        return _run(svc.build_payment_link(invoice, settings or {}))
    return build


def test_the_payment_link_returns_to_the_configured_address(link_for, stripe_calls):
    assert link_for() == "https://checkout.stripe.test/1"
    assert stripe_calls[-1]["success_url"] == "https://nomos.example.gr/payment/success?session_id={CHECKOUT_SESSION_ID}"
    assert stripe_calls[-1]["cancel_url"] == "https://nomos.example.gr/payment/cancel"


def test_the_offices_public_url_comes_first_and_is_normalised(link_for, stripe_calls):
    link_for({"public_url": "https://Portal.Skotanis.example/"})
    assert stripe_calls[-1]["success_url"].startswith("https://portal.skotanis.example/payment/success?")


def test_a_junk_public_url_falls_back_to_the_installations_address(link_for, stripe_calls):
    for junk in ("javascript:alert(1)", "https://nomos.example.gr/app", "not a url", "ftp://x.example", "  "):
        assert link_for({"public_url": junk}) != ""
        assert stripe_calls[-1]["success_url"].startswith("https://nomos.example.gr/payment/success?"), junk


def test_without_any_valid_address_no_link_is_created_instead_of_sending_customers_to_example_com(
        link_for, stripe_calls, monkeypatch, caplog):
    """Παλιότερα το τελευταίο ρεζέρβα ήταν «https://example.com»: ο πελάτης πλήρωνε και μετά οδηγούνταν σε ξένο ιστότοπο."""
    import logging
    for name in ("PUBLIC_URL", "PREVIEW_ENDPOINT", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level(logging.WARNING, logger="invoice_services"):
        assert link_for({"public_url": "garbage"}) == ""
    assert stripe_calls == [], "ούτε συνεδρία Stripe"
    assert any("διεύθυνση εφαρμογής" in r.getMessage() for r in caplog.records)
    assert link_for({"public_url": "https://portal.skotanis.example"}) != ""


# ------------------------------------------------ τα υπόλοιπα HTML email του γραφείου ---

NASTY = '<script>alert(1)</script>"&'
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;&quot;&amp;"


def test_the_invoice_email_escapes_names_numbers_and_bank_details():
    from email_utils import invoice_email_html
    html = invoice_email_html(
        {"client_name": NASTY, "number": NASTY, "payment_method": NASTY, "total": 124.0},
        {"company_name": NASTY, "iban": NASTY, "bank_name": NASTY})
    assert "<script>" not in html and html.count(ESCAPED) >= 5


def test_the_reminder_email_escapes_names_numbers_bank_details_and_the_payment_link():
    from email_utils import reminder_email_html
    html = reminder_email_html(
        {"client_name": NASTY, "number": NASTY, "payment_method": NASTY, "total": 124.0},
        {"company_name": NASTY, "iban": NASTY, "bank_name": NASTY}, 5, 0, payment_link='https://x.example/"><script>')
    assert "<script>" not in html and html.count(ESCAPED) >= 5
    assert 'href="https://x.example/&quot;&gt;&lt;script&gt;"' in html


# =================================================================== T3 — όρια ρυθμού ====

@pytest.fixture()
def frozen_clock(monkeypatch):
    """Σταθερό παράθυρο ρυθμού: τα όρια είναι ανά λεπτό και ένα test που διασχίζει την αλλαγή λεπτού θα ήταν τυχαίο.
    (Επιστρέφει απλά `datetime`, όχι υποκλάση: η mongomock δεν δέχεται υποκλάσεις στις εγγραφές.)"""
    import rate_limit
    real = rate_limit.datetime
    # Στο μέλλον (+1 ώρα) και όχι σε σταθερή ημερομηνία: η mongomock εφαρμόζει το TTL index του `rate_limits`, και ένας
    # μετρητής με `expires_at` στο παρελθόν σβήνεται αμέσως — τότε κανένα όριο δεν θα συσσωρευόταν ποτέ.
    state = {"now": (real.now(timezone.utc) + timedelta(hours=1)).replace(second=5, microsecond=0)}

    class Clock:
        now = staticmethod(lambda tz=None: state["now"])
        fromtimestamp = staticmethod(real.fromtimestamp)

    monkeypatch.setattr(rate_limit, "datetime", Clock)
    return state


def _chat(client, token):
    return client.get(f"/api/portal/{token}/chat")


def test_the_portal_is_limited_per_token(client, office, frozen_clock):
    from routes_client_portal import PORTAL_TOKEN_LIMIT
    for _ in range(PORTAL_TOKEN_LIMIT):
        assert _chat(client, office.token).status_code == 200
    r = _chat(client, office.token)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "55", "το παράθυρο τελειώνει στο δευτερόλεπτο 60· τώρα είμαστε στο 5"
    assert "Πολλά αιτήματα" in r.json()["detail"]


def test_the_token_limit_does_not_lock_other_tokens_and_resets_with_the_window(client, office, frozen_clock):
    from routes_client_portal import PORTAL_TOKEN_LIMIT
    other = client.post("/api/client-portal/invite", json={"client_id": office.other_client_id}).json()["token"]
    for _ in range(PORTAL_TOKEN_LIMIT + 1):
        _chat(client, office.token)
    assert _chat(client, office.token).status_code == 429
    assert _chat(client, other).status_code == 200, "το όριο είναι ανά token"
    frozen_clock["now"] += timedelta(seconds=60)
    assert _chat(client, office.token).status_code == 200, "νέο παράθυρο, νέο όριο"


def test_the_portal_is_limited_per_address_across_tokens(client, office, frozen_clock):
    """Μια σάρωση πολλών (άκυρων) tokens από την ίδια διεύθυνση κόβεται, ακόμη κι αν κάθε token δοκιμάζεται μία φορά."""
    from routes_client_portal import PORTAL_IP_LIMIT, PORTAL_TOKEN_LIMIT
    assert PORTAL_IP_LIMIT > PORTAL_TOKEN_LIMIT, "αλλιώς το όριο ανά IP δεν θα ήταν ποτέ το αυστηρότερο"
    for i in range(PORTAL_IP_LIMIT):
        assert _chat(client, f"junk-{i}").status_code == 404
    r = _chat(client, "junk-final")
    assert r.status_code == 429 and r.headers["Retry-After"] == "55"
    # …και ο γνήσιος σύνδεσμος από την ίδια διεύθυνση περιμένει κι αυτός (αποδεκτό: μία διεύθυνση, ένα όριο)
    assert _chat(client, office.token).status_code == 429


def test_the_address_limit_is_per_address(office, frozen_clock):
    """Η διεύθυνση προκύπτει από το X-Forwarded-For που βάζει ο nginx (βλ. test_unit_deploy_r5)· δύο διευθύνσεις έχουν
    ξεχωριστά όρια."""
    import server
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from routes_client_portal import PORTAL_IP_LIMIT

    def from_address(ip):
        return TestClient(ProxyHeadersMiddleware(server.app, trusted_hosts="*"), base_url="https://testserver",
                          headers={"X-Forwarded-For": ip})

    noisy, quiet = from_address("203.0.113.10"), from_address("203.0.113.11")
    for i in range(PORTAL_IP_LIMIT):
        assert _chat(noisy, f"junk-{i}").status_code == 404
    assert _chat(noisy, "junk-final").status_code == 429
    assert _chat(quiet, "junk-final").status_code == 404, "άλλη διεύθυνση, άλλο όριο"
    assert _chat(quiet, office.token).status_code == 200


def test_a_rate_limit_counter_failure_does_not_lock_customers_out_but_is_not_silent(client, office, monkeypatch, caplog):
    """Ο περιορισμός ρυθμού δεν είναι έλεγχος πρόσβασης: αν χαλάσει η εγγραφή του μετρητή, το αίτημα περνά. Αλλά ένας
    μετρητής που χάλασε απενεργοποιεί κάθε όριο χωρίς σημάδι· πρέπει να φαίνεται στο αρχείο καταγραφής."""
    import logging
    import rate_limit

    class Broken:
        async def find_one_and_update(self, *a, **kw):
            raise RuntimeError("η βάση δεν απαντά")

    monkeypatch.setattr(rate_limit.db, "rate_limits", Broken(), raising=False)
    with caplog.at_level(logging.WARNING, logger="rate_limit"):
        assert _chat(client, office.token).status_code == 200
    warned = [r for r in caplog.records if r.name == "rate_limit" and r.levelno == logging.WARNING]
    assert warned and "η βάση δεν απαντά" in warned[0].getMessage() and "portal:token" in warned[0].getMessage()


# ================================================== επικύρωση διεύθυνσης (http_utils) ====

ORIGINS = ["https://nomos.example.gr"]


@pytest.mark.parametrize("url", [
    "https://nomos.\nexample.gr", "https://no\tmos.example.gr", "https://nomos.example.gr/po\rrtal", "https://nomos.exam\r\nple.gr",
    "https://nomos.example.gr\x00", "https://nomos.example\x7f.gr", "https://nomos.example.gr/\x1b",
])
def test_control_characters_are_refused_even_where_the_url_parser_would_silently_drop_them(url):
    """Το urlsplit αφαιρεί σιωπηλά tab/CR/LF **μέσα** στη διεύθυνση· χωρίς ρητό έλεγχο ένα `https://nomos.<LF>example.gr`
    θα περνούσε ως έγκυρο (και θα κανονικοποιούνταν σε διεύθυνση που ο καλών δεν έγραψε ποτέ)."""
    from http_utils import checked_return_url
    assert checked_return_url(url, ORIGINS) is None


def test_whitespace_around_the_address_is_trimmed_not_refused():
    from http_utils import checked_return_url
    assert checked_return_url("\t https://nomos.example.gr \r\n", ORIGINS) == "https://nomos.example.gr"


@pytest.mark.parametrize("url,expected", [
    ("https://nomos.example.gr", "https://nomos.example.gr"),
    ("HTTPS://NOMOS.Example.GR/", "https://nomos.example.gr"),
    ("https://nomos.example.gr:443/", "https://nomos.example.gr"),
])
def test_the_canonical_form_is_lower_case_without_default_port(url, expected):
    from http_utils import checked_return_url
    assert checked_return_url(url, ORIGINS) == expected


def test_the_allowlist_itself_is_normalised_too():
    """Ρύθμιση με κεφαλαία, τελική κάθετο και ρητή θύρα 443 ισοδυναμεί με την κανονική μορφή."""
    from http_utils import checked_return_url
    assert checked_return_url("https://nomos.example.gr", ["HTTPS://Nomos.Example.GR:443/"]) == "https://nomos.example.gr"
    assert checked_return_url("https://nomos.example.gr:8080", ["https://Nomos.Example.GR:8080/"]) == "https://nomos.example.gr:8080"
    assert checked_return_url("https://nomos.example.gr:8080", ["https://nomos.example.gr"]) is None


def test_only_http_and_https_entries_can_be_allowed():
    from http_utils import checked_return_url
    assert checked_return_url("ftp://nomos.example.gr", ["ftp://nomos.example.gr"]) is None
    assert checked_return_url("https://nomos.example.gr", ["not a url", "ftp://x", "", "https://nomos.example.gr"]) \
        == "https://nomos.example.gr"


def test_the_return_address_uses_only_the_invoices_own_firm_settings(client, office, stripe_calls):
    """Η `public_url` ενός ΑΛΛΟΥ γραφείου δεν επιτρέπεται ως διεύθυνση επιστροφής για τιμολόγιο αυτού του γραφείου."""
    from db import db
    _run(db.settings.delete_many({}))
    _run(db.settings.insert_one({"firm_id": "firm-" + "2" * 32, "public_url": "https://other-firm.example"}))   # πρώτο στη βάση
    assert _checkout(client, office, office.invoice_id, origin="https://other-firm.example").status_code == 400
    assert stripe_calls == []


# ======================================================= συνομιλία πύλης: τα ΝΕΟΤΕΡΑ 500 ====

def _fill_chat(office, count, client_indexes=()):
    """`count` μηνύματα του δικηγόρου, το i-στό στις 10:00 + i δευτερόλεπτα· όσα έχουν δείκτη στο `client_indexes`
    είναι του πελάτη. Όλα αδιάβαστα."""
    from db import db
    base = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    docs = [{
        "client_id": office.client_id, "client_name": "ΑΛΦΑ ΑΕ", "firm_id": office.firm_id,
        "sender": "client" if i in client_indexes else "lawyer", "sender_name": "x", "text": f"μήνυμα {i:04d}",
        "case_id": None, "read_at": None, "created_at": (base + timedelta(seconds=i)).isoformat(),
    } for i in range(count)]
    _run(db.portal_messages.insert_many(docs))
    return docs


@pytest.mark.parametrize("side", ["portal", "lawyer"])
def test_the_conversation_shows_the_newest_messages_in_order(client, office, side):
    """Παλιότερα: `.sort(created_at, 1).to_list(500)` = τα ΠΑΛΑΙΟΤΕΡΑ 500. Σε μακρά συνομιλία τα νεότερα δεν φαίνονταν ποτέ."""
    _fill_chat(office, 520)
    url = f"/api/portal/{office.token}/chat" if side == "portal" else f"/api/portal-chat/messages/{office.client_id}"
    rows = client.get(url).json()
    assert len(rows) == 500
    assert [r["text"] for r in rows] == [f"μήνυμα {i:04d}" for i in range(20, 520)], "τα νεότερα 500, παλαιότερο πρώτο"


def test_messages_outside_the_window_are_not_marked_as_read(client, office):
    """Η ένδειξη «διαβάστηκε» αφορά ό,τι εμφανίστηκε· τα αδιάβαστα που δεν φάνηκαν μένουν αδιάβαστα (και μετρούν)."""
    from db import db
    _fill_chat(office, 520, client_indexes={0, 1, 2, 100, 101})    # τα #0–#2 πέφτουν έξω από το παράθυρο (#20–#519)
    assert client.get(f"/api/portal-chat/messages/{office.client_id}").status_code == 200
    unread = _run(db.portal_messages.find({"sender": "client", "read_at": None}).to_list(1000))
    assert sorted(m["text"] for m in unread) == ["μήνυμα 0000", "μήνυμα 0001", "μήνυμα 0002"]
    assert client.get("/api/portal-chat/unread-count").json() == {"unread": 3}


def test_the_client_side_marks_only_the_lawyers_messages_it_displayed(client, office):
    from db import db
    _fill_chat(office, 520, client_indexes=set(range(520)) - {0, 100})     # του δικηγόρου είναι μόνο το #0 (έξω) και το #100
    assert client.get(f"/api/portal/{office.token}/chat").status_code == 200
    unread = _run(db.portal_messages.find({"sender": "lawyer", "read_at": None}).to_list(1000))
    assert [m["text"] for m in unread] == ["μήνυμα 0000"], "το παλιό, εκτός παραθύρου, μένει αδιάβαστο· το #100 εμφανίστηκε"


def test_a_short_conversation_is_returned_whole_and_marked_read(client, office):
    from db import db
    _fill_chat(office, 6, client_indexes={1, 4})
    rows = client.get(f"/api/portal-chat/messages/{office.client_id}").json()
    assert [r["text"] for r in rows] == [f"μήνυμα {i:04d}" for i in range(6)]
    assert _run(db.portal_messages.count_documents({"sender": "client", "read_at": None})) == 0
