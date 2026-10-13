"""Γύρος 5 (δεύτερος έλεγχος) — η εισαγωγή πελατών από CSV/Excel ελέγχει το ψηφίο ελέγχου του ΑΦΜ.

Η χειροκίνητη δημιουργία πελάτη (`routes_clients._validate_client_payload`) απορρίπτει ΑΦΜ με λάθος ψηφίο ελέγχου, γιατί το λάθος
αλλιώς φαίνεται μήνες αργότερα — στην πρώτη έκδοση τιμολογίου ή στη διαβίβαση στο myDATA, όταν το παραστατικό έχει ήδη εκδοθεί.
Η μαζική εισαγωγή (`routes_import`) δεν είχε τον έλεγχο: ένα Excel με τυπογραφικό λάθος στη στήλη «ΑΦΜ» γέμιζε τη βάση με πελάτες
που δεν μπορούν να τιμολογηθούν. Τώρα ισχύει ο ίδιος κανόνας: η γραμμή με άκυρο ΑΦΜ παραλείπεται και η απάντηση λέει ΠΟΙΑ γραμμή
και ΤΙ φταίει (το αρχείο διορθώνεται και ξαναφορτώνεται· οι πελάτες που υπάρχουν ήδη παραλείπονται ως διπλότυποι).

Μαζί: (α) τα ΑΦΜ του παραδείγματος που κατεβάζει ο χρήστης (`/import/clients/template`) ήταν άκυρα· (β) το Excel δίνει τους αριθμούς ως
αριθμούς — ένα ΑΦΜ ή τηλέφωνο ως `123456783.0` αποθηκευόταν με την τελεία· (γ) ένα ΑΦΜ που ξεκινά από 0 (π.χ. 094259216) χάνει το
αρχικό 0 όταν η στήλη δεν είναι κείμενο: το μήνυμα το λέει.
"""
import asyncio
import csv
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import routes_import
from db import db, fetch_all
from tax_utils import is_valid_afm

VALID_PERSON = "123456783"
VALID_COMPANY = "999888771"
VALID_LEADING_ZERO = "094259216"
HEADER = "Όνομα,ΑΦΜ,ΔΟΥ,Email,Τηλέφωνο,Διεύθυνση,Τύπος\n"


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture()
def office():
    import server
    with TestClient(server.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/register", json={"email": "import@example.gr", "password": "Str0ngPass!", "name": "Διαχειριστής"})
        assert r.status_code == 200, r.text
        yield c


def _csv(office, rows: str, name="clients.csv"):
    return office.post("/api/import/clients", files={"file": (name, (HEADER + rows).encode("utf-8"), "text/csv")})


def _xlsx(office, rows: list[list]):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Όνομα", "ΑΦΜ", "ΔΟΥ", "Email", "Τηλέφωνο", "Διεύθυνση", "Τύπος"])
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return office.post("/api/import/clients", files={"file": (
        "clients.xlsx", stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def _stored():
    return {c["name"]: c for c in _run(fetch_all(db.clients.find({})))}


def test_the_afm_used_by_these_tests_have_valid_check_digits():
    assert all(is_valid_afm(a) for a in (VALID_PERSON, VALID_COMPANY, VALID_LEADING_ZERO))


# ---------------------------------------------------------------- ο έλεγχος


def test_rows_with_a_valid_afm_are_imported_and_rows_with_a_wrong_check_digit_are_not(office):
    r = _csv(office,
             f"Παπαδόπουλος Ι.,{VALID_PERSON},ΑΘΗΝΩΝ,,,,individual\n"
             f"Λάθος Ψηφίο Ε.Π.Ε.,123456789,ΑΘΗΝΩΝ,,,,company\n"
             f"ΑΒΓ Α.Ε.,{VALID_COMPANY},ΦΑΕ,,,,company\n")
    body = r.json()
    assert r.status_code == 200, r.text
    assert (body["created"], body["skipped"], body["total_rows"]) == (2, 1, 3)
    assert body["message"] == "Εισήχθησαν 2 νέοι πελάτες, παραλείφθηκαν 1."
    assert set(_stored()) == {"Παπαδόπουλος Ι.", "ΑΒΓ Α.Ε."}
    assert len(body["errors"]) == 1
    assert body["errors"][0].startswith("Γραμμή 3: ")                     # γραμμή 1 η επικεφαλίδα του Excel, 2 η πρώτη εγγραφή
    assert "«123456789»" in body["errors"][0] and "ψηφίο ελέγχου" in body["errors"][0]


def test_every_kind_of_wrong_afm_gets_the_message_of_the_manual_form(office):
    from tax_utils import afm_error
    wrong = ["123456789", "12345678", "1234567890", "12345678A", "000000000", "١٢٣٤٥٦٧٨٩", "12 345 6783"]
    rows = "".join(f"Πελάτης {i},{afm},,,,,individual\n" for i, afm in enumerate(wrong))
    body = _csv(office, rows).json()
    assert body["created"] == 0 and body["skipped"] == len(wrong)
    expected = [f"Γραμμή {i + 2}: {afm_error(afm, 'ΑΦΜ')}" for i, afm in enumerate(wrong)]
    # το μήνυμα για 8 ψηφία έχει και την υπόδειξη για το Excel· τα υπόλοιπα είναι ακριβώς του χειροκίνητου τύπου
    for got, want in zip(body["errors"], expected):
        assert got.startswith(want), (got, want)
    assert _stored() == {}


def test_an_afm_that_lost_its_leading_zero_is_refused_with_the_reason(office):
    body = _csv(office, f"Χωρίς Μηδενικό,{VALID_LEADING_ZERO[1:]},,,,,individual\n").json()
    assert body["created"] == 0 and body["skipped"] == 1
    message = body["errors"][0]
    assert "8 ψηφία" in message and "αρχικό 0" in message and "κείμενο" in message
    # τα άλλα μηνύματα δεν έχουν την υπόδειξη (δεν αφορούν το Excel)
    for not_a_lost_zero in ("1234567", "1234567A", "١٢٣٤٥٦٧٨", "123 4567", "123456789"):
        other = _csv(office, f"Άλλο,{not_a_lost_zero},,,,,individual\n").json()["errors"][0]
        assert "αρχικό 0" not in other, not_a_lost_zero


def test_an_afm_with_a_leading_zero_is_imported_when_it_is_text(office):
    assert _csv(office, f"Με Μηδενικό,{VALID_LEADING_ZERO},,,,,company\n").json()["created"] == 1
    assert _stored()["Με Μηδενικό"]["afm"] == VALID_LEADING_ZERO


def test_rows_without_an_afm_are_still_imported(office):
    body = _csv(office, "Ιδιώτης Χωρίς ΑΦΜ,,,,,,individual\nΆλλος Ιδιώτης,,,,,,\n").json()
    assert body["created"] == 2 and body["errors"] == []
    assert all(c["afm"] == "" for c in _stored().values())


def test_the_afm_is_trimmed_before_it_is_checked_and_stored(office):
    body = _csv(office, f'Με Κενά,"  {VALID_PERSON}  ",,,,,individual\n').json()
    assert body["created"] == 1, body
    assert _stored()["Με Κενά"]["afm"] == VALID_PERSON


def test_a_wrong_afm_is_refused_even_when_the_name_already_exists(office):
    _csv(office, f"Υπάρχων,{VALID_PERSON},,,,,individual\n")
    body = _csv(office, "Υπάρχων,123456789,,,,,individual\n").json()
    assert body["created"] == 0 and body["skipped"] == 1 and "ψηφίο ελέγχου" in body["errors"][0]


def test_the_duplicate_rules_are_unchanged(office):
    body = _csv(office,
                f"Πρώτος,{VALID_PERSON},,,,,individual\n"
                f"Ίδιο ΑΦΜ άλλο όνομα,{VALID_PERSON},,,,,individual\n"
                f"Πρώτος,{VALID_COMPANY},,,,,individual\n"
                f",{VALID_COMPANY},,,,,individual\n").json()
    assert (body["created"], body["skipped"]) == (1, 3)
    assert body["errors"] == ["Γραμμή 5: λείπει όνομα/επωνυμία"]
    assert list(_stored()) == ["Πρώτος"]


def test_nothing_is_written_for_a_refused_row_not_even_a_partial_client(office):
    _csv(office, "Άκυρος,123456789,ΑΘΗΝΩΝ,a@example.gr,2101234567,Αθήνα,company\n")
    assert _run(db.clients.count_documents({})) == 0


# ---------------------------------------------------------------- οι επικεφαλίδες


@pytest.mark.parametrize("header, field", [
    ("Ονομα", "name"), ("Όνομα", "name"), ("ΟΝΟΜΑ", "name"), ("όνομα", "name"), ("Επωνυμία", "name"), ("ΕΠΩΝΥΜΙΑ", "name"),
    ("Name", "name"), ("onoma", "name"),
    ("ΑΦΜ", "afm"), ("αφμ", "afm"), ("AFM", "afm"),
    ("ΔΟΥ", "doy"), ("Δ.Ο.Υ.", "doy"), ("Δοϋ", "doy"), ("DOY", "doy"),
    ("Τηλέφωνο", "phone"), ("ΤΗΛΕΦΩΝΟ", "phone"), ("Τηλεφωνο", "phone"), ("Τηλ.", "phone"), ("Phone", "phone"),
    ("Διεύθυνση", "address"), ("ΔΙΕΥΘΥΝΣΗ", "address"), ("Διευθυνση", "address"), ("Address", "address"),
    ("Τύπος", "client_type"), ("ΤΥΠΟΣ", "client_type"), ("Τυπος", "client_type"), ("Type", "client_type"), ("client_type", "client_type"),
    ("Email", "email"), ("E-mail", "email"), ("EMAIL", "email"), ("e_mail", "email"), ("E mail", "email"),
    ("Client Type", "client_type"), (" client_type ", "client_type"),
])
def test_a_column_is_recognised_whatever_its_accents_and_case(header, field):
    assert routes_import._CLIENT_KEYS.get(routes_import._norm_key(header)) == field


def test_the_accent_free_table_agrees_with_the_readable_one():
    for raw, field in routes_import.CLIENT_KEY_MAP.items():
        assert routes_import._CLIENT_KEYS[routes_import._norm_key(raw)] == field, raw


@pytest.mark.parametrize("header", ["Κωδικός", "Σχόλια", "ΑΦΜ2", "Ονομασία", ""])
def test_a_column_the_application_does_not_know_is_ignored(header):
    assert routes_import._parse_client_row({"Όνομα": "Χ", header: "τιμή"}) == {"name": "Χ", "client_type": "individual"}


def test_a_greek_excel_export_imports(office):
    """«Αποθήκευση ως CSV» από ελληνικό Excel: κωδικοποίηση Windows-1253, διαχωριστής `;`, επικεφαλίδες με κεφαλαία."""
    text = (f"ΟΝΟΜΑ;ΑΦΜ;ΤΗΛΕΦΩΝΟ;ΤΥΠΟΣ\nΠαπαδόπουλος Ιωάννης;{VALID_PERSON};2101234567;ιδιώτης\n"
            f"ΑΒΓ Α.Ε.;{VALID_COMPANY};2109999999;εταιρεία\n")
    r = office.post("/api/import/clients", files={"file": ("clients.csv", text.encode("cp1253"), "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and r.json()["errors"] == [], r.json()
    stored = _stored()
    assert stored["Παπαδόπουλος Ιωάννης"]["afm"] == VALID_PERSON and stored["Παπαδόπουλος Ιωάννης"]["phone"] == "2101234567"
    assert stored["ΑΒΓ Α.Ε."]["client_type"] == "company"


# ---------------------------------------------------------------- το παράδειγμα και το Excel


def test_the_template_the_user_downloads_imports_without_a_single_error(office):
    template = office.get("/api/import/clients/template")
    assert template.status_code == 200
    rows = list(csv.reader(io.StringIO(template.content.decode("utf-8-sig"))))
    assert len(rows) >= 3
    for row in rows[1:]:
        assert is_valid_afm(row[1]), row                                 # κάθε ΑΦΜ του παραδείγματος είναι έγκυρο
    body = office.post("/api/import/clients", files={"file": ("clients_template.csv", template.content, "text/csv")}).json()
    assert body["created"] == len(rows) - 1 and body["skipped"] == 0 and body["errors"] == []


def test_excel_numbers_are_stored_without_a_trailing_dot_zero(office):
    body = _xlsx(office, [["Αριθμητικό ΑΦΜ", float(VALID_PERSON), "ΑΘΗΝΩΝ", "x@example.gr", 2101234567.0, "Αθήνα", "individual"],
                          ["Ακέραιο ΑΦΜ", int(VALID_COMPANY), "ΑΘΗΝΩΝ", "y@example.gr", 2101234568, "Αθήνα", "company"]]).json()
    assert body["created"] == 2 and body["errors"] == [], body
    stored = _stored()
    assert (stored["Αριθμητικό ΑΦΜ"]["afm"], stored["Αριθμητικό ΑΦΜ"]["phone"]) == (VALID_PERSON, "2101234567")
    assert (stored["Ακέραιο ΑΦΜ"]["afm"], stored["Ακέραιο ΑΦΜ"]["phone"]) == (VALID_COMPANY, "2101234568")


@pytest.mark.parametrize("cell, text", [(123456783.0, "123456783"), (2101234567.0, "2101234567"), (7, "7"), (" 12 ", "12"),
                                        (12.5, "12.5"), ("κείμενο", "κείμενο")])
def test_a_cell_becomes_text_without_losing_or_inventing_digits(cell, text):
    assert routes_import._cell_text(cell) == text


def test_a_float_afm_cell_is_stored_as_an_integer_string():
    parsed = routes_import._parse_client_row({"Όνομα": "Χ", "ΑΦΜ": 123456783.0, "Τηλέφωνο": 2101234567.0})
    assert parsed["afm"] == "123456783" and parsed["phone"] == "2101234567"


def test_an_empty_or_blank_cell_is_still_ignored():
    assert routes_import._parse_client_row({"Όνομα": "Χ", "ΑΦΜ": None, "Τηλέφωνο": "   ", "Email": ""}) == {
        "name": "Χ", "client_type": "individual"}


def test_an_excel_afm_that_lost_its_zero_is_refused_with_the_hint(office):
    body = _xlsx(office, [["Χωρίς Μηδενικό", int(VALID_LEADING_ZERO), "", "", "", "", "individual"]]).json()
    assert body["created"] == 0 and "αρχικό 0" in body["errors"][0]


def test_an_excel_afm_stored_as_text_keeps_its_zero(office):
    body = _xlsx(office, [["Με Μηδενικό", VALID_LEADING_ZERO, "", "", "", "", "company"]]).json()
    assert body["created"] == 1 and _stored()["Με Μηδενικό"]["afm"] == VALID_LEADING_ZERO


def test_the_error_list_is_still_capped_and_the_counts_stay_exact(office):
    rows = "".join(f"Πελάτης {i},12345678{i % 10},,,,,individual\n" for i in range(40))
    body = _csv(office, rows).json()
    assert body["skipped"] + body["created"] == 40
    assert len(body["errors"]) == 15
    assert body["total_rows"] == 40
