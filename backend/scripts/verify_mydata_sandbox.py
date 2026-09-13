#!/usr/bin/env python3
"""Επαλήθευση της ενσωμάτωσης myDATA στο **sandbox** της ΑΑΔΕ.

Οι κωδικοί των πινάκων της ΑΑΔΕ (είδη παραστατικών, χαρακτηρισμοί εσόδων,
κατηγορίες παρακράτησης) αλλάζουν με αποφάσεις και δεν μπορούν να επαληθευτούν
offline. Αυτό το script τρέχει μια πλήρη μπαταρία ελέγχων με τα δικά σας
δοκιμαστικά credentials και τυπώνει τι πέρασε και τι όχι, ώστε να μη γίνει η
πρώτη δοκιμή σε πραγματικά παραστατικά.

    cd backend
    export AADE_SUBSCRIPTION_KEY=...        # όχι στη γραμμή εντολών: μένει στο ιστορικό
    python scripts/verify_mydata_sandbox.py \\
        --user-id ΟΝΟΜΑ_ΧΡΗΣΤΗ --afm ΑΦΜ_ΓΡΑΦΕΙΟΥ --counterpart-afm ΑΦΜ_ΔΟΚΙΜΗΣ

Διαπιστευτήρια sandbox: εγγραφή στο https://mydata-dev.portal.azure-api.net
(ο λογαριασμός δημιουργείται από εσάς — το script δεν αποθηκεύει πουθενά το κλειδί).

Προαιρετικά:
    --production        (χτυπά το πραγματικό περιβάλλον — ΜΟΝΟ αν ξέρετε τι κάνετε)
    --skip-cancel       (μην ακυρώσεις τα δοκιμαστικά παραστατικά που έστειλες)
    --json report.json  (αποθήκευση αναφοράς)

Το script **δεν** αγγίζει τη βάση: στέλνει παραστατικά που φτιάχνει το ίδιο.
Στο sandbox αυτό είναι ακίνδυνο· στην παραγωγή θα δημιουργούσε πραγματικά
παραστατικά, γι' αυτό ζητά ρητή επιβεβαίωση.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mydata_client as md  # noqa: E402

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _line(quantity: float, price: float, rate: int) -> dict:
    return {"description": "Νομικές υπηρεσίες", "quantity": quantity, "unit_price": price, "vat_rate": rate}


class Report:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "", data: dict | None = None):
        self.checks.append({"name": name, "ok": ok, "detail": detail, "data": data or {}})
        mark = f"{GREEN}✔{RESET}" if ok else f"{RED}✘{RESET}"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"]]

    def summary(self) -> str:
        passed = len(self.checks) - len(self.failed)
        color = GREEN if not self.failed else RED
        return f"{color}{passed}/{len(self.checks)} έλεγχοι πέρασαν{RESET}"


async def _send(settings: dict, invoice: dict, client: dict, report: Report, name: str) -> str | None:
    """Στέλνει ένα παραστατικό και επιστρέφει το MARK, ή None σε αποτυχία."""
    try:
        xml = md.build_invoices_doc_xml(invoice, client, settings)
    except md.MyDataError as e:
        report.add(f"{name}: σύνθεση XML", False, str(e))
        return None
    report.add(f"{name}: σύνθεση XML", True, f"{len(xml)} bytes")
    try:
        result = await md.send_invoice(settings, invoice, client)
    except md.MyDataError as e:
        report.add(f"{name}: αποδοχή από ΑΑΔΕ", False, str(e))
        return None
    report.add(f"{name}: αποδοχή από ΑΑΔΕ", True, f"MARK {result['mark']}", result)
    return result["mark"]


async def run(args) -> Report:
    report = Report()
    settings = {
        "aade_user_id": args.user_id,
        "aade_subscription_key": args.key,
        "aade_environment": "production" if args.production else "sandbox",
        "afm": args.afm,
        "income_classification_type": args.classification_type,
        "income_classification_category": args.classification_category,
        "withholding_enabled": True,
        "withholding_min_net": 0,
        "withholding_rate": md.WITHHOLDING_RATE_FREELANCER,
    }
    company = {"name": "Δοκιμαστική ΑΕ", "afm": args.counterpart_afm, "client_type": "company"}
    individual = {"name": "Δοκιμαστικός Ιδιώτης", "client_type": "individual"}
    today = date.today().isoformat()
    stamp = date.today().strftime("%m%d")

    print(f"\n{YELLOW}Περιβάλλον:{RESET} {settings['aade_environment']}  ·  ΑΦΜ {args.afm}\n")

    print("1. Διαπιστευτήρια")
    try:
        await md.validate_credentials(settings)
        report.add("Σύνδεση με την ΑΑΔΕ", True)
    except md.MyDataError as e:
        report.add("Σύνδεση με την ΑΑΔΕ", False, str(e))
        return report      # χωρίς σύνδεση, τα υπόλοιπα δεν έχουν νόημα

    print("\n2. Εξερχόμενα παραστατικά")
    marks: list[str] = []

    b2b = {"number": f"ΤΠΥ-TEST{stamp}-0001", "date": today, "payment_method": "Τραπεζική κατάθεση",
           "line_items": [_line(1, 100, 24)], "withholding_amount": 0}
    mark_b2b = await _send(settings, b2b, company, report, "ΤΠΥ 2.1 προς επιχείρηση")
    if mark_b2b:
        marks.append(mark_b2b)

    retail = {"number": f"ΑΠΥ-TEST{stamp}-0001", "date": today, "payment_method": "Μετρητά",
              "line_items": [_line(1, 50, 24)]}
    mark_retail = await _send(settings, retail, individual, report, "ΑΠΥ 11.2 λιανικής")
    if mark_retail:
        marks.append(mark_retail)

    withheld = {"number": f"ΤΠΥ-TEST{stamp}-0002", "date": today, "payment_method": "Τραπεζική κατάθεση",
                "line_items": [_line(1, 1000, 24)]}
    xml = None
    try:
        xml = md.build_invoices_doc_xml(withheld, company, settings)
    except md.MyDataError as e:
        report.add("Παρακράτηση 20%: σύνθεση", False, str(e))
    if xml:
        expected = "<totalWithheldAmount>200.00</totalWithheldAmount>"
        report.add("Παρακράτηση 20% στο XML", expected in xml,
                   "βρέθηκε" if expected in xml else "λείπει το totalWithheldAmount")
        mark_withheld = await _send(settings, withheld, company, report, "ΤΠΥ με παρακράτηση")
        if mark_withheld:
            marks.append(mark_withheld)

    if mark_b2b:
        # Θετικά ποσά: στο myDATA το είδος 5.1 φέρει το πρόσημο. Η εφαρμογή αποθηκεύει
        # αρνητικά και τα κανονικοποιεί κατά τη διαβίβαση (CREDIT_TYPES στο mydata_client).
        credit = {"number": f"ΠΙΣ-TEST{stamp}-0001", "date": today, "correlated_mark": mark_b2b,
                  "credit_note_for": "test", "line_items": [_line(1, 100, 24)], "withholding_amount": 0}
        mark_credit = await _send(settings, credit, company, report, "Πιστωτικό 5.1")
        if mark_credit:
            marks.append(mark_credit)
    else:
        report.add("Πιστωτικό 5.1", False, "παραλείφθηκε — δεν υπάρχει MARK αρχικού")

    print("\n3. Ακύρωση")
    if args.skip_cancel:
        report.add("Ακύρωση παραστατικού", True, "παραλείφθηκε με --skip-cancel")
    elif marks:
        try:
            result = await md.cancel_invoice(settings, marks[0])
            report.add("Ακύρωση παραστατικού", True, f"cancellation MARK {result['cancellation_mark']}")
        except md.MyDataError as e:
            report.add("Ακύρωση παραστατικού", False, str(e))
    else:
        report.add("Ακύρωση παραστατικού", False, "δεν υπάρχει MARK για ακύρωση")

    print("\n4. Εισερχόμενα (έξοδα προμηθευτών)")
    try:
        expenses, next_mark = await md.request_docs(settings, "0")
        report.add("RequestDocs", True, f"{len(expenses)} παραστατικά εξόδων · επόμενο MARK {next_mark}",
                   {"count": len(expenses), "next_mark": next_mark})
        if expenses:
            sample = expenses[0]
            complete = all(sample.get(f) is not None for f in ("mark", "date", "net_value", "vat_amount"))
            report.add("Πληρότητα πεδίων εισερχομένου", complete,
                       f"π.χ. {sample.get('issuer_name') or sample.get('issuer_afm')} "
                       f"{sample.get('gross_value')} € ({sample.get('date')})")
    except md.MyDataError as e:
        report.add("RequestDocs", False, str(e))

    return report


def main():
    parser = argparse.ArgumentParser(description="Επαλήθευση myDATA στο sandbox της ΑΑΔΕ")
    parser.add_argument("--user-id", required=True, help="aade-user-id")
    parser.add_argument("--key", default=os.environ.get("AADE_SUBSCRIPTION_KEY", ""),
                        help="Ocp-Apim-Subscription-Key (προτιμότερα μέσω AADE_SUBSCRIPTION_KEY)")
    parser.add_argument("--afm", required=True, help="ΑΦΜ του γραφείου (εκδότης)")
    parser.add_argument("--counterpart-afm", default="123456783",
                        help="ΑΦΜ δοκιμαστικού πελάτη-επιχείρησης (προεπιλογή: έγκυρο ψηφίο ελέγχου, placeholder)")
    parser.add_argument("--classification-type", default="E3_561_001")
    parser.add_argument("--classification-category", default="category1_3")
    parser.add_argument("--production", action="store_true", help="Πραγματικό περιβάλλον (δημιουργεί αληθινά παραστατικά!)")
    parser.add_argument("--skip-cancel", action="store_true")
    parser.add_argument("--json", help="Αρχείο αποθήκευσης αναφοράς")
    args = parser.parse_args()

    if not args.key:
        print(f"{RED}Λείπει το Subscription Key.{RESET} Ορίστε AADE_SUBSCRIPTION_KEY ή δώστε --key.")
        return 1

    if args.production:
        answer = input(f"{RED}Θα σταλούν ΠΡΑΓΜΑΤΙΚΑ παραστατικά στην ΑΑΔΕ. Γράψτε «ΝΑΙ» για να συνεχίσετε: {RESET}")
        if answer.strip().upper() != "ΝΑΙ":
            print("Ακυρώθηκε.")
            return 1

    report = asyncio.run(run(args))
    print(f"\n{report.summary()}")
    if report.failed:
        print(f"\n{YELLOW}Τι πρέπει να ελεγχθεί:{RESET}")
        for check in report.failed:
            print(f"  · {check['name']}: {check['detail']}")
        print("\nΟι κωδικοί των πινάκων ρυθμίζονται στις Ρυθμίσεις (income_classification_*, "
              "withholding_category) χωρίς αλλαγή κώδικα.")
    if args.json:
        Path(args.json).write_text(json.dumps(report.checks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nΑναφορά: {args.json}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
