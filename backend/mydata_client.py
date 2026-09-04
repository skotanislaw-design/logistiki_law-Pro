"""ΑΑΔΕ myDATA REST client.

Καλύπτει και τις δύο κατευθύνσεις:

* **Εξερχόμενα** — διαβίβαση παραστατικών (`SendInvoices`) και ακύρωση (`CancelInvoice`).
  Υποστηρίζονται τα είδη που εκδίδει ένα δικηγορικό γραφείο: ΤΠΥ προς επιχείρηση (2.1),
  ΑΠΥ λιανικής προς ιδιώτη (11.2) και πιστωτικό συσχετιζόμενο (5.1), με προαιρετική
  παρακράτηση φόρου αμοιβών ελεύθερων επαγγελματιών.
* **Εισερχόμενα** — λήψη των παραστατικών που διαβίβασαν οι προμηθευτές μας
  (`RequestDocs`), ώστε τα έξοδα να καταχωρούνται χωρίς πληκτρολόγηση ή OCR.

Οι κωδικοί (invoiceType, classification, withholding) είναι παραμετρικοί στο `Settings`,
γιατί οι πίνακες της ΑΑΔΕ αλλάζουν· οι default τιμές αντιστοιχούν σε παροχή νομικών
υπηρεσιών. Επαληθεύστε τα στο sandbox πριν την παραγωγή.
"""
import os
from datetime import date
from xml.sax.saxutils import escape
from xml.etree.ElementTree import fromstring

import httpx

INVOICE_NS = "http://www.aade.gr/myDATA/invoice/v1.0"
ICLS_NS = "https://www.aade.gr/myDATA/incomeClassificaton/v1.0"
SANDBOX_BASE = "https://mydataapidev.aade.gr"
PROD_BASE = "https://mydatapi.aade.gr/myDATA"

VAT_CATEGORY = {24: "1", 13: "2", 6: "3", 0: "7"}

# Είδη παραστατικού που εκδίδει δικηγορικό γραφείο
TYPE_SERVICES_B2B = "2.1"    # Τιμολόγιο Παροχής Υπηρεσιών προς επιχείρηση/επαγγελματία
TYPE_RECEIPT_B2C = "11.2"    # Απόδειξη Παροχής Υπηρεσιών (λιανική, προς ιδιώτη)
TYPE_CREDIT_NOTE = "5.1"     # Πιστωτικό τιμολόγιο — συσχετιζόμενο
INVOICE_TYPE_LABELS = {
    TYPE_SERVICES_B2B: "Τιμολόγιο Παροχής Υπηρεσιών (προς επιχείρηση)",
    TYPE_RECEIPT_B2C: "Απόδειξη Παροχής Υπηρεσιών (προς ιδιώτη)",
    TYPE_CREDIT_NOTE: "Πιστωτικό Τιμολόγιο (συσχετιζόμενο)",
}
RETAIL_TYPES = {TYPE_RECEIPT_B2C}

# Παρακρατούμενος φόρος: taxType 1, κατηγορία 2 = αμοιβές ελεύθερων επαγγελματιών 20%
WITHHOLDING_TAX_TYPE = "1"
WITHHOLDING_CATEGORY_FREELANCER = "2"
WITHHOLDING_RATE_FREELANCER = 0.20


class MyDataError(Exception):
    pass


MYDATA_NOT_CONFIGURED_MSG = (
    "Δεν έχουν καταχωρηθεί credentials ΑΑΔΕ (User ID & Subscription Key). "
    "Το παραστατικό παραμένει σε αναμονή διαβίβασης."
)


def mydata_mock_enabled() -> bool:
    """Explicit opt-in simulation for demos. Never on by default: a MOCK mark that
    shows as «synced» misleads the user into thinking AADE received the invoice."""
    return os.environ.get("MYDATA_MOCK", "").lower() in ("1", "true", "yes")


def mydata_ready(settings: dict) -> bool:
    return bool((settings.get("aade_user_id") or "").strip() and (settings.get("aade_subscription_key") or "").strip())


def _urls(settings: dict) -> dict:
    base = PROD_BASE if settings.get("aade_environment") == "production" else SANDBOX_BASE
    return {
        "send": f"{base}/SendInvoices",
        "cancel": f"{base}/CancelInvoice",
        "transmitted": f"{base}/RequestTransmittedDocs",
        "docs": f"{base}/RequestDocs",
    }


def _headers(settings: dict) -> dict:
    return {
        "aade-user-id": settings["aade_user_id"].strip(),
        "ocp-apim-subscription-key": settings["aade_subscription_key"].strip(),
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }


def _payment_type(method: str) -> str:
    m = (method or "").lower()
    if "μετρητ" in m:
        return "3"
    if "κάρτ" in m or "pos" in m:
        return "7"
    if "επιταγ" in m:
        return "4"
    if "πίστωσ" in m:
        return "5"
    return "1"


# --------------------------------------------------------------- outgoing ---

def resolve_invoice_type(invoice: dict, client: dict) -> str:
    """Ποιο είδος παραστατικού αντιστοιχεί σε αυτό το τιμολόγιο.

    Ρητή επιλογή στο τιμολόγιο υπερισχύει· αλλιώς: πιστωτικό αν είναι πιστωτικό,
    ΑΠΥ λιανικής αν ο πελάτης είναι ιδιώτης χωρίς ΑΦΜ, αλλιώς ΤΠΥ 2.1.
    """
    explicit = (invoice.get("invoice_type") or "").strip()
    if explicit:
        return explicit
    if invoice.get("credit_note_for") or invoice.get("correlated_mark"):
        return TYPE_CREDIT_NOTE
    is_company = (client or {}).get("client_type") == "company"
    has_afm = bool(((client or {}).get("afm") or "").strip())
    return TYPE_SERVICES_B2B if (is_company or has_afm) else TYPE_RECEIPT_B2C


def withholding_for(invoice: dict, settings: dict, invoice_type: str, net_value: float) -> float:
    """Παρακράτηση 20% επί της καθαρής αξίας για αμοιβές δικηγόρου προς επιχείρηση.

    Δεν εφαρμόζεται στη λιανική (ιδιώτες) ούτε στα πιστωτικά, και μόνο πάνω από το
    όριο που ορίζεται στις Ρυθμίσεις. Ρητή τιμή στο τιμολόγιο υπερισχύει.
    """
    if invoice.get("withholding_amount") is not None:
        return round(float(invoice["withholding_amount"]), 2)
    if not settings.get("withholding_enabled", False):
        return 0.0
    if invoice_type in RETAIL_TYPES or invoice_type == TYPE_CREDIT_NOTE:
        return 0.0
    threshold = float(settings.get("withholding_min_net", 300) or 0)
    if net_value < threshold:
        return 0.0
    rate = float(settings.get("withholding_rate", WITHHOLDING_RATE_FREELANCER) or 0)
    return round(net_value * rate, 2)


def _split_number(number: str) -> tuple[str, str]:
    if "-" in (number or ""):
        series, aa = number.rsplit("-", 1)
        return series, aa
    return "A", number or "1"


def build_invoices_doc_xml(invoice: dict, client: dict, settings: dict) -> str:
    afm = (settings.get("afm") or "").strip()
    if not afm:
        raise MyDataError("Συμπληρώστε το ΑΦΜ της εταιρείας στις Ρυθμίσεις πριν τη διαβίβαση στο myDATA.")

    invoice_type = resolve_invoice_type(invoice, client)
    client_afm = ((client or {}).get("afm") or "").strip()
    if invoice_type not in RETAIL_TYPES and not client_afm:
        raise MyDataError(
            f"Ο πελάτης «{(client or {}).get('name', '')}» δεν έχει ΑΦΜ — απαιτείται για παραστατικό "
            f"τύπου {invoice_type} στο myDATA. Συμπληρώστε το ΑΦΜ ή εκδώστε ΑΠΥ λιανικής."
        )
    if invoice_type == TYPE_CREDIT_NOTE and not invoice.get("correlated_mark"):
        raise MyDataError("Το πιστωτικό τιμολόγιο απαιτεί το MARK του αρχικού παραστατικού.")

    series, aa = _split_number(invoice.get("number") or "")
    classification_type = settings.get("income_classification_type") or "E3_561_001"
    classification_category = settings.get("income_classification_category") or "category1_3"

    lines_xml = []
    total_net = 0.0
    total_vat = 0.0
    for idx, line in enumerate(invoice.get("line_items", []), start=1):
        qty = float(line.get("quantity", 1))
        price = float(line.get("unit_price", 0))
        net = round(qty * price, 2)
        rate = int(line.get("vat_rate", 24))
        vat = round(net * rate / 100, 2)
        total_net = round(total_net + net, 2)
        total_vat = round(total_vat + vat, 2)
        exemption = "<vatExemptionCategory>1</vatExemptionCategory>" if rate == 0 else ""
        lines_xml.append(
            f"<invoiceDetails><lineNumber>{idx}</lineNumber>"
            f"<netValue>{net:.2f}</netValue>"
            f"<vatCategory>{VAT_CATEGORY.get(rate, '1')}</vatCategory>"
            f"<vatAmount>{vat:.2f}</vatAmount>{exemption}"
            f"<incomeClassification>"
            f"<icls:classificationType>{escape(classification_type)}</icls:classificationType>"
            f"<icls:classificationCategory>{escape(classification_category)}</icls:classificationCategory>"
            f"<icls:amount>{net:.2f}</icls:amount>"
            f"</incomeClassification></invoiceDetails>"
        )

    withheld = withholding_for(invoice, settings, invoice_type, total_net)
    total_gross = round(total_net + total_vat, 2)
    payable = round(total_gross - withheld, 2)

    counterpart = ""
    if invoice_type not in RETAIL_TYPES:
        counterpart = (f"<counterpart><vatNumber>{escape(client_afm)}</vatNumber>"
                       f"<country>GR</country><branch>0</branch></counterpart>")

    correlated = ""
    if invoice.get("correlated_mark"):
        correlated = f"<correlatedInvoices>{escape(str(invoice['correlated_mark']))}</correlatedInvoices>"

    taxes_xml = ""
    if withheld:
        category = str(settings.get("withholding_category") or WITHHOLDING_CATEGORY_FREELANCER)
        taxes_xml = (
            "<taxesTotals><taxes>"
            f"<taxType>{WITHHOLDING_TAX_TYPE}</taxType>"
            f"<taxCategory>{escape(category)}</taxCategory>"
            f"<underlyingValue>{total_net:.2f}</underlyingValue>"
            f"<taxAmount>{withheld:.2f}</taxAmount>"
            "</taxes></taxesTotals>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<InvoicesDoc xmlns="{INVOICE_NS}" xmlns:icls="{ICLS_NS}">'
        "<invoice>"
        f"<issuer><vatNumber>{escape(afm)}</vatNumber><country>GR</country><branch>0</branch></issuer>"
        f"{counterpart}"
        "<invoiceHeader>"
        f"<series>{escape(series)}</series><aa>{escape(aa)}</aa>"
        f"<issueDate>{(invoice.get('date') or '')[:10]}</issueDate>"
        f"<invoiceType>{escape(invoice_type)}</invoiceType><currency>EUR</currency>"
        f"{correlated}"
        "</invoiceHeader>"
        "<paymentMethods><paymentMethodDetails>"
        f"<type>{_payment_type(invoice.get('payment_method', ''))}</type>"
        f"<amount>{payable:.2f}</amount>"
        "</paymentMethodDetails></paymentMethods>"
        f"{''.join(lines_xml)}"
        f"{taxes_xml}"
        "<invoiceSummary>"
        f"<totalNetValue>{total_net:.2f}</totalNetValue>"
        f"<totalVatAmount>{total_vat:.2f}</totalVatAmount>"
        f"<totalWithheldAmount>{withheld:.2f}</totalWithheldAmount>"
        "<totalFeesAmount>0.00</totalFeesAmount>"
        "<totalStampDutyAmount>0.00</totalStampDutyAmount>"
        "<totalOtherTaxesAmount>0.00</totalOtherTaxesAmount>"
        "<totalDeductionsAmount>0.00</totalDeductionsAmount>"
        f"<totalGrossValue>{total_gross:.2f}</totalGrossValue>"
        "<incomeClassification>"
        f"<icls:classificationType>{escape(classification_type)}</icls:classificationType>"
        f"<icls:classificationCategory>{escape(classification_category)}</icls:classificationCategory>"
        f"<icls:amount>{total_net:.2f}</icls:amount>"
        "</incomeClassification>"
        "</invoiceSummary>"
        "</invoice></InvoicesDoc>"
    )
    return xml


def _parse_send_response(xml_text: str) -> dict:
    try:
        root = fromstring(xml_text)
    except Exception:
        raise MyDataError("Μη αναγνώσιμη απόκριση από την ΑΑΔΕ.")
    status = mark = uid = None
    errors = []
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag == "statuscode":
            status = (el.text or "").strip()
        elif tag == "invoicemark":
            mark = (el.text or "").strip()
        elif tag == "invoiceuid":
            uid = (el.text or "").strip()
        elif tag in ("message", "errormessage") and el.text:
            errors.append(el.text.strip())
    if status and status.lower() == "success" and mark:
        return {"mark": mark, "uid": uid or ""}
    detail = "· ".join(errors) if errors else f"Κατάσταση: {status or 'άγνωστη'}"
    raise MyDataError(f"Η ΑΑΔΕ απέρριψε το παραστατικό: {detail}")


def _raise_for_status(resp: httpx.Response):
    if resp.status_code in (401, 403):
        raise MyDataError("Μη έγκυρα διαπιστευτήρια ΑΑΔΕ. Ελέγξτε το User ID & Subscription Key στις Ρυθμίσεις.")
    if resp.status_code == 429:
        raise MyDataError("Υπέρβαση ορίου κλήσεων προς την ΑΑΔΕ. Θα γίνει νέα προσπάθεια αργότερα.")
    if resp.status_code >= 400:
        raise MyDataError(f"Σφάλμα ΑΑΔΕ (HTTP {resp.status_code}): {resp.text[:300]}")


async def send_invoice(settings: dict, invoice: dict, client: dict) -> dict:
    xml = build_invoices_doc_xml(invoice, client, settings)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(_urls(settings)["send"], headers=_headers(settings), content=xml.encode("utf-8"))
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    _raise_for_status(resp)
    return _parse_send_response(resp.text)


async def cancel_invoice(settings: dict, mark: str) -> dict:
    """Ακύρωση διαβιβασμένου παραστατικού. Επιστρέφει το cancellation MARK."""
    if not (mark or "").strip():
        raise MyDataError("Δεν υπάρχει MARK — το παραστατικό δεν έχει διαβιβαστεί στο myDATA.")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(_urls(settings)["cancel"], headers=_headers(settings),
                                   params={"mark": str(mark).strip()})
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    _raise_for_status(resp)
    try:
        root = fromstring(resp.text)
    except Exception:
        raise MyDataError("Μη αναγνώσιμη απόκριση ακύρωσης από την ΑΑΔΕ.")
    status = cancellation_mark = None
    errors = []
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag == "statuscode":
            status = (el.text or "").strip()
        elif tag == "cancellationmark":
            cancellation_mark = (el.text or "").strip()
        elif tag in ("message", "errormessage") and el.text:
            errors.append(el.text.strip())
    if status and status.lower() == "success" and cancellation_mark:
        return {"cancellation_mark": cancellation_mark}
    detail = "· ".join(errors) if errors else f"Κατάσταση: {status or 'άγνωστη'}"
    raise MyDataError(f"Η ΑΑΔΕ απέρριψε την ακύρωση: {detail}")


async def validate_credentials(settings: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(_urls(settings)["transmitted"], headers=_headers(settings), params={"mark": "1"})
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    _raise_for_status(resp)
    return {"ok": True}


# --------------------------------------------------------------- incoming ---

def _text(el) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def _find(el, tag: str):
    """Namespace-agnostic descendant lookup (η ΑΑΔΕ αλλάζει namespaces ανά έκδοση)."""
    if el is None:
        return None
    for child in el.iter():
        if child is not el and child.tag.split("}")[-1] == tag:
            return child
    return None


def _find_all(root, tag: str) -> list:
    return [el for el in root.iter() if el.tag.split("}")[-1] == tag]


def _vat_rate_from_category(category: str) -> int:
    reverse = {v: k for k, v in VAT_CATEGORY.items()}
    return reverse.get((category or "").strip(), 24)


def parse_requested_docs(xml_text: str, our_afm: str) -> list[dict]:
    """Μετατρέπει την απόκριση του RequestDocs σε υποψήφια έξοδα.

    Κρατά μόνο τα παραστατικά όπου ο ΑΦΜ μας είναι ο **λήπτης** (counterpart):
    αυτά είναι τα έξοδα του γραφείου. Τα δικά μας εκδοθέντα επιστρέφονται επίσης
    από την ΑΑΔΕ και αγνοούνται εδώ.
    """
    try:
        root = fromstring(xml_text)
    except Exception:
        raise MyDataError("Μη αναγνώσιμη απόκριση RequestDocs από την ΑΑΔΕ.")
    our_afm = (our_afm or "").strip()
    docs = []
    for inv in _find_all(root, "invoice"):
        issuer = _find(inv, "issuer")
        counterpart = _find(inv, "counterpart")
        issuer_afm = _text(_find(issuer, "vatNumber"))
        counterpart_afm = _text(_find(counterpart, "vatNumber"))
        if our_afm and counterpart_afm != our_afm:
            continue  # δικό μας εκδοθέν ή τρίτου — δεν είναι έξοδο
        if our_afm and issuer_afm == our_afm:
            continue
        header = _find(inv, "invoiceHeader")
        summary = _find(inv, "invoiceSummary")
        mark = _text(_find(inv, "mark")) or _text(_find(inv, "invoiceMark"))
        if not mark:
            continue
        net = float(_text(_find(summary, "totalNetValue")) or 0)
        vat = float(_text(_find(summary, "totalVatAmount")) or 0)
        gross_text = _text(_find(summary, "totalGrossValue"))
        gross = float(gross_text) if gross_text else round(net + vat, 2)
        details = _find_all(inv, "invoiceDetails")
        if details:
            rate = _vat_rate_from_category(_text(_find(details[0], "vatCategory")))
        else:
            rate = round(vat / net * 100) if net else 24
        docs.append({
            "mark": mark,
            "uid": _text(_find(inv, "uid")),
            "issuer_afm": issuer_afm,
            "issuer_name": _text(_find(issuer, "name")),
            "date": _text(_find(header, "issueDate"))[:10] or date.today().isoformat(),
            "invoice_type": _text(_find(header, "invoiceType")),
            "series": _text(_find(header, "series")),
            "aa": _text(_find(header, "aa")),
            "net_value": round(net, 2),
            "vat_amount": round(vat, 2),
            "gross_value": round(gross, 2),
            "vat_rate": rate if rate in VAT_CATEGORY else 24,
        })
    return docs


def next_mark_from(xml_text: str, fallback: str = "0") -> str:
    """Το μεγαλύτερο MARK της απόκρισης, ώστε η επόμενη κλήση να συνεχίσει από εκεί."""
    try:
        root = fromstring(xml_text)
    except Exception:
        return fallback
    marks = [_text(el) for el in _find_all(root, "mark")] + [_text(el) for el in _find_all(root, "invoiceMark")]
    numeric = [int(m) for m in marks if m.isdigit()]
    return str(max(numeric)) if numeric else fallback


async def request_docs(settings: dict, mark: str = "0") -> tuple[list[dict], str]:
    """Κατεβάζει τα παραστατικά με MARK μεγαλύτερο του δοθέντος.

    Επιστρέφει (υποψήφια έξοδα, τελευταίο MARK) για incremental λήψη.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(_urls(settings)["docs"], headers=_headers(settings),
                                  params={"mark": str(mark or "0")})
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    _raise_for_status(resp)
    expenses = parse_requested_docs(resp.text, settings.get("afm", ""))
    return expenses, next_mark_from(resp.text, str(mark or "0"))
