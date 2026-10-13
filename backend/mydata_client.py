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
from decimal import Decimal
from tax_utils import athens_today, round_half_up
from xml.sax.saxutils import escape
from xml.etree.ElementTree import fromstring

import httpx

from crypto import decrypt

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

# Είδη που εκφράζουν πίστωση. Στο myDATA το **είδος** του παραστατικού φέρει το πρόσημο:
# τα ποσά υποβάλλονται πάντα θετικά (`AmountType` με `minInclusive="0"`). Εσωτερικά η
# εφαρμογή αποθηκεύει το πιστωτικό με αρνητικά ποσά, γιατί έτσι μειώνει έσοδα και ΦΠΑ σε
# κάθε άθροισμα — καμία συγκεντρωτική δεν εξαιρεί τα πιστωτικά. Οι δύο συμβάσεις
# συμφιλιώνονται εδώ, στο σημείο της διαβίβασης, και πουθενά αλλού.
CREDIT_TYPES = {TYPE_CREDIT_NOTE}

# Χαρακτηρισμός εσόδων. Οι επιτρεπτοί συνδυασμοί ανά τύπο παραστατικού ορίζονται στο
# επίσημο `syndiasmoi_xaraktirismwn_v2.0.1.xlsx`: για ΑΠΥ λιανικής (11.2) το
# E3_561_001 (χονδρικές) **δεν** είναι αποδεκτό — η λιανική θέλει E3_561_003.
CLASSIFICATION_WHOLESALE = "E3_561_001"   # Χονδρικές — επιτρεπτό στο 2.1
CLASSIFICATION_RETAIL = "E3_561_003"      # Λιανικές — επιτρεπτό στο 11.2

# Παρακρατούμενος φόρος: taxType 1, κατηγορία 2 = αμοιβές ελεύθερων επαγγελματιών 20%
WITHHOLDING_TAX_TYPE = "1"

# Κατηγορία συντελεστή παρακράτησης (Παράρτημα 8.4 του οδηγού ERP v2.0.1):
#   2  = Δικαιώματα — 20%
#   3  = Αμοιβές Συμβουλών Διοίκησης — 20%
#   10 = Προκαταβλητέος Φόρος στις Αμοιβές Δικηγόρων — 15%
# Η προεπιλογή ήταν «2» (δικαιώματα), που δεν αφορά αμοιβές υπηρεσιών. Το «3» είναι
# το μόνο συνεπές με τον συντελεστή 20% που ήδη χρησιμοποιεί ο κώδικας και με το
# επίσημο δείγμα αναφοράς. Αν η πρακτική του γραφείου εμπίπτει στο 10 (15%
# προκαταβλητέος), ορίζεται μαζί με το `withholding_rate` στις Ρυθμίσεις.
WITHHOLDING_CATEGORY_SERVICES = "3"
WITHHOLDING_RATE_FREELANCER = 0.20
# Οι μόνες κατηγορίες που δέχονται οι Ρυθμίσεις (έλεγχος στο PUT/PATCH /api/settings και επιλογές στην οθόνη).
# Τιμή εκτός λίστας θα διαβιβαζόταν ως `taxCategory` και θα απορριπτόταν από την ΑΑΔΕ τη στιγμή της έκδοσης.
WITHHOLDING_CATEGORIES = {
    "2": "Δικαιώματα — 20%",
    "3": "Αμοιβές Συμβουλών Διοίκησης — 20%",
    "10": "Προκαταβλητέος Φόρος στις Αμοιβές Δικηγόρων — 15%",
}


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
        "ocp-apim-subscription-key": decrypt(settings["aade_subscription_key"]).strip(),
        "Content-Type": "application/xml",
        "Accept": "application/xml",
    }


def _payment_type(method) -> str:
    """Τρόπος πληρωμής ΑΑΔΕ. Ανεκτικό σε μη-string (legacy/imported δεδομένα)."""
    m = str(method or "").strip().lower()
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
        return round_half_up(float(invoice["withholding_amount"]))
    if not settings.get("withholding_enabled", False):
        return 0.0
    if invoice_type in RETAIL_TYPES or invoice_type in CREDIT_TYPES:
        return 0.0
    threshold = float(settings.get("withholding_min_net", 300) or 0)
    if net_value < threshold:
        return 0.0
    rate = float(settings.get("withholding_rate", WITHHOLDING_RATE_FREELANCER) or 0)
    # Δεκαδικός υπολογισμός: με float, 300,50 × 15% έδινε 45,07 αντί 45,08 (βλ. tax_utils.round_half_up).
    return round_half_up(Decimal(str(net_value)) * Decimal(str(rate)))


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

    # Αλλοδαπός λήπτης: ο κώδικας δήλωνε <country>GR</country> για κάθε πελάτη, οπότε ένα
    # ΑΦΜ άλλης χώρας έφευγε ως ελληνικό και επέστρεφε δυσνόητη απόρριψη. Η αποτυχία εδώ
    # λέει τι πραγματικά λείπει. Τα είδη 2.2/2.3 (ενδοκοινοτική / τρίτες χώρες) δεν
    # υλοποιούνται — είναι χαρακτηριστικό, όχι διόρθωση.
    country = ((client or {}).get("country") or "GR").upper()
    if country != "GR":
        raise MyDataError(
            f"Ο πελάτης «{(client or {}).get('name', '')}» έχει χώρα {country}. "
            "Η διαβίβαση προς αλλοδαπό λήπτη απαιτεί είδος παραστατικού 2.2/2.3, "
            "που δεν υποστηρίζεται ακόμη."
        )

    client_afm = ((client or {}).get("afm") or "").strip()
    if invoice_type not in RETAIL_TYPES and not client_afm:
        raise MyDataError(
            f"Ο πελάτης «{(client or {}).get('name', '')}» δεν έχει ΑΦΜ — απαιτείται για παραστατικό "
            f"τύπου {invoice_type} στο myDATA. Συμπληρώστε το ΑΦΜ ή εκδώστε ΑΠΥ λιανικής."
        )
    if invoice_type == TYPE_CREDIT_NOTE and not invoice.get("correlated_mark"):
        raise MyDataError("Το πιστωτικό τιμολόγιο απαιτεί το MARK του αρχικού παραστατικού.")

    series, aa = _split_number(invoice.get("number") or "")
    if invoice_type in RETAIL_TYPES:
        classification_type = (settings.get("income_classification_type_retail")
                               or CLASSIFICATION_RETAIL)
    else:
        classification_type = settings.get("income_classification_type") or CLASSIFICATION_WHOLESALE
    classification_category = settings.get("income_classification_category") or "category1_3"

    # Ίδιος υπολογιστής με το αποθηκευμένο τιμολόγιο και το PDF (tax_utils), ώστε το
    # παραστατικό του πελάτη και η εγγραφή στην ΑΑΔΕ να μη διαφέρουν ποτέ ούτε κατά λεπτό.
    from tax_utils import compute_line_values

    line_values = compute_line_values(invoice.get("line_items", []))

    # Το πρόσημο κανονικοποιείται **μία φορά**, πριν συντεθεί οτιδήποτε: όλα τα ποσά του
    # παραστατικού (σύνολα, χαρακτηρισμοί, τρόπος πληρωμής) προκύπτουν από αυτές τις
    # γραμμές, οπότε μια διόρθωση εδώ τα καλύπτει όλα.
    if invoice_type in CREDIT_TYPES:
        line_values = [{"net": abs(v["net"]), "vat": abs(v["vat"]), "rate": v["rate"]} for v in line_values]
    else:
        # Αρνητικό ποσό σε μη πιστωτικό δεν διορθώνεται σιωπηλά: είτε λείπει το πιστωτικό
        # είτε η γραμμή είναι λάθος, και τα δύο θέλουν άνθρωπο. Το μήνυμα φτάνει στον
        # δικηγόρο ως αποτυχία διαβίβασης, εκεί ακριβώς που θα το ψάξει.
        if any(v["net"] < 0 or v["vat"] < 0 for v in line_values):
            raise MyDataError(
                "Το myDATA δέχεται αρνητικά ποσά μόνο σε πιστωτικά παραστατικά. "
                "Εκδώστε πιστωτικό τιμολόγιο ή διορθώστε τη γραμμή με το αρνητικό ποσό."
            )

    lines_xml = []
    total_net = Decimal("0")
    total_vat = Decimal("0")
    for idx, values in enumerate(line_values, start=1):
        net, vat, rate = values["net"], values["vat"], values["rate"]
        total_net += net
        total_vat += vat
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

    # Η ακαθάριστη αξία είναι **μετά** την παρακράτηση: καθαρή + ΦΠΑ − παρακρατηθέν.
    # Ο κώδικας δήλωνε το ποσό πριν την παρακράτηση, οπότε κάθε τιμολόγιο με ενεργή
    # παρακράτηση θα απορριπτόταν από την ΑΑΔΕ με ValidationError 203 («Η Ακαθάριστη
    # Αξία δεν ταιριάζει με το άθροισμα της καθαρής αξίας συν τους φόρους»).
    # Το ποσό πληρωμής ισούται με το totalGrossValue (οδηγός ERP v2.0.1, §4.2.5).
    withheld = withholding_for(invoice, settings, invoice_type, float(total_net))
    total_gross = total_net + total_vat - Decimal(str(withheld))
    payable = total_gross

    counterpart = ""
    if invoice_type not in RETAIL_TYPES:
        counterpart = (f"<counterpart><vatNumber>{escape(client_afm)}</vatNumber>"
                       f"<country>GR</country><branch>0</branch></counterpart>")

    correlated = ""
    if invoice.get("correlated_mark"):
        correlated = f"<correlatedInvoices>{escape(str(invoice['correlated_mark']))}</correlatedInvoices>"

    taxes_xml = ""
    if withheld:
        category = str(settings.get("withholding_category") or WITHHOLDING_CATEGORY_SERVICES)
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
            "date": _text(_find(header, "issueDate"))[:10] or athens_today().isoformat(),
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
