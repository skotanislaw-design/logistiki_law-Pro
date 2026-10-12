from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import ParagraphStyle

from branding import LETTERHEAD_LOGO


def _gr_date(value) -> str:
    """ΕΕΕΕ-ΜΜ-ΗΗ -> ΗΗ/ΜΜ/ΕΕΕΕ. Ελληνικό παραστατικό δεν γράφει ISO ημερομηνία."""
    from datetime import date as _date
    try:
        return _date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(value or "—")



# Greek-capable TrueType fonts. The first pair that exists on the host wins; without any
# of them we fall back to ReportLab's built-in Helvetica so the API never fails to import
# (the original hard-coded FreeSans path crashed the whole backend on hosts without it).
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/freefont/FreeSans.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
]


def _register_fonts() -> tuple[str, str]:
    import os
    custom = os.environ.get("PDF_FONT_REGULAR"), os.environ.get("PDF_FONT_BOLD")
    for regular, bold in ([custom] if all(custom) else []) + _FONT_CANDIDATES:
        if os.path.isfile(regular) and os.path.isfile(bold):
            try:
                pdfmetrics.registerFont(TTFont("Free", regular))
                pdfmetrics.registerFont(TTFont("FreeB", bold))
                return "Free", "FreeB"
            except Exception:
                continue
    import logging
    logging.getLogger(__name__).warning("No Greek TrueType font found for PDFs; falling back to Helvetica (set PDF_FONT_REGULAR/PDF_FONT_BOLD)")
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()

NAVY = colors.HexColor("#0a0f1e")
NAVY_LIGHT = colors.HexColor("#141b30")
GOLD = colors.HexColor("#c9a227")
GRAY = colors.HexColor("#5a6172")
LIGHT = colors.HexColor("#f4f2ec")

STATUS_LABEL = {"draft": "ΠΡΟΧΕΙΡΟ", "issued": "ΕΚΔΟΘΗΚΕ", "paid": "ΕΞΟΦΛΗΜΕΝΟ", "cancelled": "ΑΚΥΡΩΜΕΝΟ"}

# Ο τίτλος ακολουθεί το είδος του παραστατικού (myDATA): μια ΑΠΥ ή ένα πιστωτικό δεν
# επιτρέπεται να τυπώνονται ως «Τιμολόγιο Παροχής Υπηρεσιών».
DOCUMENT_TITLES = {
    "2.1": "ΤΙΜΟΛΟΓΙΟ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ",
    "11.2": "ΑΠΟΔΕΙΞΗ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ",
    "5.1": "ΠΙΣΤΩΤΙΚΟ ΤΙΜΟΛΟΓΙΟ",
}
DEFAULT_DOCUMENT_TITLE = DOCUMENT_TITLES["2.1"]


def _money(v):
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _p(text, size=9, bold=False, color=colors.black, align=0, leading=None):
    style = ParagraphStyle(
        "s", fontName=FONT_BOLD if bold else FONT, fontSize=size,
        textColor=color, alignment=align, leading=leading or size * 1.35,
    )
    return Paragraph(text, style)


def build_invoice_pdf(invoice: dict, client: dict, settings: dict) -> bytes:
    from mydata_client import resolve_invoice_type, withholding_for
    invoice_type = resolve_invoice_type(invoice, client or {})
    # Ίδιος υπολογισμός με το XML προς την ΑΑΔΕ: το ποσό που τυπώνεται είναι αυτό που δηλώνεται.
    withheld = withholding_for(invoice, settings, invoice_type, float(invoice.get("subtotal") or 0))
    payable = round(float(invoice.get("total") or 0) - withheld, 2)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    story = []

    company = settings.get("company_name") or "Δικηγορική Εταιρεία"
    comp_lines = []
    if settings.get("afm"):
        comp_lines.append(f"ΑΦΜ: {settings['afm']}" + (f" · ΔΟΥ: {settings['doy']}" if settings.get("doy") else ""))
    if settings.get("address"):
        comp_lines.append(settings["address"])
    contact = " · ".join(filter(None, [settings.get("phone"), settings.get("email")]))
    if contact:
        comp_lines.append(contact)

    brand_cell = [
        _p("NOMOS ONE", 17, bold=True, color=GOLD),
        _p("PERSONAL LOGISTICS · PERSACC", 6.5, color=colors.HexColor("#8a93a8")),
        Spacer(1, 4),
        _p(company, 10, bold=True, color=colors.white),
    ] + [_p(l, 8, color=colors.HexColor("#aeb6c8")) for l in comp_lines]

    meta_cell = [
        _p(DOCUMENT_TITLES.get(invoice_type, DEFAULT_DOCUMENT_TITLE), 10, bold=True, color=colors.white, align=2),
        Spacer(1, 3),
        _p(invoice.get("number") or "—", 13, bold=True, color=GOLD, align=2),
        _p(f"Ημερομηνία: {_gr_date(invoice.get('date'))}", 8.5, color=colors.HexColor("#aeb6c8"), align=2),
        _p(f"Κατάσταση: {STATUS_LABEL.get(invoice.get('status'), '—')}", 8.5, color=colors.HexColor("#aeb6c8"), align=2),
    ]

    header = Table([[brand_cell, meta_cell]], colWidths=[100 * mm, 78 * mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("LINEBELOW", (0, -1), (-1, -1), 2, GOLD),
    ]))
    story.append(header)
    story.append(Spacer(1, 8 * mm))

    client_lines = [_p("ΣΤΟΙΧΕΙΑ ΠΕΛΑΤΗ", 7.5, bold=True, color=GOLD), Spacer(1, 2),
                    _p(client.get("name") or invoice.get("client_name") or "—", 10.5, bold=True)]
    if client.get("afm"):
        client_lines.append(_p(f"ΑΦΜ: {client['afm']}" + (f" · ΔΟΥ: {client['doy']}" if client.get("doy") else ""), 8.5, color=GRAY))
    if client.get("address"):
        client_lines.append(_p(client["address"], 8.5, color=GRAY))
    c_contact = " · ".join(filter(None, [client.get("phone"), client.get("email")]))
    if c_contact:
        client_lines.append(_p(c_contact, 8.5, color=GRAY))

    client_box = Table([[client_lines]], colWidths=[178 * mm])
    client_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("LINEBEFORE", (0, 0), (0, -1), 2, GOLD),
    ]))
    story.append(client_box)
    story.append(Spacer(1, 7 * mm))

    rows = [[_p("ΠΕΡΙΓΡΑΦΗ", 8, bold=True, color=colors.white),
             _p("ΠΟΣ.", 8, bold=True, color=colors.white, align=2),
             _p("ΤΙΜΗ ΜΟΝ.", 8, bold=True, color=colors.white, align=2),
             _p("ΦΠΑ %", 8, bold=True, color=colors.white, align=2),
             _p("ΑΞΙΑ", 8, bold=True, color=colors.white, align=2)]]
    for li in invoice.get("line_items", []):
        line_total = (li.get("quantity") or 0) * (li.get("unit_price") or 0)
        rows.append([
            _p(li.get("description") or "—", 9),
            _p(f"{li.get('quantity', 0):g}", 9, align=2),
            _p(_money(li.get("unit_price")), 9, align=2),
            _p(f"{li.get('vat_rate', 0)}%", 9, align=2),
            _p(_money(line_total), 9, align=2),
        ])
    items = Table(rows, colWidths=[86 * mm, 18 * mm, 26 * mm, 20 * mm, 28 * mm], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f6f2")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9d5c9")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(items)
    story.append(Spacer(1, 5 * mm))

    totals_rows = []
    for rate, amount in (invoice.get("vat_breakdown") or {}).items():
        totals_rows.append([_p(f"ΦΠΑ {rate}%", 8.5, color=GRAY, align=2), _p(_money(amount), 8.5, color=GRAY, align=2)])
    totals_rows.append([_p("Καθαρή Αξία", 9, align=2), _p(_money(invoice.get("subtotal")), 9, align=2)])
    totals_rows.append([_p("Σύνολο ΦΠΑ", 9, align=2), _p(_money(invoice.get("vat_amount")), 9, align=2)])
    if withheld:
        totals_rows.append([_p("Σύνολο με ΦΠΑ", 9, align=2), _p(_money(invoice.get("total")), 9, align=2)])
        totals_rows.append([_p("Παρακράτηση φόρου", 9, align=2), _p(_money(-withheld), 9, align=2)])
    totals_rows.append([_p("ΠΛΗΡΩΤΕΟ ΠΟΣΟ", 10.5, bold=True, color=NAVY, align=2), _p(_money(payable), 11, bold=True, color=GOLD, align=2)])
    totals = Table(totals_rows, colWidths=[135 * mm, 43 * mm])
    totals.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEABOVE", (0, -1), (-1, -1), 1.2, GOLD),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
    ]))
    story.append(totals)
    story.append(Spacer(1, 7 * mm))

    pay_lines = [_p("ΣΤΟΙΧΕΙΑ ΠΛΗΡΩΜΗΣ", 7.5, bold=True, color=GOLD), Spacer(1, 2),
                 _p(f"Τρόπος πληρωμής: {invoice.get('payment_method') or '—'}", 9)]
    if settings.get("iban"):
        pay_lines.append(_p(f"IBAN: {settings['iban']}" + (f" ({settings['bank_name']})" if settings.get("bank_name") else ""), 9))
    if invoice.get("mydata_mark"):
        pay_lines.append(Spacer(1, 3))
        pay_lines.append(_p(f"myDATA MARK: {invoice['mydata_mark']} · UID: {invoice.get('mydata_uid') or '—'}", 8, color=GRAY))
    if invoice.get("correlated_mark"):
        pay_lines.append(Spacer(1, 3))
        pay_lines.append(_p(f"Συσχετιζόμενο παραστατικό (MARK): {invoice['correlated_mark']}", 8, color=GRAY))
    if invoice.get("notes"):
        pay_lines.append(Spacer(1, 3))
        pay_lines.append(_p(f"Σημειώσεις: {invoice['notes']}", 8.5, color=GRAY))
    pay_box = Table([[pay_lines]], colWidths=[178 * mm])
    pay_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("LINEBEFORE", (0, 0), (0, -1), 2, GOLD),
    ]))
    story.append(pay_box)
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=GOLD))
    story.append(Spacer(1, 2 * mm))
    story.append(_p("Nomos One — Legal Operations Platform · Εμπιστευτικό Έγγραφο", 7.5, color=GRAY, align=1))

    doc.build(story)
    return buf.getvalue()


GREEK_MONTHS = ["", "Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου",
                "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"]


def _document_date(issued_at):
    """Η ημερομηνία που φέρει το έγγραφο: της έκδοσης (ώρα Αθήνας) αν έχει εκδοθεί, αλλιώς η σημερινή.

    Ο server τρέχει σε UTC — η `date.today()` δίνει την προηγούμενη μέρα μεταξύ 00:00 και 03:00 ώρα Ελλάδας.
    Και ένα εκδοθέν έγγραφο δεν αλλάζει ημερομηνία κάθε φορά που κατεβαίνει ξανά το PDF του.
    """
    from datetime import datetime, timezone
    from tax_utils import ATHENS, athens_today
    if issued_at:
        try:
            stamp = datetime.fromisoformat(str(issued_at))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(ATHENS).date()
        except ValueError:
            pass
    return athens_today()


def build_grammatio_pdf(grammatio: dict, settings: dict) -> bytes:
    from reportlab.platypus import Image as RLImage
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    story = []

    story.append(RLImage(LETTERHEAD_LOGO, width=48 * mm, height=32 * mm, hAlign="CENTER"))
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=GOLD))
    story.append(Spacer(1, 6 * mm))

    status_lbl = {"draft": "ΠΡΟΣΧΕΔΙΟ", "issued": "ΕΚΔΟΘΕΝ", "cancelled": "ΑΚΥΡΩΜΕΝΟ"}.get(grammatio.get("status"), "")
    story.append(_p(f"ΓΡΑΜΜΑΤΙΟ ΠΡΟΕΙΣΠΡΑΞΗΣ — {status_lbl}", 13, bold=True, color=NAVY, align=1))
    story.append(Spacer(1, 2 * mm))
    story.append(_p(grammatio.get("number") or "—", 11, bold=True, color=GOLD, align=1))
    story.append(Spacer(1, 6 * mm))

    today = _document_date(grammatio.get("issued_at"))
    rows = [
        [_p("Ημερομηνία", 9, bold=True), _p(f"{today.day} {GREEK_MONTHS[today.month]} {today.year}", 9)],
        [_p("Πελάτης / Εντολέας", 9, bold=True), _p(grammatio.get("client_name") or "—", 9)],
        [_p("Υπόθεση", 9, bold=True), _p(grammatio.get("case_title") or "—", 9)],
        [_p("Δικαστήριο", 9, bold=True), _p(grammatio.get("court_type") or "—", 9)],
        [_p("Διαδικασία", 9, bold=True), _p(grammatio.get("procedure") or "—", 9)],
    ]
    if grammatio.get("portal_ref"):
        rows.append([_p("Αρ. Γραμματίου (Portal Ολομέλειας)", 9, bold=True), _p(grammatio["portal_ref"], 9)])
    info = Table(rows, colWidths=[62 * mm, 104 * mm])
    info.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#e0ddd3")),
    ]))
    story.append(info)
    story.append(Spacer(1, 6 * mm))

    amount_rows = [
        [_p("ΑΝΑΛΥΣΗ", 8, bold=True, color=colors.white), _p("ΠΟΣΟ", 8, bold=True, color=colors.white, align=2)],
        [_p("Αμοιβή αναφοράς", 9.5), _p(_money(grammatio.get("reference_amount")), 9.5, align=2)],
        [_p("Εισφορές (ΔΣ/ΕΦΚΑ/Διανεμητικός)", 9.5), _p(_money(grammatio.get("contributions_amount")), 9.5, align=2)],
        [_p("ΦΠΑ 24% επί αμοιβής", 9.5), _p(_money(grammatio.get("vat_amount")), 9.5, align=2)],
        [_p("ΣΥΝΟΛΟ ΓΡΑΜΜΑΤΙΟΥ", 10.5, bold=True, color=NAVY), _p(_money(grammatio.get("total")), 11, bold=True, color=GOLD, align=2)],
    ]
    amounts = Table(amount_rows, colWidths=[120 * mm, 46 * mm])
    amounts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY_LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9d5c9")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
    ]))
    story.append(amounts)
    story.append(Spacer(1, 6 * mm))

    if grammatio.get("notes"):
        story.append(_p(f"Σημειώσεις: {grammatio['notes']}", 8.5, color=GRAY))
        story.append(Spacer(1, 4 * mm))

    story.append(_p("Η επίσημη έκδοση του γραμματίου προείσπραξης πραγματοποιείται μέσω portal.olomeleia.gr. Τα ποσά αποτελούν ποσά αναφοράς βάσει Ν.4194/2013 — επιβεβαιώστε με τον πίνακα του οικείου Δικηγορικού Συλλόγου.", 7.5, color=GRAY, align=4))
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=GOLD))
    story.append(Spacer(1, 2 * mm))
    parts = [settings.get("company_name"), settings.get("address"), f"Τηλ. {settings['phone']}" if settings.get("phone") else None]
    story.append(_p("  |  ".join([p for p in parts if p]), 8, color=GRAY, align=1))

    doc.build(story)
    return buf.getvalue()


def build_letter_pdf(settings: dict, recipient: str, subject: str, body: str) -> bytes:
    from reportlab.platypus import Image as RLImage
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    story = []

    story.append(RLImage(LETTERHEAD_LOGO, width=52 * mm, height=34.7 * mm, hAlign="CENTER"))
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=GOLD))
    story.append(Spacer(1, 8 * mm))

    today = _document_date(None)
    story.append(_p(f"Αθήνα, {today.day} {GREEK_MONTHS[today.month]} {today.year}", 9.5, color=GRAY, align=2))
    story.append(Spacer(1, 5 * mm))
    if recipient:
        story.append(_p(f"Προς: {recipient}", 10.5, bold=True, color=NAVY))
        story.append(Spacer(1, 3 * mm))
    if subject:
        story.append(_p(f"Θέμα: {subject}", 11, bold=True, color=NAVY))
        story.append(Spacer(1, 6 * mm))

    for para in [p.strip() for p in body.replace("\r", "").split("\n") if p.strip()]:
        story.append(_p(para, 10.5, align=4, leading=16))
        story.append(Spacer(1, 3 * mm))

    story.append(Spacer(1, 8 * mm))
    story.append(_p("Με εκτίμηση,", 10.5))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_p(settings.get("company_name") or "Nomos One", 11, bold=True, color=NAVY))

    story.append(Spacer(1, 12 * mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=GOLD))
    story.append(Spacer(1, 2 * mm))
    parts = [settings.get("address"), f"Τηλ. {settings['phone']}" if settings.get("phone") else None, settings.get("email")]
    story.append(_p("  |  ".join([p for p in parts if p]), 8, color=GRAY, align=1))

    doc.build(story)
    return buf.getvalue()
