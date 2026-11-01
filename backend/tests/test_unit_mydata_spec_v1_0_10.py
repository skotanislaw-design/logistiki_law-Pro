"""Αντιστοίχιση με την «Τεχνική περιγραφή διεπαφών REST API», έκδοση 1.0.10 (11/2024).

Κάθε test κλειδώνει μια τιμή που **διασταυρώθηκε με το επίσημο έγγραφο** και αναφέρει
την παράγραφο. Σκοπός δεν είναι η κάλυψη κώδικα αλλά η αποτροπή σιωπηλής παλινδρόμησης:
οι κωδικοί της ΑΑΔΕ δεν προκαλούν σφάλμα όταν είναι λάθος — περνούν και φαίνονται σε
έλεγχο. Όταν βγει νέα έκδοση του εγγράφου, αυτό το αρχείο είναι το σημείο σύγκρισης.
"""
import pytest

import mydata_client as md


# --------------------------------------------------- §4.1.2 + §4.2 endpoints ---

def test_endpoints_match_spec_chapter_4():
    """Το sandbox **δεν** έχει το πρόθεμα /myDATA — λάθος που δίνει 404 στην παραγωγή."""
    prod = md._urls({"aade_environment": "production"})
    dev = md._urls({"aade_environment": "sandbox"})
    assert prod["send"] == "https://mydatapi.aade.gr/myDATA/SendInvoices"
    assert prod["cancel"] == "https://mydatapi.aade.gr/myDATA/CancelInvoice"
    assert prod["docs"] == "https://mydatapi.aade.gr/myDATA/RequestDocs"
    assert prod["transmitted"] == "https://mydatapi.aade.gr/myDATA/RequestTransmittedDocs"
    assert dev["send"] == "https://mydataapidev.aade.gr/SendInvoices"
    assert dev["docs"] == "https://mydataapidev.aade.gr/RequestDocs"


def test_required_headers_are_exactly_the_two_the_spec_names():
    headers = md._headers({"aade_user_id": " chrhsths ", "aade_subscription_key": "k3y"})
    assert headers["aade-user-id"] == "chrhsths"           # §4.1.2, trimmed
    assert headers["ocp-apim-subscription-key"] == "k3y"


# ------------------------------------------------------- §8.1 είδη παραστατικών ---

@pytest.mark.parametrize("code,label_fragment", [
    ("2.1", "Τιμολόγιο Παροχής"),
    ("11.2", "ΑΠΥ"),
    ("5.1", "Πιστωτικό"),
])
def test_invoice_type_codes_exist_in_appendix_8_1(code, label_fragment):
    """2.1 Τιμολόγιο Παροχής · 11.2 ΑΠΥ · 5.1 Πιστωτικό Συσχετιζόμενο."""
    assert code in md.INVOICE_TYPE_LABELS
    assert label_fragment.split()[0] in md.INVOICE_TYPE_LABELS[code] or code in ("11.2",)


def test_credit_note_is_the_correlated_variant_not_5_2():
    """5.1 = συσχετιζόμενο (έχει correlatedInvoices). Το 5.2 είναι μη συσχετιζόμενο."""
    assert md.TYPE_CREDIT_NOTE == "5.1"


# ------------------------------------------------------------ §8.2 κατηγορία ΦΠΑ ---

@pytest.mark.parametrize("rate,code", [
    (24, "1"), (13, "2"), (6, "3"), (17, "4"), (9, "5"), (4, "6"), (0, "7"), (3, "9"),
])
def test_vat_categories_match_appendix_8_2(rate, code):
    assert md.VAT_CATEGORY[rate] == code


def test_vat_category_8_is_not_offered_for_invoices():
    """Ο κωδικός 8 είναι «εγγραφές χωρίς ΦΠΑ» (μισθοδοσία, αποσβέσεις), όχι συντελεστής."""
    assert "8" not in md.VAT_CATEGORY.values()


def test_vat_rate_round_trips_through_the_category_code():
    for rate, code in md.VAT_CATEGORY.items():
        assert md._vat_rate_from_category(code) == rate


# ----------------------------------------------- §8.4 παρακρατούμενοι φόροι ---

def test_withholding_table_matches_appendix_8_4():
    assert md.WITHHOLDING_CATEGORIES["2"][0] == 0.20     # Δικαιώματα
    assert md.WITHHOLDING_CATEGORIES["3"][0] == 0.20     # Αμοιβές Συμβουλών Διοίκησης
    assert md.WITHHOLDING_CATEGORIES["10"][0] == 0.15    # Αμοιβές Δικηγόρων
    assert md.WITHHOLDING_CATEGORIES["4"][0] == 0.03     # Τεχνικά Έργα
    assert md.WITHHOLDING_CATEGORIES["17"][0] is None    # «ποσό», χωρίς ποσοστό


def test_withholding_tax_type_is_1():
    """§5.5: taxType 1 = Παρακρατούμενος Φόρος."""
    assert md.WITHHOLDING_TAX_TYPE == "1"


def test_royalties_category_is_flagged_even_though_the_rate_agrees():
    """Το 20% συμφωνεί, άρα μόνο αριθμητικός έλεγχος δεν αρκεί — η κατηγορία είναι λάθος."""
    warning = md.withholding_mismatch(
        {"withholding_enabled": True, "withholding_category": "2", "withholding_rate": 0.20})
    assert warning and "Δικαιώματα" in warning


def test_lawyer_category_requires_the_15_percent_rate():
    assert md.withholding_mismatch(
        {"withholding_enabled": True, "withholding_category": "10", "withholding_rate": 0.15}) is None
    mismatch = md.withholding_mismatch(
        {"withholding_enabled": True, "withholding_category": "10", "withholding_rate": 0.20})
    assert mismatch and "15%" in mismatch and "20%" in mismatch


def test_amount_based_categories_skip_the_rate_check():
    assert md.withholding_mismatch(
        {"withholding_enabled": True, "withholding_category": "17", "withholding_rate": 0.33}) is None


def test_unknown_category_is_reported_not_silently_accepted():
    assert md.withholding_mismatch(
        {"withholding_enabled": True, "withholding_category": "77", "withholding_rate": 0.2})


def test_no_warning_when_withholding_is_off():
    assert md.withholding_mismatch({"withholding_enabled": False, "withholding_category": "2"}) is None


# --------------------------------------------- §8.8/8.9 χαρακτηρισμός εσόδων ---

def test_income_classification_codes_exist_in_the_appendix():
    """E3_561_001 Χονδρικές-Επιτηδευματιών · E3_561_003 Λιανικές-Ιδιωτική Πελατεία."""
    assert md.CLASSIFICATION_WHOLESALE == "E3_561_001"
    assert md.CLASSIFICATION_RETAIL == "E3_561_003"


# ------------------------------------------------------ §8.12 τρόποι πληρωμής ---

@pytest.mark.parametrize("text,code", [
    ("Μετρητά", "3"),
    ("Επιταγή", "4"),
    ("Επί πιστώσει", "5"),           # ο τόνος μετακινείται στην κλίση — βλ. _unaccented
    ("Πίστωση", "5"),
    ("Κάρτα / POS", "7"),
    ("e-POS", "7"),
    ("Web Banking", "6"),
    ("Άμεσες Πληρωμές IRIS", "8"),
    ("Λογαριασμός αλλοδαπής", "2"),
    ("Τραπεζική κατάθεση", "1"),
    (None, "1"),
    (123, "1"),                      # legacy/imported δεδομένα
])
def test_payment_methods_map_into_appendix_8_12(text, code):
    assert md._payment_type(text) == code


def test_payment_type_is_accent_insensitive():
    """Μία γραφή δεν πρέπει να δίνει άλλον κωδικό από την τονισμένη της εκδοχή."""
    assert md._payment_type("ΕΠΙ ΠΙΣΤΩΣΕΙ") == md._payment_type("επί πιστώσει") == "5"
    assert md._payment_type("ΜΕΤΡΗΤΑ") == md._payment_type("μετρητά") == "3"


# ------------------------------- §4.2.5-4.2.7 entityVatNumber (για λογιστές) ---

def test_entity_vat_number_is_omitted_when_we_transmit_for_ourselves():
    """Κενή παράμετρος, όχι κενή τιμή: η ΑΑΔΕ χρησιμοποιεί τον ΑΦΜ της εγγραφής."""
    assert md._entity_params({}) == {}
    assert md._entity_params({"aade_entity_vat_number": "   "}) == {}


def test_entity_vat_number_is_sent_when_acting_for_a_client():
    assert md._entity_params({"aade_entity_vat_number": " 123456789 "}) == {"entityVatNumber": "123456789"}


# --------------------------------------- §4.2.6 παρ.1 + §6.2 τμηματική λήψη ---

def _docs_xml(marks: list[str], *, continuation: tuple[str, str] | None = None) -> str:
    invoices = "".join(f"""
      <invoice>
        <mark>{m}</mark>
        <issuer><vatNumber>999888777</vatNumber><name>Προμηθευτής ΑΕ</name></issuer>
        <counterpart><vatNumber>123456789</vatNumber></counterpart>
        <invoiceHeader><issueDate>2026-03-01</issueDate><invoiceType>2.1</invoiceType></invoiceHeader>
        <invoiceDetails><vatCategory>1</vatCategory></invoiceDetails>
        <invoiceSummary><totalNetValue>100.00</totalNetValue><totalVatAmount>24.00</totalVatAmount>
          <totalGrossValue>124.00</totalGrossValue></invoiceSummary>
      </invoice>""" for m in marks)
    token = ""
    if continuation:
        token = (f"<continuationToken><nextPartitionKey>{continuation[0]}</nextPartitionKey>"
                 f"<nextRowKey>{continuation[1]}</nextRowKey></continuationToken>")
    return f"<RequestedDoc>{token}{invoices}</RequestedDoc>"


def test_continuation_token_is_read_when_present():
    nxt = md.parse_continuation(_docs_xml(["1"], continuation=("p1", "r1")))
    assert nxt == {"nextPartitionKey": "p1", "nextRowKey": "r1"}


def test_no_continuation_token_means_the_listing_is_complete():
    assert md.parse_continuation(_docs_xml(["1"])) is None
    assert md.parse_continuation("όχι xml") is None


@pytest.mark.asyncio
async def test_request_docs_follows_every_page_before_advancing_the_cursor(monkeypatch):
    """Η παλιά υλοποίηση κρατούσε μόνο την 1η σελίδα και προωθούσε το MARK — τα
    υπόλοιπα έξοδα χάνονταν οριστικά. Εδώ πρέπει να επιστραφούν και τα 5."""
    pages = [
        _docs_xml(["101", "102"], continuation=("p2", "r2")),
        _docs_xml(["103", "104"], continuation=("p3", "r3")),
        _docs_xml(["105"]),
    ]
    calls: list[dict] = []

    class _Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(dict(params or {}))
            return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: _Client())
    settings = {"aade_user_id": "u", "aade_subscription_key": "k", "afm": "123456789"}

    expenses, cursor = await md.request_docs(settings, "100")

    assert [e["mark"] for e in expenses] == ["101", "102", "103", "104", "105"]
    assert cursor == "105"
    assert len(calls) == 3
    assert "nextPartitionKey" not in calls[0]
    assert calls[1]["nextPartitionKey"] == "p2" and calls[1]["nextRowKey"] == "r2"
    assert calls[2]["nextPartitionKey"] == "p3"
    assert all(c["mark"] == "100" for c in calls)      # το mark μένει σταθερό ανά σελίδα


@pytest.mark.asyncio
async def test_request_docs_stops_if_the_service_repeats_the_same_token(monkeypatch):
    """Άμυνα σε ελλιπή απόκριση: ίδιο token ξανά δεν πρέπει να γυρίζει ατέρμονα."""
    page = _docs_xml(["201"], continuation=("same", "same"))
    calls = []

    class _Resp:
        status_code = 200
        text = page

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            calls.append(params)
            return _Resp()

    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: _Client())
    expenses, _ = await md.request_docs({"aade_user_id": "u", "aade_subscription_key": "k",
                                         "afm": "123456789"}, "200")
    assert [e["mark"] for e in expenses] == ["201"]
    assert len(calls) == 2                             # 1η σελίδα + 1 επανάληψη, μετά stop


@pytest.mark.asyncio
async def test_request_docs_passes_entity_vat_number_through(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = _docs_xml(["301"])

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            seen.update(params or {})
            return _Resp()

    monkeypatch.setattr(md.httpx, "AsyncClient", lambda *a, **k: _Client())
    await md.request_docs({"aade_user_id": "u", "aade_subscription_key": "k",
                           "afm": "123456789", "aade_entity_vat_number": "987654321"}, "300")
    assert seen["entityVatNumber"] == "987654321"
