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

Το αρχείο ρυθμίσεων αναλύεται εδώ (μικρός αναλυτής στο ``deploy_files.py``, χωρίς νέα εξάρτηση) και οι θέσεις (``location``)
επιλέγονται με τον αλγόριθμο του ίδιου του nginx: ακριβές, μακρύτερο πρόθεμα, κανονική έκφραση, ``^~``. Ο αναλυτής ακολουθεί και
την ΚΛΗΡΟΝΟΜΙΑ του nginx (μια θέση με δική της ``add_header`` χάνει τις κεφαλίδες του ``server``) και αρνείται κατασκευές που δεν
μοντελοποιεί (``if``, ``include``, ένθετο ``location``). Ό,τι δεν μπορεί να αποδειχθεί στατικά — τι κάνει πραγματικά ο nginx και το
uvicorn μαζί — ελέγχεται στο ``test_unit_nginx_lab_r5.py``, που τρέχει τον πραγματικό nginx όπου υπάρχει.

Δεύτερος έλεγχος (εύρημα D-04): η πρώτη εκδοχή αυτού του αρχείου περνούσε και με λάθος ανάπτυξη (12 από 21 μεταλλάξεις επέζησαν) —
έβλεπε μόνο τα άμεσα παιδιά του ``server``, δεν ήξερε από κληρονομιά, ικανοποιούνταν από ΣΧΟΛΙΟ του Dockerfile και δεν κοιτούσε
``network_mode``. Τα παρακάτω τα καλύπτουν.
"""
import pytest

import deploy_files as df
from deploy_files import (
    NGINX_CONF, compose_services as _compose_services, directive as _directive,
    dockerfile_cmd, effective, headers_added, location_for as _location_for, locations as _locations,
    option_value, seconds as _seconds, size_bytes as _size, strip_comments,
)


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
        values = [args[1] for args in effective(loc, "proxy_set_header") if args[0].lower() == "x-forwarded-for"]
        assert values == ["$remote_addr"], f"{loc['uri']}: X-Forwarded-For = {values}"
    assert "$proxy_add_x_forwarded_for" not in strip_comments(NGINX_CONF.read_text(encoding="utf-8"))


def test_every_proxied_location_forwards_the_headers_the_backend_relies_on():
    """Ένα `location` με δικό του `proxy_set_header` δεν κληρονομεί τα του `server`: κάθε θέση τα δηλώνει ρητά."""
    for loc in (loc for loc in _locations() if _directive(loc, "proxy_pass")):
        names = {args[0].lower() for args in effective(loc, "proxy_set_header")}
        assert {"host", "x-real-ip", "x-forwarded-for", "x-forwarded-proto"} <= names, (loc["uri"], names)


def test_the_backend_is_not_reachable_except_through_nginx():
    """Με `--forwarded-allow-ips "*"` το backend εμπιστεύεται την κεφαλίδα από οποιονδήποτε τον φτάσει· ασφαλές μόνο
    αν η μοναδική είσοδος είναι ο nginx (που την ξαναγράφει)."""
    services = _compose_services()
    assert {"backend", "worker", "frontend", "mongo"} <= set(services)
    assert "ports" not in services["backend"], "το backend δεν δημοσιεύεται: μόνο ο nginx το φτάνει"
    assert "ports" not in services["worker"]
    # `network_mode: host` δημοσιεύει όλες τις θύρες του container στον host ΧΩΡΙΣ κλειδί `ports`
    for name, keys in services.items():
        assert keys.get("network_mode", []) not in (["host"], ["'host'"]), f"{name}: network_mode: host"
    published = sorted(name for name, keys in services.items() if "ports" in keys)
    assert published == ["frontend", "mongo"]
    assert all(p.startswith("127.0.0.1:") for p in services["mongo"]["ports"]), "η βάση μόνο τοπικά"
    assert all(not p.startswith("127.0.0.1:") for p in services["frontend"]["ports"])


def test_the_dockerfile_command_is_read_as_docker_reads_it():
    """Το CMD διαβάζεται ως JSON (μορφή exec) και όχι ως υποσυμβολοσειρά του αρχείου: μια λέξη μέσα σε ΣΧΟΛΙΟ δεν το ικανοποιεί."""
    cmd = dockerfile_cmd()
    assert cmd[0] == "uvicorn" and "server:app" in cmd
    assert option_value(cmd, "--port") == "8001"
    assert option_value(cmd, "--host") == "0.0.0.0"


def test_the_dockerfile_command_makes_uvicorn_read_the_forwarded_address():
    """Χωρίς `--proxy-headers` ΚΑΙ `--forwarded-allow-ips` ο πελάτης φαίνεται πάντα ως η διεύθυνση του nginx: ένα κοινό όριο
    ρυθμού για όλους τους πελάτες της πύλης και ίδια IP σε κάθε ίχνος ελέγχου."""
    cmd = dockerfile_cmd()
    assert "--proxy-headers" in cmd and "--no-proxy-headers" not in cmd, cmd
    assert option_value(cmd, "--forwarded-allow-ips"), "χωρίς --forwarded-allow-ips το uvicorn εμπιστεύεται μόνο το 127.0.0.1"


def test_the_dockerfile_trusts_forwarded_headers_only_under_the_invariants_above():
    """Αν κάποτε αλλάξει το `*` (π.χ. σε συγκεκριμένη διεύθυνση), οι δύο προϋποθέσεις (backend μη δημοσιευμένο,
    nginx που ξαναγράφει την κεφαλίδα) μπορούν να χαλαρώσουν — μέχρι τότε πρέπει να ισχύουν και οι δύο."""
    if option_value(dockerfile_cmd(), "--forwarded-allow-ips") == "*":
        test_forwarded_for_is_overwritten_with_the_connecting_address()
        test_the_backend_is_not_reachable_except_through_nginx()
        test_compose_does_not_replace_the_command_of_the_backend()


def test_compose_does_not_replace_the_command_of_the_backend():
    """Ένα `command:` στο compose αντικαθιστά το CMD του Dockerfile και μαζί τα flags `--proxy-headers`/`--forwarded-allow-ips`."""
    backend = _compose_services()["backend"]
    assert "command" not in backend and "entrypoint" not in backend, backend


def test_nginx_proxies_to_the_port_the_backend_listens_on():
    port = option_value(dockerfile_cmd(), "--port")
    targets = {args[0] for loc in _locations() for args in _directive(loc, "proxy_pass")}
    assert targets == {f"http://backend:{port}"}, targets
    assert "backend" in _compose_services()


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


# --------------------------------------------------- D-04: όσα δεν φαίνονταν στην πρώτη εκδοχή ---

def test_the_config_uses_only_constructs_the_checks_understand():
    """Οι έλεγχοι εδώ δεν ξέρουν να αξιολογήσουν `if`, `include`, `rewrite`, ένθετο `location` ή δεύτερο `server`: μια τέτοια
    κατασκευή θα άλλαζε τη συμπεριφορά χωρίς να φαίνεται. Αν χρειαστεί, επεκτείνετε τον αναλυτή (deploy_files.py) ΠΡΩΤΑ."""
    assert df.unsupported_constructs() == []


@pytest.mark.parametrize("text, expected", [
    ("server { location / { if ($a) { return 403; } } }", ["if ($a)"]),
    ("server { include /etc/nginx/extra.conf; }", ["include /etc/nginx/extra.conf"]),
    ("server { location /api/ { location ~ x { return 200; } } }", ["ένθετο location ~ x"]),
    ("server { listen 80; } server { listen 81; }", ["περισσότερα από ένα server"]),
    ("server { location /a { rewrite ^/a /b; } }", ["rewrite ^/a /b"]),
    ("map $a $b { default 1; } server { location / { return 200; } }", []),
])
def test_the_checker_for_unsupported_constructs_detects_them(text, expected):
    assert df.unsupported_constructs(text) == expected


def test_nginx_inheritance_is_modelled():
    """Μια θέση με δική της `add_header` χάνει ΟΛΕΣ τις κεφαλίδες του server· χωρίς δική της, τις κληρονομεί."""
    text = """server { add_header A 1; add_header B 2; client_max_body_size 1m;
        location /own { add_header C 3; }
        location /inherits { proxy_pass http://x; client_max_body_size 5m; } }"""
    own, inherits = df.location_for("/own", text), df.location_for("/inherits", text)
    assert [a[0] for a in df.effective(own, "add_header", text)] == ["C"]
    assert [a[0] for a in df.effective(inherits, "add_header", text)] == ["A", "B"]
    assert df.effective(own, "client_max_body_size", text) == [["1m"]]
    assert df.effective(inherits, "client_max_body_size", text) == [["5m"]]


SECURITY_HEADER_PATHS = ["/", "/index.html", "/dashboard", "/portal/abc", "/static/js/main.abc123.js", "/logo.svg", "/favicon.ico",
                         "/service-worker.js", "/api/clients", "/api/portal/abc", "/api/backup/import", "/api/export/report.png"]


@pytest.mark.parametrize("path", SECURITY_HEADER_PATHS)
def test_security_headers_reach_every_kind_of_response(path):
    """`Referrer-Policy` προστατεύει το token της πύλης (βρίσκεται στο URL) από διαρροή σε τρίτους (Google Fonts)· `nosniff` και
    `X-Frame-Options` ισχύουν παντού. Επειδή ένα `location` με δική του `add_header` τις χάνει, κάθε θέση τις επαναλαμβάνει."""
    found = headers_added(_location_for(path))
    assert found.get("referrer-policy", ("", False))[0] in ("strict-origin-when-cross-origin", "strict-origin", "same-origin", "no-referrer"), found
    assert found.get("x-content-type-options", ("", False))[0] == "nosniff", found
    assert found.get("x-frame-options", ("", False))[0] in ("DENY", "SAMEORIGIN"), found
    for name in ("referrer-policy", "x-content-type-options", "x-frame-options"):
        assert found[name][1], f"{path}: το {name} χωρίς `always` λείπει από τις απαντήσεις σφάλματος"


def test_the_service_worker_is_never_cached():
    """Ο browser πρέπει να ξαναρωτά τον server για τον worker σε κάθε φόρτωση· με `immutable`/`expires 30d` (ο κανόνας των
    στατικών αρχείων) οι χρήστες θα έμεναν για 30 ημέρες στην παλιά έκδοση της εφαρμογής."""
    loc = _location_for("/service-worker.js")
    assert loc["modifier"] == "=" and loc["uri"] == "/service-worker.js", loc["uri"]
    cache_control = headers_added(loc)["cache-control"][0]
    assert "no-store" in cache_control and "no-cache" in cache_control and "immutable" not in cache_control, cache_control
    assert not _directive(loc, "expires") and not effective(loc, "expires")


@pytest.mark.parametrize("path", ["/", "/index.html", "/dashboard", "/portal/abc"])
def test_the_pages_themselves_are_not_cached_for_long(path):
    """Το `index.html` ονομάζει τα bundles με hash· αν κρατιόταν στην κρυφή μνήμη, ο χρήστης θα έβλεπε παλιά bundles."""
    loc = _location_for(path)
    assert not _directive(loc, "expires"), path
    assert "immutable" not in " ".join(" ".join(a) for a in _directive(loc, "add_header")), path


def test_no_wildcard_trust_for_the_real_client_address():
    """`set_real_ip_from 0.0.0.0/0` (ή `all`) θα έκανε τον nginx να πιστεύει την κεφαλίδα X-Forwarded-For του οποιουδήποτε πελάτη."""
    for node, _ in df.walk(df.nginx_tree()):
        if node["name"] == "set_real_ip_from":
            assert node["args"][0] not in ("0.0.0.0/0", "::/0", "all", "0.0.0.0", "::"), node["args"]


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
