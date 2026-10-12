"""Γύρος 5 (ανασκόπηση 4) — υποδομή: nginx, docker-compose, IP πελάτη.

Τι διορθώνεται εδώ και γιατί δεν ήταν ορατό από τα υπόλοιπα tests:

**P1 — η IP του πελάτη ήταν πλαστογραφήσιμη.** Το backend τρέχει με ``--proxy-headers --forwarded-allow-ips "*"`` και
το uvicorn, με ``*``, παίρνει ως διεύθυνση πελάτη το **αριστερότερο** στοιχείο του ``X-Forwarded-For``. Ο nginx έστελνε
``$proxy_add_x_forwarded_for``, που *προσθέτει* στο τέλος ό,τι έστειλε ο πελάτης· αρκούσε ένα αίτημα με
``X-Forwarded-For: 1.2.3.4`` και η «IP» του ήταν η 1.2.3.4. Με μια νέα τιμή σε κάθε αίτημα ακυρώνονταν το όριο ανά IP της
πύλης, τα κλειδιά κλειδώματος σύνδεσης/2FA (``<ip>:<email>``) και οι IP στο ελεγκτικό ίχνος και στις υπογραφές.

**P3 — η πρόσκληση πελάτη (magic link) έδινε JSON αντί για σελίδα.** Ο nginx προωθούσε κάθε ``/portal/…`` στο backend
(``/api/portal/…``). Ο σύνδεσμος του email ``https://<γραφείο>/portal/<token>`` ανοίγει σε browser ως πλοήγηση, όχι ως
κλήση του frontend· ο πελάτης έβλεπε JSON. Η σελίδα της πύλης δεν μπορούσε να φορτωθεί σε εγκατάσταση με Docker.

**Εισαγωγή αντιγράφου:** το όριο σώματος του nginx ήταν 20 MB ενώ το backend δέχεται έως 100 MB (``MAX_UPLOAD_BYTES``)·
μεγαλύτερο αντίγραφο απορριπτόταν από τον nginx με σελίδα HTML, πριν φτάσει στον κώδικα που εξηγεί το λάθος.

Ο ``nginx`` δεν είναι διαθέσιμος στο CI, οπότε το αρχείο ρυθμίσεων αναλύεται εδώ (μικρός αναλυτής, χωρίς νέα
εξάρτηση) και οι θέσεις (``location``) επιλέγονται με τον αλγόριθμο του ίδιου του nginx: ακριβές, μακρύτερο πρόθεμα,
κανονική έκφραση, ``^~``. Έτσι ελέγχεται τι πραγματικά θα συναντήσει ένα συγκεκριμένο αίτημα, όχι απλώς τι γράφει το αρχείο.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "backend" / "Dockerfile"


# ---------------------------------------------------------------- μικρός αναλυτής nginx ---

def _tokens(text: str) -> list[str]:
    text = re.sub(r"#[^\n]*", "", text)
    return re.findall(r'"[^"]*"|\'[^\']*\'|[{};]|[^\s{};]+', text)


def _parse(tokens: list[str], i: int = 0) -> tuple[list[dict], int]:
    out: list[dict] = []
    while i < len(tokens):
        if tokens[i] == "}":
            return out, i + 1
        name, args = tokens[i], []
        i += 1
        while tokens[i] not in (";", "{"):
            args.append(tokens[i])
            i += 1
        if tokens[i] == ";":
            out.append({"name": name, "args": args, "children": None})
            i += 1
        else:
            children, i = _parse(tokens, i + 1)
            out.append({"name": name, "args": args, "children": children})
    return out, i


def _server() -> dict:
    tree, _ = _parse(_tokens(NGINX_CONF.read_text(encoding="utf-8")))
    servers = [d for d in tree if d["name"] == "server"]
    assert len(servers) == 1
    return servers[0]


def _locations() -> list[dict]:
    found = []
    for d in _server()["children"]:
        if d["name"] == "location":
            args = d["args"]
            modifier, uri = ("", args[0]) if len(args) == 1 else (args[0], args[1])
            found.append({**d, "modifier": modifier, "uri": uri})
    return found


def _location_for(path: str) -> dict:
    """Η θέση που επιλέγει ο nginx για ένα αίτημα (ακριβές → μακρύτερο πρόθεμα → regex → πρόθεμα)."""
    locations = _locations()
    for loc in locations:
        if loc["modifier"] == "=" and loc["uri"] == path:
            return loc
    prefixes = [loc for loc in locations if loc["modifier"] in ("", "^~") and path.startswith(loc["uri"])]
    longest = max(prefixes, key=lambda loc: len(loc["uri"]), default=None)
    if longest is not None and longest["modifier"] == "^~":
        return longest
    for loc in locations:
        if loc["modifier"] in ("~", "~*"):
            flags = re.IGNORECASE if loc["modifier"] == "~*" else 0
            if re.search(loc["uri"], path, flags):
                return loc
    assert longest is not None, f"καμία θέση για {path}"
    return longest


def _directive(loc: dict, name: str) -> list[list[str]]:
    return [c["args"] for c in loc["children"] if c["name"] == name]


def _size(value: str) -> int:
    m = re.fullmatch(r"(\d+)([kKmMgG]?)", value)
    assert m, value
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]


def _seconds(value: str) -> int:
    m = re.fullmatch(r"(\d+)([smh]?)", value)
    assert m, value
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


# ---------------------------------------------------------------------- P1: X-Forwarded-For ---

def test_the_parser_reads_the_real_config():
    """Αν ο αναλυτής δεν βλέπει τις θέσεις, όλα τα παρακάτω θα περνούσαν χωρίς να ελέγχουν τίποτα."""
    uris = [loc["uri"] for loc in _locations()]
    assert "/api/" in uris or any(u.startswith("/api") for u in uris)
    assert "/" in uris


def test_forwarded_for_is_overwritten_with_the_connecting_address():
    """`$proxy_add_x_forwarded_for` κρατά ό,τι έστειλε ο πελάτης και προσθέτει τη δική του διεύθυνση στο τέλος· το uvicorn
    με `--forwarded-allow-ips "*"` διαβάζει το ΑΡΙΣΤΕΡΟ στοιχείο, δηλαδή του πελάτη."""
    proxied = [loc for loc in _locations() if _directive(loc, "proxy_pass")]
    assert proxied, "δεν βρέθηκε καμία θέση με proxy_pass"
    for loc in proxied:
        values = [args[1] for args in _directive(loc, "proxy_set_header") if args[0].lower() == "x-forwarded-for"]
        assert values == ["$remote_addr"], f"{loc['uri']}: X-Forwarded-For = {values}"
    assert "$proxy_add_x_forwarded_for" not in re.sub(r"#[^\n]*", "", NGINX_CONF.read_text(encoding="utf-8"))


def test_every_proxied_location_forwards_the_headers_the_backend_relies_on():
    """Ένα `location` με δικό του `proxy_set_header` δεν κληρονομεί τα του `server`: κάθε θέση τα δηλώνει ρητά."""
    for loc in (loc for loc in _locations() if _directive(loc, "proxy_pass")):
        names = {args[0].lower() for args in _directive(loc, "proxy_set_header")}
        assert {"host", "x-real-ip", "x-forwarded-for", "x-forwarded-proto"} <= names, (loc["uri"], names)


def test_the_backend_is_not_reachable_except_through_nginx():
    """Με `--forwarded-allow-ips "*"` το backend εμπιστεύεται την κεφαλίδα από οποιονδήποτε τον φτάσει· ασφαλές μόνο
    αν η μοναδική είσοδος είναι ο nginx (που την ξαναγράφει)."""
    services = _compose_services()
    assert {"backend", "worker", "frontend", "mongo"} <= set(services)
    assert "ports" not in services["backend"], "το backend δεν δημοσιεύεται: μόνο ο nginx το φτάνει"
    assert "ports" not in services["worker"]
    published = sorted(name for name, keys in services.items() if "ports" in keys)
    assert published == ["frontend", "mongo"]
    assert all(p.startswith("127.0.0.1:") for p in services["mongo"]["ports"]), "η βάση μόνο τοπικά"
    assert all(not p.startswith("127.0.0.1:") for p in services["frontend"]["ports"])


def test_the_dockerfile_still_trusts_forwarded_headers_only_under_the_invariants_above():
    """Αν κάποτε αλλάξει το `*` (π.χ. σε συγκεκριμένη διεύθυνση), οι δύο προϋποθέσεις (backend μη δημοσιευμένο,
    nginx που ξαναγράφει την κεφαλίδα) μπορούν να χαλαρώσουν — μέχρι τότε πρέπει να ισχύουν και οι δύο."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--proxy-headers" in text
    if '"--forwarded-allow-ips", "*"' in text:
        test_forwarded_for_is_overwritten_with_the_connecting_address()
        test_the_backend_is_not_reachable_except_through_nginx()


def test_uvicorn_takes_the_leftmost_entry_which_is_why_the_header_must_be_overwritten():
    """Χαρακτηρισμός της συμπεριφοράς του uvicorn (0.25): αν αλλάξει σε μελλοντική έκδοση, ο έλεγχος αυτός θα
    σπάσει και θα υποχρεώσει σε επανεξέταση της παραπάνω ρύθμισης αντί να αλλάξει σιωπηλά η ασφάλεια."""
    import asyncio

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["client"][0])

    def call(forwarded_for: str):
        scope = {"type": "http", "client": ("172.18.0.5", 40000),
                 "headers": [(b"x-forwarded-for", forwarded_for.encode())]}
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            ProxyHeadersMiddleware(app, trusted_hosts="*")(scope, None, None))

    call("203.0.113.7")                     # όπως τη στέλνει ο διορθωμένος nginx: μόνο η διεύθυνση σύνδεσης
    call("6.6.6.6, 203.0.113.7")            # όπως τη στέλνει ο παλιός: ό,τι έγραψε ο πελάτης + η πραγματική
    assert seen == ["203.0.113.7", "6.6.6.6"]


# ------------------------------------------------------------------- P3: η σελίδα της πύλης ---

@pytest.mark.parametrize("path", [
    "/portal/abcDEF123_-xyz", "/portal/abcDEF123_-xyz/", "/portal/abc/payment/success", "/portal/", "/portal",
])
def test_the_portal_page_is_served_by_the_spa_not_proxied_to_the_api(path):
    loc = _location_for(path)
    assert not _directive(loc, "proxy_pass"), f"{path} → {loc['uri']} προωθείται στο backend"
    assert any("/index.html" in args for args in _directive(loc, "try_files")), f"{path} → {loc['uri']}"


def test_no_location_rewrites_portal_urls_to_the_api():
    for loc in _locations():
        for args in _directive(loc, "proxy_pass"):
            assert "/portal" not in args[0], f"{loc['uri']} → {args[0]}"


@pytest.mark.parametrize("path", [
    "/api/portal/abc", "/api/portal/abc/invoice/1/pdf", "/api/payments/portal-checkout", "/api/stripe/webhook",
    "/api/clients", "/api/backup/export", "/api/backup/import",
])
def test_api_paths_reach_the_backend(path):
    assert _directive(_location_for(path), "proxy_pass") == [["http://backend:8001"]]


@pytest.mark.parametrize("path", ["/api/export/report.png", "/api/files/x.js", "/api/styles.css", "/api/logo.svg"])
def test_the_static_asset_rule_never_takes_an_api_path(path):
    """Μια κανονική έκφραση (`\\.(css|js|png…)$`) υπερισχύει του απλού προθέματος `/api/`· χωρίς `^~` θα έπαιρνε τέτοιες
    κλήσεις του API και θα απαντούσε 404 από το δίσκο του nginx."""
    loc = _location_for(path)
    assert loc["modifier"] == "^~" and _directive(loc, "proxy_pass"), (path, loc["uri"])


@pytest.mark.parametrize("path", ["/static/js/main.abc123.js", "/static/css/main.css", "/logo.svg", "/favicon.ico"])
def test_static_files_are_still_cached(path):
    loc = _location_for(path)
    assert loc["modifier"] in ("~", "~*")
    assert any("immutable" in " ".join(args) for args in _directive(loc, "add_header"))


# ------------------------------------------------------------- εισαγωγή/εξαγωγή αντιγράφου ---

def test_the_proxy_accepts_every_backup_the_backend_accepts():
    from routes_backup import MAX_UPLOAD_BYTES
    loc = _location_for("/api/backup/import")
    (limit,) = _directive(loc, "client_max_body_size")
    assert _size(limit[0]) >= MAX_UPLOAD_BYTES, "ο nginx θα απέρριπτε αντίγραφο που το backend δέχεται"
    assert _size(limit[0]) <= 2 * MAX_UPLOAD_BYTES, "και δεν υπάρχει λόγος να είναι απεριόριστο"


def test_only_the_backup_endpoints_get_the_large_body_limit():
    from routes_backup import MAX_UPLOAD_BYTES
    for path in ("/api/clients", "/api/invoices", "/api/ocr/analyze", "/api/portal/abc/chat"):
        (limit,) = _directive(_location_for(path), "client_max_body_size")
        assert _size(limit[0]) < MAX_UPLOAD_BYTES, path


@pytest.mark.parametrize("path", ["/api/backup/import", "/api/backup/export"])
def test_backup_requests_have_time_to_finish(path):
    """Η εξαγωγή χτίζεται ολόκληρη πριν σταλεί το πρώτο byte και η επαναφορά γράφει χιλιάδες έγγραφα."""
    (timeout,) = _directive(_location_for(path), "proxy_read_timeout")
    assert _seconds(timeout[0]) >= 300


def _compose_services() -> dict[str, dict]:
    """Ελάχιστος αναλυτής του docker-compose.yml: κλειδιά ανά υπηρεσία και λίστα `ports`."""
    services: dict[str, dict] = {}
    current = last_key = None
    in_services = False
    for raw in COMPOSE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 0:
            in_services, current, last_key = body == "services:", None, None
        elif not in_services:
            continue
        elif indent == 2 and body.endswith(":"):
            current, last_key = body[:-1], None
            services[current] = {}
        elif current and indent == 4:
            last_key = body.split(":", 1)[0]
            services[current].setdefault(last_key, [])
        elif current and last_key and indent >= 6 and body.startswith("- "):
            services[current][last_key].append(body[2:].strip().strip('"'))
    return services
