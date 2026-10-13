"""Γύρος 5 (έλεγχος μεταλλάξεων) — «Ανείσπρακτα», υπόλοιπα στο λεπτό και παράθυρο του dashboard.

Κάθε test εδώ υπάρχει επειδή μια μικρή, εύλογη αλλαγή στον κώδικα δεν έριχνε κανένα υπάρχον test:

* το οφειλόμενο και το σύνολο ανείσπρακτων δεν στρογγυλοποιούνται στο λεπτό (100,01 − 0,02 = 99,99000000000001,
  10,1 + 20,2 + 30,3 = 60,599999999999994)· ένα ποσό που δεν είναι ακέραιο πλήθος λεπτών φτάνει στο Stripe, στις συγκρίσεις
  εξόφλησης και στην οθόνη·
* το παράθυρο ημερομηνιών του dashboard (γράφημα έξι μηνών + έτος) και τα όριά του: ο Νοέμβριος που «τρώει» τον Δεκέμβριο,
  ο μήνας 0 που δεν γίνεται Δεκέμβριος, το παράθυρο που ξεκινά από λάθος άκρο. Τα tests αυτά τρέχουν με παγωμένο ρολόι την
  πιο δύσκολη ώρα (01:30 της 1/1/2027 στην Ελλάδα = 23:30 της 31/12 σε UTC), όπου αλλάζουν ταυτόχρονα μήνας και έτος.
"""
import asyncio
import calendar
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tax_utils import athens_today


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
                    json={"email": "totals@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
    assert r.status_code == 200, r.text
    from db import db
    user = _run(db.users.find_one({"email": "totals@example.gr"}))
    _run(db.users.update_one({"email": "totals@example.gr"}, {"$set": {"role": "admin"}}))
    return SimpleNamespace(firm_id=user["firm_id"], user={"email": "totals@example.gr", "firm_id": user["firm_id"]})


def _invoice(office, *, status="issued", total=124.0, subtotal=100.0, date="2027-01-01", client_id="client-a", **over):
    return {
        "firm_id": office.firm_id, "client_id": client_id, "client_name": "ΠΕΛΑΤΗΣ", "number": "", "date": date,
        "line_items": [], "subtotal": subtotal, "vat_amount": round(total - subtotal, 2), "total": total,
        "vat_breakdown": {}, "status": status, "mydata_status": "synced", "invoice_type": "2.1",
        "created_at": datetime.now(timezone.utc).isoformat(), **over}


def _expense(office, *, date, amount, vat):
    return {"firm_id": office.firm_id, "vendor": "Προμηθευτής", "date": date, "amount": amount, "vat_amount": vat,
            "vat_rate": 24, "category": "Λοιπά", "created_at": datetime.now(timezone.utc).isoformat()}


def _insert(collection: str, docs: list[dict]) -> None:
    """Απευθείας στη βάση (χωρίς τα μοναδικά ευρετήρια του server: τα τιμολόγια του test δεν έχουν αριθμούς)."""
    from db import db

    async def go():
        await db[collection].drop_indexes()
        await db[collection].insert_many(docs)
    _run(go())


# ======================================================== 1. Υπόλοιπα στο λεπτό ====

@pytest.mark.asyncio
@pytest.mark.parametrize("total, applied, owed", [
    (100.01, 0.02, 99.99), (0.3, 0.1, 0.2), (4.35, 0.35, 4.0), (2.3, 0.1, 2.2), (124.0, 100.0, 24.0), (124.0, 124.0, 0.0)])
async def test_the_outstanding_amount_is_a_whole_number_of_cents(total, applied, owed):
    """Η διαφορά δύο ποσών σε float δεν είναι ποσό σε λεπτά (100,01 − 0,02 = 99,99000000000001): αυτό το νούμερο φτάνει
    στην οθόνη, στη χρέωση της κάρτας και στη σύγκριση εξόφλησης."""
    from invoice_services import invoice_balances, outstanding_amount
    invoice = {"_id": ObjectId(), "status": "issued", "total": total, "trust_applied": applied}
    due, withheld = await outstanding_amount("f", invoice, {})
    assert due == owed and withheld == 0.0
    balances = await invoice_balances("f", [invoice], {})
    assert balances[str(invoice["_id"])] == (total, owed)


def test_the_total_owed_is_rounded_to_the_cent_after_adding_the_clients():
    """Τρεις πελάτες που χρωστούν 0,10 € ο καθένας: 0,1 + 0,1 + 0,1 είναι 0,30000000000000004 σε float."""
    from invoice_services import open_total
    invoices = [{"_id": ObjectId(), "status": "issued", "client_id": f"client-{i}"} for i in range(3)]
    balances = {str(inv["_id"]): (0.1, 0.1) for inv in invoices}
    assert open_total(invoices, balances) == 0.3


def test_a_credit_note_only_reduces_what_the_same_client_owes():
    from invoice_services import open_total
    mine = {"_id": ObjectId(), "status": "issued", "client_id": "a"}
    credit = {"_id": ObjectId(), "status": "issued", "client_id": "a"}
    other = {"_id": ObjectId(), "status": "issued", "client_id": "b"}
    excess = {"_id": ObjectId(), "status": "issued", "client_id": "c"}
    balances = {str(mine["_id"]): (124.0, 124.0), str(credit["_id"]): (-24.0, -24.0),
                str(other["_id"]): (50.0, 50.0), str(excess["_id"]): (-80.0, -80.0)}
    assert open_total([mine, credit, other, excess], balances) == 150.0      # 100 + 50 — το πλεόνασμα του «c» δεν μειώνει τίποτα


def test_the_dashboard_adds_what_each_client_owes_to_the_cent(client, office):
    _insert("invoices", [_invoice(office, total=10.1, subtotal=10.1, client_id="a"),
                         _invoice(office, total=20.2, subtotal=20.2, client_id="b"),
                         _invoice(office, total=30.3, subtotal=30.3, client_id="c")])
    body = client.get("/api/dashboard/summary").json()
    assert body["unpaid_total"] == 60.6 and body["unpaid_count"] == 3
    assert "60.60 €" in body["advice"]


# =================================== 2. Το παράθυρο του dashboard στην αλλαγή του έτους ====

def test_the_dashboard_chart_and_year_window_across_the_new_year(client, office, athens_clock):
    """Σήμερα (ώρα Ελλάδας) είναι 1/1/2027: το γράφημα δείχνει Αύγουστο 2026 … Ιανουάριο 2027 και το «έτος» μόνο το 2027.
    Κάθε όριο μήνα έχει τιμολόγιο ακριβώς πάνω του (31/8, 1/9, 30/11, 1/12), για να φανεί κάθε μετατόπιση κατά μία ημέρα."""
    assert athens_today().isoformat() == "2027-01-01"
    paid = {"status": "paid"}
    _insert("invoices", [
        _invoice(office, date="2026-07-31", subtotal=50.0, total=62.0, **paid),       # πριν το γράφημα: δεν μετράει πουθενά
        _invoice(office, date="2026-08-31", subtotal=100.0, total=124.0, **paid),
        _invoice(office, date="2026-09-01", subtotal=200.0, total=248.0, **paid),
        _invoice(office, date="2026-10-15", subtotal=300.0, total=372.0, **paid),
        _invoice(office, date="2026-11-30", subtotal=400.0, total=496.0, **paid),
        _invoice(office, date="2026-12-01", subtotal=500.0, total=620.0, **paid),
        _invoice(office, date="2027-01-01", subtotal=600.0, total=744.0, **paid),
        _invoice(office, date="2026-11-15", subtotal=50.0, total=62.0, client_id="client-b"),      # εκδοθέν, ανεξόφλητο
        _invoice(office, date="2019-05-02", subtotal=100.0, total=124.0, client_id="client-c"),    # παλιό ανεξόφλητο
    ])
    _insert("expenses", [_expense(office, date="2026-09-30", amount=62.0, vat=12.0),
                         _expense(office, date="2026-07-31", amount=124.0, vat=24.0),              # εκτός γραφήματος
                         _expense(office, date="2027-01-01", amount=124.0, vat=24.0)])
    body = client.get("/api/dashboard/summary").json()

    labels = [f"{calendar.month_abbr[m]} {y}" for y, m in [(2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12), (2027, 1)]]
    assert [point["month"] for point in body["chart_data"]] == labels
    assert [point["income"] for point in body["chart_data"]] == [100.0, 200.0, 300.0, 450.0, 500.0, 600.0]
    assert [point["expense"] for point in body["chart_data"]] == [0.0, 50.0, 0.0, 0.0, 0.0, 100.0]

    assert (body["income_total"], body["expense_total"]) == (600.0, 100.0)            # ο μήνας (Ιανουάριος 2027)
    assert (body["year_income"], body["year_expense"], body["year_profit"]) == (600.0, 100.0, 500.0)
    assert (body["unpaid_total"], body["unpaid_count"]) == (186.0, 2)                  # 62 + 124, όποια κι αν είναι η ημερομηνία


# ================================================= 3. Λίστα τιμολογίων και πρόβλεψη ταμείου ====

def test_the_invoice_list_shows_drafts_issued_paid_and_cancelled_invoices_newest_first(client, office):
    """Η λίστα έχει όριο μόνο στα κλειστά (εξοφλημένα ΚΑΙ ακυρωμένα): κανένα είδος δεν «χάνεται» από το ερώτημα."""
    stamps = {status: f"2026-05-0{n}T10:00:00+00:00" for n, status in enumerate(("draft", "issued", "paid", "cancelled"), start=1)}
    _insert("invoices", [_invoice(office, status=status, created_at=stamp, number=f"ΤΠΥ-{status}", client_id=f"c-{status}")
                         for status, stamp in stamps.items()])
    r = client.get("/api/invoices")
    assert r.status_code == 200 and "x-closed-truncated" not in r.headers
    rows = r.json()
    assert [row["status"] for row in rows] == ["cancelled", "paid", "issued", "draft"]
    assert {row["status"]: row["outstanding"] for row in rows} == {"cancelled": 0.0, "paid": 0.0, "issued": 124.0, "draft": 0.0}


def test_a_credit_note_reduces_the_expected_income_of_the_cash_flow_forecast(client, office):
    """Το πιστωτικό είναι αρνητική είσπραξη: δεν παραλείπεται όπως ένα τιμολόγιο χωρίς υπόλοιπο."""
    today = athens_today().isoformat()
    _insert("invoices", [
        _invoice(office, total=124.0, subtotal=100.0, number="ΤΠΥ-1", date=today),
        _invoice(office, total=-24.8, subtotal=-20.0, number="ΠΙΣ-1", date=today, credit_note_for="abc"),
        _invoice(office, total=62.0, subtotal=50.0, number="ΤΠΥ-ΜΗΔΕΝ", date=today, trust_applied=62.0),     # εξοφλήθηκε από πελατολογικό
    ])
    body = client.get("/api/analytics/cashflow").json()
    incomes = {event["label"].split("—")[0].strip(): event["amount"] for event in body["events"] if event["type"] == "income"}
    assert incomes == {"Είσπραξη ΤΠΥ-1": 124.0, "Είσπραξη ΠΙΣ-1": -24.8}      # το τιμολόγιο χωρίς υπόλοιπο δεν δίνει γεγονός
    assert body["total_expected_income"] == 99.2
