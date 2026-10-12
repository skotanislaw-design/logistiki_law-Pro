"""Γύρος 5 (ανασκόπηση 2) — «Ανείσπρακτα» και φορολογικά σύνολα χωρίς σιωπηλά όρια.

Οι υπολογισμοί διάβαζαν «τα πρώτα N έγγραφα» με `to_list(N)`: 2.000 στο dashboard και στο cashflow, 1.000 στη λίστα
τιμολογίων, 500 στον Copilot, 200 στην πύλη του πελάτη, 5.000 στο στιγμιότυπο της ενημέρωσης, 10.000 στην περίοδο ΦΠΑ
και 20.000 στη δήλωση Φ2. Ό,τι περίσσευε δεν διαβαζόταν — και συνήθως ήταν ό,τι ήταν παλιότερο, δηλαδή τα ληξιπρόθεσμα.
Το αποτέλεσμα ήταν ένας μικρότερος αριθμός, χωρίς κανένα σφάλμα.

Τα tests εδώ δουλεύουν με ΠΡΑΓΜΑΤΙΚΟΣ όγκους: ο διπλός της βάσης (βλ. conftest) κόβει το `to_list(n)` όπως ο Motor της
παραγωγής, οπότε ένα όριο που επιστρέφει θα ξαναφανεί ως αποτυχία. Το ανεξόφλητο είναι πάντα το ΤΕΛΕΥΤΑΙΟ έγγραφο που
εισάγεται (ή το παλαιότερο ως προς την ημερομηνία) — το χειρότερο σημείο για κάθε όριο.
"""
import ast
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tax_utils import athens_today

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        yield c


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office(client):
    r = client.post("/api/auth/register",
                    json={"email": "caps@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "caps@example.gr"}))
    _run(db.users.update_one({"email": "caps@example.gr"}, {"$set": {"role": "admin"}}))
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company",
                                              "email": "alpha@example.gr"}).json()["id"]
    return SimpleNamespace(alpha=alpha, firm_id=user["firm_id"], user={"email": "caps@example.gr", "firm_id": user["firm_id"]})


def _today() -> str:
    return athens_today().isoformat()


def _invoice(office, *, status="issued", total=124.0, subtotal=100.0, vat=24.0, date=None, created_at=None, client_id=None):
    return {
        "firm_id": office.firm_id, "client_id": client_id or office.alpha, "client_name": "ΑΛΦΑ ΑΕ", "number": "",
        "date": date or _today(), "line_items": [], "subtotal": subtotal, "vat_amount": vat, "total": total,
        "vat_breakdown": {"24": vat}, "status": status, "mydata_status": "synced", "invoice_type": "2.1",
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def _expense(office, *, date, amount=124.0, vat=24.0):
    return {"firm_id": office.firm_id, "vendor": "Προμηθευτής", "date": date, "amount": amount, "vat_amount": vat,
            "vat_rate": 24, "category": "Λοιπά", "created_at": datetime.now(timezone.utc).isoformat()}


def _insert(collection: str, docs: list[dict]) -> None:
    """Εισαγωγή όγκου απευθείας στη βάση. Τα μοναδικά ευρετήρια του server φεύγουν πρώτα: ο διπλός της βάσης ελέγχει
    τη μοναδικότητα συγκρίνοντας με κάθε υπάρχον έγγραφο (τετραγωνικό κόστος) και 20.000 εγγραφές θα κρατούσαν λεπτά."""
    from db import db

    async def go():
        await db[collection].drop_indexes()
        await db[collection].insert_many(docs)
    _run(go())


def _created(i: int) -> str:
    return (datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)).isoformat()


# ============================================================ 1. Dashboard ====

def test_dashboard_unpaid_survives_more_than_2000_other_invoices(client, office):
    """Παλιά: `to_list(2000)` πάνω σε όλα τα εκδοθέντα+εξοφλημένα, χωρίς σειρά — το 2.001ο (ανεξόφλητο) δεν διαβαζόταν."""
    _insert("invoices", [_invoice(office, status="paid", date="2020-03-01") for _ in range(2000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0, date=_today())])      # το 2.001ο έγγραφο
    body = client.get("/api/dashboard/summary").json()
    assert body["unpaid_total"] == 124.0
    assert body["unpaid_count"] == 1


def test_dashboard_unpaid_includes_an_old_invoice_outside_the_reporting_window(client, office):
    """Τα ανείσπρακτα δεν έχουν παράθυρο ημερομηνιών: ένα τιμολόγιο του 2019 που δεν εισπράχθηκε μετράει."""
    _insert("invoices", [_invoice(office, status="issued", total=124.0, date="2019-05-02")])
    body = client.get("/api/dashboard/summary").json()
    assert (body["unpaid_total"], body["unpaid_count"]) == (124.0, 1)
    assert body["income_total"] == 0                 # ...αλλά δεν είναι έσοδο του τρέχοντος μήνα


def test_dashboard_month_totals_survive_more_than_2000_old_expenses(client, office):
    _insert("expenses", [_expense(office, date="2020-03-01") for _ in range(2000)])
    _insert("expenses", [_expense(office, date=_today(), amount=124.0, vat=24.0)])
    body = client.get("/api/dashboard/summary").json()
    assert body["expense_total"] == 100.0
    assert body["input_vat"] == 24.0


def test_dashboard_month_income_survives_more_than_2000_old_invoices(client, office):
    _insert("invoices", [_invoice(office, status="paid", date="2020-03-01") for _ in range(2000)])
    _insert("invoices", [_invoice(office, status="paid", total=62.0, subtotal=50.0, vat=12.0, date=_today())])
    body = client.get("/api/dashboard/summary").json()
    assert (body["income_total"], body["output_vat"]) == (50.0, 12.0)
    assert body["year_income"] == 50.0


def test_dashboard_chart_covers_the_last_six_months_not_the_first_2000_documents(client, office):
    today = athens_today()
    first_of_this_month = today.replace(day=1)
    earlier = (first_of_this_month - timedelta(days=95)).replace(day=10).isoformat()    # ~3 μήνες πίσω, μέσα στο γράφημα
    _insert("invoices", [_invoice(office, status="paid", date="2020-03-01") for _ in range(2000)])
    _insert("invoices", [_invoice(office, status="paid", total=124.0, subtotal=100.0, vat=24.0, date=earlier)])
    chart = client.get("/api/dashboard/summary").json()["chart_data"]
    assert len(chart) == 6
    assert sum(point["income"] for point in chart) == 100.0


# ================================================== 2. Λίστα τιμολογίων ====

def test_list_returns_every_issued_invoice_even_when_it_is_the_oldest_of_more_than_1000(client, office):
    """Παλιά: τα 1.000 νεότερα ανά `created_at` — το παλαιότερο ανεξόφλητο έμενε έξω και ο KPI «Ανείσπρακτα» έβγαινε μικρός."""
    _insert("invoices", [_invoice(office, status="issued", total=124.0, created_at="2019-01-01T00:00:00+00:00", date="2019-01-01")])
    _insert("invoices", [_invoice(office, status="paid", created_at=_created(i)) for i in range(1000)])
    r = client.get("/api/invoices")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1001
    assert [x for x in rows if x["status"] == "issued"][0]["outstanding"] == 124.0
    assert "x-closed-truncated" not in r.headers                 # τα κλειστά χωρούν ακριβώς: τίποτα δεν κόπηκε


def test_list_flags_truncation_of_old_closed_invoices_but_never_of_open_ones(client, office):
    _insert("invoices", [_invoice(office, status="draft", created_at="2018-01-01T00:00:00+00:00")])
    _insert("invoices", [_invoice(office, status="issued", created_at="2018-01-02T00:00:00+00:00")])
    _insert("invoices", [_invoice(office, status="paid", created_at=_created(i)) for i in range(1001)])
    r = client.get("/api/invoices")
    rows = r.json()
    assert r.headers.get("x-closed-truncated") == "1"
    assert sum(1 for x in rows if x["status"] == "paid") == 1000
    assert sum(1 for x in rows if x["status"] == "draft") == 1          # το παλιό πρόχειρο δεν κόπηκε
    assert sum(1 for x in rows if x["status"] == "issued") == 1
    assert rows[-1]["created_at"] >= "2018-01-01"                       # ταξινόμηση: νεότερα πρώτα
    created = [x["created_at"] for x in rows]
    assert created == sorted(created, reverse=True)


def test_list_keeps_newest_closed_invoices_and_drops_the_oldest_ones(client, office):
    _insert("invoices", [_invoice(office, status="paid", created_at=_created(i)) for i in range(1002)])
    rows = client.get("/api/invoices").json()
    stamps = {x["created_at"] for x in rows}
    assert _created(1001) in stamps and _created(2) in stamps
    assert _created(0) not in stamps and _created(1) not in stamps


# ================================================ 3. Cashflow & κερδοφορία ====

def test_cashflow_includes_an_unpaid_invoice_beyond_the_2000th(client, office):
    _insert("invoices", [_invoice(office, status="issued", total=0.0, subtotal=0.0, vat=0.0) for _ in range(2000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0)])
    body = client.get("/api/analytics/cashflow").json()
    incomes = [e for e in body["events"] if e["type"] == "income"]
    assert [e["amount"] for e in incomes] == [124.0]
    assert body["total_expected_income"] == 124.0


def test_cashflow_expense_average_uses_every_expense_of_the_last_three_months(client, office):
    from routes_analytics import _add_months
    recent = (_add_months(athens_today(), -1)).isoformat()
    _insert("expenses", [_expense(office, date=recent, amount=1.0, vat=0.0) for _ in range(3000)])
    _insert("expenses", [_expense(office, date=recent, amount=3000.0, vat=0.0)])
    body = client.get("/api/analytics/cashflow").json()
    # 3.001 έξοδα του ίδιου μήνα: μέσος όρος = (3000·1 + 3000)/1 μήνα. Με `to_list(3000)` το τελευταίο έλειπε (3.000).
    assert body["expense_by_category"]["Λοιπά"] == 6000.0


def test_profitability_counts_an_invoice_beyond_the_5000th(client, office):
    _insert("invoices", [_invoice(office, status="paid", total=0.0, subtotal=0.0, vat=0.0) for _ in range(5000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0, subtotal=100.0)])
    totals = client.get("/api/analytics/profitability").json()["totals"]
    assert totals["invoice_count"] == 5001
    assert totals["revenue"] == 100.0
    assert totals["unpaid"] == 124.0


# ======================================================== 4. Copilot ====

def test_copilot_context_reports_old_unpaid_invoices_beyond_the_newest_500(office):
    """Παλιά: ο βοηθός έβλεπε τα 500 νεότερα τιμολόγια — ένα παλιό ανεξόφλητο δεν υπήρχε στο «ανείσπρακτα_σύνολο»."""
    from copilot import build_context
    _insert("invoices", [_invoice(office, status="issued", total=124.0, created_at="2019-01-01T00:00:00+00:00", date="2019-01-01")])
    _insert("invoices", [_invoice(office, status="paid", created_at=_created(i)) for i in range(500)])
    context = _run(build_context(office.user))
    assert context["ανείσπρακτα_σύνολο"] == "124,00 €"
    assert context["ανεξόφλητα_πλήθος"] == 1
    assert [row["οφειλόμενο"] for row in context["ανεξόφλητα_τιμολόγια"]] == ["124,00 €"]


def test_copilot_context_counts_drafts_and_recent_invoices_without_loading_everything(office):
    from copilot import build_context
    _insert("invoices", [_invoice(office, status="draft", created_at="2019-01-01T00:00:00+00:00")])
    _insert("invoices", [_invoice(office, status="paid", created_at=_created(i)) for i in range(600)])
    context = _run(build_context(office.user))
    assert context["πρόχειρα_τιμολόγια"] == 1                 # παλιά: μετρούσε μόνο μέσα στα 500 νεότερα (0)
    assert len(context["τελευταία_τιμολόγια"]) == 10


def test_copilot_context_month_totals_do_not_depend_on_the_newest_500(office):
    from copilot import build_context
    _insert("invoices", [_invoice(office, status="paid", total=124.0, subtotal=100.0, vat=24.0, date=_today(),
                                  created_at="2019-01-01T00:00:00+00:00")])
    _insert("invoices", [_invoice(office, status="paid", total=0.0, subtotal=0.0, vat=0.0, date="2020-01-01",
                                  created_at=_created(i)) for i in range(500)])
    _insert("expenses", [_expense(office, date=_today(), amount=124.0, vat=24.0)])
    context = _run(build_context(office.user))
    assert context["τρέχων_μήνας"]["καθαρά_έσοδα"] == "100,00 €"
    assert context["τρέχων_μήνας"]["ΦΠΑ_εκροών"] == "24,00 €"
    assert context["τρέχων_μήνας"]["ΦΠΑ_εισροών"] == "24,00 €"


def test_copilot_context_caps_client_names_but_reports_the_total(office):
    from copilot import build_context, CLIENT_NAMES_LIMIT
    _insert("clients", [{"firm_id": office.firm_id, "name": f"Πελάτης {i:04d}", "afm": "", "client_type": "individual"}
                        for i in range(CLIENT_NAMES_LIMIT + 5)])
    context = _run(build_context(office.user))
    assert len(context["πελάτες"]) == CLIENT_NAMES_LIMIT
    assert context["πελάτες_σύνολο"] == CLIENT_NAMES_LIMIT + 6          # + ο ΑΛΦΑ ΑΕ του office


# ==================================================== 5. Πύλη πελάτη ====

def _portal(client, client_id):
    token = client.post("/api/client-portal/invite", json={"client_id": client_id}).json()["token"]
    r = client.get(f"/api/portal/{token}")
    assert r.status_code == 200, r.text
    return r.json()


def test_portal_shows_the_oldest_unpaid_invoice_even_with_more_than_200_paid_ones(client, office):
    """Παλιά: τα 200 νεότερα ανά ημερομηνία — ο πελάτης με πολλά τιμολόγια έβλεπε μικρότερη οφειλή από την πραγματική."""
    _insert("invoices", [_invoice(office, status="issued", total=124.0, date="2018-01-01")])
    _insert("invoices", [_invoice(office, status="paid", date=(datetime(2022, 1, 1) + timedelta(days=i)).date().isoformat())
                         for i in range(200)])
    data = _portal(client, office.alpha)
    assert data["totals"]["unpaid_total"] == 124.0
    assert data["totals"]["unpaid_count"] == 1
    assert data["totals"]["paid_count"] == 200
    assert len(data["invoices"]) == 201


def test_portal_lists_at_most_200_paid_invoices_but_counts_all_of_them(client, office):
    _insert("invoices", [_invoice(office, status="paid", date=(datetime(2020, 1, 1) + timedelta(days=i)).date().isoformat())
                         for i in range(230)])
    data = _portal(client, office.alpha)
    assert data["totals"]["paid_count"] == 230                  # παλιά: 200 (το μήκος της κομμένης λίστας)
    assert sum(1 for i in data["invoices"] if i["status"] == "paid") == 200
    dates = [i["date"] for i in data["invoices"]]
    assert dates == sorted(dates, reverse=True)
    assert "2020-01-01" not in dates                            # κόβονται τα παλαιότερα εξοφλημένα, όχι τα νεότερα


def test_portal_never_mixes_in_another_clients_unpaid_invoices(client, office):
    other = client.post("/api/clients", json={"name": "ΒΗΤΑ ΕΠΕ", "afm": "987654324", "client_type": "company"}).json()["id"]
    _insert("invoices", [_invoice(office, status="issued", total=124.0, client_id=other)])
    _insert("invoices", [_invoice(office, status="issued", total=248.0, subtotal=200.0, vat=48.0)])
    data = _portal(client, office.alpha)
    assert data["totals"]["unpaid_total"] == 248.0
    assert len(data["invoices"]) == 1


# ================================== 6. Ενημέρωση Autopilot & περίοδος ΦΠΑ ====

def test_digest_snapshot_counts_unpaid_invoices_beyond_the_5000th_document(office):
    from automation import firm_snapshot
    _insert("invoices", [_invoice(office, status="paid", date="2020-03-01") for _ in range(5000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0, date="2019-05-02")])
    snapshot = _run(firm_snapshot(office.firm_id, {}))
    assert snapshot["unpaid_total"] == 124.0
    assert [row["open_amount"] for row in snapshot["unpaid"]] == [124.0]


def test_digest_snapshot_month_figures_come_from_the_month_not_the_first_5000_documents(office):
    from automation import firm_snapshot
    _insert("invoices", [_invoice(office, status="paid", total=0.0, subtotal=0.0, vat=0.0, date="2020-03-01") for _ in range(5000)])
    _insert("invoices", [_invoice(office, status="paid", total=124.0, subtotal=100.0, vat=24.0, date=_today())])
    _insert("expenses", [_expense(office, date="2020-03-01") for _ in range(5000)])
    _insert("expenses", [_expense(office, date=_today(), amount=62.0, vat=12.0)])
    snapshot = _run(firm_snapshot(office.firm_id, {}))
    assert (snapshot["income_net"], snapshot["output_vat"]) == (100.0, 24.0)
    assert (snapshot["expense_net"], snapshot["input_vat"]) == (50.0, 12.0)


def test_vat_period_includes_every_invoice_and_expense_of_the_period(office):
    from automation import compute_vat_period
    from datetime import date
    period = {"period": "2024-03", "start": date(2024, 3, 1), "end": date(2024, 3, 31), "due": date(2024, 4, 30)}
    _insert("invoices", [_invoice(office, status="paid", total=0.0, subtotal=0.0, vat=0.0, date="2024-03-10") for _ in range(10000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0, subtotal=100.0, vat=24.0, date="2024-03-31")])
    _insert("expenses", [_expense(office, date="2024-03-10", amount=0.0, vat=0.0) for _ in range(10000)])
    _insert("expenses", [_expense(office, date="2024-03-31", amount=62.0, vat=12.0)])
    vat = _run(compute_vat_period(office.firm_id, period))
    assert vat.invoice_count == 10001 and vat.expense_count == 10001
    assert (vat.output_vat, vat.input_vat, vat.vat_due) == (24.0, 12.0, 12.0)


def test_vat_return_includes_every_document_of_the_period(client, office):
    """Η δήλωση Φ2 (αντίγραφο για το TAXISnet) διάβαζε μέχρι 20.000 παραστατικά και 20.000 έξοδα."""
    from db import db
    _run(db.vat_periods.insert_one({"firm_id": office.firm_id, "period": "2024-03", "period_start": "2024-03-01",
                                    "period_end": "2024-03-31", "due_date": "2024-04-30", "status": "computed"}))
    _insert("invoices", [_invoice(office, status="paid", total=0.0, subtotal=0.0, vat=0.0, date="2024-03-10") for _ in range(20000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0, subtotal=100.0, vat=24.0, date="2024-03-31")])
    _insert("expenses", [_expense(office, date="2024-03-10", amount=0.0, vat=0.0) for _ in range(20000)])
    _insert("expenses", [_expense(office, date="2024-03-31", amount=62.0, vat=12.0)])
    r = client.get("/api/automation/vat-periods/2024-03/return")
    assert r.status_code == 200, r.text
    lines = {line["key"]: line["amount"] for line in r.json()["lines"]}
    assert lines["total_output_vat"] == 24.0           # παλιά: 0 — το παραστατικό ήταν το 20.001ο
    assert lines["total_input_vat"] == 12.0
    assert lines["vat_payable"] == 12.0


# ================================================ 7. Συμφωνία τράπεζας ====

def test_bank_match_pool_has_every_open_invoice_not_just_the_first_2000(office):
    from routes_bank import build_match_pool
    _insert("invoices", [_invoice(office, status="issued", total=0.0, subtotal=0.0, vat=0.0) for _ in range(2000)])
    _insert("invoices", [_invoice(office, status="issued", total=124.0)])
    pool = _run(build_match_pool(office.firm_id, {}))
    assert [inv["_due"] for inv in pool] == [124.0]                # τα αρνητικά/μηδενικά σύνολα εξαιρούνται· το 2.001ο μένει


# ====================================================== 8. Πελάτες ====

def test_client_list_is_not_truncated_at_1000(client, office):
    _insert("clients", [{"firm_id": office.firm_id, "name": f"Α{i:05d}", "afm": "", "client_type": "individual",
                         "country": "GR"} for i in range(1000)])
    _insert("clients", [{"firm_id": office.firm_id, "name": "Ωμέγα Τελευταίος", "afm": "", "client_type": "individual",
                         "country": "GR"}])
    names = [c["name"] for c in client.get("/api/clients").json()]
    assert len(names) == 1002                                      # + ο ΑΛΦΑ ΑΕ
    assert "Ωμέγα Τελευταίος" in names


# =========================================== 9. Κανόνας: ούτε σιωπηλά όρια ====

CRITICAL_COLLECTIONS = ("invoices", "expenses", "clients")


def _capped_reads(path: Path):
    """(γραμμή, συλλογή, όριο) για κάθε `db.<συλλογή>.find(...)....to_list(N)` με N ≥ 100 χωρίς ρητό `.limit(`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "to_list"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, int):
            continue
        chain = ast.unparse(node.func.value)
        found = re.search(r"db\.(\w+)\.find\(", chain)
        if node.args[0].value >= 100 and found and ".limit(" not in chain:
            yield node.lineno, found.group(1), node.args[0].value


def test_no_unbounded_total_reads_a_silent_cap_on_invoices_expenses_or_clients():
    """`to_list(n)` κόβει σιωπηλά. Για τα τιμολόγια, τα έξοδα και τους πελάτες επιτρέπεται μόνο ως ρητό «τα τελευταία N»
    (`.limit(N)` στον ίδιο cursor)· οτιδήποτε άλλο πρέπει να περνά από `db.fetch_all`. Μοναδική εξαίρεση: η λίστα
    εξόδων της οθόνης (προβολή των πρόσφατων, χωρίς σύνολα)."""
    allowed = {("routes_expenses.py", "expenses")}
    offenders = []
    for path in sorted(BACKEND.glob("*.py")):
        for lineno, collection, limit in _capped_reads(path):
            if collection in CRITICAL_COLLECTIONS and (path.name, collection) not in allowed:
                offenders.append(f"{path.name}:{lineno} db.{collection} ... to_list({limit})")
    assert not offenders, "Σιωπηλό όριο σε αθροιστική ανάγνωση:\n" + "\n".join(offenders)


def test_the_cap_guard_itself_detects_a_capped_read(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text("async def f():\n    return await db.invoices.find({}).sort('date', -1).to_list(500)\n"
                      "async def g():\n    return await db.invoices.find({}).sort('date', -1).limit(500).to_list(500)\n"
                      "async def h():\n    return await db.invoices.find({}).to_list(20)\n")
    assert list(_capped_reads(sample)) == [(2, "invoices", 500)]


def test_fetch_all_returns_every_document_unlike_a_capped_to_list():
    from db import db, fetch_all

    async def scenario():
        await db.fetch_probe.insert_many([{"n": i} for i in range(1500)])
        capped = await db.fetch_probe.find({}).to_list(1000)
        everything = await fetch_all(db.fetch_probe.find({}))
        return len(capped), len(everything)

    assert _run(scenario()) == (1000, 1500)                     # ο διπλός της conftest κόβει όπως ο Motor
