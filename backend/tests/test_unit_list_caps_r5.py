"""Γύρος 5 (δεύτερος έλεγχος) — λίστες που κόβονται χωρίς να το λένε, και σύνολα που βγαίνουν από κομμένες λίστες.

Οι λίστες οθόνης δεν έχουν σελιδοποίηση: επιστρέφουν τα πρώτα N (500 ή 1000) έγγραφα. Το `to_list(N)` τα έκοβε σιωπηλά —
ο χρήστης έβλεπε μια λίστα που φαινόταν πλήρης. Τώρα κάθε τέτοια λίστα ζητά N+1, επιστρέφει N και, αν υπήρχε και άλλο,
βάζει την κεφαλίδα `X-List-Truncated: N` (το frontend την εμφανίζει). Και δύο πραγματικά σφάλματα που βρέθηκαν στη διαδρομή:

* Η σελίδα της υπόθεσης αθροίζει «Αμοιβές» και «Έξοδα» από τα πρώτα 100 γραμμάτια/παράβολα που δείχνει — το σύνολο λείπει
  ό,τι πέρα από τα 100, χωρίς καμία ένδειξη. Τώρα αθροίζει όλα (τα μη ακυρωμένα) ανεξάρτητα από τη λίστα.
* Η λίστα δικασίμων ήταν `sort(date, 1).to_list(500)`: με 501 δικασίμους χανόταν η πιο **μελλοντική**. Και οι ειδοποιήσεις
  («Σήμερα», «ΣΕΙΡΑ ΣΑΣ») διάβαζαν `to_list(200)` χωρίς σειρά από όλα τα μη ολοκληρωμένα, όπου οι παλιές δικάσιμοι που κανείς
  δεν σημείωσε «ολοκληρώθηκε» μένουν για πάντα· μετά τις 200 η σημερινή δεν έφτανε ποτέ στο αποτέλεσμα.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from db import db, fetch_capped
from http_utils import LIST_TRUNCATED_HEADER
from tax_utils import athens_today

HEADER = "x-list-truncated"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "caps@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture()
def firm(client):
    return _run(db.users.find_one({"email": "caps@example.gr"}))["firm_id"]


def _stamp(i: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)).isoformat()


# ------------------------------------------------------------------------------- ο βοηθός ----

def test_the_header_name_is_the_one_the_frontend_reads():
    assert LIST_TRUNCATED_HEADER == "X-List-Truncated"


def test_fetch_capped_tells_whether_there_was_more():
    async def scenario():
        await db.letters.insert_many([{"n": i} for i in range(5)])
        exact, cut_exact = await fetch_capped(db.letters.find({}).sort("n", 1), 5)
        over, cut_over = await fetch_capped(db.letters.find({}).sort("n", 1), 4)
        none, cut_none = await fetch_capped(db.nothing.find({}).sort("n", 1), 4)
        return exact, cut_exact, over, cut_over, none, cut_none

    exact, cut_exact, over, cut_over, none, cut_none = _run(scenario())
    assert [d["n"] for d in exact] == [0, 1, 2, 3, 4] and cut_exact is False
    assert [d["n"] for d in over] == [0, 1, 2, 3] and cut_over is True            # το έξτρα δεν επιστρέφεται
    assert none == [] and cut_none is False


def test_the_browser_may_read_the_header_of_a_cross_origin_list(client):
    import server
    cors = next(m for m in server.app.user_middleware if m.cls is CORSMiddleware)
    options = getattr(cors, "kwargs", None) or getattr(cors, "options")
    origin = options["allow_origins"][0]
    r = client.get("/api/cases", headers={"Origin": origin})
    exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",") if h.strip()}
    assert {"x-closed-truncated", HEADER} <= exposed, sorted(exposed)


# ------------------------------------------------------------------------------- κάθε λίστα ----

def _seed_cases(firm, n):
    _run(db.cases.insert_many([{"firm_id": firm, "number": f"ΥΠΘ-2026-{i:04d}", "title": f"Υπόθεση {i}", "created_at": _stamp(i)} for i in range(n)]))


def _seed_grammatia(firm, n):
    _run(db.grammatia.insert_many([{"firm_id": firm, "number": f"ΓΡΠ-2026-{i:04d}", "court_type": "Ειρηνοδικείο", "reference_amount": 10.0,
                                    "total": 10.0, "status": "draft", "created_at": _stamp(i)} for i in range(n)]))


def _seed_paravola(firm, n):
    _run(db.paravola.insert_many([{"firm_id": firm, "number": f"ΠΡΒ-2026-{i:04d}", "category": "Έφεση", "amount": 10.0,
                                   "status": "draft", "created_at": _stamp(i)} for i in range(n)]))


def _seed_expenses(firm, n):
    _run(db.expenses.insert_many([{"firm_id": firm, "vendor": f"Προμηθευτής {i}", "date": "2026-03-01", "amount": 10.0,
                                   "vat_amount": 0.0, "vat_rate": 24, "created_at": _stamp(i)} for i in range(n)]))


def _seed_kyc(firm, n):
    _run(db.kyc.insert_many([{"firm_id": firm, "client_id": f"c{i}", "client_name": f"Πελάτης {i}", "updated_at": _stamp(i)} for i in range(n)]))


def _seed_employees(firm, n):
    _run(db.payroll_employees.insert_many([{"firm_id": firm, "name": f"Εργαζόμενος {i:04d}", "gross_salary": 1000.0} for i in range(n)]))


def _seed_runs(firm, n):
    _run(db.payroll_runs.insert_many([{"firm_id": firm, "employee_id": f"e{i}", "period": f"2026-{(i % 12) + 1:02d}", "gross_salary": 1000.0,
                                       "employee_efka": 137.0, "employer_efka": 222.0, "taxable_income": 863.0, "income_tax": 0.0,
                                       "tax_credit": 0.0, "net_salary": 863.0, "employer_total_cost": 1222.0, "created_at": _stamp(i)}
                                      for i in range(n)]))


def _seed_accounts(firm, n):
    _run(db.trust_accounts.insert_many([{"firm_id": firm, "client_id": f"c{i}", "client_name": f"Πελάτης {i:04d}", "balance": 0.0} for i in range(n)]))


def _seed_bank(firm, n):
    _run(db.bank_transactions.insert_many([{"firm_id": firm, "date": f"2026-01-{(i % 28) + 1:02d}", "description": f"κίνηση {i}",
                                            "amount": 10.0, "match_status": "unmatched", "external_id": f"ext-{firm}-{i}"}
                                           for i in range(n)]))


# (διαδρομή, μεταβλητή ορίου στο module, seed, πώς ξεχωρίζω ποια έγγραφα επέστρεψε)
LISTS = [
    ("/api/cases", "routes_cases.CASES_LIST_LIMIT", _seed_cases, "title", ["Υπόθεση 4", "Υπόθεση 3", "Υπόθεση 2"]),
    ("/api/grammatia", "routes_grammatia.LIST_LIMIT", _seed_grammatia, "number", ["ΓΡΠ-2026-0004", "ΓΡΠ-2026-0003", "ΓΡΠ-2026-0002"]),
    ("/api/paravola", "routes_paravola.LIST_LIMIT", _seed_paravola, "number", ["ΠΡΒ-2026-0004", "ΠΡΒ-2026-0003", "ΠΡΒ-2026-0002"]),
    ("/api/expenses", "routes_expenses.LIST_LIMIT", _seed_expenses, "vendor", ["Προμηθευτής 4", "Προμηθευτής 3", "Προμηθευτής 2"]),
    ("/api/kyc/records", "routes_kyc.LIST_LIMIT", _seed_kyc, "client_name", ["Πελάτης 4", "Πελάτης 3", "Πελάτης 2"]),
    ("/api/payroll/employees", "routes_payroll.EMPLOYEES_LIMIT", _seed_employees, "name",
     ["Εργαζόμενος 0000", "Εργαζόμενος 0001", "Εργαζόμενος 0002"]),
    ("/api/payroll/runs", "routes_payroll.RUNS_LIMIT", _seed_runs, "period", ["2026-05", "2026-04", "2026-03"]),
    ("/api/trust/accounts", "routes_trust.ACCOUNTS_LIMIT", _seed_accounts, "client_name",
     ["Πελάτης 0000", "Πελάτης 0001", "Πελάτης 0002"]),
    ("/api/bank/transactions", "routes_bank.TRANSACTIONS_LIMIT", _seed_bank, "description", None),
]
LIST_IDS = [entry[0] for entry in LISTS]


def _set_limit(monkeypatch, dotted: str, value: int):
    module_name, constant = dotted.split(".")
    module = __import__(module_name)
    monkeypatch.setattr(module, constant, value)


@pytest.mark.parametrize("path,limit_var,seed,field,expected", LISTS, ids=LIST_IDS)
def test_a_list_that_fits_carries_no_header(client, firm, monkeypatch, path, limit_var, seed, field, expected):
    _set_limit(monkeypatch, limit_var, 3)
    seed(firm, 3)
    r = client.get(path)
    assert r.status_code == 200 and len(r.json()) == 3
    assert HEADER not in r.headers


@pytest.mark.parametrize("path,limit_var,seed,field,expected", LISTS, ids=LIST_IDS)
def test_a_list_that_was_cut_says_so_and_keeps_the_documented_end(client, firm, monkeypatch, path, limit_var, seed, field, expected):
    _set_limit(monkeypatch, limit_var, 3)
    seed(firm, 5)
    r = client.get(path)
    assert r.status_code == 200
    assert len(r.json()) == 3
    assert r.headers[HEADER] == "3"
    if expected is not None:
        assert [row[field] for row in r.json()] == expected


@pytest.mark.parametrize("path,limit_var,seed,field,expected", LISTS, ids=LIST_IDS)
def test_another_offices_documents_never_count_towards_the_cut(client, firm, monkeypatch, path, limit_var, seed, field, expected):
    _set_limit(monkeypatch, limit_var, 3)
    seed("firm-someone-else", 10)
    seed(firm, 2)
    r = client.get(path)
    assert len(r.json()) == 2 and HEADER not in r.headers


def test_the_ledger_of_a_trust_account_says_when_older_entries_are_not_shown(client, firm, monkeypatch):
    import routes_trust
    monkeypatch.setattr(routes_trust, "LEDGER_LIMIT", 3)
    client_id = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company"}).json()["id"]
    for i in range(5):
        r = client.post("/api/trust/transactions", json={"client_id": client_id, "type": "deposit", "amount": 100.0,
                                                          "date": f"2026-03-0{i + 1}", "description": f"κατάθεση {i}"})
        assert r.status_code == 200, r.text

    r = client.get(f"/api/trust/accounts/{client_id}")

    assert r.status_code == 200 and len(r.json()["transactions"]) == 3 and r.headers[HEADER] == "3"
    assert [t["date"] for t in r.json()["transactions"]] == ["2026-03-05", "2026-03-04", "2026-03-03"]
    assert r.json()["ledger_check"] == {"ledger_balance": 500.0, "difference": 0.0, "consistent": True}     # το υπόλοιπο καλύπτει όλες


# ------------------------------------------------------------------------------- σύνολα υπόθεσης ----

@pytest.fixture()
def case_with_many_documents(client, firm, monkeypatch):
    import routes_cases
    monkeypatch.setattr(routes_cases, "CASE_SUBLIST_LIMIT", 3)
    case_id = str(_run(db.cases.insert_one({"firm_id": firm, "number": "ΥΠΘ-2026-0001", "title": "Υπόθεση", "created_at": _stamp(0)})).inserted_id)
    _run(db.grammatia.insert_many(
        [{"firm_id": firm, "case_id": case_id, "number": f"ΓΡΠ-2026-{i:04d}", "total": 100.0, "status": "issued", "created_at": _stamp(i),
          "court_type": "Ειρηνοδικείο", "reference_amount": 100.0} for i in range(8)]
        + [{"firm_id": firm, "case_id": case_id, "number": "ΓΡΠ-2026-0099", "total": 5000.0, "status": "cancelled", "created_at": _stamp(99),
            "court_type": "Ειρηνοδικείο", "reference_amount": 5000.0}]
        + [{"firm_id": firm, "case_id": "another-case", "number": "ΓΡΠ-2026-0100", "total": 7000.0, "status": "issued", "created_at": _stamp(100),
            "court_type": "Ειρηνοδικείο", "reference_amount": 7000.0}]))
    _run(db.paravola.insert_many(
        [{"firm_id": firm, "case_id": case_id, "number": f"ΠΡΒ-2026-{i:04d}", "amount": 50.0, "status": "paid", "created_at": _stamp(i),
          "category": "Έφεση"} for i in range(6)]
        + [{"firm_id": firm, "case_id": case_id, "number": "ΠΡΒ-2026-0099", "amount": 900.0, "status": "cancelled", "created_at": _stamp(99),
            "category": "Έφεση"}]))
    return case_id


def test_the_totals_of_a_case_cover_every_document_not_just_the_ones_listed(client, case_with_many_documents):
    r = client.get(f"/api/cases/{case_with_many_documents}")

    body = r.json()
    assert r.status_code == 200
    assert len(body["grammatia"]) == 3 and len(body["paravola"]) == 3                       # η λίστα είναι κομμένη…
    assert body["fees_total"] == 800.0                                                      # …το σύνολο όχι (8 × 100, χωρίς το ακυρωμένο)
    assert body["expenses_total"] == 300.0                                                  # 6 × 50, χωρίς το ακυρωμένο
    assert r.headers[HEADER] == "3"


@pytest.mark.parametrize("grammatia,paravola", [(5, 2), (2, 5), (5, 5)])
def test_a_case_says_so_when_either_of_its_two_lists_was_cut(client, firm, monkeypatch, grammatia, paravola):
    import routes_cases
    monkeypatch.setattr(routes_cases, "CASE_SUBLIST_LIMIT", 3)
    case_id = str(_run(db.cases.insert_one({"firm_id": firm, "number": "ΥΠΘ-2026-0001", "title": "Υπόθεση", "created_at": _stamp(0)})).inserted_id)
    _run(db.grammatia.insert_many([{"firm_id": firm, "case_id": case_id, "total": 10.0, "status": "issued", "created_at": _stamp(i),
                                    "court_type": "Ειρηνοδικείο", "reference_amount": 10.0} for i in range(grammatia)]))
    _run(db.paravola.insert_many([{"firm_id": firm, "case_id": case_id, "amount": 10.0, "status": "paid", "created_at": _stamp(i),
                                   "category": "Έφεση"} for i in range(paravola)]))

    r = client.get(f"/api/cases/{case_id}")

    assert r.headers[HEADER] == "3"
    assert r.json()["fees_total"] == grammatia * 10.0 and r.json()["expenses_total"] == paravola * 10.0


def test_a_case_whose_documents_all_fit_has_no_header(client, firm):
    case_id = str(_run(db.cases.insert_one({"firm_id": firm, "number": "ΥΠΘ-2026-0001", "title": "Υπόθεση", "created_at": _stamp(0)})).inserted_id)
    _run(db.grammatia.insert_one({"firm_id": firm, "case_id": case_id, "total": 124.0, "status": "issued", "created_at": _stamp(1),
                                  "court_type": "Ειρηνοδικείο", "reference_amount": 124.0}))

    r = client.get(f"/api/cases/{case_id}")

    assert r.json()["fees_total"] == 124.0 and r.json()["expenses_total"] == 0.0 and HEADER not in r.headers


def test_another_offices_documents_never_enter_a_case_total(client, firm):
    case_id = str(_run(db.cases.insert_one({"firm_id": firm, "number": "ΥΠΘ-2026-0001", "title": "Υπόθεση", "created_at": _stamp(0)})).inserted_id)
    _run(db.grammatia.insert_one({"firm_id": "firm-someone-else", "case_id": case_id, "total": 999.0, "status": "issued", "created_at": _stamp(1)}))

    assert client.get(f"/api/cases/{case_id}").json()["fees_total"] == 0.0


# ------------------------------------------------------------------------------- δικάσιμοι ----

def _hearing(firm, day: str, **over):
    return {"firm_id": firm, "court": "Πρωτοδικείο Αθηνών", "date": day, "time": "09:00", "pinakio_number": 10, "current_number": 0,
            "status": "scheduled", "notify_threshold": 5, **over}


def test_when_hearings_are_cut_the_future_ones_are_kept_and_the_oldest_dropped(client, firm, monkeypatch):
    import routes_hearings
    monkeypatch.setattr(routes_hearings, "LIST_LIMIT", 3)
    _run(db.hearings.insert_many([_hearing(firm, f"2026-0{m}-15") for m in range(1, 7)]))          # Ιαν … Ιούν

    r = client.get("/api/hearings")

    assert [h["date"] for h in r.json()] == ["2026-04-15", "2026-05-15", "2026-06-15"]            # αύξουσα σειρά, οι πιο πρόσφατες
    assert r.headers[HEADER] == "3"


def test_hearings_of_the_same_day_keep_a_stable_order_by_time_then_number(client, firm):
    _run(db.hearings.insert_many([_hearing(firm, "2026-05-15", time="09:00", pinakio_number=9),
                                  _hearing(firm, "2026-05-15", time="11:00", pinakio_number=3),
                                  _hearing(firm, "2026-05-15", time="09:00", pinakio_number=2)]))

    got = [(h["time"], h["pinakio_number"]) for h in client.get("/api/hearings").json()]

    assert got == [("09:00", 2), ("09:00", 9), ("11:00", 3)]


def test_todays_hearing_is_announced_even_beside_hundreds_of_old_ones_nobody_closed(client, firm):
    today = athens_today()
    old = [_hearing(firm, (today - timedelta(days=30 + i)).isoformat(), pinakio_number=i + 1) for i in range(250)]      # ξεχασμένες «scheduled»
    _run(db.hearings.insert_many(old))
    _run(db.hearings.insert_one(_hearing(firm, today.isoformat(), pinakio_number=77)))
    _run(db.hearings.insert_one(_hearing(firm, today.isoformat(), pinakio_number=5, status="in_progress", current_number=5,
                                         court="Εφετείο Αθηνών")))
    _run(db.hearings.insert_one(_hearing(firm, (today + timedelta(days=3)).isoformat(), pinakio_number=8)))          # μελλοντική: καμία ειδοποίηση

    alerts = client.get("/api/hearings/alerts/list").json()

    assert sorted(a["level"] for a in alerts) == ["critical", "info"]
    assert any("Σήμερα: πινάκιο Νο 77" in a["message"] for a in alerts)
    assert any("ΣΕΙΡΑ ΣΑΣ" in a["message"] and "Εφετείο Αθηνών" in a["message"] for a in alerts)


def test_an_interrupted_hearing_whose_turn_has_come_alerts_whatever_its_date(client, firm):
    today = athens_today()
    _run(db.hearings.insert_one(_hearing(firm, (today - timedelta(days=2)).isoformat(), status="interrupted", pinakio_number=9,
                                         current_number=9, court="Εφετείο Πειραιώς")))

    alerts = client.get("/api/hearings/alerts/list").json()

    assert [(a["level"], a["court"]) for a in alerts] == [("critical", "Εφετείο Πειραιώς")]


def test_a_live_hearing_of_another_day_still_alerts_and_a_closed_one_never_does(client, firm):
    today = athens_today()
    _run(db.hearings.insert_many([
        _hearing(firm, (today - timedelta(days=1)).isoformat(), status="in_progress", pinakio_number=20, current_number=18),
        _hearing(firm, today.isoformat(), status="completed"),
        _hearing(firm, today.isoformat(), status="postponed"),
    ]))

    alerts = client.get("/api/hearings/alerts/list").json()

    assert [(a["level"], a["pinakio_number"]) for a in alerts] == [("warning", 20)]
