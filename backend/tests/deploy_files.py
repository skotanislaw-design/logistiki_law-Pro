"""Ανάγνωση των αρχείων ανάπτυξης (nginx.conf, Dockerfile, docker-compose.yml) για τα tests υποδομής.

Κοινό για το `test_unit_deploy_r5.py` (στατικός έλεγχος) και το `test_unit_nginx_lab_r5.py` (λειτουργία σε πραγματικό nginx).
Δεν είναι test (δεν ταιριάζει στο `test_unit_*.py`)· το φορτώνει το pytest επειδή ο φάκελος `tests/` μπαίνει στο `sys.path`.

Ο αναλυτής του nginx μοντελοποιεί ό,τι χρειάζεται για να μη βγάζει ψευδώς πράσινα:

* **κληρονομιά**: οι `add_header`, `proxy_set_header` (και `access_log`) κληρονομούνται από το επίπεδο πάνω ΜΟΝΟ αν το τρέχον επίπεδο
  δεν ορίζει καμία. Ένα `location` με δική του `add_header` χάνει όλες τις κεφαλίδες του `server`· οι υπόλοιπες οδηγίες (π.χ.
  `client_max_body_size`) κληρονομούνται μία προς μία. Η `effective()` δίνει ό,τι πραγματικά ισχύει σε μια θέση.
* **αναδρομή**: κάθε κόμβος του δέντρου επισκέπτεται (`walk`), όχι μόνο τα άμεσα παιδιά του `server`.
* **όρια**: ό,τι ο αναλυτής ΔΕΝ μοντελοποιεί (`if`, `include`, ένθετο `location`, δεύτερο `server`) δεν επιτρέπεται στο αρχείο — το
  `unsupported_constructs()` τα εντοπίζει, αντί να τα προσπερνά σιωπηλά.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
NGINX_CONF = ROOT / "frontend" / "nginx.conf"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "backend" / "Dockerfile"

# Οδηγίες που κληρονομούνται «όλες ή καμία» από το επίπεδο πάνω.
LIST_DIRECTIVES = ("add_header", "proxy_set_header", "access_log", "error_log", "error_page")
# Κατασκευές που ο αναλυτής δεν ξέρει να αξιολογήσει — δεν επιτρέπονται στο αρχείο.
UNSUPPORTED = ("if", "include", "rewrite")


# ---------------------------------------------------------------- μικρός αναλυτής nginx ---

def strip_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", "", text)


def _tokens(text: str) -> list[str]:
    return re.findall(r'"[^"]*"|\'[^\']*\'|[{};]|[^\s{};]+', strip_comments(text))


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


def nginx_tree(text: str | None = None) -> list[dict]:
    tree, _ = _parse(_tokens(NGINX_CONF.read_text(encoding="utf-8") if text is None else text))
    return tree


def walk(nodes, depth: int = 0):
    """Κάθε κόμβος του δέντρου, με το βάθος του (0 = επίπεδο αρχείου)."""
    for node in nodes:
        yield node, depth
        if node["children"]:
            yield from walk(node["children"], depth + 1)


def server(text: str | None = None) -> dict:
    servers = [d for d in nginx_tree(text) if d["name"] == "server"]
    assert len(servers) == 1, f"αναμένεται ένα server, βρέθηκαν {len(servers)}"
    return servers[0]


def locations(text: str | None = None) -> list[dict]:
    found = []
    for d in server(text)["children"]:
        if d["name"] == "location":
            args = d["args"]
            modifier, uri = ("", args[0]) if len(args) == 1 else (args[0], args[1])
            found.append({**d, "modifier": modifier, "uri": uri})
    return found


def location_for(path: str, text: str | None = None) -> dict:
    """Η θέση που επιλέγει ο nginx για ένα αίτημα (ακριβές → μακρύτερο πρόθεμα με ^~ → regex → μακρύτερο πρόθεμα)."""
    locs = locations(text)
    for loc in locs:
        if loc["modifier"] == "=" and loc["uri"] == path:
            return loc
    prefixes = [loc for loc in locs if loc["modifier"] in ("", "^~") and path.startswith(loc["uri"])]
    longest = max(prefixes, key=lambda loc: len(loc["uri"]), default=None)
    if longest is not None and longest["modifier"] == "^~":
        return longest
    for loc in locs:
        if loc["modifier"] in ("~", "~*"):
            flags = re.IGNORECASE if loc["modifier"] == "~*" else 0
            if re.search(loc["uri"], path, flags):
                return loc
    assert longest is not None, f"καμία θέση για {path}"
    return longest


def directive(node: dict, name: str) -> list[list[str]]:
    """Οι οδηγίες `name` που ορίζει ΑΜΕΣΩΣ ο κόμβος (χωρίς κληρονομιά)."""
    return [c["args"] for c in (node["children"] or []) if c["name"] == name]


def effective(loc: dict, name: str, text: str | None = None) -> list[list[str]]:
    """Οι οδηγίες `name` που ισχύουν πραγματικά στη θέση, με τους κανόνες κληρονομιάς του nginx.

    `add_header`/`proxy_set_header`/`access_log`: αν η θέση ορίζει έστω μία, ΟΛΕΣ του server αγνοούνται. Οι υπόλοιπες
    (`client_max_body_size`, `proxy_read_timeout`, …): η θέση υπερισχύει, αλλιώς ισχύει η τιμή του server."""
    own = directive(loc, name)
    return own if own else directive(server(text), name)


def unsupported_constructs(text: str | None = None) -> list[str]:
    """Κατασκευές που ο αναλυτής δεν μοντελοποιεί: `if`, `include`, `rewrite`, δεύτερο `server`, ένθετο `location`."""
    problems = []
    tree = nginx_tree(text)
    if len([d for d in tree if d["name"] == "server"]) != 1:
        problems.append("περισσότερα από ένα server")
    for node, depth in walk(tree):
        if node["name"] in UNSUPPORTED:
            problems.append(f"{node['name']} {' '.join(node['args'])}".strip())
        if node["name"] == "location" and depth > 1:
            problems.append(f"ένθετο location {' '.join(node['args'])}")
        if node["name"] == "server" and depth > 0:
            problems.append("ένθετο server")
    return problems


def size_bytes(value: str) -> int:
    m = re.fullmatch(r"(\d+)([kKmMgG]?)", value)
    assert m, value
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]


def seconds(value: str) -> int:
    m = re.fullmatch(r"(\d+)([smh]?)", value)
    assert m, value
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def headers_added(loc: dict, text: str | None = None) -> dict[str, tuple[str, bool]]:
    """`{όνομα κεφαλίδας (πεζά): (τιμή, always)}` όπως θα τα έβλεπε ένα αίτημα που επιλέγει αυτή τη θέση."""
    found = {}
    for args in effective(loc, "add_header", text):
        found[args[0].lower()] = (unquote(args[1]), "always" in args[2:])
    return found


# ------------------------------------------------------------------------- Dockerfile ---

def dockerfile_cmd(text: str | None = None) -> list[str]:
    """Τα στοιχεία της τελευταίας εντολής `CMD [...]` (μορφή exec/JSON), όπως τα βλέπει ο Docker — όχι υποσυμβολοσειρά
    του αρχείου, που θα ικανοποιούνταν και από ένα σχόλιο."""
    lines = (DOCKERFILE.read_text(encoding="utf-8") if text is None else text).splitlines()
    commands, current = [], None
    for raw in lines:
        stripped = raw.strip()
        if current is None:
            if not stripped or stripped.startswith("#"):
                continue
            current = stripped
        elif stripped.startswith("#"):
            continue                                  # σχόλιο μέσα σε εντολή πολλών γραμμών
        else:
            current += " " + stripped
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        commands.append(current)
        current = None
    cmds = [c for c in commands if re.match(r"CMD\s", c, re.IGNORECASE)]
    assert cmds, "το Dockerfile δεν έχει εντολή CMD"
    value = cmds[-1].split(None, 1)[1].strip()
    parsed = json.loads(value)                        # μορφή exec: αλλιώς το CMD θα έτρεχε μέσα από shell
    assert isinstance(parsed, list) and all(isinstance(x, str) for x in parsed), value
    return parsed


def option_value(tokens: list[str], option: str) -> str | None:
    """Η τιμή που ακολουθεί την επιλογή `option` (ή η τιμή της μορφής `option=τιμή`)."""
    for i, token in enumerate(tokens):
        if token == option and i + 1 < len(tokens):
            return tokens[i + 1]
        if token.startswith(option + "="):
            return token.split("=", 1)[1]
    return None


# ------------------------------------------------------------------------- docker-compose ---

def compose_services() -> dict[str, dict]:
    """Ελάχιστος αναλυτής του docker-compose.yml: για κάθε υπηρεσία, τα κλειδιά επιπέδου 4 με τιμή (`['host']` για
    `network_mode: host`) ή λίστα (`ports`, `volumes`, …)."""
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
            key, _, rest = body.partition(":")
            last_key = key
            rest = rest.strip().strip('"')
            if rest:
                services[current][key] = [rest]
            else:
                services[current].setdefault(key, [])
        elif current and last_key and indent >= 6 and body.startswith("- "):
            services[current][last_key].append(body[2:].strip().strip('"'))
    return services
