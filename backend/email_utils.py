import html
import aiosmtplib
from crypto import decrypt
from email.message import EmailMessage
from email.header import Header
from email.utils import formataddr


def smtp_ready(settings: dict) -> bool:
    return bool(settings.get("smtp_user") and settings.get("smtp_password"))


def esc(value) -> str:
    """Escape για κάθε τιμή που μπαίνει σε HTML email. Ονόματα πελατών, αριθμοί και στοιχεία τράπεζας προέρχονται από
    φόρμες, εισαγωγές αρχείων και OCR: δεν είναι αξιόπιστο κείμενο και δεν επιτρέπεται να γίνουν markup."""
    return html.escape("" if value is None else str(value))


async def send_email_with_pdf(settings: dict, to_email: str, subject: str, body_text: str,
                              body_html: str, pdf_bytes: bytes = None, pdf_filename: str = "document.pdf"):
    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user")
    password = decrypt(settings.get("smtp_password") or "")
    from_name = settings.get("smtp_from_name") or settings.get("company_name") or "Nomos One"

    msg = EmailMessage()
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), user))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text, subtype="plain", charset="utf-8")
    msg.add_alternative(body_html, subtype="html", charset="utf-8")

    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename)

    use_tls = port == 465
    await aiosmtplib.send(
        msg, hostname=host, port=port, username=user, password=password,
        use_tls=use_tls, start_tls=not use_tls, timeout=30,
    )


def _payable(invoice: dict) -> float:
    """Ό,τι πρέπει να καταβάλει ο πελάτης: σύνολο μείον την παρακράτηση που κρατά ο ίδιος.

    Ταυτίζεται με το «ΠΛΗΡΩΤΕΟ ΠΟΣΟ» του PDF και με το ποσό πληρωμής στο XML της ΑΑΔΕ.
    """
    return round(float(invoice.get("total") or 0) - float(invoice.get("withholding_amount") or 0), 2)


def _outstanding(invoice: dict) -> float:
    """Ό,τι απομένει να καταβάλει ο πελάτης: το πληρωτέο μείον ό,τι εξοφλήθηκε από πελατολογικό λογαριασμό."""
    return round(max(_payable(invoice) - float(invoice.get("trust_applied") or 0), 0.0), 2)


def invoice_email_html(invoice: dict, settings: dict) -> str:
    company = esc(settings.get("company_name") or "Nomos One")
    total = f"{_payable(invoice):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    iban_row = ""
    if settings.get("iban"):
        bank = f" ({esc(settings['bank_name'])})" if settings.get("bank_name") else ""
        iban_row = f'<p style="margin:4px 0;">IBAN: <strong>{esc(settings["iban"])}</strong>{bank}</p>'
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:560px;">
      <div style="background:#0a0f1e;padding:20px 24px;border-bottom:3px solid #c9a227;">
        <span style="color:#c9a227;font-size:20px;font-weight:bold;letter-spacing:2px;">NOMOS ONE</span><br/>
        <span style="color:#8a93a8;font-size:10px;letter-spacing:3px;">PERSONAL LOGISTICS · PERSACC</span>
      </div>
      <div style="padding:24px;">
        <p>Αγαπητέ/ή <strong>{esc(invoice.get('client_name') or 'πελάτη')}</strong>,</p>
        <p>Σας αποστέλλουμε συνημμένο το τιμολόγιο <strong>{esc(invoice.get('number'))}</strong> για τις παρεχόμενες νομικές υπηρεσίες.</p>
        <div style="background:#f4f2ec;border-left:3px solid #c9a227;padding:12px 16px;margin:16px 0;">
          <p style="margin:4px 0;">Πληρωτέο ποσό: <strong style="color:#0a0f1e;font-size:16px;">{total}</strong></p>
          <p style="margin:4px 0;">Τρόπος πληρωμής: {esc(invoice.get('payment_method') or '—')}</p>
          {iban_row}
        </div>
        <p>Για οποιαδήποτε διευκρίνιση παραμένουμε στη διάθεσή σας.</p>
        <p>Με εκτίμηση,<br/><strong>{company}</strong></p>
      </div>
      <div style="padding:12px 24px;border-top:1px solid #e0ddd3;color:#8a93a8;font-size:11px;">
        Nomos One — Εμπιστευτικό έγγραφο. Εάν δεν είστε ο παραλήπτης, παρακαλούμε διαγράψτε το παρόν μήνυμα.
      </div>
    </body></html>
    """


def invoice_email_text(invoice: dict, settings: dict) -> str:
    company = settings.get("company_name") or "Nomos One"
    total = f"{_payable(invoice):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    lines = [
        f"Αγαπητέ/ή {invoice.get('client_name') or 'πελάτη'},",
        "",
        f"Σας αποστέλλουμε συνημμένο το τιμολόγιο {invoice.get('number')} για τις παρεχόμενες νομικές υπηρεσίες.",
        f"Πληρωτέο ποσό: {total}",
        f"Τρόπος πληρωμής: {invoice.get('payment_method') or '—'}",
    ]
    if settings.get("iban"):
        lines.append(f"IBAN: {settings['iban']}" + (f" ({settings['bank_name']})" if settings.get("bank_name") else ""))
    lines += ["", "Με εκτίμηση,", company]
    return "\n".join(lines)


# Ο σύνδεσμος κάρτας της υπενθύμισης λήγει σε λιγότερο από 24 ώρες (όριο του Stripe — βλ. invoice_services.PAYMENT_LINK_VALID_HOURS).
# Ο παραλήπτης πρέπει να το ξέρει πριν ανοίξει τον σύνδεσμο μέρες αργότερα και βρει μια ληγμένη σελίδα.
_LINK_VALIDITY = "Ο σύνδεσμος πληρωμής λήγει μέσα σε 24 ώρες από την αποστολή του μηνύματος."
_LINK_VALIDITY_WITH_IBAN = _LINK_VALIDITY[:-1] + " — αν δεν προλάβετε, μπορείτε να πληρώσετε με έμβασμα στο IBAN που αναγράφεται."


def _fmt_money(v) -> str:
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def reminder_email_html(invoice: dict, settings: dict, days_overdue: int, reminder_count: int, payment_link: str = "") -> str:
    company = esc(settings.get("company_name") or "Nomos One")
    total = _fmt_money(_outstanding(invoice))
    iban_row = ""
    if settings.get("iban"):
        bank = f" ({esc(settings['bank_name'])})" if settings.get("bank_name") else ""
        iban_row = f'<p style="margin:4px 0;">IBAN: <strong>{esc(settings["iban"])}</strong>{bank}</p>'
    tone = "ευγενική υπενθύμιση" if reminder_count == 0 else ("δεύτερη υπενθύμιση" if reminder_count == 1 else "νέα υπενθύμιση")
    overdue_note = ""
    if days_overdue > 0:
        overdue_note = (
            f'<p style="margin:8px 0;color:#7a5a1a;">'
            f'Η ημερομηνία έκδοσης του τιμολογίου ήταν πριν από <strong>{days_overdue}</strong> ημέρες.'
            f'</p>'
        )
    pay_button = ""
    if payment_link:
        link_validity = esc(_LINK_VALIDITY_WITH_IBAN if settings.get("iban") else _LINK_VALIDITY)
        pay_button = (
            f'<p style="margin:20px 0;text-align:center;">'
            f'<a href="{esc(payment_link)}" style="background:#c9a227;color:#0a0f1e;padding:14px 32px;'
            f'text-decoration:none;font-weight:600;border-radius:6px;display:inline-block;">'
            f'💳 Πληρωμή με κάρτα — {total}</a></p>'
            f'<p style="text-align:center;font-size:11px;color:#8a93a8;">Ασφαλής πληρωμή μέσω Stripe. {link_validity}</p>'
        )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:560px;">
      <div style="background:#0a0f1e;padding:20px 24px;border-bottom:3px solid #c9a227;">
        <span style="color:#c9a227;font-size:20px;font-weight:bold;letter-spacing:2px;">NOMOS ONE</span><br/>
        <span style="color:#8a93a8;font-size:10px;letter-spacing:3px;">PERSONAL LOGISTICS · PERSACC</span>
      </div>
      <div style="padding:24px;">
        <p>Αγαπητέ/ή <strong>{esc(invoice.get('client_name') or 'πελάτη')}</strong>,</p>
        <p>Σας αποστέλλουμε μια <strong>{tone}</strong> για το εκκρεμές τιμολόγιο <strong>{esc(invoice.get('number'))}</strong>,
        το οποίο σύμφωνα με τα αρχεία μας δεν έχει ακόμη εξοφληθεί.</p>
        {overdue_note}
        <div style="background:#f4f2ec;border-left:3px solid #c9a227;padding:12px 16px;margin:16px 0;">
          <p style="margin:4px 0;">Οφειλόμενο ποσό: <strong style="color:#0a0f1e;font-size:16px;">{total}</strong></p>
          <p style="margin:4px 0;">Τρόπος πληρωμής: {esc(invoice.get('payment_method') or '—')}</p>
          {iban_row}
        </div>
        {pay_button}
        <p>Παρακαλούμε να διευθετήσετε την εξόφληση σε σύντομο χρονικό διάστημα. Σε περίπτωση που η πληρωμή έχει ήδη
        πραγματοποιηθεί, αγνοήστε το παρόν μήνυμα και δεχθείτε τις ευχαριστίες μας.</p>
        <p>Για οποιαδήποτε διευκρίνιση παραμένουμε στη διάθεσή σας.</p>
        <p>Με εκτίμηση,<br/><strong>{company}</strong></p>
      </div>
      <div style="padding:12px 24px;border-top:1px solid #e0ddd3;color:#8a93a8;font-size:11px;">
        Nomos One — Εμπιστευτικό έγγραφο. Εάν δεν είστε ο παραλήπτης, παρακαλούμε διαγράψτε το παρόν μήνυμα.
      </div>
    </body></html>
    """


def reminder_email_text(invoice: dict, settings: dict, days_overdue: int, reminder_count: int, payment_link: str = "") -> str:
    company = settings.get("company_name") or "Nomos One"
    total = _fmt_money(_outstanding(invoice))
    tone = "Ευγενική υπενθύμιση" if reminder_count == 0 else ("Δεύτερη υπενθύμιση" if reminder_count == 1 else "Υπενθύμιση")
    lines = [
        f"Αγαπητέ/ή {invoice.get('client_name') or 'πελάτη'},",
        "",
        f"{tone}: το τιμολόγιο {invoice.get('number')} δεν έχει ακόμη εξοφληθεί.",
    ]
    if days_overdue > 0:
        lines.append(f"Η ημερομηνία έκδοσης ήταν πριν από {days_overdue} ημέρες.")
    lines += [
        f"Οφειλόμενο ποσό: {total}",
        f"Τρόπος πληρωμής: {invoice.get('payment_method') or '—'}",
    ]
    if settings.get("iban"):
        lines.append(f"IBAN: {settings['iban']}" + (f" ({settings['bank_name']})" if settings.get("bank_name") else ""))
    if payment_link:
        lines += ["", f"💳 Πληρωμή με κάρτα (ασφαλής μέσω Stripe): {payment_link}",
                  _LINK_VALIDITY_WITH_IBAN if settings.get("iban") else _LINK_VALIDITY]
    lines += [
        "",
        "Σε περίπτωση που η πληρωμή έχει ήδη πραγματοποιηθεί, αγνοήστε το παρόν μήνυμα.",
        "",
        "Με εκτίμηση,",
        company,
    ]
    return "\n".join(lines)
