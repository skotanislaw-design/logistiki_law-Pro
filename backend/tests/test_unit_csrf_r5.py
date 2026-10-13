"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D, εύρημα D-01) — πλαστά αιτήματα από άλλον ιστότοπο (CSRF).

Η σύνδεση γίνεται με cookie που ο browser στέλνει μόνος του· τα cookies ήταν `SameSite=None` και τίποτα δεν έλεγχε από πού
ξεκίνησε το αίτημα. Μια σελίδα τρίτου μπορούσε να εκτελέσει με τη σύνδεση του χρήστη ενέργειες χωρίς σώμα (έκδοση τιμολογίου,
«Συμφωνία» κίνησης, Autopilot), κάθε ενέργεια με σώμα JSON (η FastAPI διαβάζει ως JSON ένα σώμα χωρίς Content-Type) και την
επαναφορά αντιγράφου, που αντικαθιστά IBAN, SMTP και `public_url`. Διορθώθηκε με τρία στρώματα (βλ. `csrf_guard.py`): `SameSite=Lax`,
έλεγχος προέλευσης για αιτήματα που αλλάζουν δεδομένα και φέρουν cookie σύνδεσης, και απόρριψη σώματος χωρίς Content-Type.

Δύο επίπεδα εδώ: (α) ο ίδιος ο φρουρός ως ASGI middleware, με κάθε συνδυασμό κεφαλίδων που στέλνουν οι browsers, και (β) η πλήρης
εφαρμογή, με τα πραγματικά endpoints που ανέφερε η αναφορά ως στόχους.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from csrf_guard import CsrfGuard, FOREIGN_MESSAGE, NO_CONTENT_TYPE_MESSAGE
from db import db
from http_utils import origin_of

APP = "https://app.firma.gr"
FOREIGN = "https://evil.example"
SESSION = ("cookie", "access_token=jwt")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Below:
    """Η εφαρμογή «από κάτω»: καταγράφει αν το αίτημα έφτασε ως εδώ."""

    def __init__(self):
        self.reached = 0

    async def __call__(self, scope, receive, send):
        self.reached += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def call(method, path, headers=(), *, allowed=(APP,), guard_allowed="given"):
    below = _Below()
    guard = CsrfGuard(below) if guard_allowed is None else CsrfGuard(below, allowed=lambda: list(allowed))
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]}
    sent, receives = [], []

    async def receive():
        receives.append(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    _run(guard(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return SimpleNamespace(status=status, body=body, reached=below.reached, read_body=bool(receives))


def refused(result):
    return result.status == 403 and result.reached == 0


# ---------------------------------------------------------------------------------------------------------- origin_of ---

@pytest.mark.parametrize("value, expected", [
    ("https://app.firma.gr", "https://app.firma.gr"),
    ("HTTPS://APP.Firma.GR:443", "https://app.firma.gr"),
    ("http://app.firma.gr:80/", "http://app.firma.gr"),
    ("https://app.firma.gr:8443/page?x=1#y", "https://app.firma.gr:8443"),
    ("  https://app.firma.gr  ", "https://app.firma.gr"),
    ("http://[::1]:3000", "http://[::1]:3000"),
    ("http://localhost", "http://localhost"),
])
def test_origin_of_normalises_scheme_host_and_port(value, expected):
    assert origin_of(value) == expected


@pytest.mark.parametrize("value", ["null", "", "   ", "not a url", "ftp://files.example", "https://", "//app.firma.gr",
                                   "https://app.firma.gr:notaport", None, 7, b"https://app.firma.gr"])
def test_origin_of_returns_none_for_anything_that_identifies_nobody(value):
    assert origin_of(value) is None


# --------------------------------------------------------------------------------------- the decision, header by header ---

DECISIONS = [
    # --- Fetch Metadata: ο browser το βάζει και η σελίδα δεν μπορεί να το αλλάξει
    pytest.param([("sec-fetch-site", "same-origin")], False, id="same-origin"),
    pytest.param([("sec-fetch-site", "same-origin"), ("origin", FOREIGN)], False, id="same-origin-metadata-is-authoritative"),
    pytest.param([("sec-fetch-site", "none")], False, id="user-initiated"),
    pytest.param([("sec-fetch-site", "  Same-Origin ")], False, id="metadata-case-and-spaces"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", FOREIGN)], True, id="cross-site-foreign-origin"),
    pytest.param([("sec-fetch-site", "cross-site")], True, id="cross-site-without-origin"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", "null")], True, id="cross-site-origin-null"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", "garbage")], True, id="cross-site-unreadable-origin"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", APP)], False, id="cross-site-but-configured-origin"),
    pytest.param([("sec-fetch-site", "same-site"), ("origin", APP)], False, id="same-site-configured-origin"),
    pytest.param([("sec-fetch-site", "same-site"), ("origin", "https://evil.firma.gr")], True, id="same-site-sibling-subdomain"),
    pytest.param([("sec-fetch-site", "same-site")], True, id="same-site-without-origin"),
    pytest.param([("sec-fetch-site", "somewhere-new"), ("origin", FOREIGN)], True, id="unknown-metadata-foreign"),
    pytest.param([("sec-fetch-site", "somewhere-new"), ("origin", APP)], False, id="unknown-metadata-configured"),
    pytest.param([("sec-fetch-site", "cross-site"), ("referer", FOREIGN + "/page")], True, id="cross-site-foreign-referer"),
    pytest.param([("sec-fetch-site", "cross-site"), ("referer", APP + "/invoices")], False, id="cross-site-configured-referer"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", "http://app.firma.gr")], True, id="configured-host-other-scheme"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", "https://app.firma.gr:8443")], True, id="configured-host-other-port"),
    pytest.param([("sec-fetch-site", "cross-site"), ("origin", "HTTPS://APP.FIRMA.GR:443")], False, id="configured-written-differently"),
    # --- χωρίς Fetch Metadata (παλιός browser ή όχι browser)
    pytest.param([], False, id="no-metadata-no-origin-script-or-curl"),
    pytest.param([("origin", FOREIGN)], True, id="no-metadata-foreign-origin"),
    pytest.param([("origin", APP)], False, id="no-metadata-configured-origin"),
    pytest.param([("origin", "null")], True, id="no-metadata-origin-null"),
    pytest.param([("origin", "not a url")], True, id="no-metadata-garbage-origin"),
    pytest.param([("referer", FOREIGN + "/x")], True, id="no-metadata-foreign-referer"),
    pytest.param([("referer", APP + "/x")], False, id="no-metadata-configured-referer"),
    pytest.param([("origin", FOREIGN), ("referer", APP + "/x")], True, id="origin-wins-over-referer"),
    pytest.param([("origin", "http://clinic.local:8080"), ("host", "clinic.local")], False, id="same-host-port-dropped-by-nginx"),
    pytest.param([("origin", "https://evil.firma.gr"), ("host", "app.firma.gr")], True, id="sibling-subdomain-is-not-the-host"),
    pytest.param([("origin", FOREIGN), ("host", "clinic.local")], True, id="foreign-origin-other-host"),
    pytest.param([("origin", "https://nomos.firma.gr"), ("host", "backend:8001"), ("x-forwarded-host", "nomos.firma.gr")], False,
                 id="host-from-outer-proxy"),
    pytest.param([("origin", "https://nomos.firma.gr"), ("host", "backend:8001"), ("x-forwarded-host", "other.example, nomos.firma.gr")], True,
                 id="only-the-first-forwarded-host-counts"),
    pytest.param([("origin", "https://nomos.firma.gr"), ("host", "backend:8001"), ("x-forwarded-host", "nomos.firma.gr, proxy-1.internal")], False,
                 id="forwarded-host-chain-starts-with-the-public-name"),
    pytest.param([("origin", "http://[::1]:3000"), ("host", "[::1]:8001")], False, id="ipv6-same-host"),
    pytest.param([("origin", FOREIGN), ("host", "")], True, id="empty-host"),
]


@pytest.mark.parametrize("headers, foreign", DECISIONS)
def test_which_requests_with_a_session_cookie_are_accepted(headers, foreign):
    result = call("POST", "/api/clients", [SESSION, *headers])
    if foreign:
        assert refused(result), (result.status, result.reached)
        assert json.loads(result.body) == {"detail": FOREIGN_MESSAGE}
    else:
        assert result.reached == 1 and result.status == 204


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_state_changing_method_is_checked(method):
    result = call(method, "/api/clients/abc", [SESSION, ("origin", FOREIGN), ("sec-fetch-site", "cross-site")])
    assert refused(result)


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_reads_and_preflights_are_never_checked(method):
    """Η ανάγνωση δεν αλλάζει τίποτα (και το CORS αποφασίζει αν η απάντηση διαβάζεται)· το OPTIONS το απαντά το CORS."""
    result = call(method, "/api/clients", [SESSION, ("origin", FOREIGN), ("sec-fetch-site", "cross-site")])
    assert result.reached == 1 and result.status == 204


def test_a_refused_request_is_answered_before_its_body_is_read():
    result = call("POST", "/api/backup/import", [SESSION, ("origin", FOREIGN), ("sec-fetch-site", "cross-site"),
                                                 ("content-type", "multipart/form-data; boundary=x"), ("content-length", "99999999")])
    assert refused(result) and not result.read_body


def test_websocket_and_lifespan_scopes_pass_through_untouched():
    below = _Below()
    guard = CsrfGuard(below)
    for scope in ({"type": "lifespan"}, {"type": "websocket", "path": "/ws", "headers": []}):
        _run(guard(scope, None, lambda message: asyncio.sleep(0)))
    assert below.reached == 2


# ---------------------------------------------------------------------------------------------------- who is covered ---

def test_a_request_without_a_session_cookie_is_not_affected():
    """Webhook του Stripe, πύλη πελάτη (token στη διαδρομή), πελάτες με κεφαλίδα Authorization: δεν υπάρχει cookie να εκμεταλλευτεί κανείς."""
    foreign = [("origin", FOREIGN), ("sec-fetch-site", "cross-site")]
    for path in ("/api/stripe/webhook", "/api/clients", "/api/portal/sometoken/pay"):
        assert call("POST", path, foreign).reached == 1, path
    assert call("POST", "/api/clients", [("authorization", "Bearer x"), *foreign]).reached == 1


@pytest.mark.parametrize("cookie", ["theme=dark", "my_access_token=1", "access_token_old=1", "", "; ;"])
def test_other_cookies_do_not_count_as_a_session(cookie):
    assert call("POST", "/api/clients", [("cookie", cookie), ("origin", FOREIGN), ("sec-fetch-site", "cross-site")]).reached == 1


@pytest.mark.parametrize("cookie", ["refresh_token=r", "access_token=a; refresh_token=r", "theme=dark; access_token=a=b=c", "x=1;access_token=a"])
def test_either_session_cookie_is_enough(cookie):
    assert refused(call("POST", "/api/clients", [("cookie", cookie), ("origin", FOREIGN), ("sec-fetch-site", "cross-site")]))


def test_a_second_cookie_header_is_read_too():
    """Στο HTTP/2 ο browser μπορεί να στείλει τα cookies σε περισσότερες από μία κεφαλίδες."""
    result = call("POST", "/api/clients", [("cookie", "theme=dark"), ("cookie", "access_token=a"), ("origin", FOREIGN)])
    assert refused(result)


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/register", "/api/auth/forgot-password", "/api/auth/mfa/verify"])
def test_authentication_requests_are_checked_even_without_a_cookie(path):
    """Αλλιώς ξένη σελίδα θα «συνέδεε» τον χρήστη σε λογαριασμό του επιτιθέμενου (login CSRF) και θα έστελνε email επαναφοράς."""
    assert refused(call("POST", path, [("origin", FOREIGN), ("sec-fetch-site", "cross-site")]))
    assert call("POST", path, [("origin", APP), ("sec-fetch-site", "same-origin")]).reached == 1
    assert call("POST", path, []).reached == 1                                      # script, curl, test client


def test_the_origin_check_does_not_depend_on_the_path():
    """Ό,τι έχει cookie σύνδεσης και αλλάζει κάτι ελέγχεται — όχι μόνο όσα βρίσκονται σε μια λίστα διαδρομών που μπορεί να ξεχαστεί."""
    for path in ("/api/clients", "/api/some/future/route", "/health", "/"):
        assert refused(call("POST", path, [SESSION, ("origin", FOREIGN), ("sec-fetch-site", "cross-site")])), path


# ------------------------------------------------------------------------------------------------ body without a type ---

def test_a_body_without_content_type_is_refused_when_a_session_cookie_is_present():
    for extra in ([("content-length", "17")], [("transfer-encoding", "chunked")], [("content-length", "garbage")]):
        result = call("POST", "/api/clients", [SESSION, *extra])
        assert result.status == 415 and result.reached == 0, extra
        assert json.loads(result.body) == {"detail": NO_CONTENT_TYPE_MESSAGE}


def test_an_empty_content_type_is_the_same_as_none():
    assert call("POST", "/api/clients", [SESSION, ("content-length", "5"), ("content-type", "  ")]).status == 415


@pytest.mark.parametrize("content_type", ["application/json", "application/json; charset=utf-8", "multipart/form-data; boundary=x",
                                          "text/plain", "application/octet-stream"])
def test_any_declared_content_type_gets_through_this_layer(content_type):
    """Ο έλεγχος δεν κρίνει τον τύπο (η FastAPI απαντά 422 για text/plain)· αυτό που κλείνει εδώ είναι η ΑΠΟΥΣΙΑ του."""
    assert call("POST", "/api/clients", [SESSION, ("content-length", "17"), ("content-type", content_type)]).reached == 1


def test_a_request_without_a_body_needs_no_content_type():
    assert call("POST", "/api/bank/confirm-auto-matches", [SESSION]).reached == 1
    assert call("POST", "/api/bank/confirm-auto-matches", [SESSION, ("content-length", "0")]).reached == 1
    assert call("DELETE", "/api/hearings/abc", [SESSION]).reached == 1


def test_the_content_type_rule_needs_a_session_cookie_and_the_api():
    """Το webhook του Stripe και οι κλήσεις χωρίς cookie δεν επηρεάζονται· ούτε ό,τι δεν είναι API."""
    assert call("POST", "/api/stripe/webhook", [("content-length", "120")]).reached == 1
    assert call("POST", "/static/upload", [SESSION, ("content-length", "17")]).reached == 1


def test_a_foreign_request_is_refused_before_the_content_type_is_looked_at():
    result = call("POST", "/api/clients", [SESSION, ("content-length", "17"), ("origin", FOREIGN), ("sec-fetch-site", "cross-site")])
    assert result.status == 403


# ------------------------------------------------------------------------------------------------------- the allow-list ---

def test_the_allow_list_is_read_on_every_request(monkeypatch):
    """Η ίδια λίστα με του CORS (`http_utils.allowed_origins`), όπως διαβάζεται από τις μεταβλητές περιβάλλοντος."""
    headers = [SESSION, ("origin", "https://crm.example.gr"), ("sec-fetch-site", "cross-site")]
    monkeypatch.setenv("CORS_ORIGINS", "https://crm.example.gr, https://other.example.gr/")
    assert call("POST", "/api/clients", headers, guard_allowed=None).reached == 1
    assert call("POST", "/api/clients", [SESSION, ("origin", "https://other.example.gr"), ("sec-fetch-site", "cross-site")], guard_allowed=None).reached == 1
    monkeypatch.setenv("CORS_ORIGINS", "https://nomos.example.gr")
    assert refused(call("POST", "/api/clients", headers, guard_allowed=None))
    monkeypatch.delenv("CORS_ORIGINS")
    monkeypatch.setenv("PUBLIC_URL", "https://crm.example.gr")
    assert call("POST", "/api/clients", headers, guard_allowed=None).reached == 1


def test_configured_origins_are_compared_in_their_canonical_form(monkeypatch):
    """Ο διαχειριστής μπορεί να γράψει το `CORS_ORIGINS` με κεφαλαία ή με τη θύρα 443· ο browser στέλνει πάντα την κανονική μορφή."""
    monkeypatch.setenv("CORS_ORIGINS", "HTTPS://CRM.Example.gr:443")
    page = [SESSION, ("origin", "https://crm.example.gr"), ("sec-fetch-site", "cross-site")]
    assert call("POST", "/api/clients", page, guard_allowed=None).reached == 1
    assert refused(call("POST", "/api/clients", [SESSION, ("origin", "https://crm.example.gr:8443"), ("sec-fetch-site", "cross-site")],
                        guard_allowed=None))


def test_a_refusal_is_logged_without_the_session_cookie(caplog):
    caplog.set_level("WARNING", logger="csrf")
    result = call("POST", "/api/invoices/abc/issue", [("cookie", "access_token=top-secret-jwt-value"), ("origin", FOREIGN),
                                                      ("referer", FOREIGN + "/win"), ("sec-fetch-site", "cross-site")])
    assert refused(result)
    assert "POST /api/invoices/abc/issue" in caplog.text and FOREIGN in caplog.text and "cross-site" in caplog.text
    assert "top-secret-jwt-value" not in caplog.text and "access_token" not in caplog.text


def test_a_deployment_without_configuration_trusts_only_its_own_origin(monkeypatch):
    for name in ("CORS_ORIGINS", "PUBLIC_URL", "PREVIEW_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    foreign = [SESSION, ("origin", FOREIGN), ("sec-fetch-site", "cross-site")]
    assert refused(call("POST", "/api/clients", foreign, guard_allowed=None))
    assert call("POST", "/api/clients", [SESSION, ("sec-fetch-site", "same-origin")], guard_allowed=None).reached == 1
    assert call("POST", "/api/clients", [SESSION, ("origin", "http://localhost:3000"), ("sec-fetch-site", "same-site")], guard_allowed=None).reached == 1


# ------------------------------------------------------------------------------------------------ the whole application ---

@pytest.fixture()
def office():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "csrf@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        _run(db.users.update_one({"email": "csrf@example.gr"}, {"$set": {"role": "admin"}}))
        yield c


ATTACKER = {"Origin": FOREIGN, "Referer": FOREIGN + "/win-a-prize", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}
OWN_PAGE = {"Origin": "https://testserver", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}


def _client_count():
    return _run(db.clients.count_documents({}))


def test_a_foreign_page_cannot_create_a_client_even_without_a_content_type(office):
    """Ο δρόμος της αναφοράς: `fetch(..., {mode: "no-cors", body: new Uint8Array(bytes)})` — σώμα JSON χωρίς Content-Type."""
    body = json.dumps({"name": "ΑΛΦΑ ΑΕ", "client_type": "company"}).encode()
    r = office.post("/api/clients", content=body, headers=ATTACKER)
    assert r.status_code == 403 and r.json() == {"detail": FOREIGN_MESSAGE}
    assert _client_count() == 0
    r = office.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "client_type": "company"}, headers=ATTACKER)
    assert r.status_code == 403 and _client_count() == 0


def test_the_missing_content_type_path_is_closed_even_for_the_applications_own_origin(office):
    body = json.dumps({"name": "ΑΛΦΑ ΑΕ", "client_type": "company"}).encode()
    r = office.post("/api/clients", content=body, headers=OWN_PAGE)
    assert r.status_code == 415 and _client_count() == 0


@pytest.mark.parametrize("path", ["/api/bank/confirm-auto-matches", "/api/automation/run", "/api/automation/notifications/read-all",
                                  "/api/bank/openbanking/disconnect"])
def test_a_foreign_page_cannot_trigger_actions_that_need_no_body(office, path):
    r = office.post(path, headers=ATTACKER)
    assert r.status_code == 403, (path, r.status_code, r.text)
    assert r.json() == {"detail": FOREIGN_MESSAGE}


def test_a_foreign_page_cannot_restore_a_backup_over_the_live_settings(office):
    """Ο στόχος με τη μεγαλύτερη επίπτωση της αναφοράς: η επαναφορά αντικαθιστά IBAN, SMTP και `public_url` του γραφείου."""
    r = office.post("/api/backup/import", files={"file": ("backup.zip", b"PK\x03\x04", "application/zip")}, headers=ATTACKER)
    assert r.status_code == 403 and r.json() == {"detail": FOREIGN_MESSAGE}


def test_a_foreign_page_cannot_delete_or_change_anything_either(office):
    for method, path in (("PUT", "/api/settings"), ("PATCH", "/api/hearings/abc"), ("DELETE", "/api/hearings/abc")):
        assert office.request(method, path, headers=ATTACKER).status_code == 403, (method, path)


def test_a_foreign_page_cannot_log_the_user_in_to_the_attackers_account(office):
    """Login CSRF: χωρίς cookie ακόμη, αλλά το αίτημα προς το /api/auth/ ελέγχεται κι αυτό."""
    import server
    with TestClient(server.app, base_url="https://testserver") as stranger:
        r = stranger.post("/api/auth/login", json={"email": "csrf@example.gr", "password": "Str0ngPass!"}, headers=ATTACKER)
        assert r.status_code == 403
        assert "set-cookie" not in r.headers
        ok = stranger.post("/api/auth/login", json={"email": "csrf@example.gr", "password": "Str0ngPass!"}, headers=OWN_PAGE)
        assert ok.status_code == 200 and "access_token" in ok.headers["set-cookie"]


def test_the_application_itself_keeps_working(office):
    """Οι ίδιες ενέργειες από τη σελίδα της εφαρμογής (Sec-Fetch-Site: same-origin) δεν επηρεάζονται."""
    created = office.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "client_type": "company"}, headers=OWN_PAGE)
    assert created.status_code == 200, created.text
    assert _client_count() == 1
    assert office.post("/api/automation/run", headers=OWN_PAGE).status_code != 403
    assert office.get("/api/auth/me", headers=OWN_PAGE).status_code == 200
    assert office.post("/api/auth/refresh", headers=OWN_PAGE).status_code == 200


def test_a_foreign_page_can_still_send_reads(office):
    """Η ανάγνωση δεν αλλάζει τίποτα· ότι η απάντηση δεν διαβάζεται από ξένη σελίδα το εξασφαλίζει το CORS."""
    r = office.get("/api/clients", headers=ATTACKER)
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_a_deployment_with_the_app_on_another_origin_keeps_working(office, monkeypatch):
    """Frontend και API σε διαφορετικά sites ΚΑΤ' ΕΠΙΛΟΓΗ: η προέλευση της εφαρμογής βρίσκεται στο CORS_ORIGINS."""
    monkeypatch.setenv("CORS_ORIGINS", APP)
    app_page = {"Origin": APP, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}
    assert office.post("/api/clients", json={"name": "ΒΗΤΑ ΑΕ", "client_type": "company"}, headers=app_page).status_code == 200
    assert office.post("/api/clients", json={"name": "ΓΑΜΜΑ ΑΕ", "client_type": "company"}, headers=ATTACKER).status_code == 403


def test_a_bearer_token_without_the_cookie_is_not_a_csrf_target(office):
    """Η κεφαλίδα Authorization δεν μπορεί να οριστεί από ξένη σελίδα χωρίς προκαταρκτικό έλεγχο CORS, που το CORS αρνείται."""
    import server
    token = office.cookies.get("access_token")
    assert token
    with TestClient(server.app, base_url="https://testserver") as script:
        r = script.post("/api/clients", json={"name": "ΔΕΛΤΑ ΑΕ", "client_type": "company"},
                        headers={"Authorization": f"Bearer {token}", **ATTACKER})
        assert r.status_code == 200, r.text


def test_the_stripe_webhook_is_not_blocked(office):
    import server
    with TestClient(server.app, base_url="https://testserver") as stripe:
        r = stripe.post("/api/stripe/webhook", content=b"{}", headers={"Stripe-Signature": "t=1,v1=x", "Content-Type": "application/json"})
        assert r.status_code in (400, 503), r.text       # η απάντηση της ίδιας της εφαρμογής (λάθος υπογραφή / χωρίς μυστικό), όχι του φρουρού
        assert r.json().get("detail") != FOREIGN_MESSAGE


def test_the_guard_sits_inside_cors_and_before_the_json_limit():
    import server
    from fastapi.middleware.cors import CORSMiddleware
    from json_guard import RejectNonFiniteJson
    classes = [m.cls for m in server.app.user_middleware]            # από έξω προς τα μέσα
    assert classes.index(CORSMiddleware) < classes.index(CsrfGuard) < classes.index(RejectNonFiniteJson)


# --------------------------------------------------------------------------------------------------------- the cookies ---

def _cookies(response):
    return {c.split("=", 1)[0]: c for c in response.headers.get_list("set-cookie")}


def _login(client):
    return client.post("/api/auth/login", json={"email": "csrf@example.gr", "password": "Str0ngPass!"})


@pytest.fixture()
def clean_samesite(monkeypatch):
    monkeypatch.delenv("COOKIE_SAMESITE", raising=False)


def test_the_session_cookies_are_lax_by_default(office, clean_samesite):
    cookies = _cookies(_login(office))
    for name in ("access_token", "refresh_token"):
        attributes = cookies[name].lower()
        assert "samesite=lax" in attributes and "httponly" in attributes and "secure" in attributes, cookies[name]
    assert "samesite=none" not in " ".join(cookies.values()).lower()


def test_the_refreshed_access_cookie_has_the_same_attributes(office, clean_samesite):
    refreshed = _cookies(office.post("/api/auth/refresh"))["access_token"].lower()
    assert "samesite=lax" in refreshed and "httponly" in refreshed and "secure" in refreshed and "max-age=1800" in refreshed


def test_logout_deletes_the_cookies_with_the_attributes_they_were_set_with(office, clean_samesite):
    cookies = _cookies(office.post("/api/auth/logout"))
    for name in ("access_token", "refresh_token"):
        attributes = cookies[name].lower()
        assert "max-age=0" in attributes and "path=/" in attributes
        assert "samesite=lax" in attributes and "secure" in attributes and "httponly" in attributes, cookies[name]


@pytest.mark.parametrize("configured, expected", [("none", "none"), ("NONE", "none"), (" Strict ", "strict"), ("lax", "lax"),
                                                  ("", "lax"), ("sometimes", "lax"), ("None;Secure", "lax")])
def test_the_samesite_setting_is_read_from_the_environment(office, monkeypatch, configured, expected):
    monkeypatch.setenv("COOKIE_SAMESITE", configured)
    cookies = _cookies(_login(office))
    assert f"samesite={expected}" in cookies["access_token"].lower()
    assert f"samesite={expected}" in cookies["refresh_token"].lower()
    assert f"samesite={expected}" in _cookies(office.post("/api/auth/refresh"))["access_token"].lower()
    deleted = _cookies(office.post("/api/auth/logout"))
    assert f"samesite={expected}" in deleted["access_token"].lower() and f"samesite={expected}" in deleted["refresh_token"].lower()
    if expected == "none":
        assert "secure" in deleted["access_token"].lower()      # ο Chrome αγνοεί cookie SameSite=None χωρίς Secure: δεν θα διαγραφόταν


# ----------------------------------------------------------------------------------------- start-up: placeholder values ---

STRONG = "q7Vn2xKd9LmB4tRw8ZpYc3HsJf6GuAe1"


@pytest.fixture()
def environment(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", STRONG)
    monkeypatch.setenv("MONGO_URL", "mongodb://nomos:q7Vn2xKd9LmB@mongo:27017/?authSource=admin")
    monkeypatch.delenv("COOKIE_SAMESITE", raising=False)
    return monkeypatch


def test_a_real_configuration_starts(environment):
    import server
    server.validate_environment()


@pytest.mark.parametrize("secret", ["change-me-to-a-long-random-string", "CHANGE-ME-to-a-long-random-string", "change_me_please_0123456789abcdef",
                                    "xx-changeme-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])
def test_the_published_jwt_secret_placeholder_is_refused(environment, secret):
    import server
    environment.setenv("JWT_SECRET", secret)
    with pytest.raises(RuntimeError, match="JWT_SECRET") as failure:
        server.validate_environment()
    assert "τιμή-παράδειγμα" in str(failure.value)
    assert secret not in str(failure.value)


def test_the_published_mongo_password_placeholder_is_refused(environment):
    import server
    environment.setenv("MONGO_URL", "mongodb://nomos:change-me-to-a-long-random-string@mongo:27017/?authSource=admin")
    with pytest.raises(RuntimeError, match="MongoDB") as failure:
        server.validate_environment()
    assert "change-me-to-a-long-random-string" not in str(failure.value)


def test_a_short_development_secret_is_still_only_a_warning(environment, caplog):
    """Το README προτείνει `JWT_SECRET=dev` για τοπική δοκιμή· μόνο η δημόσια τιμή-παράδειγμα απορρίπτεται."""
    import server
    environment.setenv("JWT_SECRET", "dev")
    server.validate_environment()
    assert "μόνο 3 χαρακτήρες" in caplog.text


def test_an_invalid_samesite_value_is_reported_and_none_is_flagged(environment, caplog):
    import server
    environment.setenv("COOKIE_SAMESITE", "sometimes")
    server.validate_environment()
    assert "COOKIE_SAMESITE" in caplog.text and "lax" in caplog.text
    caplog.clear()
    environment.setenv("COOKIE_SAMESITE", "none")
    server.validate_environment()
    assert "COOKIE_SAMESITE=none" in caplog.text and "CORS_ORIGINS" in caplog.text
    caplog.clear()
    environment.setenv("COOKIE_SAMESITE", "lax")
    server.validate_environment()
    assert "COOKIE_SAMESITE" not in caplog.text
