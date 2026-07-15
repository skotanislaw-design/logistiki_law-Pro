import httpx
from xml.sax.saxutils import escape
from xml.etree.ElementTree import fromstring

INVOICE_NS = "http://www.aade.gr/myDATA/invoice/v1.0"
ICLS_NS = "https://www.aade.gr/myDATA/incomeClassificaton/v1.0"
SANDBOX_BASE = "https://mydataapidev.aade.gr"
PROD_BASE = "https://mydatapi.aade.gr/myDATA"
VAT_CATEGORY = {24: "1", 13: "2", 6: "3", 0: "7"}


class MyDataError(Exception):
    pass


def mydata_ready(settings: dict) -> bool:
    return bool((settings.get("aade_user_id") or "").strip() and (settings.get("aade_subscription_key") or "").strip())


def _urls(settings: dict) -> dict:
    base = PROD_BASE if settings.get("aade_environment") == "production" else SANDBOX_BASE
    return {"send": f"{base}/SendInvoices", "docs": f"{base}/RequestTransmittedDocs"}


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


def build_invoices_doc_xml(invoice: dict, client: dict, settings: dict) -> str:
    afm = (settings.get("afm") or "").strip()
    if not afm:
        raise MyDataError("Συμπληρώστε το ΑΦΜ της εταιρείας στις Ρυθμίσεις πριν τη διαβίβαση στο myDATA.")
    client_afm = (client.get("afm") or "").strip()
    if not client_afm:
        raise MyDataError(f"Ο πελάτης «{client.get('name', '')}» δεν έχει ΑΦΜ — απαιτείται για τιμολόγιο τύπου 2.1 στο myDATA.")

    number = invoice.get("number") or ""
    if "-" in number:
        series, aa = number.rsplit("-", 1)
    else:
        series, aa = "A", number or "1"

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
            f"<icls:classificationType>E3_561_001</icls:classificationType>"
            f"<icls:classificationCategory>category1_3</icls:classificationCategory>"
            f"<icls:amount>{net:.2f}</icls:amount>"
            f"</incomeClassification></invoiceDetails>"
        )

    total_gross = round(total_net + total_vat, 2)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<InvoicesDoc xmlns="{INVOICE_NS}" xmlns:icls="{ICLS_NS}">'
        "<invoice>"
        f"<issuer><vatNumber>{escape(afm)}</vatNumber><country>GR</country><branch>0</branch></issuer>"
        f"<counterpart><vatNumber>{escape(client_afm)}</vatNumber><country>GR</country><branch>0</branch></counterpart>"
        "<invoiceHeader>"
        f"<series>{escape(series)}</series><aa>{escape(aa)}</aa>"
        f"<issueDate>{(invoice.get('date') or '')[:10]}</issueDate>"
        "<invoiceType>2.1</invoiceType><currency>EUR</currency>"
        "</invoiceHeader>"
        "<paymentMethods><paymentMethodDetails>"
        f"<type>{_payment_type(invoice.get('payment_method', ''))}</type>"
        f"<amount>{total_gross:.2f}</amount>"
        "</paymentMethodDetails></paymentMethods>"
        f"{''.join(lines_xml)}"
        "<invoiceSummary>"
        f"<totalNetValue>{total_net:.2f}</totalNetValue>"
        f"<totalVatAmount>{total_vat:.2f}</totalVatAmount>"
        "<totalWithheldAmount>0.00</totalWithheldAmount>"
        "<totalFeesAmount>0.00</totalFeesAmount>"
        "<totalStampDutyAmount>0.00</totalStampDutyAmount>"
        "<totalOtherTaxesAmount>0.00</totalOtherTaxesAmount>"
        "<totalDeductionsAmount>0.00</totalDeductionsAmount>"
        f"<totalGrossValue>{total_gross:.2f}</totalGrossValue>"
        "<incomeClassification>"
        "<icls:classificationType>E3_561_001</icls:classificationType>"
        "<icls:classificationCategory>category1_3</icls:classificationCategory>"
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
        elif tag == "message" and el.text:
            errors.append(el.text.strip())
    if status and status.lower() == "success" and mark:
        return {"mark": mark, "uid": uid or ""}
    detail = "· ".join(errors) if errors else f"Κατάσταση: {status or 'άγνωστη'}"
    raise MyDataError(f"Η ΑΑΔΕ απέρριψε το παραστατικό: {detail}")


async def send_invoice(settings: dict, invoice: dict, client: dict) -> dict:
    xml = build_invoices_doc_xml(invoice, client, settings)
    urls = _urls(settings)
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(urls["send"], headers=_headers(settings), content=xml.encode("utf-8"))
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    if resp.status_code in (401, 403):
        raise MyDataError("Μη έγκυρα διαπιστευτήρια ΑΑΔΕ (401). Ελέγξτε το User ID & Subscription Key στις Ρυθμίσεις.")
    if resp.status_code >= 400:
        raise MyDataError(f"Σφάλμα ΑΑΔΕ (HTTP {resp.status_code}): {resp.text[:300]}")
    return _parse_send_response(resp.text)


async def validate_credentials(settings: dict) -> dict:
    urls = _urls(settings)
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(urls["docs"], headers=_headers(settings), params={"mark": "1"})
    except httpx.RequestError as e:
        raise MyDataError(f"Αδυναμία επικοινωνίας με την ΑΑΔΕ: {e}")
    if resp.status_code in (401, 403):
        raise MyDataError("Μη έγκυρα διαπιστευτήρια ΑΑΔΕ. Ελέγξτε το User ID & Subscription Key.")
    if resp.status_code >= 400:
        raise MyDataError(f"Σφάλμα ΑΑΔΕ (HTTP {resp.status_code}): {resp.text[:200]}")
    return {"ok": True}
