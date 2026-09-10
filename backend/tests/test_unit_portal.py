"""Η δημόσια επιφάνεια του portal: ό,τι μπορεί να κάνει κάποιος χωρίς λογαριασμό.

Ο έλεγχος πρόσβασης ήταν ήδη σωστός και τα tests εδώ τον καθηλώνουν, ώστε να μη
χαλάσει. Αυτό που έλειπε ήταν αντίσταση σε **κατάχρηση**: επαληθεύτηκε σε ζωντανή
δοκιμή ότι το portal δεχόταν μήνυμα 2.000.000 χαρακτήρων και 60 διαδοχικά μηνύματα
χωρίς καμία παύση.
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _firm_with_portal_client(client, email="dikigoros@example.gr", name="ΠΕΛΑΤΗΣ ΕΝΑ"):
    """Γραφείο, πελάτης με email, και ενεργό portal token."""
    client.post("/api/auth/register",
                json={"email": email, "password": "Str0ngPass!", "name": "Δικηγόρος"})
    cid = client.post("/api/clients", json={
        "name": name, "afm": "123456789", "email": "pelatis@example.gr"}).json()["id"]
    token = client.post("/api/client-portal/invite", json={"client_id": cid}).json()["token"]
    return cid, token


# ------------------------------------------------------- έλεγχος πρόσβασης ---

def test_client_sees_only_their_own_file(client):
    cid, token = _firm_with_portal_client(client)
    client.post("/api/invoices", json={
        "client_id": cid, "date": "2026-09-10",
        "line_items": [{"description": "Αμοιβή", "quantity": 1, "unit_price": 1000.0, "vat_rate": 24}],
    })
    data = client.get(f"/api/portal/{token}").json()
    assert data["client"]["name"] == "ΠΕΛΑΤΗΣ ΕΝΑ"
    assert data["totals"]["unpaid_count"] == 0      # μόνο εκδοθέντα φαίνονται


def test_unknown_token_is_404_and_revoked_token_stops_working(client):
    cid, token = _firm_with_portal_client(client)
    assert client.get("/api/portal/" + "x" * 43).status_code == 404

    tid = client.get(f"/api/client-portal/tokens/{cid}").json()[0]["id"]
    assert client.delete(f"/api/client-portal/tokens/{tid}").status_code == 200
    assert client.get(f"/api/portal/{token}").status_code == 404


def test_expired_token_is_401(client):
    from db import db
    _, token = _firm_with_portal_client(client)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _run(db.client_portal_tokens.update_one({"token": token}, {"$set": {"expires_at": past}}))
    assert client.get(f"/api/portal/{token}").status_code == 401


def test_expiry_without_timezone_is_still_honoured(client):
    """Η παλιά σύγκριση ήταν αλφαβητική: ημερομηνία χωρίς ζώνη ώρας τη χαλούσε σιωπηλά."""
    from db import db
    _, token = _firm_with_portal_client(client)
    naive_past = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    _run(db.client_portal_tokens.update_one({"token": token}, {"$set": {"expires_at": naive_past}}))
    assert client.get(f"/api/portal/{token}").status_code == 401


def test_token_without_expiry_fails_closed(client):
    from db import db
    _, token = _firm_with_portal_client(client)
    _run(db.client_portal_tokens.update_one({"token": token}, {"$unset": {"expires_at": ""}}))
    assert client.get(f"/api/portal/{token}").status_code == 401


# ------------------------------------------------------------ κατάχρηση ---

def test_oversized_message_is_rejected(client):
    """Δεκτό ήταν μήνυμα δύο εκατομμυρίων χαρακτήρων — αποθηκευόταν ολόκληρο."""
    _, token = _firm_with_portal_client(client)
    r = client.post(f"/api/portal/{token}/chat", json={"text": "α" * 4001})
    assert r.status_code == 422
    assert client.post(f"/api/portal/{token}/chat", json={"text": "α" * 4000}).status_code == 200


def test_chat_flood_is_throttled(client):
    _, token = _firm_with_portal_client(client)
    codes = [client.post(f"/api/portal/{token}/chat", json={"text": f"μήνυμα {i}"}).status_code
             for i in range(15)]
    assert codes.count(200) == 10, codes
    assert codes[-1] == 429


def test_throttle_is_per_token_not_global(client):
    """Ένας πελάτης που ξεπερνά το όριο δεν κλειδώνει τους υπόλοιπους."""
    _, token_a = _firm_with_portal_client(client)
    cid_b = client.post("/api/clients", json={
        "name": "ΠΕΛΑΤΗΣ ΔΥΟ", "afm": "987654321", "email": "duo@example.gr"}).json()["id"]
    token_b = client.post("/api/client-portal/invite", json={"client_id": cid_b}).json()["token"]

    for i in range(12):
        client.post(f"/api/portal/{token_a}/chat", json={"text": f"μ{i}"})
    assert client.post(f"/api/portal/{token_a}/chat", json={"text": "ακόμη ένα"}).status_code == 429
    assert client.post(f"/api/portal/{token_b}/chat", json={"text": "γεια"}).status_code == 200


def test_rate_limit_response_tells_the_client_when_to_retry(client):
    _, token = _firm_with_portal_client(client)
    for i in range(11):
        r = client.post(f"/api/portal/{token}/chat", json={"text": f"μ{i}"})
    assert r.status_code == 429
    assert 0 < int(r.headers["Retry-After"]) <= 60


def test_counter_resets_in_the_next_window(client):
    """Το όριο είναι παύση, όχι μόνιμος αποκλεισμός."""
    import rate_limit
    allowed, _ = _run(rate_limit.check("δοκιμή", "κλειδί", limit=1, window_seconds=1))
    assert allowed
    assert _run(rate_limit.check("δοκιμή", "κλειδί", limit=1, window_seconds=1))[0] is False
    # Επόμενο παράθυρο: νέο έγγραφο, μηδενισμένος μετρητής.
    import time
    time.sleep(1.1)
    assert _run(rate_limit.check("δοκιμή", "κλειδί", limit=1, window_seconds=1))[0] is True


def test_rate_limit_key_never_contains_the_token():
    """Το κλειδί καταλήγει στη βάση· το token είναι διαπιστευτήριο."""
    from rate_limit import key_for_token
    token = "μυστικό-token-πελάτη-abcdef123456"
    assert token not in key_for_token(token)
    assert key_for_token(token) == key_for_token(token)


def test_database_failure_does_not_lock_clients_out(client, monkeypatch):
    """Ο περιορισμός ρυθμού δεν είναι έλεγχος πρόσβασης: αν σπάσει, ανοίγει."""
    import rate_limit

    _, token = _firm_with_portal_client(client)

    async def boom(*a, **kw):
        raise RuntimeError("έπεσε ο μετρητής")

    monkeypatch.setattr(rate_limit.db.rate_limits, "find_one_and_update", boom)
    assert client.get(f"/api/portal/{token}").status_code == 200


# -------------------------------------------------------- ελεγκτικό ίχνος ---

def test_opening_the_file_leaves_a_trail_without_the_token(client):
    """Ο φάκελος βγήκε από το γραφείο — πρέπει να υπάρχει γραμμή που να το λέει."""
    from db import db

    cid, token = _firm_with_portal_client(client, "admin@example.gr")
    _run(db.users.update_one({"email": "admin@example.gr"}, {"$set": {"role": "admin"}}))
    client.get(f"/api/portal/{token}")

    rows = client.get("/api/audit", params={"entity": "client_portal"}).json()["items"]
    reads = [r for r in rows if r["action"] == "read:client_portal"]
    assert len(reads) == 1
    assert reads[0]["actor_type"] == "portal_client"
    assert reads[0]["entity_id"] == cid
    assert reads[0]["user_email"] == ""
    assert token not in str(reads[0]), "το token δεν επιτρέπεται να μπει στο ίχνος"
    assert reads[0]["path"] == "/api/portal/<token>"


def test_token_is_redacted_from_every_recorded_path(client):
    """Το token ταξιδεύει μέσα στο URL. Χωρίς απόκρυψη, το ίχνος γινόταν κατάλογος
    ενεργών συνδέσμων — και ο διαχειριστής που το διάβαζε άνοιγε κάθε φάκελο πελάτη."""
    from db import db

    _, token = _firm_with_portal_client(client)
    client.get(f"/api/portal/{token}")
    client.post(f"/api/portal/{token}/chat", json={"text": "Καλησπέρα"})
    client.get(f"/api/portal/{token}/chat")

    rows = _run(db.audit_logs.find({}).to_list(500))
    assert rows
    assert token not in str(rows)
    portal_paths = {r["path"] for r in rows if "/portal/" in (r.get("path") or "")}
    assert portal_paths == {"/api/portal/<token>", "/api/portal/<token>/chat"}, portal_paths


def test_chat_updates_last_used_at(client):
    """Το διπλό _validate_token δεν ενημέρωνε το «τελευταία χρήση»: ο δικηγόρος έβλεπε
    ως αχρησιμοποίητο έναν σύνδεσμο που ο πελάτης χρησιμοποιούσε καθημερινά."""
    cid, token = _firm_with_portal_client(client)
    assert client.get(f"/api/client-portal/tokens/{cid}").json()[0]["last_used_at"] is None

    client.post(f"/api/portal/{token}/chat", json={"text": "Καλησπέρα σας"})
    assert client.get(f"/api/client-portal/tokens/{cid}").json()[0]["last_used_at"] is not None


def test_one_validate_token_implementation_remains():
    """Δύο αντίγραφα είχαν ήδη αποκλίνει μία φορά."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    defs = [p.name for p in root.glob("routes_*.py")
            if "async def _validate_token" in p.read_text(encoding="utf-8")]
    assert defs == ["routes_client_portal.py"], defs
