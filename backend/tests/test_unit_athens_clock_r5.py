"""Γύρος 5 — ο server δεν διαβάζει την ώρα του μηχανήματος για να βρει «σήμερα».

Ο server τρέχει σε UTC. Μεταξύ 00:00 και 03:00 ώρα Ελλάδας (21:00–24:00 UTC) η `date.today()` δίνει ακόμη τη χθεσινή
ημέρα: το dashboard έδειχνε τον προηγούμενο μήνα την 1η του μήνα, οι υπενθυμίσεις και οι προθεσμίες υπολογίζονταν με
μία μέρα καθυστέρηση, το ημερολογιακό κλειδί του ημερήσιου email άλλαζε σε λάθος ώρα. Το λάθος είχε διορθωθεί στα
παραστατικά (`athens_today`) αλλά έμεινε σε 20 άλλα σημεία.

Δύο κατηγορίες ελέγχων: (1) στατικός φρουρός — κανένα module δεν καλεί `date.today()` / `datetime.now()` χωρίς ζώνη /
`utcnow()` και δεν παρουσιάζει ως ελληνική την ημερομηνία UTC μιας σφραγίδας (`datetime.now(utc).date()`,
`created_at[:10]`, `fromisoformat(σφραγίδα).date()`)· (2) συμπεριφορά — με παγωμένο ρολόι (23:30 UTC της 31/12 = 01:30
της 1/1 στην Ελλάδα) κάθε σημείο που άλλαξε βλέπει την ελληνική ημερομηνία. Οι έλεγχοι του δεύτερου γύρου
(`test_unit_athens_clock_r5b.py`) καλύπτουν όσα σημεία δεν είχαν έλεγχο συμπεριφοράς.
"""
import ast
import asyncio
import pathlib
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ==================================================================== στατικός φρουρός ====

#
# Ο φρουρός βρίσκει τρεις οικογένειες λαθών, και για κάθε μία όλα τα σχήματα με τα οποία γράφεται (δεν αρκεί ένα πρότυπο
# κειμένου: η δεύτερη επιθεώρηση βρήκε 13 τρόπους που ο πρώτος φρουρός δεν έβλεπε):
#
# 1. **Ανάγνωση της ώρας του μηχανήματος**: `date.today()`, `datetime.now()` χωρίς ζώνη, `utcnow()`, `utcfromtimestamp()`,
#    `fromtimestamp()` χωρίς ζώνη, `astimezone()` χωρίς όρισμα, και οι συναρτήσεις του `time` (`localtime`, `gmtime`,
#    `ctime`, `asctime`, `mktime`, `strftime` χωρίς ρητή ώρα) — και με ψευδώνυμο (`import time as _t`, `from time import localtime`).
# 2. **Ημερολόγιο UTC μιας ώρας**: `.date()`, `.strftime('%Y…')`, `.year/.month/.day`, `.weekday()`, `.isocalendar()`… πάνω σε
#    ώρα UTC — είτε γράφεται απευθείας (`datetime.now(timezone.utc).date()`) είτε περνά από όνομα (`now = datetime.now(timezone.utc)`,
#    `now.date()`), από πράξη (`now - timedelta(days=1)`), από κείμενο (`when.isoformat()`, `str(when)`) ή από την ασύνδετη
#    μορφή `datetime.date(when)`.
# 3. **Ημερομηνία UTC μιας σφραγίδας-κειμένου**: τμήμα του κειμένου (`created_at[:10]`, `[:7]`, `[:4]`, `[0:10]`,
#    `.split('T')[0]`, `.partition('T')[0]`) όταν η τιμή μοιάζει με σφραγίδα (`created_at`, `doc['created']`, `str(created)`,
#    `updated_at`, `createdAt`, `.isoformat()`, `_now()`) ή προέρχεται από ώρα UTC.
#
# Τα ονόματα που κρατούν ώρα UTC βρίσκονται ανά συνάρτηση: ένα όνομα που παίρνει ΠΑΝΤΑ τιμή UTC. Ένα όνομα που ξαναορίζεται
# με ώρα Ελλάδας (`now = now.astimezone(ATHENS)`), παράμετρος, ή μεταβλητή βρόχου δεν θεωρείται UTC — προτιμάται να
# μείνει ένα σημείο αδιάγνωστο (το πιάνουν οι έλεγχοι συμπεριφοράς) παρά ένα ψευδές συναγερμό που θα κάνει τους
# προγραμματιστές να αγνοούν τον φρουρό.

_STAMP_VALUE = re.compile(
    r"(?i:_at\b|_ts\b|\.isoformat\(\)|stamp|\bcreated\b|\bupdated\b)|[a-z]At\b")

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
_UTC_HELPERS = frozenset({"_now", "now_iso", "utc_now", "now_utc", "utcnow"})
_CALENDAR_METHODS = frozenset({"date", "weekday", "isoweekday", "isocalendar", "timetuple", "toordinal"})
_CALENDAR_FIELDS = frozenset({"year", "month", "day"})
_DATE_DIRECTIVES = re.compile(r"%[-#]?[aAbBcCdDeFgGhjmuUVwWxyY]")
_TIME_FUNCTIONS = frozenset({"localtime", "gmtime", "ctime", "asctime", "mktime"})
# Τμήματα ημερομηνίας μέσα σε σφραγίδα `YYYY-MM-DDTHH:MM:SS…`: [:4] έτος, [:7] μήνας, [:10] ημέρα, [5:7], [8:10].
_DATE_SLICES = frozenset({(None, 4), (0, 4), (None, 7), (0, 7), (None, 10), (0, 10), (5, 7), (8, 10), (5, 10)})


def _mentions_utc(node) -> bool:
    return "utc" in ast.unparse(node).lower()


def _call_args(node):
    return [*node.args, *(k.value for k in node.keywords)]


def _is_utc_value(node, names) -> bool:
    """Τιμή που μετράει το ημερολόγιο της UTC: ώρα UTC ή κείμενο που βγήκε από αυτήν (`isoformat()`, `str()`, `strftime()`)."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.NamedExpr):
        return _is_utc_value(node.value, names)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        return _is_utc_value(node.left, names)                       # ώρα UTC ± timedelta
    if isinstance(node, ast.BoolOp):
        return any(_is_utc_value(v, names) for v in node.values)      # `now_utc or datetime.now(timezone.utc)`
    if isinstance(node, ast.IfExp):
        return _is_utc_value(node.body, names) or _is_utc_value(node.orelse, names)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "str" and node.args:
            return _is_utc_value(node.args[0], names)
        return func.id in _UTC_HELPERS
    if not isinstance(func, ast.Attribute):
        return False
    attr = func.attr
    if attr in _UTC_HELPERS or attr == "utcfromtimestamp":
        return True
    if attr in {"now", "fromtimestamp", "astimezone"}:
        return any(_mentions_utc(a) for a in _call_args(node))
    if attr in {"replace", "isoformat", "strftime"}:
        return _is_utc_value(func.value, names)
    return False


def _is_stamp(node, names) -> bool:
    """Μοιάζει η τιμή με σφραγίδα χρόνου (κείμενο ISO σε UTC);"""
    return bool(_STAMP_VALUE.search(ast.unparse(node))) or _is_utc_value(node, names)


def _bound_names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _bound_names(element)
    elif isinstance(target, ast.Starred):
        yield from _bound_names(target.value)


def _scope_nodes(scope):
    """Οι κόμβοι ενός πεδίου ορατότητας, χωρίς το εσωτερικό των εμφωλευμένων συναρτήσεων και κλάσεων (είναι δικά τους)."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _utc_names(scope, inherited):
    """Τα ονόματα του πεδίου που κρατούν ΠΑΝΤΑ ώρα UTC (και τα κληρονομημένα από το εξωτερικό πεδίο, αν δεν τα σκιάζει)."""
    assigned: dict[str, list] = {}

    def add(name, value):
        assigned.setdefault(name, []).append(value)

    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        spec = scope.args
        for arg in (*spec.posonlyargs, *spec.args, *spec.kwonlyargs, spec.vararg, spec.kwarg):
            if arg is not None:
                add(arg.arg, None)                                   # παράμετρος: άγνωστης προέλευσης
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    add(target.id, node.value)
                else:
                    for name in _bound_names(target):
                        add(name, None)
        elif isinstance(node, ast.AnnAssign):
            for name in _bound_names(node.target):
                add(name, node.value)
        elif isinstance(node, ast.NamedExpr):
            add(node.target.id, node.value)
        elif isinstance(node, ast.AugAssign):
            if not isinstance(node.op, (ast.Add, ast.Sub)):          # `now += timedelta(…)` μένει ώρα UTC
                for name in _bound_names(node.target):
                    add(name, None)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for name in _bound_names(node.target):
                add(name, None)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            for name in _bound_names(node.optional_vars):
                add(name, None)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                add((alias.asname or alias.name).split(".")[0], None)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node.name, None)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            add(node.name, None)

    names = {name for name in inherited if name not in assigned}
    changed = True
    while changed:                                                   # σταθερό σημείο: `a = utc_now; b = a + delta`
        changed = False
        for name, values in assigned.items():
            if name in names:
                continue
            known = [v is not None and _is_utc_value(v, names) for v in values]
            if all(known) or (any(known) and all(v is not None and _is_utc_value(v, names | {name}) for v in values)):
                names.add(name)
                changed = True
    return frozenset(names)


def _scopes(tree):
    """(πεδίο ορατότητας, ονόματα με ώρα UTC) για το module και κάθε συνάρτηση, κλάση και lambda που περιέχει."""
    pending = [(tree, frozenset())]
    while pending:
        scope, inherited = pending.pop()
        names = _utc_names(scope, inherited)
        yield scope, names
        pending.extend((node, names) for node in _scope_nodes(scope) if isinstance(node, _SCOPES))


def _time_imports(tree):
    modules, functions = {"time"}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == "time")
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            functions.update({alias.asname or alias.name: alias.name for alias in node.names})
    return modules, functions


def _calendar_format(call) -> bool:
    """Περιέχει η μορφή του `strftime` στοιχεία ημερομηνίας; (άγνωστη μορφή → ναι, δεν αποδεικνύεται το αντίθετο)"""
    fmt = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "format"), None)
    if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
        return bool(_DATE_DIRECTIVES.search(fmt.value))
    return True


def _slice_bounds(part):
    def number(bound):
        return bound.value if isinstance(bound, ast.Constant) and isinstance(bound.value, int) else "?"
    return number(part.lower) if part.lower is not None else None, number(part.upper) if part.upper is not None else None


def _time_read(name, who, node):
    """Μήνυμα αν η συνάρτηση `name` της μονάδας `time` διαβάζει την ώρα του μηχανήματος ή το ημερολόγιο UTC."""
    if name in _TIME_FUNCTIONS:
        return f"{who}.{name}()" if who else f"{ast.unparse(node.func)}()"
    if name == "strftime" and len(node.args) < 2:
        label = f"{who}.strftime()" if who else f"{ast.unparse(node.func)}()"
        return f"{label} χωρίς ρητή ώρα — διαβάζει την τοπική ώρα του μηχανήματος"
    return None


def _reads_in(node, names, time_modules, time_functions):
    """Τα ευρήματα (γραμμή, περιγραφή) ενός κόμβου."""
    line = getattr(node, "lineno", 0)

    if isinstance(node, ast.Subscript):
        part = node.slice
        if isinstance(part, ast.Slice) and part.step is None and _slice_bounds(part) in _DATE_SLICES \
                and _is_stamp(node.value, names):
            return [(line, f"{ast.unparse(node)} — η ημερομηνία UTC μιας σφραγίδας")]
        # `σφραγίδα.split('T')[0]`, `σφραγίδα.partition(' ')[0]`: το πρώτο τμήμα είναι η ημερομηνία UTC
        if (isinstance(part, ast.Constant) and part.value == 0 and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in {"split", "rsplit", "partition"}
                and node.value.args and isinstance(node.value.args[0], ast.Constant) and node.value.args[0].value in ("T", " ")
                and _is_stamp(node.value.func.value, names)):
            return [(line, f"{ast.unparse(node)} — η ημερομηνία UTC μιας σφραγίδας")]
        return []

    if isinstance(node, ast.Attribute):
        if node.attr in _CALENDAR_FIELDS and _is_utc_value(node.value, names):
            return [(line, f"{ast.unparse(node)} — η ημερομηνία UTC")]
        return []

    if not isinstance(node, ast.Call):
        return []
    func = node.func

    if isinstance(func, ast.Name):                                   # `from time import localtime [as lt]`
        original = time_functions.get(func.id)
        message = _time_read(original, "", node) if original else None
        return [(line, message)] if message else []
    if not isinstance(func, ast.Attribute):
        return []

    attr, owner = func.attr, func.value
    who = ast.unparse(owner)
    found = []
    if attr == "today":
        found.append((line, f"{who}.today()"))
    elif attr == "utcnow":
        found.append((line, f"{who}.utcnow()"))
    elif attr == "utcfromtimestamp":
        found.append((line, f"{who}.utcfromtimestamp() — ημερολόγιο UTC"))
    elif attr == "now":
        zone = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "tz"), None)
        if zone is None or (isinstance(zone, ast.Constant) and zone.value is None):
            found.append((line, f"{who}.now() χωρίς ζώνη ώρας"))
    elif attr == "fromtimestamp":
        if len(node.args) < 2 and not any(k.arg == "tz" for k in node.keywords):
            found.append((line, f"{who}.fromtimestamp() χωρίς ζώνη ώρας"))
    elif attr == "astimezone":
        zone = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "tz"), None)
        if zone is None or (isinstance(zone, ast.Constant) and zone.value is None):
            found.append((line, f"{who}.astimezone() χωρίς ζώνη ώρας — η ζώνη του μηχανήματος"))
    elif isinstance(owner, ast.Name) and owner.id in time_modules:
        message = _time_read(attr, owner.id, node)
        if message:
            found.append((line, message))

    # Ημερολόγιο UTC μιας ώρας. Όταν ο ίδιος ο κάτοχος είναι ήδη εύρημα (`utcnow().date()`), δεν το ξαναλέμε.
    already = isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute) and owner.func.attr in {
        "utcnow", "utcfromtimestamp"}
    if attr in _CALENDAR_METHODS and not already and _is_utc_value(owner, names):
        found.append((line, f"{ast.unparse(node)} — η ημερομηνία UTC"))
    elif attr == "strftime" and not already and _is_utc_value(owner, names) and _calendar_format(node):
        found.append((line, f"{ast.unparse(node)} — η ημερομηνία UTC"))
    elif attr in _CALENDAR_METHODS and node.args and _is_utc_value(node.args[0], names):
        found.append((line, f"{ast.unparse(node)} — η ημερομηνία UTC"))        # η ασύνδετη μορφή `datetime.date(when)`
    if attr == "date" and isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute) \
            and owner.func.attr == "fromisoformat":
        found.append((line, "fromisoformat(...).date() — χωρίς μετατροπή σε ώρα Ελλάδας (για ημερομηνίες: date.fromisoformat)"))
    return found


def _clock_reads(tree):
    """(γραμμή, περιγραφή) για κάθε ανάγνωση της ώρας του μηχανήματος και κάθε ημερομηνία UTC που θα εμφανιζόταν ως ελληνική.

    Το AST αγνοεί σχόλια και docstrings και δεν εξαρτάται από τα ονόματα με τα οποία εισάγονται οι μονάδες (`date as _d`,
    `import time as _t`, `from time import localtime`)."""
    time_modules, time_functions = _time_imports(tree)
    found = []
    for scope, names in _scopes(tree):
        for node in _scope_nodes(scope):
            found.extend(_reads_in(node, names, time_modules, time_functions))
    yield from sorted(found)


def _sources(root: pathlib.Path, *, skip_dirs=(), only=None, skip_prefix=()):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] in {"__pycache__", *skip_dirs} or path.name.startswith(tuple(skip_prefix)):
            continue
        if only and not only(path):
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_module_reads_the_machine_clock_for_today():
    offenders = [f"{rel}:{line} {what}" for rel, tree in _sources(BACKEND, skip_dirs={"tests"}) for line, what in _clock_reads(tree)]
    assert not offenders, (
        "Η «σημερινή» ημερομηνία πρέπει να βγαίνει από tax_utils.athens_today() και η ημερομηνία μιας σφραγίδας από "
        "tax_utils.athens_date_of() (ο server τρέχει σε UTC):\n  " + "\n  ".join(offenders))


def test_no_unit_test_reads_the_machine_clock_either():
    """Ένα test που διαβάζει το τοπικό ρολόι αποτυγχάνει τυχαία στο CI (UTC) τις ώρες 00:00–03:00 ώρα Ελλάδας. Εξαιρούνται
    τα tests αυτής της ομάδας: αποδεικνύουν ότι το μηχάνημα είναι σε άλλη ημερομηνία από την ελληνική."""
    tests = BACKEND / "tests"
    offenders = [f"tests/{rel}:{line} {what}"
                 for rel, tree in _sources(tests, only=lambda p: p.name.startswith("test_unit_") or p.name == "conftest.py",
                                           skip_prefix=("test_unit_athens_clock",))
                 for line, what in _clock_reads(tree)]
    assert not offenders, "Χρησιμοποιήστε tax_utils.athens_today():\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("code, expected", [
    ("date.today()", ["date.today()"]),
    ("_d.today()", ["_d.today()"]),                                              # με ψευδώνυμο: `date as _d`
    ("dt.date.today()", ["dt.date.today()"]),
    ("datetime.now()", ["datetime.now() χωρίς ζώνη ώρας"]),
    ("datetime.now(None)", ["datetime.now() χωρίς ζώνη ώρας"]),
    ("datetime.now(tz=None)", ["datetime.now() χωρίς ζώνη ώρας"]),
    ("datetime.utcnow()", ["datetime.utcnow()"]),
    ("datetime.fromtimestamp(ts)", ["datetime.fromtimestamp() χωρίς ζώνη ώρας"]),
    ("date.fromtimestamp(ts)", ["date.fromtimestamp() χωρίς ζώνη ώρας"]),
    ("time.localtime()", ["time.localtime()"]),
    ("time.mktime(t)", ["time.mktime()"]),
    ("datetime.now(timezone.utc).date()", ["datetime.now(timezone.utc).date() — η ημερομηνία UTC"]),
    ("datetime.now(tz=UTC).date()", ["datetime.now(tz=UTC).date() — η ημερομηνία UTC"]),
    ("datetime.fromisoformat(stamp).date()",
     ["fromisoformat(...).date() — χωρίς μετατροπή σε ώρα Ελλάδας (για ημερομηνίες: date.fromisoformat)"]),
    ("inv.get('created_at', '')[:10]", ["inv.get('created_at', '')[:10] — η ημερομηνία UTC μιας σφραγίδας"]),
    ("_now()[:10]", ["_now()[:10] — η ημερομηνία UTC μιας σφραγίδας"]),
    ("when.isoformat()[:10]", ["when.isoformat()[:10] — η ημερομηνία UTC μιας σφραγίδας"]),
])
def test_the_guard_detects_the_patterns_it_forbids(code, expected):
    assert [what for _line, what in _clock_reads(ast.parse(code))] == expected


UTC_DATE = "η ημερομηνία UTC"
STAMP_DATE = "η ημερομηνία UTC μιας σφραγίδας"
NO_ZONE = "χωρίς ζώνη ώρας"
LOCAL_STRFTIME = "χωρίς ρητή ώρα — διαβάζει την τοπική ώρα του μηχανήματος"


@pytest.mark.parametrize("code, expected", [
    # --- η ώρα του μηχανήματος μέσω της μονάδας `time`, με κάθε τρόπο εισαγωγής
    ("time.strftime('%Y-%m-%d')", [f"time.strftime() {LOCAL_STRFTIME}"]),
    ("time.strftime(fmt)", [f"time.strftime() {LOCAL_STRFTIME}"]),
    ("time.gmtime()", ["time.gmtime()"]),
    ("time.gmtime(ts)", ["time.gmtime()"]),
    ("time.ctime()", ["time.ctime()"]),
    ("time.asctime()", ["time.asctime()"]),
    ("import time as _t\n_t.localtime()", ["_t.localtime()"]),
    ("import time as _t\n_t.strftime('%d/%m/%Y')", [f"_t.strftime() {LOCAL_STRFTIME}"]),
    ("from time import localtime\nlocaltime()", ["localtime()"]),
    ("from time import localtime as lt\nlt()", ["lt()"]),
    ("from time import gmtime\ngmtime()", ["gmtime()"]),
    ("from time import strftime\nstrftime('%Y')", [f"strftime() {LOCAL_STRFTIME}"]),
    ("date(*time.gmtime()[:3])", ["time.gmtime()"]),
    ("time.strftime('%Y', time.localtime())", ["time.localtime()"]),             # η ρητή ώρα δεν σώζει την κλήση που τη δίνει
    # --- ώρες που δεν έχουν ζώνη ή έχουν τη ζώνη του μηχανήματος
    ("datetime.utcfromtimestamp(ts)", ["datetime.utcfromtimestamp() — ημερολόγιο UTC"]),
    ("x.astimezone()", [f"x.astimezone() {NO_ZONE} — η ζώνη του μηχανήματος"]),
    ("x.astimezone(None)", [f"x.astimezone() {NO_ZONE} — η ζώνη του μηχανήματος"]),
    ("x.astimezone(tz=None)", [f"x.astimezone() {NO_ZONE} — η ζώνη του μηχανήματος"]),
    ("datetime.now(timezone.utc).astimezone().date()",
     [f"datetime.now(timezone.utc).astimezone() {NO_ZONE} — η ζώνη του μηχανήματος"]),
    # --- το ημερολόγιο UTC μιας ώρας, απευθείας
    ("datetime.now(timezone.utc).strftime('%Y-%m-%d')", [f"datetime.now(timezone.utc).strftime('%Y-%m-%d') — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')",
     [f"datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M') — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).strftime(fmt)", [f"datetime.now(timezone.utc).strftime(fmt) — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).year", [f"datetime.now(timezone.utc).year — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).month", [f"datetime.now(timezone.utc).month — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).day", [f"datetime.now(timezone.utc).day — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).weekday()", [f"datetime.now(timezone.utc).weekday() — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).isoweekday()", [f"datetime.now(timezone.utc).isoweekday() — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).isocalendar()", [f"datetime.now(timezone.utc).isocalendar() — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).timetuple()", [f"datetime.now(timezone.utc).timetuple() — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).toordinal()", [f"datetime.now(timezone.utc).toordinal() — {UTC_DATE}"]),
    ("(datetime.now(timezone.utc) - timedelta(days=1)).date()",
     [f"(datetime.now(timezone.utc) - timedelta(days=1)).date() — {UTC_DATE}"]),
    ("datetime.now(ZoneInfo('UTC')).date()", [f"datetime.now(ZoneInfo('UTC')).date() — {UTC_DATE}"]),
    ("datetime.fromtimestamp(ts, timezone.utc).date()", [f"datetime.fromtimestamp(ts, timezone.utc).date() — {UTC_DATE}"]),
    ("stamp.astimezone(timezone.utc).date()", [f"stamp.astimezone(timezone.utc).date() — {UTC_DATE}"]),
    ("(now_utc or datetime.now(timezone.utc)).date()", [f"(now_utc or datetime.now(timezone.utc)).date() — {UTC_DATE}"]),
    ("(now := datetime.now(timezone.utc)).date()", [f"(now := datetime.now(timezone.utc)).date() — {UTC_DATE}"]),
    ("datetime.utcnow().date()", ["datetime.utcnow()"]),                           # μία φορά, όχι δύο
    ("_now().date()", [f"_now().date() — {UTC_DATE}"]),
    ("utc_now().date()", [f"utc_now().date() — {UTC_DATE}"]),                    # βοηθητικές συναρτήσεις που επιστρέφουν ώρα UTC
    ("now_utc().date()", [f"now_utc().date() — {UTC_DATE}"]),
    ("clock.utc_now().strftime('%d/%m/%Y')", [f"clock.utc_now().strftime('%d/%m/%Y') — {UTC_DATE}"]),
    ("clock.now_utc().year", [f"clock.now_utc().year — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).strftime(format='%Y')", [f"datetime.now(timezone.utc).strftime(format='%Y') — {UTC_DATE}"]),
    ("outbox._now().strftime('%Y%m%d')", [f"outbox._now().strftime('%Y%m%d') — {UTC_DATE}"]),
    # --- η ασύνδετη μορφή
    ("datetime.date(datetime.now(timezone.utc))", [f"datetime.date(datetime.now(timezone.utc)) — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\ndatetime.date(now)", [f"datetime.date(now) — {UTC_DATE}"]),
    # --- το ίδιο, μέσα από όνομα
    ("now = datetime.now(timezone.utc)\nday = now.date()", [f"now.date() — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\nnow.strftime('%d/%m/%Y')", [f"now.strftime('%d/%m/%Y') — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\nnow.year", [f"now.year — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc) + timedelta(days=1)\nnow.date()", [f"now.date() — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\nnow += timedelta(days=1)\nnow.date()", [f"now.date() — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\ntomorrow = now + timedelta(days=1)\ntomorrow.date()",
     [f"tomorrow.date() — {UTC_DATE}"]),                                          # δεύτερο όνομα που προκύπτει από το πρώτο
    ("now = datetime.now(timezone.utc)\n(now - timedelta(days=3)).date()", [f"(now - timedelta(days=3)).date() — {UTC_DATE}"]),
    ("def report():\n    now = datetime.now(timezone.utc)\n    return now.date()", [f"now.date() — {UTC_DATE}"]),
    ("async def report():\n    now = datetime.now(timezone.utc)\n    return now.date()", [f"now.date() — {UTC_DATE}"]),
    ("class A:\n    def report(self):\n        now = datetime.now(timezone.utc)\n        return now.date()",
     [f"now.date() — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\ndef report():\n    return now.date()", [f"now.date() — {UTC_DATE}"]),   # καθολικό όνομα
    ("now: datetime = datetime.now(timezone.utc)\nnow.date()", [f"now.date() — {UTC_DATE}"]),
    ("when = datetime.now(timezone.utc)\nlabel = str(when)\nlabel[:4]",
     [f"label[:4] — {STAMP_DATE}"]),                                              # κείμενο που βγήκε από ώρα UTC
    ("when = datetime.now(timezone.utc)\nlabel = when.isoformat()\nlabel[:10]", [f"label[:10] — {STAMP_DATE}"]),
    ("when = datetime.now(timezone.utc)\nstr(when)[:10]", [f"str(when)[:10] — {STAMP_DATE}"]),
    ("when = datetime.now(timezone.utc)\nwhen.isoformat().split('T')[0]", [f"when.isoformat().split('T')[0] — {STAMP_DATE}"]),
    ("a = datetime.now(timezone.utc)\nb = a\nb.date()", [f"b.date() — {UTC_DATE}"]),
    # --- κείμενο-σφραγίδα: κάθε τμήμα ημερομηνίας και κάθε σχήμα του ονόματος
    ("doc['created'][:10]", [f"doc['created'][:10] — {STAMP_DATE}"]),
    ("str(created)[:10]", [f"str(created)[:10] — {STAMP_DATE}"]),
    ("created_at[:4]", [f"created_at[:4] — {STAMP_DATE}"]),
    ("created_at[:7]", [f"created_at[:7] — {STAMP_DATE}"]),
    ("created_at[0:10]", [f"created_at[0:10] — {STAMP_DATE}"]),
    ("created_at[5:7]", [f"created_at[5:7] — {STAMP_DATE}"]),
    ("created_at[8:10]", [f"created_at[8:10] — {STAMP_DATE}"]),
    ("row.get('updated_at', '')[:7]", [f"row.get('updated_at', '')[:7] — {STAMP_DATE}"]),
    ("row['updated'][:10]", [f"row['updated'][:10] — {STAMP_DATE}"]),
    ("row['createdAt'][:10]", [f"row['createdAt'][:10] — {STAMP_DATE}"]),
    ("row['paid_ts'][:10]", [f"row['paid_ts'][:10] — {STAMP_DATE}"]),
    ("now_iso()[:10]", [f"now_iso()[:10] — {STAMP_DATE}"]),
    ("models.now_iso()[:7]", [f"models.now_iso()[:7] — {STAMP_DATE}"]),
    ("created_at.split('T')[0]", [f"created_at.split('T')[0] — {STAMP_DATE}"]),
    ("created_at.split(' ')[0]", [f"created_at.split(' ')[0] — {STAMP_DATE}"]),
    ("created_at.partition('T')[0]", [f"created_at.partition('T')[0] — {STAMP_DATE}"]),
    ("created_at.rsplit('T', 1)[0]", [f"created_at.rsplit('T', 1)[0] — {STAMP_DATE}"]),
    ("when.isoformat().split('T')[0]", [f"when.isoformat().split('T')[0] — {STAMP_DATE}"]),
    ("x.isoformat().split('T')[0]", [f"x.isoformat().split('T')[0] — {STAMP_DATE}"]),
    ("datetime.now(timezone.utc).isoformat()[:10]", [f"datetime.now(timezone.utc).isoformat()[:10] — {STAMP_DATE}"]),
    ("datetime.now(timezone.utc).isoformat().split('T')[0]",
     [f"datetime.now(timezone.utc).isoformat().split('T')[0] — {STAMP_DATE}"]),
    # --- ώρα UTC που προκύπτει από κλάδο, μετατροπή ή σύνθετη έκφραση
    ("(datetime.now(timezone.utc) if strict else other).date()",
     [f"(datetime.now(timezone.utc) if strict else other).date() — {UTC_DATE}"]),
    ("(other if strict else datetime.now(timezone.utc)).date()",
     [f"(other if strict else datetime.now(timezone.utc)).date() — {UTC_DATE}"]),
    ("datetime.now(timezone.utc).replace(hour=0).date()", [f"datetime.now(timezone.utc).replace(hour=0).date() — {UTC_DATE}"]),
    ("datetime.utcfromtimestamp(ts).date()", ["datetime.utcfromtimestamp() — ημερολόγιο UTC"]),
    ("when = datetime.utcfromtimestamp(ts)\nwhen.date()",
     ["datetime.utcfromtimestamp() — ημερολόγιο UTC", f"when.date() — {UTC_DATE}"]),
    ("when = datetime.now(timezone.utc)\nlabel = when.strftime('%Y%m%d')\nlabel[:4]",
     [f"when.strftime('%Y%m%d') — {UTC_DATE}", f"label[:4] — {STAMP_DATE}"]),
    ("datetime.now(timezone.utc).strftime('%-d.%-m')", [f"datetime.now(timezone.utc).strftime('%-d.%-m') — {UTC_DATE}"]),
    # --- πεδία ορατότητας: το όνομα το βλέπουν και οι εσωτερικές συναρτήσεις, εκτός αν το σκιάζουν
    ("def outer():\n    now = datetime.now(timezone.utc)\n    def inner():\n        return now.date()\n    return inner",
     [f"now.date() — {UTC_DATE}"]),
    ("b = a + timedelta(days=1)\na = datetime.now(timezone.utc)\nb.date()", [f"b.date() — {UTC_DATE}"]),
    ("now = datetime.now(timezone.utc)\nnow = now + timedelta(days=1)\nnow.date()", [f"now.date() — {UTC_DATE}"]),
    # --- ονόματα σφραγίδας
    ("stamp[:10]", [f"stamp[:10] — {STAMP_DATE}"]),
    ("last_stamp[:7]", [f"last_stamp[:7] — {STAMP_DATE}"]),
    ("row['CREATED_AT'][:10]", [f"row['CREATED_AT'][:10] — {STAMP_DATE}"]),
    ("created_at[0:4]", [f"created_at[0:4] — {STAMP_DATE}"]),
    ("created_at[0:7]", [f"created_at[0:7] — {STAMP_DATE}"]),
    ("created_at[5:10]", [f"created_at[5:10] — {STAMP_DATE}"]),
    # --- πολλά ευρήματα στο ίδιο κομμάτι κώδικα, με τη σειρά των γραμμών
    ("a = time.gmtime()\nb = created_at[:10]\nc = date.today()",
     ["time.gmtime()", f"created_at[:10] — {STAMP_DATE}", "date.today()"]),
])
def test_the_guard_detects_every_shape_of_the_same_mistakes(code, expected):
    assert [what for _line, what in _clock_reads(ast.parse(code))] == expected


@pytest.mark.parametrize("directive", list("aAbBcCdDeFgGhjmuUVwWxyY"))
def test_every_calendar_directive_of_strftime_on_a_utc_time_is_a_finding(directive):
    code = f"datetime.now(timezone.utc).strftime('%{directive}')"
    assert [what for _line, what in _clock_reads(ast.parse(code))] == [f"{code} — {UTC_DATE}"]


@pytest.mark.parametrize("directive", list("HIMpSfzZX%"))
def test_a_strftime_of_a_utc_time_without_calendar_directives_is_not(directive):
    code = f"datetime.now(timezone.utc).strftime('%{directive}')"
    assert list(_clock_reads(ast.parse(code))) == []


@pytest.mark.parametrize("code", [
    # --- ώρα Ελλάδας, ρητή ζώνη, ή τίποτα που να είναι ημερολόγιο
    "datetime.now(ATHENS).date()", "datetime.now(ZoneInfo('Europe/Athens')).date()", "datetime.now(timezone.utc)",
    "datetime.now(tz)", "datetime.now(tz=ATHENS)", "datetime.fromtimestamp(ts, timezone.utc)",
    "datetime.fromtimestamp(ts, tz=ATHENS)", "date.fromisoformat(value[:10])", "datetime.strptime(text, '%Y-%m-%d').date()",
    "stamp.astimezone(ATHENS).date()", "invoice.get('date', '')[:10]", "items[:10]", "athens_today()", "time.time()",
    "time.perf_counter()", "number[:10]",
    "datetime.now(ATHENS).strftime('%Y%m%d-%H%M%S')", "datetime.now(ATHENS).year",
    "athens_today().year", "athens_today().strftime('%d/%m/%Y')", "athens_date_of(created_at)",
    # --- ώρα UTC που χρησιμοποιείται ως στιγμή, όχι ως ημερολόγιο
    "datetime.now(timezone.utc).isoformat()", "datetime.now(timezone.utc) + timedelta(days=1)",
    "datetime.now(timezone.utc).timestamp()", "int(datetime.now(timezone.utc).timestamp())",
    "datetime.now(timezone.utc) - started", "(datetime.now(timezone.utc) - started).days",
    "(datetime.now(timezone.utc) - started).total_seconds()", "datetime.now(timezone.utc).strftime('%H:%M:%S')",
    "datetime.now(timezone.utc).replace(microsecond=0).isoformat()", "stamp = datetime.now(timezone.utc).isoformat()",
    "datetime.now(timezone.utc).astimezone(ATHENS).date()", "datetime.now(timezone.utc).astimezone(ATHENS).strftime('%Y')",
    "datetime.fromtimestamp(ts, timezone.utc).astimezone(ATHENS).date()", "now_iso()", "_now()", "now_iso() > cutoff",
    "now = datetime.now(timezone.utc)\nnow.astimezone(ATHENS).date()",
    "now = datetime.now(timezone.utc)\nexpires = now + timedelta(days=30)\nexpires.isoformat()",
    "now = datetime.now(timezone.utc)\nelapsed = now - started\nelapsed.days",
    # --- ονόματα που ξαναορίζονται, παράμετροι, άλλα πεδία ορατότητας
    "now = datetime.now(timezone.utc)\nnow = now.astimezone(ATHENS)\nnow.date()",
    "now = datetime.now(timezone.utc)\nnow = athens_now()\nnow.date()",
    "now = datetime.now(timezone.utc)\n\ndef report(now):\n    return now.date()",
    "def report(now):\n    return now.date()", "def report(now=None):\n    now = now or athens_now()\n    return now.date()",
    "def report(now=None):\n    now = now or datetime.now(timezone.utc)\n    return now.date()",
    "def a():\n    now = datetime.now(timezone.utc)\n    return now\n\ndef b():\n    return now.date()",
    "for now in days:\n    now.date()", "with clock() as now:\n    now.date()", "now, other = clock()\nnow.date()",
    "now = datetime.now(timezone.utc)\nlambda now: now.date()",
    "now = athens_now()\nnow.date()", "when = parse(text)\nwhen.date()", "when = datetime.fromisoformat(text)\nwhen.date()",
    "now = datetime.now(timezone.utc)\nfor now in days:\n    now.date()",
    "now = datetime.now(timezone.utc)\nwith clock() as now:\n    now.date()",
    "now = datetime.now(timezone.utc)\nfrom clock import now\nnow.date()",
    "now = datetime.now(timezone.utc)\nimport now\nnow.date()",
    "now = datetime.now(timezone.utc)\ndef now():\n    pass\nnow.date()",
    "now = datetime.now(timezone.utc)\nclass now:\n    pass\nnow.date()",
    "now = datetime.now(timezone.utc)\ntry:\n    pass\nexcept Exception as now:\n    now.date()",
    "now = datetime.now(timezone.utc)\nnow //= 2\nnow.date()",
    "now = datetime.now(timezone.utc)\nvalues = [now for now in days]\nnow.date()",
    "now = datetime.now(timezone.utc)\nnow, other = clock()\nnow.date()",
    "now = datetime.now(timezone.utc)\n(now := athens_now())\nnow.date()",
    "def outer():\n    now = datetime.now(timezone.utc)\n    def inner(now):\n        return now.date()\n    return inner",
    # --- ονόματα που δεν είναι σφραγίδες
    "order['date'].split('T')[0]", "expense['date'][:7]", "row['date'][:4]", "created_by[:3]", "created_by[:4]",
    "code[:4]", "number[:7]", "invoice['number'][5:7]", "created_at[:3]", "created_at[:11]", "created_at[2:10]",
    "created_at[:10:2]", "created_at[0]", "created_at.split(',')[0]", "name.split('T')[0]", "created_at.split('T')[1]",
    "formats[:4]", "stat[:7]", "category[:10]", "created_at[:limit]", "created_at[start:10]", "created_at[:]",
    "dataAtlas[:10]", "boatAnchor[:7]", "creAtion[:10]", "now_iso().isoformat()",
    "datetime.now(timezone.utc).strftime(format='%H:%M')", "utc_now().isoformat()", "now_utc().timestamp()",
    # --- μονάδα `time` χωρίς ανάγνωση ρολογιού
    "time.strftime('%H:%M', t)", "time.strftime('%H:%M', parsed)", "time.monotonic()", "time.sleep(1)", "time.timezone",
    "import time as _t\n_t.time()", "import time as _t\n_t.monotonic()", "from time import sleep\nsleep(1)",
    "from time import time\ntime()", "from time import monotonic as tick\ntick()", "localtime()",
    "obj.localtime()", "clock.gmtime()",
])
def test_the_guard_leaves_correct_code_alone(code):
    assert list(_clock_reads(ast.parse(code))) == []


# ==================================================================== συμπεριφορά ====

def test_days_since_invoice_counts_from_the_greek_date(athens_clock):
    import invoice_services
    assert invoice_services.days_since_invoice({"date": "2026-12-31"}) == 1        # με UTC θα έβγαινε 0


def test_reminder_due_uses_the_greek_date(athens_clock):
    import automation
    invoice = {"status": "issued", "date": "2026-12-28", "reminder_count": 0}
    assert automation.reminder_due(invoice, {"reminder_after_days": 4}) is True     # 4 ημέρες στην Ελλάδα, 3 σε UTC
    assert automation.reminder_due(invoice, {"reminder_after_days": 5}) is False


def test_open_banking_expiry_and_fallback_booking_date_use_the_greek_date(athens_clock):
    import openbanking
    assert openbanking.requisition_expiring({"gocardless_access_valid_until": "2027-01-08"}, within_days=7) is True
    assert openbanking.requisition_expiring({"gocardless_access_valid_until": "2027-01-09"}, within_days=7) is False
    assert openbanking.normalize_transaction({"transactionAmount": {"amount": "10.00", "currency": "EUR"}})["date"] == "2027-01-01"


def test_inbound_mydata_document_without_issue_date_gets_the_greek_date(athens_clock):
    import mydata_client
    xml = ("<RequestedDoc><invoiceDoc><invoice><uid>U1</uid><mark>400001</mark>"
           "<issuer><vatNumber>999999999</vatNumber><name>Προμηθευτής</name></issuer>"
           "<counterpart><vatNumber>123456783</vatNumber></counterpart>"
           "<invoiceHeader><invoiceType>1.1</invoiceType></invoiceHeader>"
           "<invoiceSummary><totalNetValue>100</totalNetValue><totalVatAmount>24</totalVatAmount></invoiceSummary>"
           "</invoice></invoiceDoc></RequestedDoc>")
    docs = mydata_client.parse_requested_docs(xml, "123456783")
    assert docs and docs[0]["date"] == "2027-01-01"


def test_autopilot_snapshot_and_daily_digest_key_use_the_greek_date(athens_clock, monkeypatch):
    import automation
    import outbox
    snapshot = _run(automation.firm_snapshot("firm-clock", {}))
    assert snapshot["today"] == date(2027, 1, 1)

    keys = []

    async def capture(firm_id, kind, payload, dedupe_key=None, **kw):
        keys.append(dedupe_key)
        return {"id": "x"}

    monkeypatch.setattr(outbox, "enqueue", capture)
    settings = {"digest_email": "d@example.gr", "smtp_host": "smtp.example.gr", "smtp_user": "u", "smtp_password": "p"}
    monkeypatch.setattr(automation, "smtp_ready", lambda s: True)
    _run(automation.job_digest("firm-clock", settings, force=True))
    assert keys == ["email_digest:2027-01-01"]


def test_letterhead_documents_are_dated_in_athens(athens_clock):
    import io
    import docx
    import docx_utils
    data = docx_utils.build_letter_docx({"company_name": "Γραφείο"}, "Προς Δοκιμή", "Θέμα", "Κείμενο.")
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    assert "Αθήνα, 1 Ιανουαρίου 2027" in text


@pytest.fixture()
def client(athens_clock):
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "clock@example.gr", "password": "Str0ngPass!", "name": "Χρήστης"})
        assert r.status_code == 200, r.text
        yield c


def test_dashboard_labels_the_greek_month(client):
    labels = [o["label"] for o in client.get("/api/dashboard/summary").json()["obligations"]]
    assert "ΕΦΚΑ εισφορές 01/2027" in labels, labels


def test_todays_hearing_alert_uses_the_greek_date(client):
    r = client.post("/api/hearings", json={"court": "Πρωτοδικείο Αθηνών", "date": "2027-01-01", "pinakio_number": 12})
    assert r.status_code == 200, r.text
    messages = [a["message"] for a in client.get("/api/hearings/alerts/list").json()]
    assert any("Σήμερα" in m and "Νο 12" in m for m in messages), messages


def test_drafting_templates_are_dated_in_athens(client):
    templates = client.get("/api/drafting/templates").json()["builtin"]
    dated = next(t for t in templates if "{date}" in t["body"])
    body = client.post("/api/drafting/render", json={"template_id": dated["id"], "variables": {}}).json()["body"]
    assert "01/01/2027" in body and "31/12/2026" not in body


def test_tax_calendar_and_dashboard_deadlines_use_the_greek_date(client):
    # Με ημερομηνία UTC (31/12/2026) η προθεσμία ΕΦΚΑ θα έβγαινε «σήμερα».
    assert client.get("/api/tax-calendar").json()["next_efka_deadline"] == "2027-01-29"
    assert client.get("/api/dashboard/summary").json()["next_efka_deadline"] == "2027-01-29"


def test_cash_flow_horizon_starts_on_the_greek_date(client):
    horizon = client.get("/api/analytics/cashflow").json()["horizon"]
    assert horizon["start"] == "2027-01-01"
