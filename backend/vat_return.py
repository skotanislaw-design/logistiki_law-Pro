"""Περιοδική δήλωση ΦΠΑ (έντυπο Φ2) — έτοιμη προς υποβολή.

Το Autopilot ήδη κλείνει κάθε περίοδο και υπολογίζει το πληρωτέο ΦΠΑ. Εδώ το
αποτέλεσμα παίρνει τη μορφή του εντύπου: κάθε ποσό μπαίνει στον κωδικό του, ώστε
ο χρήστης να αντιγράψει τιμές στο TAXISnet αντί να τις ξαναϋπολογίσει.

⚠️ **Οι αριθμοί κωδικών του Φ2 αλλάζουν με αποφάσεις της ΑΑΔΕ.** Είναι
παραμετρικοί (`Settings.vat_form_codes`) και οι default τιμές πρέπει να
επιβεβαιωθούν με τον λογιστή σας πριν την πρώτη υποβολή. Ο υπολογισμός των ποσών
δεν εξαρτάται από αυτούς — μόνο η ετικέτα κάθε γραμμής.
"""
from __future__ import annotations


# Προεπιλεγμένη αντιστοίχιση γραμμής → κωδικός εντύπου.
# Κλειδί = εσωτερικό αναγνωριστικό γραμμής· τιμή = (κωδικός, περιγραφή).
DEFAULT_FORM_CODES: dict[str, tuple[str, str]] = {
    "outputs_base_24": ("301", "Εκροές φορολογητέες με 24%"),
    "outputs_base_13": ("302", "Εκροές φορολογητέες με 13%"),
    "outputs_base_6": ("303", "Εκροές φορολογητέες με 6%"),
    "outputs_base_0": ("310", "Εκροές απαλλασσόμενες / μηδενικού συντελεστή"),
    "outputs_vat_24": ("331", "ΦΠΑ εκροών 24%"),
    "outputs_vat_13": ("332", "ΦΠΑ εκροών 13%"),
    "outputs_vat_6": ("333", "ΦΠΑ εκροών 6%"),
    "inputs_base": ("361", "Εισροές (φορολογητέα βάση)"),
    "inputs_vat": ("381", "ΦΠΑ εισροών προς έκπτωση"),
    "total_output_vat": ("480", "Σύνολο ΦΠΑ εκροών"),
    "total_input_vat": ("481", "Σύνολο ΦΠΑ εισροών"),
    "vat_payable": ("511", "ΦΠΑ για καταβολή"),
    "vat_credit": ("512", "Πιστωτικό υπόλοιπο μεταφερόμενο"),
}

RATES = (24, 13, 6, 0)


def form_codes(settings: dict | None = None) -> dict[str, tuple[str, str]]:
    """Οι κωδικοί του εντύπου, με override από τις Ρυθμίσεις."""
    codes = dict(DEFAULT_FORM_CODES)
    overrides = (settings or {}).get("vat_form_codes") or {}
    for key, value in overrides.items():
        if key in codes and value:
            codes[key] = (str(value), codes[key][1])
    return codes


def _round(value) -> float:
    return round(float(value or 0), 2)


def build_vat_return(period: dict, invoices: list[dict], expenses: list[dict],
                     settings: dict | None = None) -> dict:
    """Συνθέτει τη δήλωση από τα παραστατικά της περιόδου.

    Τα πιστωτικά έχουν αρνητικά ποσά, οπότε αφαιρούνται από μόνα τους.
    Οι εκροές αναλύονται ανά συντελεστή· οι εισροές συγκεντρωτικά, όπως στο έντυπο.
    """
    settings = settings or {}
    codes = form_codes(settings)

    outputs_base = {rate: 0.0 for rate in RATES}
    outputs_vat = {rate: 0.0 for rate in RATES}
    for inv in invoices:
        breakdown = inv.get("vat_breakdown") or {}
        if breakdown:
            for rate_str, vat_amount in breakdown.items():
                rate = int(float(rate_str))
                if rate not in outputs_vat:
                    continue
                outputs_vat[rate] += float(vat_amount or 0)
                # Η βάση κάθε συντελεστή προκύπτει από τις γραμμές του παραστατικού.
                outputs_base[rate] += sum(
                    float(li.get("quantity", 1)) * float(li.get("unit_price", 0))
                    for li in inv.get("line_items", []) if int(li.get("vat_rate", 24)) == rate
                )
        else:
            outputs_base[24] += float(inv.get("subtotal") or 0)
            outputs_vat[24] += float(inv.get("vat_amount") or 0)

    inputs_vat = sum(float(e.get("vat_amount") or 0) for e in expenses)
    inputs_base = sum(float(e.get("amount") or 0) - float(e.get("vat_amount") or 0) for e in expenses)

    total_output_vat = _round(sum(outputs_vat.values()))
    total_input_vat = _round(inputs_vat)
    balance = _round(total_output_vat - total_input_vat)

    lines = []

    def add(key: str, amount: float):
        code, label = codes[key]
        lines.append({"key": key, "code": code, "label": label, "amount": _round(amount)})

    for rate in RATES:
        if rate == 0:
            add("outputs_base_0", outputs_base[0])
        else:
            add(f"outputs_base_{rate}", outputs_base[rate])
    for rate in (24, 13, 6):
        add(f"outputs_vat_{rate}", outputs_vat[rate])
    add("inputs_base", inputs_base)
    add("inputs_vat", inputs_vat)
    add("total_output_vat", total_output_vat)
    add("total_input_vat", total_input_vat)
    add("vat_payable", balance if balance > 0 else 0)
    add("vat_credit", -balance if balance < 0 else 0)

    return {
        "period": period.get("period"),
        "period_start": period.get("period_start"),
        "period_end": period.get("period_end"),
        "due_date": period.get("due_date"),
        "company_name": settings.get("company_name", ""),
        "afm": settings.get("afm", ""),
        "doy": settings.get("doy", ""),
        "vat_period_type": settings.get("vat_period", "monthly"),
        "lines": lines,
        "totals": {
            "outputs_base": _round(sum(outputs_base.values())),
            "output_vat": total_output_vat,
            "inputs_base": _round(inputs_base),
            "input_vat": total_input_vat,
            "vat_payable": _round(balance if balance > 0 else 0),
            "vat_credit": _round(-balance if balance < 0 else 0),
        },
        "counts": {"invoices": len(invoices), "expenses": len(expenses)},
        "status": period.get("status", "computed"),
        "submitted_at": period.get("submitted_at"),
        "disclaimer": (
            "Οι αριθμοί κωδικών του εντύπου Φ2 μεταβάλλονται με αποφάσεις της ΑΑΔΕ. "
            "Επιβεβαιώστε τους με τον λογιστή σας πριν την υποβολή· τα ποσά προκύπτουν "
            "από τα καταχωρημένα παραστατικά της περιόδου."
        ),
    }


def build_vat_return_pdf(data: dict) -> bytes:
    """Εκτυπώσιμη μορφή του εντύπου, για αρχείο ή για αντιγραφή στο TAXISnet."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from pdf_utils import FONT, FONT_BOLD, NAVY, GOLD, GRAY

    def money(v):
        return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Δήλωση ΦΠΑ {data.get('period', '')}")
    title = ParagraphStyle("t", fontName=FONT_BOLD, fontSize=15, textColor=NAVY, leading=19)
    sub = ParagraphStyle("s", fontName=FONT, fontSize=9, textColor=GRAY, leading=13)
    note = ParagraphStyle("n", fontName=FONT, fontSize=7.5, textColor=GRAY, leading=10)

    period_label = "Μηνιαία" if data.get("vat_period_type") == "monthly" else "Τριμηνιαία"
    story = [
        Paragraph("ΠΕΡΙΟΔΙΚΗ ΔΗΛΩΣΗ ΦΠΑ — ΕΝΤΥΠΟ Φ2", title),
        Spacer(1, 3 * mm),
        Paragraph(
            f"{data.get('company_name', '')} · ΑΦΜ {data.get('afm', '') or '—'} · ΔΟΥ {data.get('doy', '') or '—'}<br/>"
            f"{period_label} περίοδος <b>{data.get('period', '')}</b> "
            f"({data.get('period_start', '')} έως {data.get('period_end', '')}) · "
            f"Προθεσμία υποβολής: <b>{data.get('due_date', '')}</b>", sub),
        Spacer(1, 6 * mm),
    ]

    rows = [["Κωδ.", "Περιγραφή", "Ποσό"]]
    for line in data["lines"]:
        rows.append([line["code"], line["label"], money(line["amount"])])
    table = Table(rows, colWidths=[18 * mm, 116 * mm, 40 * mm])
    style = [
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8d4c8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for idx, line in enumerate(data["lines"], start=1):
        if line["key"] in ("total_output_vat", "total_input_vat"):
            style.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#f4f2ec")))
            style.append(("FONTNAME", (0, idx), (-1, idx), FONT_BOLD))
        if line["key"] in ("vat_payable", "vat_credit") and line["amount"]:
            style.append(("BACKGROUND", (0, idx), (-1, idx), GOLD))
            style.append(("FONTNAME", (0, idx), (-1, idx), FONT_BOLD))
    table.setStyle(TableStyle(style))
    story.append(table)

    story += [
        Spacer(1, 6 * mm),
        Paragraph(f"Βάσει {data['counts']['invoices']} παραστατικών εσόδων και "
                  f"{data['counts']['expenses']} δαπανών της περιόδου.", sub),
        Spacer(1, 4 * mm),
        Paragraph(data["disclaimer"], note),
    ]
    doc.build(story)
    return buf.getvalue()
