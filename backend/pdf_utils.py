from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import ParagraphStyle

pdfmetrics.registerFont(TTFont("Free", "/usr/share/fonts/truetype/freefont/FreeSans.ttf"))
pdfmetrics.registerFont(TTFont("FreeB", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"))

NAVY = colors.HexColor("#0a0f1e")
NAVY_LIGHT = colors.HexColor("#141b30")
GOLD = colors.HexColor("#c9a227")
GRAY = colors.HexColor("#5a6172")
LIGHT = colors.HexColor("#f4f2ec")

STATUS_LABEL = {"draft": "ΠΡΟΧΕΙΡΟ", "issued": "ΕΚΔΟΘΗΚΕ", "paid": "ΕΞΟΦΛΗΜΕΝΟ", "cancelled": "ΑΚΥΡΩΜΕΝΟ"}


def _money(v):
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _p(text, size=9, bold=False, color=colors.black, align=0, leading=None):
    style = ParagraphStyle(
        "s", fontName="FreeB" if bold else "Free", fontSize=size,
        textColor=color, alignment=align, leading=leading or size * 1.35,
    )
    return Paragraph(text, style)


def build_invoice_pdf(invoice: dict, client: dict, settings: dict) -> bytes:
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
        _p("LEGAL OPERATIONS PLATFORM", 6.5, color=colors.HexColor("#8a93a8")),
        Spacer(1, 4),
        _p(company, 10, bold=True, color=colors.white),
    ] + [_p(l, 8, color=colors.HexColor("#aeb6c8")) for l in comp_lines]

    meta_cell = [
        _p("ΤΙΜΟΛΟΓΙΟ ΠΑΡΟΧΗΣ ΥΠΗΡΕΣΙΩΝ", 10, bold=True, color=colors.white, align=2),
        Spacer(1, 3),
        _p(invoice.get("number") or "—", 13, bold=True, color=GOLD, align=2),
        _p(f"Ημερομηνία: {invoice.get('date', '—')}", 8.5, color=colors.HexColor("#aeb6c8"), align=2),
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
    totals_rows.append([_p("ΠΛΗΡΩΤΕΟ ΠΟΣΟ", 10.5, bold=True, color=NAVY, align=2), _p(_money(invoice.get("total")), 11, bold=True, color=GOLD, align=2)])
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


def build_letter_pdf(settings: dict, recipient: str, subject: str, body: str) -> bytes:
    from datetime import date
    from reportlab.platypus import Image as RLImage
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=14 * mm, bottomMargin=16 * mm)
    story = []

    story.append(RLImage("/app/backend/assets/letterhead_logo.png", width=52 * mm, height=34.7 * mm, hAlign="CENTER"))
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=GOLD))
    story.append(Spacer(1, 8 * mm))

    today = date.today()
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
