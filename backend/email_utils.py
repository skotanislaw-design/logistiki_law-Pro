import aiosmtplib
from email.message import EmailMessage
from email.header import Header
from email.utils import formataddr
from email.mime.application import MIMEApplication


def smtp_ready(settings: dict) -> bool:
    return bool(settings.get("smtp_user") and settings.get("smtp_password"))


async def send_email_with_pdf(settings: dict, to_email: str, subject: str, body_text: str,
                              body_html: str, pdf_bytes: bytes = None, pdf_filename: str = "document.pdf"):
    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or 587)
    user = settings.get("smtp_user")
    password = settings.get("smtp_password")
    from_name = settings.get("smtp_from_name") or settings.get("company_name") or "Nomos One"

    msg = EmailMessage()
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), user))
    msg["To"] = to_email
    msg["Subject"] = Header(subject, "utf-8")
    msg.set_content(body_text, subtype="plain", charset="utf-8")
    msg.add_alternative(body_html, subtype="html", charset="utf-8")

    if pdf_bytes:
        pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_filename)
        msg.attach(pdf_part)

    use_tls = port == 465
    await aiosmtplib.send(
        msg, hostname=host, port=port, username=user, password=password,
        use_tls=use_tls, start_tls=not use_tls, timeout=30,
    )


def invoice_email_html(invoice: dict, settings: dict) -> str:
    company = settings.get("company_name") or "Nomos One"
    total = f"{(invoice.get('total') or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    iban_row = ""
    if settings.get("iban"):
        bank = f" ({settings['bank_name']})" if settings.get("bank_name") else ""
        iban_row = f'<p style="margin:4px 0;">IBAN: <strong>{settings["iban"]}</strong>{bank}</p>'
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#1a2035;max-width:560px;">
      <div style="background:#0a0f1e;padding:20px 24px;border-bottom:3px solid #c9a227;">
        <span style="color:#c9a227;font-size:20px;font-weight:bold;letter-spacing:2px;">NOMOS ONE</span><br/>
        <span style="color:#8a93a8;font-size:10px;letter-spacing:3px;">PERSONAL LOGISTICS · PERSACC</span>
      </div>
      <div style="padding:24px;">
        <p>Αγαπητέ/ή <strong>{invoice.get('client_name') or 'πελάτη'}</strong>,</p>
        <p>Σας αποστέλλουμε συνημμένο το τιμολόγιο <strong>{invoice.get('number')}</strong> για τις παρεχόμενες νομικές υπηρεσίες.</p>
        <div style="background:#f4f2ec;border-left:3px solid #c9a227;padding:12px 16px;margin:16px 0;">
          <p style="margin:4px 0;">Πληρωτέο ποσό: <strong style="color:#0a0f1e;font-size:16px;">{total}</strong></p>
          <p style="margin:4px 0;">Τρόπος πληρωμής: {invoice.get('payment_method') or '—'}</p>
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
    total = f"{(invoice.get('total') or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
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
