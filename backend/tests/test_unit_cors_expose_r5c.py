"""ΔΙΟΡΘΩΘΗΚΕ (εύρημα του ελέγχου μεταλλάξεων) (χαμηλής σοβαρότητας, μόνο όταν frontend και API είναι σε διαφορετικές προελεύσεις) — η κεφαλίδα
`X-Closed-Truncated` δεν είναι ορατή στον browser.

Η λίστα τιμολογίων δηλώνει ότι υπάρχουν παλαιότερα εξοφλημένα που δεν περιλαμβάνονται με την κεφαλίδα `X-Closed-Truncated: 1`,
και το frontend (`Invoices.jsx`, `Bank.jsx`) τη διαβάζει από την απάντηση του axios για να δείξει την ειδοποίηση «κομμένη λίστα».
Σε αίτημα από άλλη προέλευση (π.χ. ο dev server του React στο :3000 → API στο :8001, ή frontend και API σε διαφορετικά
domains) ο browser κρύβει κάθε κεφαλίδα που δεν αναφέρεται στο `Access-Control-Expose-Headers`. Το `CORSMiddleware` του
`server.py` δεν ορίζει `expose_headers`, οπότε η ειδοποίηση δεν εμφανίζεται ΠΟΤΕ — η λίστα φαίνεται πλήρης ενώ λείπουν
εγγραφές. (Στην παραγωγή με nginx στην ίδια προέλευση, όπως στο docker-compose.yml, δεν υπάρχει πρόβλημα.)

Αναμενόμενο: `expose_headers=["X-Closed-Truncated"]` στο CORSMiddleware.
"""
import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "cors@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def test_the_browser_may_read_the_truncation_header_of_a_cross_origin_invoice_list(client):
    import server
    cors = next(m for m in server.app.user_middleware if m.cls is CORSMiddleware)
    options = getattr(cors, "kwargs", None) or getattr(cors, "options")
    origin = options["allow_origins"][0]
    r = client.get("/api/invoices", headers={"Origin": origin})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == origin
    exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",") if h.strip()}
    assert "x-closed-truncated" in exposed, f"εκτεθειμένες κεφαλίδες: {sorted(exposed)}"
