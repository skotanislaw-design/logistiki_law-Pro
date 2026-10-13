"""Γύρος 5 (έλεγχος μεταλλάξεων) — τα σύνολα και οι λίστες ενός γραφείου δεν περιλαμβάνουν ποτέ έγγραφα άλλου γραφείου.

Η ανασκόπηση 2 ξαναέγραψε κάθε ανάγνωση «ανείσπρακτων» και συνόλων ώστε να διαβάζει ΟΛΑ τα έγγραφα (`fetch_all`, χωρίς όριο).
Σε κάθε τέτοιο ερώτημα το φίλτρο του γραφείου (`tenant_query`) είναι η μόνη προστασία: χωρίς αυτό, χωρίς κανένα σφάλμα, το
dashboard, η πρόβλεψη ταμείου, η κερδοφορία, η λίστα τιμολογίων, η λίστα πελατών και η δήλωση Φ2 ενός γραφείου θα
άθροιζαν τα τιμολόγια και τα έξοδα όλων των γραφείων της εγκατάστασης. Τα υπάρχοντα tests έχουν πάντα ένα μόνο γραφείο.

Εδώ το γραφείο του χρήστη έχει μικρά, γνωστά ποσά και ένα ΞΕΝΟ γραφείο έχει «φωνακλάδικα» έγγραφα της ίδιας περιόδου (ίδιες
ημερομηνίες, μεγάλα ποσά): οποιοδήποτε ερώτημα χωρίς φίλτρο γραφείου αλλάζει ορατά το αποτέλεσμα. Όλα τα tests τρέχουν με
παγωμένο ρολόι (01:30 της 1/1/2027 στην Ελλάδα) ώστε οι μήνες των παραθύρων να είναι σταθεροί.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

OTHER = "firm-ΞΕΝΟ"


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
                    json={"email": "tenant@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "tenant@example.gr"}))
    _run(db.users.update_one({"email": "tenant@example.gr"}, {"$set": {"role": "admin"}}))
    return SimpleNamespace(firm_id=user["firm_id"])


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invoice(firm, *, status="issued", total=124.0, subtotal=100.0, date="2027-01-01", client_id="client-own", number="",
             client_name="ΠΕΛΑΤΗΣ", **over):
    return {"firm_id": firm, "client_id": client_id, "client_name": client_name, "number": number, "date": date,
            "line_items": [], "subtotal": subtotal, "vat_amount": round(total - subtotal, 2), "total": total,
            "vat_breakdown": {}, "status": status, "mydata_status": "synced", "invoice_type": "2.1",
            "created_at": _stamp(), **over}


def _expense(firm, *, date, amount, vat, category="Λοιπά"):
    return {"firm_id": firm, "vendor": "Προμηθευτής", "date": date, "amount": amount, "vat_amount": vat, "vat_rate": 24,
            "category": category, "created_at": _stamp()}


def _insert(collection: str, docs: list[dict]) -> list:
    """Απευθείας στη βάση (χωρίς τα μοναδικά ευρετήρια του server: τα τιμολόγια του test δεν έχουν αριθμούς)."""
    from db import db

    async def go():
        await db[collection].drop_indexes()
        return (await db[collection].insert_many(docs)).inserted_ids
    return _run(go())


# ============================================================ 1. Dashboard ====

def test_the_dashboard_adds_up_only_the_documents_of_its_own_office(client, office, athens_clock):
    _insert("invoices", [
        _invoice(office.firm_id, status="paid", date="2027-01-01", subtotal=100.0, total=124.0),
        _invoice(office.firm_id, status="issued", date="2019-05-02", subtotal=50.0, total=62.0),
        _invoice(OTHER, status="paid", date="2027-01-01", subtotal=1000.0, total=1240.0),
        _invoice(OTHER, status="issued", date="2019-05-02", subtotal=10000.0, total=12400.0, client_id="client-ξένος"),
    ])
    _insert("expenses", [_expense(office.firm_id, date="2027-01-01", amount=62.0, vat=12.0),
                         _expense(OTHER, date="2027-01-01", amount=620.0, vat=120.0)])
    body = client.get("/api/dashboard/summary").json()
    assert (body["income_total"], body["output_vat"]) == (100.0, 24.0)          # το μήνα: μόνο το δικό μας
    assert (body["expense_total"], body["input_vat"]) == (50.0, 12.0)
    assert (body["year_income"], body["year_expense"]) == (100.0, 50.0)
    assert (body["unpaid_total"], body["unpaid_count"]) == (62.0, 1)           # ούτε τα 12.400 € ούτε το 2ο τιμολόγιο
    last_month = body["chart_data"][-1]
    assert (last_month["income"], last_month["expense"]) == (100.0, 50.0)


# ====================================================== 2. Πρόβλεψη ταμείου ====

def test_the_cash_flow_forecast_uses_only_the_documents_of_its_own_office(client, office, athens_clock):
    _insert("invoices", [
        _invoice(office.firm_id, total=124.0, subtotal=100.0, number="ΤΠΥ-ΜΟΥ", date="2027-01-01"),
        _invoice(OTHER, total=12400.0, subtotal=10000.0, number="ΤΠΥ-ΞΕΝΟ", date="2027-01-01", client_id="client-ξένος"),
    ])
    _insert("expenses", [_expense(office.firm_id, date="2026-12-10", amount=300.0, vat=0.0),
                         _expense(OTHER, date="2026-12-12", amount=6000.0, vat=0.0)])
    body = client.get("/api/analytics/cashflow").json()
    incomes = {event["label"].split("—")[0].strip(): event["amount"] for event in body["events"] if event["type"] == "income"}
    assert incomes == {"Είσπραξη ΤΠΥ-ΜΟΥ": 124.0}                                  # αναμενόμενες εισπράξεις
    assert body["expense_by_category"] == {"Λοιπά": 300.0}                           # μέσος όρος εξόδων 3 μηνών
    vat_events = [event for event in body["events"] if event["label"] == "Δήλωση ΦΠΑ"]
    assert vat_events and vat_events[0]["amount"] == 24.0                            # ΦΠΑ του μήνα: 24 €, όχι 24 + 2.400 €


# ============================================================ 3. Κερδοφορία ====

def test_the_profitability_report_uses_only_the_documents_of_its_own_office(client, office, athens_clock):
    alpha = client.post("/api/clients", json={"name": "ΑΛΦΑ ΑΕ", "afm": "123456783", "client_type": "company"}).json()["id"]
    foreign_client = _insert("clients", [{"firm_id": OTHER, "name": "ΞΕΝΟΣ ΠΕΛΑΤΗΣ", "afm": "", "client_type": "individual"}])[0]
    _insert("invoices", [
        _invoice(office.firm_id, status="issued", subtotal=100.0, total=124.0, client_id=alpha),
        _invoice(office.firm_id, status="paid", subtotal=50.0, total=62.0, client_id=alpha),
        # δικό μας τιμολόγιο που δείχνει (εσφαλμένα ή κακόβουλα) πελάτη ΑΛΛΟΥ γραφείου: το όνομα του ξένου πελάτη δεν διαρρέει
        _invoice(office.firm_id, status="issued", subtotal=10.0, total=12.4, client_id=str(foreign_client),
                 client_name="ΟΝΟΜΑ ΣΤΟ ΤΙΜΟΛΟΓΙΟ"),
        _invoice(OTHER, status="issued", subtotal=10000.0, total=12400.0, client_id="client-ξένος"),
        _invoice(OTHER, status="paid", subtotal=5000.0, total=6200.0, client_id="client-ξένος"),
    ])
    body = client.get("/api/analytics/profitability").json()
    assert body["totals"]["invoice_count"] == 3
    assert (body["totals"]["revenue"], body["totals"]["paid"], body["totals"]["unpaid"]) == (160.0, 62.0, 136.4)
    names = {row["client_id"]: row["client_name"] for row in body["top_clients"]}
    assert names == {alpha: "ΑΛΦΑ ΑΕ", str(foreign_client): "ΟΝΟΜΑ ΣΤΟ ΤΙΜΟΛΟΓΙΟ"}


# ======================================= 4. Λίστα τιμολογίων και λίστα πελατών ====

def test_the_invoice_list_shows_only_the_invoices_of_its_own_office(client, office):
    statuses = ("draft", "issued", "paid", "cancelled")
    own = _insert("invoices", [_invoice(office.firm_id, status=status, number=f"ΜΟΥ-{status}") for status in statuses])
    _insert("invoices", [_invoice(OTHER, status=status, number=f"ΞΕΝΟ-{status}") for status in statuses])
    rows = client.get("/api/invoices").json()
    assert {row["id"] for row in rows} == {str(i) for i in own}
    assert {row["status"] for row in rows} == set(statuses)


def test_the_client_list_shows_only_the_clients_of_its_own_office_in_name_order(client, office):
    for name in ("Ωμέγα ΑΕ", "Άλφα ΑΕ", "Βήτα ΕΠΕ"):
        assert client.post("/api/clients", json={"name": name, "afm": "", "client_type": "individual"}).status_code == 200
    _insert("clients", [{"firm_id": OTHER, "name": "Ξένος Πελάτης", "afm": "", "client_type": "individual"}])
    names = [row["name"] for row in client.get("/api/clients").json()]
    assert names == sorted(names) and len(names) == 3 and "Ξένος Πελάτης" not in names


# ================================================================ 5. Δήλωση Φ2 ====

def _vat_invoice(firm, *, status, base, vat, date):
    return _invoice(firm, status=status, subtotal=base, total=base + vat, date=date, vat_breakdown={"24": vat},
                    line_items=[{"description": "Αμοιβή", "quantity": 1, "unit_price": base, "vat_rate": 24}])


def test_the_vat_return_uses_only_the_issued_and_paid_documents_of_its_own_office(client, office):
    from db import db
    _run(db.vat_periods.insert_one({"firm_id": office.firm_id, "period": "2024-03", "period_start": "2024-03-01",
                                    "period_end": "2024-03-31", "due_date": "2024-04-30", "status": "computed"}))
    _insert("invoices", [
        _vat_invoice(office.firm_id, status="issued", base=100.0, vat=24.0, date="2024-03-10"),
        _vat_invoice(office.firm_id, status="paid", base=50.0, vat=12.0, date="2024-03-31"),              # εξοφλημένο: μετράει
        _vat_invoice(office.firm_id, status="draft", base=1000.0, vat=240.0, date="2024-03-15"),          # πρόχειρο: όχι
        _vat_invoice(OTHER, status="issued", base=10000.0, vat=2400.0, date="2024-03-10"),
    ])
    _insert("expenses", [_expense(office.firm_id, date="2024-03-12", amount=62.0, vat=12.0),
                         _expense(OTHER, date="2024-03-12", amount=1240.0, vat=240.0)])
    r = client.get("/api/automation/vat-periods/2024-03/return")
    assert r.status_code == 200, r.text
    lines = {line["key"]: line["amount"] for line in r.json()["lines"]}
    assert lines["total_output_vat"] == 36.0 and lines["outputs_base_24"] == 150.0
    assert lines["total_input_vat"] == 12.0 and lines["inputs_base"] == 50.0
    assert lines["vat_payable"] == 24.0
