"""Γύρος 5 (δεύτερος έλεγχος, ελεγκτής D, εύρημα D-04) — ο nginx του έργου ΣΕ ΛΕΙΤΟΥΡΓΙΑ.

Το `test_unit_deploy_r5.py` διαβάζει τα αρχεία ανάπτυξης· δεν μπορεί να αποδείξει τι κάνουν ο nginx και το uvicorn μαζί. Εδώ
τρέχει ο πραγματικός nginx με το πραγματικό `frontend/nginx.conf` (μόνο η διεύθυνση, η θύρα και ο φάκελος αρχείων
αντικαθίστανται) μπροστά από ένα backend-ηχώ που ξεκινά με ΤΙΣ ΙΔΙΕΣ παραμέτρους uvicorn με το `backend/Dockerfile`, και ελέγχεται η
συμπεριφορά που οι υπόλοιποι έλεγχοι μόνο υποθέτουν:

* η IP του πελάτη που βλέπει το backend είναι η πραγματική, ακόμη κι αν ο πελάτης στείλει δική του `X-Forwarded-For`
  (το όριο ρυθμού της πύλης, τα κλειδώματα σύνδεσης και η IP στο ελεγκτικό ίχνος και στις ψηφιακές υπογραφές στηρίζονται σε αυτήν)·
* κεφαλίδες ασφαλείας σε κάθε είδος απάντησης, ο service worker χωρίς κρυφή μνήμη, οι στατικοί πόροι με μακρά·
* η πύλη πελάτη σερβίρεται ως σελίδα, το `/api/` προωθείται, τα όρια σώματος είναι αυτά που δηλώνονται·
* το access log δεν περιέχει το token της πύλης ούτε Referer, και ΑΝΤΙΚΑΘΙΣΤΑ το προεπιλεγμένο log του nginx (όπως στην επίσημη εικόνα).

Απαιτεί `nginx` (στο CI εγκαθίσταται από το workflow) και τη διεύθυνση 127.0.0.2 (loopback στο Linux)· αλλιώς τα tests παραλείπονται.
Η πρώτη εκδοχή του ελέγχου της υποδομής περνούσε και με λάθος ανάπτυξη: 12 από τις 21 μεταλλάξεις του ελεγκτή επέζησαν — εδώ οι
ίδιες μεταλλάξεις αποτυγχάνουν (βλ. docs/AUDIT.md, γύρος 5).
"""
import http.client
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

import deploy_files as df

NGINX = shutil.which("nginx") or next(
    (p for p in ("/usr/sbin/nginx", "/usr/local/sbin/nginx", "/opt/homebrew/bin/nginx") if os.access(p, os.X_OK)), None)

# Στο CI (REQUIRE_NGINX_LAB=1) η απουσία nginx ή της διεύθυνσης 127.0.0.2 είναι αποτυχία, όχι παράλειψη: αλλιώς ο έλεγχος θα
# σταματούσε να τρέχει χωρίς να το πάρει κανείς είδηση.
REQUIRED = os.environ.get("REQUIRE_NGINX_LAB") == "1"

pytestmark = pytest.mark.skipif(NGINX is None and not REQUIRED, reason="δεν υπάρχει nginx σε αυτό το μηχάνημα")

CLIENT_ADDRESS = "127.0.0.2"             # ο «πελάτης»: διαφορετική από 127.0.0.1, που είναι η διεύθυνση του nginx προς το backend
TOKEN = "SECRETportalTOKEN0123456789abcdef"

ECHO_APP = '''
import json
from urllib.parse import parse_qs


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    received, more = 0, True
    while more:
        message = await receive()
        received += len(message.get("body", b""))
        more = message.get("more_body", False)

    def header(name):
        return next((v.decode("latin-1") for k, v in scope["headers"] if k == name), "")

    payload = json.dumps({
        "client_host": (scope.get("client") or [None])[0], "path": scope["path"], "method": scope["method"],
        "scheme": scope.get("scheme"), "host": header(b"host"), "forwarded_for": header(b"x-forwarded-for"),
        "real_ip": header(b"x-real-ip"), "forwarded_proto": header(b"x-forwarded-proto"), "received_bytes": received,
    }).encode()
    status = int(parse_qs(scope.get("query_string", b"").decode()).get("status", ["200"])[0])        # ?status=404 → απάντηση σφάλματος
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})
'''

INDEX_HTML = "<!doctype html><title>SPA-MARKER</title>"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(port: int, process: subprocess.Popen, what: str, log):
    deadline = time.time() + 20
    while time.time() < deadline:
        if process.poll() is not None:
            pytest.fail(f"{what} τερμάτισε στην εκκίνηση (κωδικός {process.returncode}):\n{log()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    pytest.fail(f"{what} δεν άνοιξε τη θύρα {port} σε 20″:\n{log()}")


def production_uvicorn_flags() -> list[str]:
    """Οι παράμετροι του uvicorn όπως τις δηλώνει το Dockerfile, χωρίς όσες αφορούν τη θέση (host, θύρα, workers, εφαρμογή)."""
    flags, skip = [], False
    for token in df.dockerfile_cmd()[1:]:
        if skip:
            skip = False
        elif token == "server:app":
            continue
        elif token in ("--host", "--port", "--workers"):
            skip = True
        else:
            flags.append(token)
    return flags


def render(text: str, *, port: int, root: str, backend_port: int, logs: str) -> str:
    """Το `frontend/nginx.conf` του έργου σε σύνθεση που τρέχει εδώ: αλλάζουν μόνο διεύθυνση, φάκελος αρχείων και αρχείο log."""
    import re
    out, n = re.subn(r"^(\s*)listen\s+80;", rf"\1listen 127.0.0.1:{port};", text, flags=re.M)
    assert n == 1, "το nginx.conf άλλαξε μορφή (listen): προσαρμόστε το render() του lab"
    out, n = re.subn(r"^(\s*)root\s+/usr/share/nginx/html;", rf"\1root {root};", out, flags=re.M)
    assert n == 1, "το nginx.conf άλλαξε μορφή (root): προσαρμόστε το render() του lab"
    out, n = re.subn(r"proxy_pass\s+http://backend:\d+;", f"proxy_pass http://127.0.0.1:{backend_port};", out)
    assert n >= 2, "το nginx.conf άλλαξε μορφή (proxy_pass): προσαρμόστε το render() του lab"
    out, n = re.subn(r"access_log\s+/var/log/nginx/access\.log\s+(\w+);", rf"access_log {logs}/redacted.log \1;", out)
    assert n >= 1, "το nginx.conf δεν ορίζει access_log στον server"
    return out


class Lab:
    def __init__(self, port: int, tmp):
        self.port, self.tmp = port, tmp

    def request(self, method: str, path: str, *, headers: dict | None = None, body: bytes | None = None,
                content_length: int | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, source_address=(CLIENT_ADDRESS, 0), timeout=30)
        try:
            headers = dict(headers or {})
            if content_length is not None:                  # δηλώνει μεγάλο σώμα χωρίς να το στείλει (ο nginx απαντά πριν το διαβάσει)
                conn.putrequest(method, path, skip_host="Host" in headers)
                for key, value in headers.items():
                    conn.putheader(key, value)
                conn.putheader("Content-Length", str(content_length))
                conn.endheaders()
            else:
                conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            conn.close()

    def echo(self, path: str, **kwargs):
        import json
        status, headers, body = self.request(kwargs.pop("method", "GET"), path, **kwargs)
        assert status == 200, (path, status, body[:200])
        return json.loads(body)

    def log(self, name: str) -> str:
        path = self.tmp / "logs" / name
        return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    if NGINX is None:
        pytest.fail("REQUIRE_NGINX_LAB=1 αλλά δεν βρέθηκε nginx (εγκαταστήστε το πακέτο nginx ή nginx-light)")
    try:
        with socket.socket() as probe:
            probe.bind((CLIENT_ADDRESS, 0))
    except OSError:
        message = f"η διεύθυνση {CLIENT_ADDRESS} δεν είναι διαθέσιμη (απαιτεί loopback 127.0.0.0/8)"
        pytest.fail(message) if REQUIRED else pytest.skip(message)

    tmp = tmp_path_factory.mktemp("nginx_lab")
    html, logs, scratch = tmp / "html", tmp / "logs", tmp / "scratch"
    for folder in (html / "static" / "js", logs, scratch):
        folder.mkdir(parents=True)
    (html / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (html / "static" / "js" / "main.js").write_text("console.log(1)", encoding="utf-8")
    (html / "favicon.ico").write_bytes(b"\x00")
    (html / "service-worker.js").write_text("self.skipWaiting()", encoding="utf-8")
    (tmp / "echo_app.py").write_text(ECHO_APP, encoding="utf-8")

    backend_port, nginx_port = _free_port(), _free_port()
    backend_log = open(tmp / "backend.log", "wb")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "echo_app:app", "--host", "127.0.0.1", "--port", str(backend_port),
         "--log-level", "warning", *production_uvicorn_flags()],
        cwd=tmp, stdout=backend_log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(tmp)})
    server_block = render(df.NGINX_CONF.read_text(encoding="utf-8"), port=nginx_port, root=str(html),
                          backend_port=backend_port, logs=str(logs))
    # Το περιβάλλον της επίσημης εικόνας nginx: ο http ορίζει δικό του (μη προστατευμένο) access log, που ο server πρέπει να αντικαταστήσει.
    wrapper = f"""{'user root;' if os.geteuid() == 0 else ''}
daemon off;
pid {scratch}/nginx.pid;
error_log {logs}/error.log notice;
worker_processes 1;
events {{ worker_connections 128; }}
http {{
    types {{ text/html html; text/css css; application/javascript js; image/svg+xml svg; image/x-icon ico; application/json json; }}
    default_type application/octet-stream;
    # Ο nginx του docker-compose συνδέεται στο backend από άλλη διεύθυνση (172.x) και όχι από το 127.0.0.1, που το uvicorn
    # εμπιστεύεται από προεπιλογή· εδώ το ίδιο αποτέλεσμα με πηγή 127.0.0.5. Χωρίς αυτό, η απουσία του `--forwarded-allow-ips`
    # δεν θα φαινόταν (όλα θα δούλευαν επειδή και οι δύο είναι στο 127.0.0.1).
    proxy_bind 127.0.0.5;
    client_body_temp_path {scratch}/body; proxy_temp_path {scratch}/proxy; fastcgi_temp_path {scratch}/fcgi;
    uwsgi_temp_path {scratch}/uwsgi; scgi_temp_path {scratch}/scgi;
    log_format main '$remote_addr "$request" $status "$http_referer"';
    access_log {logs}/main.log main;
{server_block}
}}
"""
    (tmp / "nginx.conf").write_text(wrapper, encoding="utf-8")
    nginx = subprocess.Popen([NGINX, "-c", str(tmp / "nginx.conf"), "-p", str(scratch)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    def nginx_diagnostics() -> str:
        parts = []
        if nginx.poll() is not None and nginx.stdout:
            parts.append(nginx.stdout.read().decode(errors="replace"))
        if (logs / "error.log").exists():
            parts.append((logs / "error.log").read_text(errors="replace"))
        return "\n".join(parts)

    try:
        _wait_for(backend_port, backend, "το backend-ηχώ", lambda: (tmp / "backend.log").read_text(errors="replace"))
        _wait_for(nginx_port, nginx, "ο nginx", nginx_diagnostics)
        yield Lab(nginx_port, tmp)
    finally:
        for process in (nginx, backend):
            process.terminate()
        for process in (nginx, backend):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        backend_log.close()


# ------------------------------------------------------------------------------------------- η IP του πελάτη ---

PROXIED = ["/api/clients", "/api/portal/abc", "/api/backup/import", "/api/stripe/webhook", "/api/health", "/api/export/report.png",
           "/api/files/script.js"]


@pytest.mark.parametrize("path", PROXIED)
def test_the_backend_sees_the_real_client_address(lab, path):
    seen = lab.echo(path)
    assert seen["client_host"] == CLIENT_ADDRESS, "το backend δεν διάβασε την X-Forwarded-For (flags του Dockerfile) ή είδε τον nginx"
    assert seen["path"] == path


@pytest.mark.parametrize("spoof", ["203.0.113.99", "6.6.6.6, 7.7.7.7", "::1", "127.0.0.1", "unknown"])
@pytest.mark.parametrize("path", PROXIED)
def test_a_forged_forwarded_for_never_reaches_the_backend(lab, path, spoof):
    """Το uvicorn με `--forwarded-allow-ips "*"` παίρνει το ΑΡΙΣΤΕΡΟ στοιχείο της κεφαλίδας: αν ο nginx την προσέθετε στο τέλος
    αντί να την ξαναγράφει, η «IP» του πελάτη θα ήταν αυτή που έγραψε ο ίδιος."""
    seen = lab.echo(path, headers={"X-Forwarded-For": spoof, "X-Real-IP": spoof, "Forwarded": f"for={spoof}"})
    assert seen["client_host"] == CLIENT_ADDRESS
    assert seen["forwarded_for"] == CLIENT_ADDRESS and seen["real_ip"] == CLIENT_ADDRESS


def test_the_host_and_the_scheme_are_forwarded(lab):
    seen = lab.echo("/api/clients", headers={"Host": "office.example.gr", "X-Forwarded-Proto": "https"})
    assert seen["host"] == "office.example.gr"
    assert seen["forwarded_proto"] == "http"             # ο nginx το ξαναγράφει με το σχήμα της σύνδεσής του (εδώ http)


# ------------------------------------------------------------------------------------------------- κεφαλίδες ---

PAGES = ["/", "/dashboard", "/portal/abc", "/portal/abc/payment/success", "/static/js/main.js", "/favicon.ico", "/service-worker.js",
         "/api/clients", "/api/portal/abc", "/api/backup/import", "/static/js/missing.js"]


@pytest.mark.parametrize("path", PAGES)
def test_security_headers_are_on_every_response(lab, path):
    status, headers, _ = lab.request("GET", path)
    assert status in (200, 404), (path, status)
    assert headers.get("x-content-type-options") == "nosniff", (path, headers)
    assert headers.get("x-frame-options") == "DENY", (path, headers)
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", (path, headers)


@pytest.mark.parametrize("status", [404, 500, 502])
def test_security_headers_are_on_error_responses_too(lab, status):
    """Χωρίς `always` ο nginx προσθέτει κεφαλίδες μόνο σε 2xx/3xx· μια απάντηση σφάλματος του backend θα τις έχανε."""
    code, headers, _ = lab.request("GET", f"/api/clients?status={status}")
    assert code == status
    assert headers.get("x-content-type-options") == "nosniff" and headers.get("x-frame-options") == "DENY", headers
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", headers


def test_the_service_worker_is_not_cacheable_and_static_files_are(lab):
    _, worker, _ = lab.request("GET", "/service-worker.js")
    assert "no-store" in worker["cache-control"] and "no-cache" in worker["cache-control"], worker
    assert "immutable" not in worker["cache-control"] and "expires" not in worker, worker
    _, asset, _ = lab.request("GET", "/static/js/main.js")
    assert "immutable" in asset["cache-control"] and "expires" in asset, asset


def test_the_spa_page_itself_is_not_marked_immutable(lab):
    for path in ("/", "/dashboard"):
        _, headers, body = lab.request("GET", path)
        assert INDEX_HTML.encode() == body
        assert "immutable" not in headers.get("cache-control", ""), (path, headers)


# --------------------------------------------------------------------------------------------- η πύλη και το API ---

@pytest.mark.parametrize("path", ["/portal/abcDEF123_-xyz", "/portal/abc/payment/success", "/portal/", "/portal"])
def test_the_portal_link_opens_the_page_not_json(lab, path):
    status, headers, body = lab.request("GET", path)
    if status in (301, 302):                              # το `/portal` χωρίς κάθετο: ο nginx το οδηγεί στη σελίδα
        status, headers, body = lab.request("GET", headers["location"].split(str(lab.port), 1)[-1])
    assert status == 200 and body == INDEX_HTML.encode() and headers["content-type"].startswith("text/html"), (path, status)


def test_unknown_application_routes_fall_back_to_the_spa(lab):
    status, _, body = lab.request("GET", "/invoices/some/deep/link")
    assert status == 200 and body == INDEX_HTML.encode()


# ------------------------------------------------------------------------------------------------- όρια σώματος ---

def test_an_ordinary_request_body_is_limited_to_twenty_megabytes(lab):
    mb = 1024 * 1024
    assert lab.echo("/api/clients", method="POST", body=b"x" * (19 * mb))["received_bytes"] == 19 * mb
    status, headers, _ = lab.request("POST", "/api/clients", body=b"x" * (21 * mb))
    assert status == 413
    assert headers.get("x-frame-options") == "DENY" and headers.get("x-content-type-options") == "nosniff", headers


def test_backups_may_be_larger_up_to_the_backend_limit(lab):
    from routes_backup import MAX_UPLOAD_BYTES
    mb = 1024 * 1024
    assert lab.echo("/api/backup/import", method="POST", body=b"x" * (21 * mb))["received_bytes"] == 21 * mb
    status, _, _ = lab.request("POST", "/api/backup/import", content_length=MAX_UPLOAD_BYTES + 20 * mb)
    assert status == 413


# ----------------------------------------------------------------------------------------------------- access log ---

def test_the_access_log_does_not_contain_portal_tokens_or_referers(lab):
    lab.request("GET", f"/portal/{TOKEN}", headers={"Referer": f"https://office.example.gr/portal/{TOKEN}"})
    lab.request("GET", f"/portal/{TOKEN}/payment/success?session_id=cs_test_123")
    lab.echo(f"/api/portal/{TOKEN}/chat?before=2026-10-01", headers={"Referer": f"https://office.example.gr/portal/{TOKEN}"})
    lab.request("GET", f"/api/portal/{TOKEN}/invoice/5/pdf")
    lab.request("GET", "/reset-password?token=RESETsecret987654321")
    lab.request("GET", "/api/clients", headers={"User-Agent": "lab-agent/1.0"})
    time.sleep(0.5)                                       # ο nginx γράφει το log μετά την απάντηση

    redacted, inherited = lab.log("redacted.log"), lab.log("main.log")
    assert "/portal/[token]" in redacted and "/api/portal/[token]/chat" in redacted and "/api/portal/[token]/invoice/5/pdf" in redacted, redacted
    assert "/payment/success" in redacted and "/api/clients" in redacted and "lab-agent/1.0" in redacted, redacted
    for secret in (TOKEN, "RESETsecret987654321", "session_id", "cs_test_123", "before=2026"):
        assert secret not in redacted, f"{secret!r} στο access log:\n{redacted}"
    assert "http_referer" not in redacted and "office.example.gr" not in redacted
    assert inherited == "", "το προεπιλεγμένο (μη προστατευμένο) access log του http συνεχίζει να γράφει:\n" + inherited
