#!/usr/bin/env python3
"""Δοκιμή σύνδεσης με το **δοκιμαστικό** περιβάλλον της ΑΑΔΕ.

Ο κώδικας διαβίβασης έχει επικυρωθεί κατά το επίσημο σχήμα XSD v2.0.1, αλλά η
επικύρωση σχήματος δεν είναι το ίδιο με αποδοχή από την ΑΑΔΕ: επιχειρησιακοί κανόνες
(συνδυασμοί χαρακτηρισμών, εγκυρότητα ΑΦΜ, δικαιώματα χρήστη) ελέγχονται μόνο από την
ίδια την υπηρεσία. Αυτό το script κάνει την πραγματική κλήση.

ΠΩΣ ΘΑ ΠΑΡΕΤΕ ΔΙΑΠΙΣΤΕΥΤΗΡΙΑ
-----------------------------
1. Εγγραφή στο δοκιμαστικό portal: https://mydata-dev.portal.azure-api.net
2. Από το προφίλ σας: «Subscription key» (Ocp-Apim-Subscription-Key)
3. Το `aade-user-id` είναι το όνομα χρήστη που δηλώσατε εκεί

ΧΡΗΣΗ
-----
    export AADE_USER_ID="το-όνομα-χρήστη"
    export AADE_SUBSCRIPTION_KEY="το-κλειδί"
    python backend/scripts/mydata_sandbox_check.py --afm 123456789

ΤΙ ΚΑΝΕΙ
--------
Στέλνει **ένα** δοκιμαστικό τιμολόγιο παροχής υπηρεσιών (2.1, 24% ΦΠΑ, χωρίς
παρακράτηση) και αμέσως μετά το ακυρώνει με το ΜΑΡΚ που επέστρεψε. Δεν αγγίζει τη
βάση της εφαρμογής, δεν αποθηκεύει πουθενά τα διαπιστευτήρια και αρνείται να τρέξει
σε περιβάλλον παραγωγής.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fail(message: str) -> None:
    print(f"\n✗ {message}\n", file=sys.stderr)
    raise SystemExit(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Δοκιμή διαβίβασης στο sandbox της ΑΑΔΕ")
    parser.add_argument("--afm", required=True, help="ΑΦΜ του γραφείου (εκδότης)")
    parser.add_argument("--counterpart-afm", default="997671770",
                        help="ΑΦΜ λήπτη· προεπιλογή δοκιμαστικό ΑΦΜ της ΑΑΔΕ")
    parser.add_argument("--keep", action="store_true",
                        help="Να μην ακυρωθεί το παραστατικό μετά την αποστολή")
    args = parser.parse_args()

    user_id = (os.environ.get("AADE_USER_ID") or "").strip()
    key = (os.environ.get("AADE_SUBSCRIPTION_KEY") or "").strip()
    if not user_id or not key:
        _fail("Ορίστε AADE_USER_ID και AADE_SUBSCRIPTION_KEY. "
              "Εγγραφή: https://mydata-dev.portal.azure-api.net")

    # Καθαρό περιβάλλον: το script δεν πρέπει να αγγίξει τη βάση της εφαρμογής.
    os.environ.setdefault("MONGO_URL", "mongomock://sandbox-check")
    os.environ.setdefault("JWT_SECRET", "sandbox-check-χωρίς-σημασία-για-τη-δοκιμή")

    import mydata_client as mc

    # Τα διαπιστευτήρια περνούν ως έχουν· η `_headers` αποκρυπτογραφεί μόνο ό,τι είναι
    # κρυπτογραφημένο, οπότε μια καθαρή τιμή περνά αυτούσια.
    settings = {
        "afm": args.afm,
        "aade_user_id": user_id,
        "aade_subscription_key": key,
        "aade_environment": "sandbox",
    }
    if settings["aade_environment"] == "production":     # άμυνα σε βάθος
        _fail("Το script δεν τρέχει σε περιβάλλον παραγωγής.")

    urls = mc._urls(settings)
    print(f"Περιβάλλον : ΔΟΚΙΜΑΣΤΙΚΟ ({urls['send']})")
    print(f"Εκδότης    : {args.afm}   Λήπτης: {args.counterpart_afm}")

    invoice = {
        "number": "ΤΠΥ-SANDBOX-0001",
        "date": __import__("datetime").date.today().isoformat(),
        "line_items": [{"description": "Δοκιμαστική παροχή νομικών υπηρεσιών",
                        "quantity": 1, "unit_price": 100.0, "vat_rate": 24}],
        "payment_method": "Τραπεζική κατάθεση",
    }
    client = {"name": "ΔΟΚΙΜΑΣΤΙΚΟΣ ΛΗΠΤΗΣ", "afm": args.counterpart_afm, "client_type": "company"}

    xml = mc.build_invoices_doc_xml(invoice, client, settings)
    print("\n--- XML προς αποστολή ---")
    print(xml[:900] + ("…" if len(xml) > 900 else ""))

    print("\n--- 1) SendInvoices ---")
    try:
        result = await mc.send_invoice(settings, invoice, client)
    except mc.MyDataError as e:
        _fail(f"Η ΑΑΔΕ απέρριψε τη διαβίβαση: {e}")
    print(f"    ✓ Έγινε δεκτό. ΜΑΡΚ: {result.get('mark')}")
    for k, v in result.items():
        if k != "mark" and v:
            print(f"      {k}: {v}")

    mark = result.get("mark")
    if not mark:
        _fail("Δεν επιστράφηκε ΜΑΡΚ — ελέγξτε την απόκριση παραπάνω.")

    if args.keep:
        print("\n(παραλείπεται η ακύρωση με --keep)")
        return

    print("\n--- 2) CancelInvoice ---")
    try:
        cancel = await mc.cancel_invoice(settings, mark)
    except mc.MyDataError as e:
        _fail(f"Η ακύρωση απέτυχε: {e}   (το ΜΑΡΚ {mark} παραμένει ενεργό)")
    print(f"    ✓ Ακυρώθηκε. ΜΑΡΚ ακύρωσης: {cancel.get('cancellation_mark') or cancel}")
    print("\n✓ Ο κύκλος αποστολής και ακύρωσης ολοκληρώθηκε.\n")


if __name__ == "__main__":
    asyncio.run(main())
