"""Γύρος 5 — ο server δεν διαβάζει την ώρα του μηχανήματος για να βρει «σήμερα».

Ο server τρέχει σε UTC. Μεταξύ 00:00 και 03:00 ώρα Ελλάδας (21:00–24:00 UTC) η `date.today()` δίνει ακόμη τη χθεσινή
ημέρα: το dashboard έδειχνε τον προηγούμενο μήνα την 1η του μήνα, οι υπενθυμίσεις και οι προθεσμίες υπολογίζονταν με
μία μέρα καθυστέρηση, το ημερολογιακό κλειδί του ημερήσιου email άλλαζε σε λάθος ώρα. Το λάθος είχε διορθωθεί στα
παραστατικά (`athens_today`) αλλά έμεινε σε 20 άλλα σημεία.

Δύο κατηγορίες ελέγχων: (1) στατικός φρουρός — κανένα module δεν καλεί `date.today()` / `datetime.now()` χωρίς ζώνη /
`utcnow()`· (2) συμπεριφορά — με παγωμένο ρολόι (23:30 UTC της 31/12 = 01:30 της 1/1 στην Ελλάδα) κάθε σημείο που
άλλαξε βλέπει την ελληνική ημερομηνία.
"""
import ast
import asyncio
import pathlib
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ATHENS = ZoneInfo("Europe/Athens")
FROZEN = datetime(2027, 1, 1, 1, 30, tzinfo=ATHENS)          # = 2026-12-31 23:30 UTC


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# ==================================================================== στατικός φρουρός ====

def _clock_reads(tree):
    """(γραμμή, περιγραφή) για κάθε ανάγνωση της τοπικής ώρας του μηχανήματος. Το AST αγνοεί σχόλια και docstrings."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr, owner = node.func.attr, node.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute) else ""
        if attr == "today" and owner_name in {"date", "_date", "datetime", "dt"}:
            yield node.lineno, f"{owner_name}.today()"
        elif attr == "utcnow":
            yield node.lineno, f"{owner_name}.utcnow()"
        elif attr == "now" and owner_name in {"datetime", "dt"} and not node.args and not node.keywords:
            yield node.lineno, f"{owner_name}.now() χωρίς ζώνη ώρας"


def _backend_sources():
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in {"tests", "__pycache__"}:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_module_reads_the_machine_clock_for_today():
    offenders = [f"{rel}:{line} {what}" for rel, tree in _backend_sources() for line, what in _clock_reads(tree)]
    assert not offenders, (
        "Η «σημερινή» ημερομηνία πρέπει να βγαίνει από tax_utils.athens_today() (ο server τρέχει σε UTC):\n  "
        + "\n  ".join(offenders))


def test_the_guard_itself_detects_the_patterns_it_forbids():
    code = ("import datetime as dt\nfrom datetime import date, datetime\n"
            "a = date.today()\nb = datetime.now()\nc = datetime.utcnow()\nd = dt.date.today()\n"
            "e = datetime.now(tz)\nf = datetime.now(timezone.utc)\n")
    found = [what for _line, what in _clock_reads(ast.parse(code))]
    assert found == ["date.today()", "datetime.now() χωρίς ζώνη ώρας", "datetime.utcnow()", "date.today()"]


# ==================================================================== συμπεριφορά ====

@pytest.fixture()
def athens_clock(monkeypatch):
    """Παγώνει το ρολόι του tax_utils: 01:30 της 1/1/2027 στην Ελλάδα, 23:30 της 31/12/2026 σε UTC."""
    import tax_utils

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN.astimezone(tz) if tz else FROZEN.astimezone(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(tax_utils, "datetime", FrozenDatetime)
    assert tax_utils.athens_today() == date(2027, 1, 1)
    return FROZEN


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
