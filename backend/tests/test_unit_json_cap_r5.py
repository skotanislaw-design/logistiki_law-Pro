"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής C — C-15) — όριο μεγέθους στα σώματα JSON.

Ο φραγμός για `NaN`/άπειρα (`json_guard.RejectNonFiniteJson`) διαβάζει και αναλύει ΟΛΟΚΛΗΡΟ το σώμα του αιτήματος πριν από
τον έλεγχο ταυτότητας, πάνω στον βρόχο γεγονότων που μοιράζονται όλα τα γραφεία· η FastAPI το αναλύει ύστερα ξανά. Μέτρηση του
ελεγκτή: 0,7–1,4 s για 20 MB (το όριο του nginx στο `/api/`), από οποιονδήποτε, χωρίς σύνδεση.

Τώρα: σώμα JSON πάνω από 2 MB απορρίπτεται με 413 ΠΡΙΝ αναλυθεί — αν ο client δηλώσει μέγεθος, δεν διαβάζεται καν· αν στέλνει
τμηματικά, μετράμε όσο έρχεται. Μόνη εξαίρεση η φωτογραφία απόδειξης ως base64 (`/api/expenses/ocr-parse`, έως 20 MB).
Τα αρχεία (αντίγραφα ασφαλείας, CSV, ήχος) ανεβαίνουν ως multipart και δεν επηρεάζονται.
"""
import json

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import json_guard
from json_guard import (LARGE_JSON_BODY, LARGE_JSON_PATHS, MAX_JSON_BODY, MESSAGE, TOO_LARGE, RejectNonFiniteJson)

MIB = 1024 * 1024


def _blob(size: int) -> bytes:
    """Έγκυρο JSON ακριβώς `size` byte: {"x": "aaa…"}."""
    overhead = len(b'{"x": ""}')
    return b'{"x": "' + b"a" * (size - overhead) + b'"}'


@pytest.fixture()
def seen():
    return []


@pytest.fixture()
def client(seen):
    async def echo(request: Request):
        body = await request.body()
        seen.append(len(body))
        return JSONResponse({"size": len(body)})

    app = Starlette(routes=[Route("/api/anything", echo, methods=["POST", "PUT"]),
                            Route("/api/expenses/ocr-parse", echo, methods=["POST"]),
                            Route("/api/backup/import", echo, methods=["POST"])])
    app.add_middleware(RejectNonFiniteJson)
    return TestClient(app)


# ----------------------------------------------------------------------------------- το όριο ----

def test_the_limits_are_what_the_deployment_expects():
    assert MAX_JSON_BODY == 2 * MIB
    assert LARGE_JSON_BODY == 20 * MIB                       # ίσο με το client_max_body_size του nginx στο /api/
    assert LARGE_JSON_PATHS == {"/api/expenses/ocr-parse"}


def test_a_body_exactly_at_the_limit_is_accepted(client, seen):
    r = client.post("/api/anything", content=_blob(MAX_JSON_BODY), headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"size": MAX_JSON_BODY} and seen == [MAX_JSON_BODY]


def test_a_body_one_byte_over_the_limit_is_refused_with_413_and_never_reaches_the_route(client, seen):
    r = client.post("/api/anything", content=_blob(MAX_JSON_BODY + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": TOO_LARGE}
    assert seen == []


def test_a_body_without_a_content_type_is_judged_too_because_fastapi_reads_it_as_json(client, seen):
    r = client.post("/api/anything", content=_blob(MAX_JSON_BODY + 1))
    assert r.status_code == 413 and seen == []


@pytest.mark.parametrize("method", ["post", "put"])
def test_the_limit_applies_to_every_method_with_a_body(client, seen, method):
    r = getattr(client, method)("/api/anything", content=_blob(MAX_JSON_BODY + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413 and seen == []


def test_the_limit_is_judged_before_the_body_is_parsed():
    """413 και όχι 400 ακόμη κι αν το σώμα περιέχει NaN: δεν αναλύεται καθόλου."""
    async def run():
        sent, calls = [], []

        async def app(scope, receive, send):
            calls.append("app")

        async def receive():
            return {"type": "http.request", "body": b'{"x": NaN, "pad": "' + b"a" * (MAX_JSON_BODY + 1) + b'"}', "more_body": False}

        async def send(message):
            sent.append(message)

        await RejectNonFiniteJson(app)(_scope(), receive, send)
        return sent, calls

    import asyncio
    sent, calls = asyncio.run(run())
    assert sent[0]["status"] == 413 and calls == []


def _scope(path="/api/anything", headers=()):
    return {"type": "http", "method": "POST", "path": path, "headers": [(b"content-type", b"application/json"), *headers]}


def test_a_declared_length_over_the_limit_is_refused_without_reading_the_body():
    import asyncio

    async def run():
        sent = []

        async def app(scope, receive, send):
            raise AssertionError("the route must not be reached")

        async def receive():
            raise AssertionError("the body must not be read")

        async def send(message):
            sent.append(message)

        await RejectNonFiniteJson(app)(_scope(headers=[(b"content-length", str(MAX_JSON_BODY + 1).encode())]), receive, send)
        return sent

    sent = asyncio.run(run())
    assert sent[0]["status"] == 413


def test_a_chunked_body_is_counted_as_it_arrives_and_stops_at_the_limit():
    import asyncio

    async def run():
        sent, pulled = [], []
        chunk = b"a" * MIB

        async def app(scope, receive, send):
            raise AssertionError("the route must not be reached")

        async def receive():
            pulled.append(1)
            return {"type": "http.request", "body": chunk, "more_body": True}        # ροή χωρίς τέλος

        async def send(message):
            sent.append(message)

        await RejectNonFiniteJson(app)(_scope(), receive, send)             # δεν δηλώνεται Content-Length
        return sent, pulled

    sent, pulled = asyncio.run(run())
    assert sent[0]["status"] == 413 and len(pulled) == MAX_JSON_BODY // MIB + 1


@pytest.mark.parametrize("declared", [b"abc", b"-5", b""])
def test_a_nonsense_content_length_does_not_confuse_the_limit(declared):
    import asyncio

    async def run():
        sent = []

        async def app(scope, receive, send):
            raise AssertionError("the route must not be reached")

        async def receive():
            return {"type": "http.request", "body": _blob(MAX_JSON_BODY + 1), "more_body": False}

        async def send(message):
            sent.append(message)

        await RejectNonFiniteJson(app)(_scope(headers=[(b"content-length", declared)]), receive, send)
        return sent

    assert asyncio.run(run())[0]["status"] == 413


# ----------------------------------------------------------------------------------- εξαιρέσεις ----

def test_the_receipt_photo_route_may_carry_a_large_json_body(client, seen):
    five = _blob(5 * MIB)
    assert client.post("/api/expenses/ocr-parse", content=five, headers={"content-type": "application/json"}).status_code == 200
    assert client.post("/api/expenses/ocr-parse/", content=five, headers={"content-type": "application/json"}).status_code in (200, 307)
    assert seen[0] == 5 * MIB


def test_even_the_receipt_photo_route_has_a_limit(client, seen):
    r = client.post("/api/expenses/ocr-parse", content=_blob(LARGE_JSON_BODY + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413 and seen == []


def test_a_large_body_for_another_route_does_not_borrow_the_exception(client, seen):
    r = client.post("/api/anything", content=_blob(5 * MIB), headers={"content-type": "application/json"})
    assert r.status_code == 413 and seen == []


def test_file_uploads_are_not_json_and_pass_through_untouched(client, seen):
    big = b"x" * (3 * MIB)
    r = client.post("/api/backup/import", content=big, headers={"content-type": "multipart/form-data; boundary=zzz"})
    assert r.status_code == 200 and seen == [3 * MIB]


def test_a_small_body_still_gets_the_non_finite_check(client, seen):
    r = client.post("/api/anything", content=b'{"amount": NaN}', headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json() == {"detail": MESSAGE} and seen == []


def test_a_small_ordinary_body_is_replayed_unchanged(client, seen):
    payload = {"amount": 12.5, "note": "Γειά σου"}
    r = client.post("/api/anything", content=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json() == {"size": len(json.dumps(payload).encode())}


# ----------------------------------------------------------------------------------- η πραγματική εφαρμογή ----

def test_the_exception_names_a_route_that_really_exists():
    """Αν η διαδρομή της φωτογραφίας απόδειξης μετονομαστεί, η εξαίρεση θα έμενε στον αέρα και η λειτουργία θα έσπαγε."""
    import server
    paths = {getattr(route, "path", None) for route in server.app.routes}
    assert LARGE_JSON_PATHS <= paths


def test_the_real_app_refuses_an_oversized_json_body_before_it_authenticates():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/login", content=_blob(MAX_JSON_BODY + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": TOO_LARGE}


def test_the_guard_module_exposes_the_message_the_frontend_can_show():
    assert json_guard.TOO_LARGE == "Το αίτημα είναι υπερβολικά μεγάλο."
