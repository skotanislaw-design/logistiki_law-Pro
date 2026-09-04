"""Ασφάλεια: κρυπτογράφηση μυστικών «σε ηρεμία» και ταυτοποίηση δύο παραγόντων."""
import time

import pytest
from fastapi.testclient import TestClient

import crypto
import mfa


# ------------------------------------------------------------- encryption ---

def test_encrypt_roundtrip_and_prefix():
    token = crypto.encrypt("app-password-123")
    assert token.startswith(crypto.PREFIX)
    assert "app-password-123" not in token          # δεν διαρρέει το plaintext
    assert crypto.decrypt(token) == "app-password-123"
    assert crypto.encrypt(token) == token            # δεν διπλοκρυπτογραφείται
    assert crypto.encrypt("") == "" and crypto.decrypt("") == ""


def test_legacy_plaintext_still_readable():
    """Υπάρχουσες εγκαταστάσεις έχουν plaintext μυστικά — δεν πρέπει να σπάσουν."""
    assert crypto.decrypt("legacy-plaintext") == "legacy-plaintext"
    assert crypto.is_encrypted("legacy-plaintext") is False


def test_wrong_key_returns_empty_not_crash(monkeypatch):
    token = crypto.encrypt("secret")
    monkeypatch.setenv("SECRETS_KEY", "a-completely-different-key")
    crypto.reset_cipher_cache()
    try:
        assert crypto.decrypt(token) == ""
    finally:
        monkeypatch.delenv("SECRETS_KEY", raising=False)
        crypto.reset_cipher_cache()


def test_settings_helpers_encrypt_mask_and_merge():
    data = {"smtp_password": "pw", "imap_password": "", "aade_subscription_key": "key", "afm": "123"}
    encrypted = crypto.encrypt_settings_secrets(dict(data))
    assert crypto.is_encrypted(encrypted["smtp_password"])
    assert crypto.is_encrypted(encrypted["aade_subscription_key"])
    assert encrypted["imap_password"] == ""           # κενό μένει κενό
    assert encrypted["afm"] == "123"                  # μη μυστικά ανέγγιχτα

    masked = crypto.mask_settings_secrets(encrypted)
    assert masked["smtp_password"] == crypto.MASK
    assert masked["imap_password"] == ""              # «δεν έχει ρυθμιστεί»
    assert masked["afm"] == "123"

    decrypted = crypto.decrypt_settings_secrets(encrypted)
    assert decrypted["smtp_password"] == "pw" and decrypted["aade_subscription_key"] == "key"

    # ο client στέλνει πίσω τη μάσκα → κρατάμε την αποθηκευμένη τιμή
    merged = crypto.merge_masked_secrets({"smtp_password": crypto.MASK, "aade_subscription_key": ""}, encrypted)
    assert merged["smtp_password"] == encrypted["smtp_password"]
    assert merged["aade_subscription_key"] == encrypted["aade_subscription_key"]
    # πραγματική νέα τιμή περνάει
    assert crypto.merge_masked_secrets({"smtp_password": "new"}, encrypted)["smtp_password"] == "new"


def test_consumers_decrypt_transparently():
    from email_utils import smtp_ready
    from mydata_client import _headers, mydata_ready
    settings = crypto.encrypt_settings_secrets(
        {"smtp_user": "a@b.gr", "smtp_password": "pw", "aade_user_id": "uid", "aade_subscription_key": "key"})
    assert smtp_ready(settings) and mydata_ready(settings)
    assert _headers(settings)["ocp-apim-subscription-key"] == "key"


# ------------------------------------------------------------------ TOTP ----

def test_totp_verifies_accepts_drift_and_blocks_replay():
    secret = mfa.generate_secret()
    now = time.time()
    code = mfa.totp_now(secret, now)
    ok, counter = mfa.verify_totp(secret, code, at=now)
    assert ok and counter == int(now // mfa.PERIOD)
    # ίδιος κωδικός δεύτερη φορά στο ίδιο βήμα → απορρίπτεται
    assert mfa.verify_totp(secret, code, at=now, used_counter=counter)[0] is False
    # ανοχή ±30″, όχι ±90″
    assert mfa.verify_totp(secret, mfa.totp_now(secret, now - 30), at=now)[0]
    assert mfa.verify_totp(secret, mfa.totp_now(secret, now + 30), at=now)[0]
    assert mfa.verify_totp(secret, mfa.totp_now(secret, now - 90), at=now)[0] is False


def test_totp_rejects_malformed_and_foreign_codes():
    secret = mfa.generate_secret()
    other = mfa.generate_secret()
    assert mfa.verify_totp(secret, "")[0] is False
    assert mfa.verify_totp(secret, "12345")[0] is False
    assert mfa.verify_totp(secret, "abcdef")[0] is False
    assert mfa.verify_totp("", "123456")[0] is False
    assert mfa.verify_totp(secret, mfa.totp_now(other))[0] is False


def test_provisioning_uri_is_scannable():
    secret = mfa.generate_secret()
    uri = mfa.provisioning_uri(secret, "user@example.gr")
    assert uri.startswith("otpauth://totp/")
    assert f"secret={secret}" in uri and "issuer=Nomos%20One" in uri
    assert "digits=6" in uri and "period=30" in uri
    assert mfa.format_secret(secret).replace(" ", "") == secret


def test_backup_codes_are_single_use_and_hashed():
    codes = mfa.generate_backup_codes()
    assert len(codes) == mfa.BACKUP_CODE_COUNT and all("-" in c for c in codes)
    hashes = mfa.hash_backup_codes(codes)
    assert all(not any(c in h for c in codes) for h in hashes)   # τα hashes δεν περιέχουν τον κωδικό
    ok, remaining = mfa.consume_backup_code(codes[0].lower(), hashes)   # ανεκτικό σε πεζά/παύλες
    assert ok and len(remaining) == len(hashes) - 1
    assert mfa.consume_backup_code(codes[0], remaining)[0] is False
    assert mfa.consume_backup_code("ΛΑΘΟΣ-ΚΩΔ", remaining)[0] is False
    assert mfa.consume_backup_code("", hashes)[0] is False


# ------------------------------------------------------------------ flow ----

@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _register(client, email="mfa@example.gr", password="Str0ngPass!"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "name": "User"})
    assert r.status_code == 200, r.text
    return email, password


def _enable_2fa(client):
    _, password = _register(client)
    setup = client.post("/api/auth/2fa/setup", json={"password": password}).json()
    enabled = client.post("/api/auth/2fa/enable", json={"code": mfa.totp_now(setup["secret"])}).json()
    return password, setup["secret"], enabled["backup_codes"]


def _next_code(secret):
    """Κωδικός του επόμενου χρονικού βήματος — κάθε OTP ισχύει μία μόνο φορά."""
    return mfa.totp_now(secret, time.time() + mfa.PERIOD)


def test_setup_requires_password_and_returns_uri(client):
    _, password = _register(client)
    assert client.post("/api/auth/2fa/setup", json={"password": "wrong"}).status_code == 401
    r = client.post("/api/auth/2fa/setup", json={"password": password})
    assert r.status_code == 200
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert client.get("/api/auth/2fa/status").json() == {
        "enabled": False, "pending_setup": True, "backup_codes_remaining": 0, "enabled_at": None}


def test_enable_rejects_wrong_code_and_stores_secret_encrypted(client):
    _, password = _register(client)
    client.post("/api/auth/2fa/setup", json={"password": password})
    assert client.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 401

    import asyncio
    from db import db
    doc = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        db.users.find_one({"email": "mfa@example.gr"}))
    assert crypto.is_encrypted(doc["mfa_pending_secret"]), "το μυστικό δεν αποθηκεύεται ποτέ σε καθαρή μορφή"


def test_login_challenges_for_second_factor_and_backup_code_works(client):
    password, secret, backup_codes = _enable_2fa(client)
    client.post("/api/auth/logout")

    r = client.post("/api/auth/login", json={"email": "mfa@example.gr", "password": password})
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and body["mfa_token"]
    # ο σωστός κωδικός μόνος του δεν δίνει συνεδρία
    assert client.get("/api/auth/me").status_code == 401

    assert client.post("/api/auth/2fa/verify", json={"mfa_token": body["mfa_token"], "code": "000000"}).status_code == 401
    # ο κωδικός που χρησιμοποιήθηκε στο enable δεν ξαναδουλεύει, με σαφές μήνυμα
    reused = client.post("/api/auth/2fa/verify", json={"mfa_token": body["mfa_token"], "code": mfa.totp_now(secret)})
    assert reused.status_code == 401 and "ήδη χρησιμοποιηθεί" in reused.json()["detail"]
    r = client.post("/api/auth/2fa/verify", json={"mfa_token": body["mfa_token"], "code": _next_code(secret)})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is True
    assert client.get("/api/auth/me").status_code == 200

    # εφεδρικός κωδικός: δουλεύει μία φορά
    client.post("/api/auth/logout")
    token = client.post("/api/auth/login", json={"email": "mfa@example.gr", "password": password}).json()["mfa_token"]
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": token, "code": backup_codes[0]}).status_code == 200
    assert client.get("/api/auth/2fa/status").json()["backup_codes_remaining"] == len(backup_codes) - 1

    client.post("/api/auth/logout")
    token = client.post("/api/auth/login", json={"email": "mfa@example.gr", "password": password}).json()["mfa_token"]
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": token, "code": backup_codes[0]}).status_code == 401


def test_mfa_token_is_not_a_session_and_disable_requires_both_factors(client):
    password, secret, backup_codes = _enable_2fa(client)
    client.post("/api/auth/logout")
    token = client.post("/api/auth/login", json={"email": "mfa@example.gr", "password": password}).json()["mfa_token"]
    # το mfa_token δεν λειτουργεί ως access token
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": "garbage", "code": "123456"}).status_code == 401

    # σύνδεση με εφεδρικό κωδικό: αφήνει το TOTP counter ανέγγιχτο για το επόμενο βήμα
    assert client.post("/api/auth/2fa/verify", json={"mfa_token": token, "code": backup_codes[0]}).status_code == 200
    assert client.post("/api/auth/2fa/disable", json={"password": "wrong", "code": _next_code(secret)}).status_code == 401
    assert client.post("/api/auth/2fa/disable", json={"password": password, "code": "000000"}).status_code == 401
    assert client.post("/api/auth/2fa/disable", json={"password": password, "code": _next_code(secret)}).status_code == 200
    assert client.get("/api/auth/2fa/status").json()["enabled"] is False
    # μετά την απενεργοποίηση το login δίνει κατευθείαν συνεδρία
    client.post("/api/auth/logout")
    assert "mfa_required" not in client.post("/api/auth/login", json={"email": "mfa@example.gr", "password": password}).json()


def test_settings_never_return_secrets_to_the_client(client):
    _, password = _register(client, email="admin2@example.gr")
    import asyncio
    from db import db
    loop = asyncio.get_event_loop_policy().new_event_loop()
    loop.run_until_complete(db.users.update_one({"email": "admin2@example.gr"}, {"$set": {"role": "admin"}}))

    r = client.patch("/api/settings", json={"smtp_user": "office@example.gr", "smtp_password": "app-secret",
                                            "aade_subscription_key": "aade-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["smtp_password"] == crypto.MASK and body["aade_subscription_key"] == crypto.MASK
    assert body["mydata_connected"] is False   # χωρίς aade_user_id

    stored = loop.run_until_complete(db.settings.find_one({}))
    assert crypto.is_encrypted(stored["smtp_password"]), "στη βάση πρέπει να είναι κρυπτογραφημένο"
    assert "app-secret" not in str(stored)

    # δεύτερη αποθήκευση με τη μάσκα δεν σβήνει το μυστικό
    client.patch("/api/settings", json={"smtp_from_name": "Γραφείο"})
    again = loop.run_until_complete(db.settings.find_one({}))
    assert crypto.decrypt(again["smtp_password"]) == "app-secret"
