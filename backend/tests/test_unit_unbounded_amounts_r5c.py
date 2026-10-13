"""ΔΙΟΡΘΩΘΗΚΕ (εύρημα του ελέγχου μεταλλάξεων) (μέτριας σοβαρότητας) — τα ποσά εξόδων (και μισθοδοσίας/παραβόλων/γραμματίων) δεν έχουν ανώτατο όριο, οπότε δύο
έγκυρα, ΠΕΠΕΡΑΣΜΕΝΑ νούμερα αρκούν για να σπάσουν τα σύνολα ολόκληρου του γραφείου (αποδεδειγμένο εδώ για τα έξοδα).

Η γραμμή τιμολογίου έχει όριο (`MAX_LINE_VALUE` = 1 δισ.) και το πελατολόγιο ίδιο (`routes_trust.MAX_AMOUNT`). Το `Expense.amount`
(και τα `PayrollEmployee.gross_salary`, `Paravolo.amount`, `Grammatio.reference_amount`) δέχονται οτιδήποτε είναι `float`:
`{"amount": 1e308}` είναι έγκυρο JSON και περνά τον φραγμό NaN/άπειρων. Δύο τέτοια έξοδα αθροίζονται σε `inf`
(`sum(e["amount"] …)` στο dashboard και στην πρόβλεψη ταμείου), η `JSONResponse` αρνείται το `inf` και ΚΑΘΕ
`GET /api/dashboard/summary` και `GET /api/analytics/cashflow` απαντά 500 για όλο το γραφείο — ακριβώς η βλάβη που περιγράφει
η τεκμηρίωση του `json_guard` για το NaN («από εκεί το άθροισμα κάθε αναφοράς γίνεται NaN και η απάντηση JSON αποτυγχάνει»).

Αναμενόμενο: ή το αίτημα απορρίπτεται (400/422, όριο τύπου `MAX_LINE_VALUE`) ή τα σύνολα εξακολουθούν να απαντούν 200.
"""
import pytest
from fastapi.testclient import TestClient

from tax_utils import athens_today

JSON = {"content-type": "application/json"}


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver", raise_server_exceptions=False) as c:
        r = c.post("/api/auth/register", json={"email": "huge@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def test_two_absurdly_large_but_finite_expenses_do_not_break_the_dashboard_and_the_forecast(client):
    today = athens_today().isoformat()
    for n in range(2):
        raw = '{"vendor": "Προμηθευτής %d", "date": "%s", "amount": 1e308, "vat_amount": 0}' % (n, today)
        client.post("/api/expenses", content=raw.encode(), headers=JSON)           # είτε απορριφθεί είτε γίνει δεκτό…
    assert client.get("/api/dashboard/summary").status_code == 200                  # …τα σύνολα πρέπει να απαντούν
    assert client.get("/api/analytics/cashflow").status_code == 200

