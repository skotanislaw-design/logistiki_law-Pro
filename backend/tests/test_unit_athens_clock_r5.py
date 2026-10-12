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

_STAMP_VALUE = re.compile(r"(_at\b|_now\(\)|\.isoformat\(\)|stamp)", re.I)


def _mentions_utc(node) -> bool:
    return "utc" in ast.unparse(node).lower()


def _clock_reads(tree):
    """(γραμμή, περιγραφή) για κάθε ανάγνωση της ώρας του μηχανήματος και κάθε ημερομηνία UTC που θα εμφανιζόταν ως ελληνική.

    Το AST αγνοεί σχόλια και docstrings και δεν εξαρτάται από το όνομα με το οποίο εισάγεται το `date` (`date as _d`)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            part = node.slice
            if (isinstance(part, ast.Slice) and part.lower is None and isinstance(part.upper, ast.Constant)
                    and part.upper.value == 10 and _STAMP_VALUE.search(ast.unparse(node.value))):
                yield node.lineno, f"{ast.unparse(node.value)}[:10] — η ημερομηνία UTC μιας σφραγίδας"
            continue
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, owner = node.func.attr, node.func.value
        who = ast.unparse(owner)
        if attr == "today":
            yield node.lineno, f"{who}.today()"
        elif attr == "utcnow":
            yield node.lineno, f"{who}.utcnow()"
        elif attr == "now":
            zone = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "tz"), None)
            if zone is None or (isinstance(zone, ast.Constant) and zone.value is None):
                yield node.lineno, f"{who}.now() χωρίς ζώνη ώρας"
        elif attr == "fromtimestamp":
            if len(node.args) < 2 and not any(k.arg == "tz" for k in node.keywords):
                yield node.lineno, f"{who}.fromtimestamp() χωρίς ζώνη ώρας"
        elif who == "time" and attr in {"localtime", "ctime", "asctime", "mktime"}:
            yield node.lineno, f"time.{attr}()"
        elif attr == "date" and isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute):
            inner = owner.func.attr
            if inner == "now" and any(_mentions_utc(a) for a in [*owner.args, *(k.value for k in owner.keywords)]):
                yield node.lineno, f"{ast.unparse(owner)}.date() — η ημερομηνία UTC"
            elif inner == "fromisoformat":
                yield node.lineno, "fromisoformat(...).date() — χωρίς μετατροπή σε ώρα Ελλάδας (για ημερομηνίες: date.fromisoformat)"


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


@pytest.mark.parametrize("code", [
    "datetime.now(ATHENS).date()", "datetime.now(ZoneInfo('Europe/Athens')).date()", "datetime.now(timezone.utc)",
    "datetime.now(tz)", "datetime.now(tz=ATHENS)", "datetime.fromtimestamp(ts, timezone.utc)",
    "datetime.fromtimestamp(ts, tz=ATHENS)", "date.fromisoformat(value[:10])", "datetime.strptime(text, '%Y-%m-%d').date()",
    "stamp.astimezone(ATHENS).date()", "invoice.get('date', '')[:10]", "items[:10]", "athens_today()", "time.time()",
    "time.perf_counter()", "number[:10]",
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
