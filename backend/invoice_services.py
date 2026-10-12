"""Reusable invoice workflows shared by the HTTP routes and the Autopilot engine.

Every function here works on raw Mongo documents and never depends on a request,
so the same code path runs whether a human clicked a button or the scheduler
decided it was time.
"""
import os
import re
import math
import asyncio
import logging
from datetime import datetime, timezone, date as _date

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from db import db
from http_utils import configured_origin
from mydata_client import (
    mydata_ready, send_invoice, cancel_invoice, MyDataError, mydata_mock_enabled,
    MYDATA_NOT_CONFIGURED_MSG, resolve_invoice_type, withholding_for,
    TYPE_SERVICES_B2B, TYPE_RECEIPT_B2C, TYPE_CREDIT_NOTE, RETAIL_TYPES, CREDIT_TYPES,
)
from tax_utils import athens_today
from email_utils import (
    smtp_ready, send_email_with_pdf, invoice_email_html, invoice_email_text,
    reminder_email_html, reminder_email_text,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def firm_query(firm_id: str, extra: dict | None = None) -> dict:
    return {**(extra or {}), "firm_id": firm_id}


async def load_client(firm_id: str, invoice: dict) -> dict | None:
    if not invoice.get("client_id"):
        return None
    try:
        return await db.clients.find_one(firm_query(firm_id, {"_id": ObjectId(invoice["client_id"])}))
    except Exception:
        return None


async def payable_amount(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> tuple[float, float]:
    """(πληρωτέο, παρακρατούμενος φόρος). Ο πελάτης-επιχείρηση πληρώνει μείον την παρακράτηση."""
    total = float(invoice.get("total", 0))
    if not settings.get("withholding_enabled", False) and invoice.get("withholding_amount") is None:
        return round(total, 2), 0.0
    client = client if client is not None else await load_client(firm_id, invoice)
    invoice_type = resolve_invoice_type(invoice, client or {})
    withheld = withholding_for(invoice, settings, invoice_type, float(invoice.get("subtotal", 0)))
    return round(total - withheld, 2), withheld


async def outstanding_amount(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> tuple[float, float]:
    """(οφειλόμενο, παρακράτηση): το πληρωτέο μείον ό,τι έχει ήδη εξοφληθεί από πελατολογικό λογαριασμό.

    Είναι το ποσό που πρέπει να φτάσει από τράπεζα ή κάρτα για να εξοφληθεί το τιμολόγιο.
    """
    payable, withheld = await payable_amount(firm_id, invoice, settings, client)
    applied = round(float(invoice.get("trust_applied") or 0), 2)
    return round(max(payable - applied, 0.0), 2), withheld


def check_explicit_withholding(invoice: dict, invoice_type: str) -> None:
    """Η ρητή παρακράτηση του τιμολογίου (`withholding_amount`) έχει νόημα μόνο σε ΤΠΥ και μέσα σε όρια.

    Την παρακράτηση φόρου αμοιβών την κρατά ο πελάτης-επιχείρηση. Στην ΑΠΥ λιανικής (ιδιώτης) και στο πιστωτικό δεν
    εφαρμόζεται — όπως και στον αυτόματο υπολογισμό (`withholding_for`), που τα εξαιρεί. Μια ρητή τιμή υπερισχύει
    και «παγώνει» στην έκδοση, οπότε χωρίς αυτόν τον έλεγχο μια απόδειξη 500 € προς ιδιώτη με `withholding_amount: 100`
    εκδιδόταν με «πληρωτέο 520 €» και διαβιβαζόταν στην ΑΑΔΕ με παρακράτηση που δεν υπάρχει. Στο ΤΠΥ η τιμή
    πρέπει να είναι από 0 έως την καθαρή αξία. Το 0 επιτρέπεται παντού («καμία παρακράτηση»).
    """
    amount = invoice.get("withholding_amount")
    if amount is None:
        return
    amount = float(amount)
    if not math.isfinite(amount):
        # Το NaN δεν «σπάει» κανέναν από τους ελέγχους ορίων παρακάτω (κάθε σύγκριση είναι ψευδής) και θα διέδιδε NaN
        # στο πληρωτέο και στο ανείσπρακτο ολόκληρου του γραφείου.
        raise InvoiceWorkflowError("Η παρακράτηση πρέπει να είναι έγκυρος αριθμός.")
    if amount == 0:
        return
    if invoice_type in CREDIT_TYPES:
        raise InvoiceWorkflowError(
            f"Το πιστωτικό τιμολόγιο δεν φέρει παρακράτηση ({_eur(amount)}). Η παρακράτηση του αρχικού τιμολογίου "
            "ρυθμίζεται με τον λογιστή του γραφείου — δεν γράφεται στο πιστωτικό.")
    if invoice_type in RETAIL_TYPES:
        raise InvoiceWorkflowError(
            f"Η παρακράτηση ({_eur(amount)}) δεν εφαρμόζεται σε απόδειξη λιανικής (ΑΠΥ): την κρατά μόνο πελάτης-επιχείρηση. "
            "Αφαιρέστε την παρακράτηση ή εκδώστε τιμολόγιο παροχής υπηρεσιών (ΤΠΥ) προς πελάτη με ΑΦΜ.")
    net = float(invoice.get("subtotal") or 0)
    if amount < 0 or amount > net:
        raise InvoiceWorkflowError(
            f"Η παρακράτηση ({_eur(amount)}) πρέπει να είναι από 0 έως την καθαρή αξία ({_eur(net)}).")


def _needs_client(invoice: dict, settings: dict) -> bool:
    """Χρειάζεται ο πελάτης για να βρεθεί η παρακράτηση του τιμολογίου;

    Μόνο όταν η παρακράτηση δεν είναι παγωμένη, είναι ενεργή στις Ρυθμίσεις και το είδος (ΤΠΥ / ΑΠΥ) δεν
    προκύπτει από το ίδιο το έγγραφο — δηλαδή για τιμολόγια που εκδόθηκαν πριν αποθηκεύονται αυτά τα πεδία.
    """
    if invoice.get("withholding_amount") is not None or not settings.get("withholding_enabled", False):
        return False
    if (invoice.get("invoice_type") or "").strip():
        return False
    return not (invoice.get("credit_note_for") or invoice.get("correlated_mark"))


async def invoice_balances(firm_id: str, invoices: list[dict], settings: dict,
                           known_clients: dict[str, dict] | None = None) -> dict[str, tuple[float, float]]:
    """{id: (πληρωτέο, οφειλόμενο)} για κάθε παραστατικό της λίστας — **ο μοναδικός ορισμός** του «ανείσπρακτου».

    Το dashboard, η εβδομαδιαία ενημέρωση, οι αναλύσεις, ο Copilot, η λίστα τιμολογίων και η πύλη του πελάτη
    δείχνουν όλα τον ίδιο αριθμό, γιατί βγαίνει από εδώ:

    * εκδοθέν τιμολόγιο: το πληρωτέο (σύνολο − παρακράτηση) μείον ό,τι εξοφλήθηκε από πελατολογικό, όχι κάτω από 0·
    * εκδοθέν πιστωτικό (αρνητικό σύνολο): το αρνητικό πληρωτέο του — συμψηφίζεται με τα υπόλοιπα του ίδιου
      πελάτη (βλ. `open_total`)· το `outstanding_amount` το έκανε «0» και το πιστωτικό χανόταν·
    * ό,τι δεν είναι εκδοθέν (πρόχειρο, εξοφλημένο, ακυρωμένο): οφειλόμενο 0.

    Χωρίς ένα ερώτημα στη βάση ανά τιμολόγιο: οι πελάτες που χρειάζονται (μόνο για παλιά τιμολόγια χωρίς παγωμένη
    παρακράτηση) διαβάζονται όλοι μαζί, και το `known_clients` {client_id: έγγραφο} γλιτώνει και αυτό το ερώτημα.
    """
    clients: dict[str, dict] = dict(known_clients or {})
    wanted: list[ObjectId] = []
    for inv in invoices:
        cid = str(inv.get("client_id") or "")
        if cid and cid not in clients and _needs_client(inv, settings):
            try:
                oid = ObjectId(cid)
            except Exception:  # noqa: BLE001 — χαλασμένο client_id: το είδος βγαίνει ως λιανική, όπως στη διαβίβαση
                clients[cid] = {}
                continue
            if oid not in wanted:
                wanted.append(oid)
    if wanted:
        async for c in db.clients.find(firm_query(firm_id, {"_id": {"$in": wanted}})):
            clients[str(c["_id"])] = c

    balances: dict[str, tuple[float, float]] = {}
    for inv in invoices:
        client = clients.get(str(inv.get("client_id") or ""), {})
        payable, _withheld = await payable_amount(firm_id, inv, settings, client)
        if inv.get("status") != "issued":
            owed = 0.0
        elif float(inv.get("total") or 0) < 0:
            owed = payable
        else:
            owed, _withheld = await outstanding_amount(firm_id, inv, settings, client)
        balances[str(inv["_id"])] = (payable, owed)
    return balances


def open_total(invoices: list[dict], balances: dict[str, tuple[float, float]]) -> float:
    """Το ποσό που απομένει να εισπραχθεί από τα εκδοθέντα: άθροισμα ανά πελάτη, με κάτω όριο το μηδέν.

    Το πιστωτικό συμψηφίζεται με τα υπόλοιπα του ΙΔΙΟΥ πελάτη. Αν τον ξεπερνά (πιστωτικό για εξοφλημένο
    τιμολόγιο), το πλεόνασμα δεν είναι «οφειλή» και δεν πρέπει να μειώνει ό,τι χρωστά άλλος πελάτης.
    """
    per_client: dict[str, float] = {}
    for inv in invoices:
        if inv.get("status") != "issued":
            continue
        key = str(inv.get("client_id") or "")
        per_client[key] = per_client.get(key, 0.0) + balances[str(inv["_id"])][1]
    return round(sum(max(round(v, 2), 0.0) for v in per_client.values()), 2)


async def effective_invoice(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> dict:
    """Το παραστατικό με την παρακράτηση που ισχύει, έτοιμο για PDF και email.

    Τα εκδοθέντα έχουν την παρακράτηση παγωμένη (`withholding_amount`). Για παλαιότερα, που
    εκδόθηκαν πριν αποθηκεύεται, την υπολογίζουμε όπως θα την υπολόγιζε η διαβίβαση.
    """
    if invoice.get("withholding_amount") is not None:
        return invoice
    _payable, withheld = await payable_amount(firm_id, invoice, settings, client)
    return {**invoice, "withholding_amount": withheld}


def safe_pdf_name(invoice: dict) -> str:
    """Όνομα συνημμένου χωρίς ελληνικούς χαρακτήρες (πολλοί mail clients τους παραμορφώνουν)."""
    name = invoice.get("number") or "invoice"
    for greek, latin in (("ΤΠΥ", "TPY"), ("ΑΠΥ", "APY"), ("ΠΙΣ", "PIS")):
        name = name.replace(greek, latin)
    return name + ".pdf"


# ----------------------------------------------------------- αρίθμηση -----
#
# Κάθε είδος παραστατικού έχει δική του σειρά και δική του αρίθμηση ανά έτος. Ο αριθμός
# δίνεται **μόνο όταν εκδίδεται** το παραστατικό: ένα πρόχειρο δεν είναι φορολογικό
# παραστατικό, και αν έπαιρνε αριθμό στη δημιουργία, κάθε διαγραφή πρόχειρου θα άφηνε
# κενό στη σειρά των εκδοθέντων — ακριβώς αυτό που η αδιάσπαστη αρίθμηση απαγορεύει.

SERIES_PREFIX = {
    TYPE_SERVICES_B2B: "ΤΠΥ",
    TYPE_RECEIPT_B2C: "ΑΠΥ",
    TYPE_CREDIT_NOTE: "ΠΙΣ",
}
DEFAULT_SERIES_PREFIX = "ΤΠΥ"


def series_prefix(invoice_type: str) -> str:
    return SERIES_PREFIX.get(invoice_type, DEFAULT_SERIES_PREFIX)


def _counter_id(firm_id: str, prefix: str, year: int) -> str:
    # Η σειρά ΤΠΥ κρατά το αρχικό όνομα μετρητή ώστε οι υπάρχουσες εγκαταστάσεις να συνεχίσουν
    # από εκεί που έμειναν.
    return f"invoice-{firm_id}-{year}" if prefix == DEFAULT_SERIES_PREFIX else f"invoice-{prefix}-{firm_id}-{year}"


async def next_invoice_number(firm_id: str, prefix: str = DEFAULT_SERIES_PREFIX, year: int | None = None, *,
                              date_iso: str | None = None, floor: int = 0) -> str:
    """Επόμενος αριθμός της σειράς. Με `date_iso` ελέγχεται ατομικά ότι η ημερομηνία δεν είναι προγενέστερη
    της τελευταίας που δόθηκε στη σειρά (`SeriesDateOutOfOrder`)· με `floor` ο αριθμός είναι πάντα μεγαλύτερος
    από τον μεγαλύτερο υπάρχοντα. Βλ. numbering.next_in_series."""
    from numbering import next_in_series
    year = year or athens_today().year
    return await next_in_series(db.invoices, _counter_id(firm_id, prefix, year), firm_id, prefix, year,
                                date_iso=date_iso, floor=floor)


async def release_invoice_number(firm_id: str, number: str) -> None:
    """Επιστρέφει αριθμό που δεν χρησιμοποιήθηκε — μόνο αν δεν έχει δοθεί κάποιος μεταγενέστερος."""
    try:
        prefix, year, seq = number.rsplit("-", 2)
        await db.counters.update_one({"_id": _counter_id(firm_id, prefix, int(year)), "seq": int(seq)},
                                     {"$inc": {"seq": -1}})
    except Exception:  # noqa: BLE001 — best effort, ένα κενό είναι προτιμότερο από μια εξαίρεση
        logger.warning("Δεν επιστράφηκε ο αριθμός %s", number)


INVOICE_NUMBER_INDEX = "uniq_firm_invoice_number"
NUMBERED_STATUSES = ["issued", "paid", "cancelled"]


async def ensure_invoice_number_index(collection=None) -> None:
    """Δύο παραστατικά του ίδιου γραφείου δεν μπορούν να έχουν τον ίδιο αριθμό — ούτε αν κάποιος κώδικας ή
    κάποια επέμβαση στη βάση το επιχειρήσει.

    Ο κώδικας της έκδοσης δίνει αριθμούς από ατομικό μετρητή, αλλά ο μετρητής δεν είναι η αλήθεια: μπορεί
    να γυρίσει πίσω με επαναφορά αντιγράφου, να αλλαχτεί χειροκίνητα ή να χαθεί. Το index είναι η δεύτερη
    γραμμή άμυνας: η βάση αρνείται τον διπλό αριθμό και η έκδοση προχωρά στον επόμενο ελεύθερο.

    Καλύπτει μόνο εκδοθέντα, εξοφλημένα και ακυρωμένα με μη κενό αριθμό: τα πρόχειρα δεν είναι φορολογικά
    παραστατικά (έχουν κενό αριθμό, ή παλιό αριθμό από παλαιότερη έκδοση της εφαρμογής). Το φίλτρο με `$in`
    απαιτεί MongoDB 6.0 ή νεότερη (η εγκατάσταση χρησιμοποιεί την 7).

    Δεν ρίχνει την εκκίνηση: αν υπάρχουν ήδη διπλοί αριθμοί η δημιουργία αποτυγχάνει, καταγράφεται σφάλμα, και
    ο έλεγχος αρίθμησης (`GET /api/invoices/numbering-check`, εργασία Autopilot `numbering_check`) δείχνει
    ποιοι είναι. Το `collection` δίνεται μόνο στα τεστ.
    """
    invoices = collection if collection is not None else db.invoices
    try:
        await invoices.create_index(
            [("firm_id", 1), ("number", 1)],
            name=INVOICE_NUMBER_INDEX,
            unique=True,
            partialFilterExpression={"status": {"$in": NUMBERED_STATUSES}, "number": {"$gt": ""}},
        )
    except Exception as e:  # noqa: BLE001 — η εκκίνηση της εφαρμογής δεν πρέπει να εξαρτάται από αυτό
        logger.error("invoices: αποτυχία δημιουργίας του unique index (firm_id, number) — πιθανόν υπάρχουν ήδη "
                     "διπλοί αριθμοί ή η MongoDB είναι παλαιότερη της 6.0· τρέξτε τον έλεγχο "
                     "GET /api/invoices/numbering-check: %s", e)


GAP_LIST_LIMIT = 50          # πόσα κενά αναγράφονται· το σύνολο μετριέται πάντα ολόκληρο
# Έως 9 ψηφία: ένας αριθμός με περισσότερα (εισαγωγή, επαναφορά, χειροκίνητη επέμβαση) δεν είναι αύξων αριθμός σειράς·
# αναφέρεται ως «εκτός μορφής» αντί να παράγει εκατομμύρια «κενά» (ή να ξεπεράσει το όριο μετατροπής του int).
_NUMBER_PATTERN = re.compile(r"^(?P<prefix>[^-\s]+)-(?P<year>[0-9]{4})-(?P<seq>[0-9]{1,9})$")


def _parse_number(number: str):
    """('ΤΠΥ', 2026, 7) από το «ΤΠΥ-2026-0007»· None αν ο αριθμός δεν έχει τη μορφή ΠΡΟΘΕΜΑ-ΕΤΟΣ-ΑΥΞΩΝ."""
    m = _NUMBER_PATTERN.match(number or "")
    return (m.group("prefix"), int(m.group("year")), int(m.group("seq"))) if m else None


async def numbering_report(firm_id: str, year: int | None = None) -> dict:
    """Ελέγχει τη σειρά των εκδοθέντων παραστατικών του γραφείου: κενά, διπλότυπα, ημερομηνίες εκτός σειράς,
    μετρητή εκτός συγχρονισμού, αριθμούς εκτός μορφής.

    Η έκδοση ελέγχει τη σειρά μία φορά, τη στιγμή που εκδίδει. Εδώ ελέγχεται το αποτέλεσμα όπως είναι
    σήμερα στη βάση — αυτό που θα δει ο ελεγκτής. Μετράνε μόνο εκδοθέντα, εξοφλημένα και ακυρωμένα: ένα
    ακυρωμένο παραστατικό κρατά τον αριθμό του, άρα δεν αφήνει κενό. Τα πρόχειρα δεν ανήκουν στη σειρά.

    Δεν διορθώνει τίποτα: ό,τι αφορά φορολογικά παραστατικά το αποφασίζει ο λογιστής.
    """
    from numbering import as_int
    query = firm_query(firm_id, {"status": {"$in": NUMBERED_STATUSES}, "number": {"$gt": ""}})
    groups: dict[tuple[str, int], list] = {}
    unparseable = []
    async for d in db.invoices.find(query, {"number": 1, "date": 1}):
        parsed = _parse_number(d["number"])
        if parsed is None:
            unparseable.append({"number": d["number"], "id": str(d["_id"])})
            continue
        prefix, y, seq = parsed
        if year is not None and y != year:
            continue
        groups.setdefault((prefix, y), []).append((seq, d["number"], (d.get("date") or "")[:10], str(d["_id"])))

    series = []
    for (prefix, y), items in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        items.sort(key=lambda t: (t[0], t[1], t[3]))
        seqs = sorted({t[0] for t in items})
        first, last = seqs[0], seqs[-1]

        # Τα κενά μετριούνται από τα διαστήματα ανάμεσα σε διαδοχικούς υπάρχοντες αριθμούς· ο κατάλογος κρατά μόνο τα
        # πρώτα GAP_LIST_LIMIT. Ένας αριθμός με πολλά ψηφία (εισαγωγή, επαναφορά, χειροκίνητη επέμβαση) δεν πρέπει
        # να χτίζει λίστα εκατομμυρίων στον βρόχο συμβάντων.
        gaps: list[int] = []
        gaps_total = 0
        for before_seq, after_seq in zip(seqs, seqs[1:]):
            missing = after_seq - before_seq - 1
            if missing > 0:
                gaps_total += missing
                room = GAP_LIST_LIMIT - len(gaps)
                if room > 0:
                    gaps.extend(range(before_seq + 1, before_seq + 1 + min(missing, room)))

        # Ο ίδιος αύξων αριθμός με διαφορετική γραφή (0007 / 007) είναι ο ίδιος αριθμός: το μοναδικό ευρετήριο
        # συγκρίνει κείμενα και θα δεχόταν και τα δύο.
        by_seq: dict[int, list] = {}
        for seq, number, _date_iso, doc_id in items:
            by_seq.setdefault(seq, []).append((number, doc_id))
        duplicates = [{"number": sorted({n for n, _ in docs})[0], "count": len(docs), "ids": [i for _, i in docs]}
                      for docs in by_seq.values() if len(docs) > 1]

        date_order = []
        ordered = [t for t in items if t[2]]
        for before, after in zip(ordered, ordered[1:]):
            if after[2] < before[2]:
                date_order.append({"number": after[1], "date": after[2],
                                   "previous_number": before[1], "previous_date": before[2]})

        counter_doc = await db.counters.find_one({"_id": _counter_id(firm_id, prefix, y)})
        raw_seq = counter_doc.get("seq") if counter_doc else None
        seq_now = as_int(raw_seq) if raw_seq is not None else None        # ο μετρητής δεν πρέπει να ρίχνει την αναφορά
        if seq_now is None:
            counter = {"seq": None, "state": "missing", "ahead_by": 0}
        elif seq_now < last:
            counter = {"seq": seq_now, "state": "behind", "ahead_by": 0}
        elif seq_now > last:
            counter = {"seq": seq_now, "state": "ahead", "ahead_by": seq_now - last}
            gaps_total += seq_now - last                    # αριθμοί που δόθηκαν και δεν χρησιμοποιήθηκαν ποτέ
            room = GAP_LIST_LIMIT - len(gaps)
            if room > 0:
                gaps.extend(range(last + 1, last + 1 + min(seq_now - last, room)))
        else:
            counter = {"seq": seq_now, "state": "ok", "ahead_by": 0}

        series.append({
            "series": f"{prefix}-{y}", "prefix": prefix, "year": y,
            "count": len(items), "first": first, "last": last,
            "gaps": gaps, "gaps_total": gaps_total,
            "duplicates": duplicates, "date_order": date_order, "counter": counter,
            "ok": not (gaps_total or duplicates or date_order or counter["state"] == "behind"),
        })

    unnumbered = await db.invoices.count_documents(firm_query(firm_id, {
        "status": {"$in": NUMBERED_STATUSES},
        "$or": [{"number": {"$exists": False}}, {"number": None}, {"number": ""}]}))
    numbered_drafts = await db.invoices.count_documents(firm_query(firm_id, {"status": "draft", "number": {"$gt": ""}}))
    try:
        unique_index = INVOICE_NUMBER_INDEX in await db.invoices.index_information()
    except Exception:  # noqa: BLE001 — ο έλεγχος σειράς δεν πρέπει να αποτυγχάνει επειδή δεν διαβάστηκαν οι δείκτες
        unique_index = False

    problems = {
        "gaps": sum(s["gaps_total"] for s in series),
        "duplicates": sum(len(s["duplicates"]) for s in series),
        "date_order": sum(len(s["date_order"]) for s in series),
        "counter_behind": sum(1 for s in series if s["counter"]["state"] == "behind"),
        "unparseable": len(unparseable),
        "unnumbered": unnumbered,
    }
    return {
        "ok": not any(problems.values()),
        "checked": sum(s["count"] for s in series) + len(unparseable),
        "unique_index": unique_index,
        "series": series,
        "unparseable": unparseable,
        "numbered_drafts": numbered_drafts,
        "problems": problems,
    }


# ---------------------------------------------------------------- myDATA ----

async def transmit_to_mydata(firm_id: str, invoice: dict, settings: dict) -> dict:
    """Send one invoice to AADE. Returns the ``$set`` update applied to the invoice.

    * live credentials  → real transmission (synced / failed)
    * ``MYDATA_MOCK=1`` → simulated MARK (explicit demo mode)
    * otherwise         → ``pending`` so Autopilot retries once credentials exist
    """
    import uuid
    now = _now()
    if mydata_ready(settings):
        client = await load_client(firm_id, invoice)
        try:
            result = await send_invoice(settings, invoice, client or {})
            update = {"mydata_status": "synced", "mydata_mark": result["mark"], "mydata_uid": result["uid"],
                      "mydata_error": None, "mydata_synced_at": now}
            inc = None
        except MyDataError as e:
            update = {"mydata_status": "failed", "mydata_error": str(e), "mydata_last_attempt_at": now}
            inc = {"mydata_attempts": 1}
    elif mydata_mock_enabled():
        update = {"mydata_status": "synced", "mydata_mark": f"MOCK-{uuid.uuid4().hex[:16].upper()}",
                  "mydata_uid": f"UID-{uuid.uuid4().hex[:12].upper()}", "mydata_error": None, "mydata_synced_at": now}
        inc = None
    else:
        update = {"mydata_status": "pending", "mydata_error": MYDATA_NOT_CONFIGURED_MSG}
        inc = None
    ops = {"$set": update}
    if inc:
        ops["$inc"] = inc
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"]}), ops)
    return update


# ------------------------------------------------------------------ email ----

class InvoiceEmailError(Exception):
    pass


async def email_invoice(firm_id: str, invoice: dict, settings: dict, client: dict | None = None) -> dict:
    """Send the invoice PDF to the client. Raises InvoiceEmailError with a Greek message."""
    from pdf_utils import build_invoice_pdf
    client = client or await load_client(firm_id, invoice)
    if not client or not client.get("email"):
        raise InvoiceEmailError("Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    if not smtp_ready(settings):
        raise InvoiceEmailError("Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")
    invoice = await effective_invoice(firm_id, invoice, settings, client)
    pdf_bytes = build_invoice_pdf(invoice, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Τιμολόγιο {invoice.get('number')} — {company}"
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            invoice_email_text(invoice, settings), invoice_email_html(invoice, settings),
            pdf_bytes=pdf_bytes, pdf_filename=safe_pdf_name(invoice),
        )
    except Exception as e:  # SMTPAuthenticationError, network, …
        raise InvoiceEmailError(f"Αποτυχία αποστολής email: {e}")
    update = {"email_sent_at": _now(), "email_sent_to": client["email"]}
    await db.invoices.update_one(firm_query(firm_id, {"_id": invoice["_id"]}), {"$set": update})
    return update


async def build_payment_link(invoice: dict, settings: dict) -> str:
    """Create a Stripe Checkout link for the invoice, or '' when Stripe is not configured.

    Χρεώνει το **οφειλόμενο** — πληρωτέο (μετά την παρακράτηση) μείον ό,τι έχει ήδη εξοφληθεί από
    πελατολογικό λογαριασμό — δηλαδή το ίδιο ποσό που αναγράφει το email υπενθύμισης και που
    χρεώνει το checkout της πύλης. Ένα μικρότερο ποσό στο email και ένα μεγαλύτερο στην κάρτα
    σημαίνει ότι ο πελάτης πληρώνει δύο φορές το ίδιο.
    """
    try:
        import stripe as _stripe
        api_key = os.environ.get("STRIPE_SECRET_KEY") or ""
        if not api_key or api_key.endswith("_emergent"):
            return ""
        _stripe.api_key = api_key
        if invoice.get("credit_note_id"):
            return ""                       # υπάρχει πιστωτικό: το ποσό πληρωμής δεν είναι πια το οφειλόμενο του τιμολογίου
        total, _withheld = await outstanding_amount(invoice.get("firm_id", ""), invoice, settings)
        if total <= 0:
            return ""
        # Η διεύθυνση όπου επιστρέφει ο πελάτης αφού πληρώσει. Παλιότερα, χωρίς ρύθμιση, έπεφτε στο «https://example.com» και
        # ο πελάτης πλήρωνε και μετά οδηγούνταν σε ξένο ιστότοπο· τώρα δεν δημιουργείται καθόλου σύνδεσμος (η υπενθύμιση
        # στέλνεται με IBAN), και το γραφείο ενημερώνεται από το αρχείο καταγραφής.
        origin = configured_origin(settings.get("public_url"), os.environ.get("PUBLIC_URL"), os.environ.get("PREVIEW_ENDPOINT"))
        if not origin:
            logger.warning("Δεν δημιουργήθηκε σύνδεσμος πληρωμής: δεν έχει οριστεί έγκυρη διεύθυνση εφαρμογής "
                           "(public_url στις Ρυθμίσεις ή PUBLIC_URL)")
            return ""
        invoice_id = str(invoice["_id"])
        session = _stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {
                        "name": f"Τιμολόγιο {invoice.get('number', '')}",
                        "description": f"Νομικές υπηρεσίες προς {invoice.get('client_name', '')}",
                    },
                    "unit_amount": int(round(total * 100)),
                },
                "quantity": 1,
            }],
            success_url=origin + "/payment/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=origin + "/payment/cancel",
            metadata={"invoice_id": invoice_id, "invoice_number": invoice.get("number", "")},
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 60 * 60 * 24 * 7,
        )
        from models import PaymentTransaction
        tx = PaymentTransaction(
            firm_id=invoice.get("firm_id", "legacy-default-firm"),
            session_id=session.id, invoice_id=invoice_id, invoice_number=invoice.get("number", ""),
            client_id=invoice.get("client_id", ""), client_name=invoice.get("client_name", ""),
            amount=total, currency="eur",
        )
        await db.payment_transactions.insert_one(tx.to_mongo())
        return session.url
    except Exception as e:
        logger.warning("Stripe payment link failed: %s", e)
        return ""


def days_since_invoice(invoice: dict, today: _date | None = None) -> int:
    today = today or athens_today()
    try:
        issued_date = _date.fromisoformat((invoice.get("date") or "")[:10])
        return max(0, (today - issued_date).days)
    except Exception:
        return 0


async def send_reminder(firm_id: str, invoice: dict, settings: dict, client: dict | None = None,
                        with_payment_link: bool = True) -> dict:
    """Send a payment reminder for an issued, unpaid invoice."""
    from pdf_utils import build_invoice_pdf
    if invoice.get("status") == "paid":
        raise InvoiceEmailError("Το τιμολόγιο είναι ήδη εξοφλημένο — δεν χρειάζεται υπενθύμιση.")
    if invoice.get("status") != "issued":
        raise InvoiceEmailError("Μόνο εκδοθέντα τιμολόγια μπορούν να λάβουν υπενθύμιση. Εκδώστε πρώτα το τιμολόγιο.")
    if invoice.get("credit_note_id"):
        # Η υπενθύμιση αναγράφει το οφειλόμενο του τιμολογίου· με πιστωτικό (ολικό ή μερικό) το ποσό αυτό δεν
        # ισχύει πια και ο πελάτης θα έβλεπε χρέωση που δεν οφείλει.
        raise InvoiceEmailError("Για το τιμολόγιο υπάρχει πιστωτικό — το ποσό που οφείλεται το ορίζει ο λογιστής· "
                                "δεν στέλνεται υπενθύμιση εξόφλησης.")
    client = client or await load_client(firm_id, invoice)
    if not client or not client.get("email"):
        raise InvoiceEmailError("Ο πελάτης δεν έχει καταχωρημένο email. Προσθέστε email στην καρτέλα του πελάτη.")
    if not smtp_ready(settings):
        raise InvoiceEmailError("Δεν έχουν ρυθμιστεί τα στοιχεία αποστολής email. Συμπληρώστε τα στις Ρυθμίσεις → Αποστολή Email (SMTP).")

    days_overdue = days_since_invoice(invoice)
    reminder_count = int(invoice.get("reminder_count") or 0)
    payment_link = await build_payment_link(invoice, settings) if with_payment_link else ""
    invoice = await effective_invoice(firm_id, invoice, settings, client)
    pdf_bytes = build_invoice_pdf(invoice, client, settings)
    company = settings.get("company_name") or "Nomos One"
    subject = f"Υπενθύμιση εξόφλησης — Τιμολόγιο {invoice.get('number')} — {company}"
    try:
        await send_email_with_pdf(
            settings, client["email"], subject,
            reminder_email_text(invoice, settings, days_overdue, reminder_count, payment_link),
            reminder_email_html(invoice, settings, days_overdue, reminder_count, payment_link),
            pdf_bytes=pdf_bytes, pdf_filename=safe_pdf_name(invoice),
        )
    except Exception as e:
        raise InvoiceEmailError(f"Αποτυχία αποστολής υπενθύμισης: {e}")
    sent_at = _now()
    await db.invoices.update_one(
        firm_query(firm_id, {"_id": invoice["_id"]}),
        {"$set": {"reminder_sent_at": sent_at, "email_sent_to": client["email"]}, "$inc": {"reminder_count": 1}},
    )
    return {"reminder_sent_at": sent_at, "payment_link": payment_link}


async def mark_paid(firm_id: str, invoice_id, paid_via: str = "manual", *, ref: str | None = None) -> bool:
    """Idempotent: returns True only if the invoice transitioned issued → paid.

    `ref`: αναγνωριστικό της πληρωμής που έκλεισε το τιμολόγιο (π.χ. η συνεδρία Stripe), γραμμένο στην ίδια
    ατομική εγγραφή. Επιτρέπει να αναγνωριστεί αργότερα ότι ένα τιμολόγιο εξοφλήθηκε ΑΠΟ ΑΥΤΗ την πληρωμή.
    """
    oid = invoice_id if isinstance(invoice_id, ObjectId) else ObjectId(invoice_id)
    changes = {"status": "paid", "paid_at": _now(), "paid_via": paid_via}
    if ref:
        changes["paid_ref"] = ref
    result = await db.invoices.update_one(
        firm_query(firm_id, {"_id": oid, "status": "issued"}),
        {"$set": changes},
    )
    return result.modified_count > 0


# ------------------------------------------------ εξόφληση από τράπεζα ----

# Τα ποσά συγκρίνονται σε ακέραια λεπτά (βλ. to_cents): «ίδιο ποσό» σημαίνει ίδιο μέχρι το λεπτό.


def to_cents(value) -> int:
    """Ποσό σε ακέραια λεπτά. Οι συγκρίσεις ποσών γίνονται σε λεπτά, όχι σε float: 1240,01 − 1240,00 δίνει
    0,009999999999990905, δηλαδή «μικρότερο από 1 λεπτό» ενώ είναι ακριβώς ένα λεπτό διαφορά."""
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(value if value is not None else 0)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def deposit_cents(value) -> int:
    """Ποσό κατάθεσης σε λεπτά, με την ίδια κανονικοποίηση παντού (πρόταση αντιστοίχισης και εξόφληση).

    Πρώτα στρογγυλοποίηση στα 2 δεκαδικά, μετά μετατροπή σε λεπτά: αλλιώς μια γραμμή extrait με 3
    δεκαδικά (100,005) μπορούσε να φαίνεται «ακριβής» στην πρόταση και «διαφορετική» στην εξόφληση.
    """
    return to_cents(round(float(value or 0), 2))


def _eur(v) -> str:
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class SettlementRefused(Exception):
    """Η εξόφληση τιμολογίου από κατάθεση δεν επιτρέπεται (το μήνυμα απευθύνεται στον χρήστη)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


async def close_tx_notifications(firm_id: str, tx_id, resolution: str, by: str = "system") -> int:
    """Κλείνει τις ανοιχτές ειδοποιήσεις (reconcile_review / reconcile_conflict) μιας τραπεζικής κίνησης.

    Καλείται όταν η κατάσταση της κίνησης αλλάζει με άλλον τρόπο (αγνόηση, χειροκίνητη συμφωνία,
    αποδέσμευση από το Autopilot): αλλιώς η παλιά κάρτα έμενε ανοιχτή και το «Ναι, εξοφλήθηκε» πάνω της
    δεν είχε πια νόημα.
    """
    result = await db.notifications.update_many(
        firm_query(firm_id, {"entity": "bank_tx", "entity_id": str(tx_id), "resolved_at": None,
                             "type": {"$in": ["reconcile_review", "reconcile_conflict"]}}),
        {"$set": {"resolved_at": _now(), "resolved_by": by, "resolution": resolution, "read": True}})
    return result.modified_count


async def flag_unchecked_bank_suggestions() -> int:
    """Παλιές, μη επιβεβαιωμένες προτάσεις τράπεζας δεν έχουν περάσει τον έλεγχο μονοσημαντότητας.

    Οι κινήσεις που προτάθηκαν πριν από τον γύρο 4 δεν έχουν το πεδίο `match_ambiguous`, δηλαδή
    δεν ξέρουμε αν υπήρχε και δεύτερο τιμολόγιο του ίδιου πελάτη με το ίδιο ποσό. Σημαίνονται ως
    «όχι μονοσήμαντες»: δεν εξοφλούνται αυτόματα ούτε μαζικά — τις επιβεβαιώνει ένας άνθρωπος.
    Idempotent· δεν αγγίζει επιβεβαιωμένες κινήσεις ούτε νέες προτάσεις (που φέρουν το πεδίο).

    Τρέχει στην εκκίνηση του API **και** του worker: ο worker μπορεί να ξεκινήσει πρώτος, και ο
    κύκλος συμφωνίας δεν πρέπει ποτέ να δει παλιά πρόταση ως «μονοσήμαντη». Επιπλέον όλοι οι
    αναγνώστες του πεδίου θεωρούν το απόν πεδίο ως «αμφίβολη» (`tx.get("match_ambiguous", True)`),
    ώστε να μη χρειάζεται να προλάβει η σήμανση.
    """
    try:
        result = await db.bank_transactions.update_many(
            {"match_status": "auto_matched", "match_ambiguous": {"$exists": False}},
            {"$set": {"match_ambiguous": True}})
        if result.modified_count:
            logger.warning("bank_transactions: %d παλιές προτάσεις σημάνθηκαν για ατομικό έλεγχο", result.modified_count)
        return result.modified_count
    except Exception as e:  # noqa: BLE001 — η εκκίνηση δεν πρέπει να εξαρτάται από αυτό
        logger.error("bank_transactions: αποτυχία σήμανσης παλιών προτάσεων: %s", e)
        return 0


async def _claimed_by_other_deposit(firm_id: str, invoice_id: str, tx_id) -> bool:
    """Έχει ήδη εξοφληθεί αυτό το τιμολόγιο από **άλλη** τραπεζική κίνηση;"""
    other = await db.bank_transactions.find_one(firm_query(firm_id, {
        "matched_invoice_id": invoice_id,
        "match_status": {"$in": ["auto_confirmed", "manual_matched"]},
        "_id": {"$ne": tx_id},
    }))
    return other is not None


async def apply_bank_payment(firm_id: str, tx: dict, invoice_id, *, settings: dict, paid_via: str,
                             status_on_success: str, accept_difference: bool = False,
                             dry_run: bool = False) -> dict:
    """Συνδέει μια κατάθεση με ένα τιμολόγιο και το εξοφλεί — με όλους τους ελέγχους, σε ένα σημείο.

    Την καλούν η αυτόματη συμφωνία, η μαζική και η χειροκίνητη επιβεβαίωση, και το inbox. Κανόνες:

    * η κίνηση πρέπει να είναι είσπραξη και να μην έχει ήδη συμφωνηθεί (μία κατάθεση ↔ ένα τιμολόγιο)·
    * το τιμολόγιο πρέπει να είναι ανοιχτό· αν είναι ήδη εξοφλημένο, η κατάθεση συνδέεται μόνο όταν
      το είχε σημειώσει ο χρήστης με το χέρι και καμία άλλη κίνηση δεν το έχει εξοφλήσει — αλλιώς
      πρόκειται για διπλοπληρωμή ή για πληρωμή από άλλο κανάλι (Stripe, πελατολογικός) και ένας
      άνθρωπος πρέπει να αποφασίσει·
    * το ποσό πρέπει να ισούται με το οφειλόμενο. Διαφορά δέχεται μόνο ρητή απόφαση ανθρώπου
      (`accept_difference`) και καταγράφεται στην κίνηση.

    Επιστρέφει {"status": "paid" | "linked" | "refused", "reason", "message", ...}. Με `dry_run`
    κάνει μόνο τους ελέγχους και επιστρέφει "ok" χωρίς να αλλάξει τίποτα.
    """
    def refuse(reason: str, message: str, **extra) -> dict:
        return {"status": "refused", "reason": reason, "message": message, **extra}

    amount = round(float(tx.get("amount") or 0), 2)
    if amount <= 0:
        return refuse("not_a_deposit", "Η κίνηση δεν είναι είσπραξη (το ποσό δεν είναι θετικό).")
    if tx.get("match_status") in ("auto_confirmed", "manual_matched"):
        return refuse("tx_settled", f"Η κίνηση έχει ήδη συμφωνηθεί με το τιμολόγιο {tx.get('matched_invoice_number') or ''}.".strip())

    try:
        oid = ObjectId(str(invoice_id))
    except Exception:
        return refuse("invoice_missing", "Το τιμολόγιο δεν βρέθηκε.")
    invoice = await db.invoices.find_one(firm_query(firm_id, {"_id": oid}))
    if not invoice:
        return refuse("invoice_missing", "Το τιμολόγιο δεν βρέθηκε.")
    number = invoice.get("number") or "(χωρίς αριθμό)"
    status = invoice.get("status")
    if status not in ("issued", "paid"):
        return refuse("invoice_not_open", f"Το τιμολόγιο {number} δεν είναι ανοιχτό (κατάσταση: {status}) και δεν μπορεί να εξοφληθεί.")
    if invoice.get("credit_note_id"):
        return refuse("has_credit_note", f"Για το τιμολόγιο {number} υπάρχει πιστωτικό: η κατάθεση {_eur(amount)} πρέπει να "
                                         "συμψηφιστεί από τον λογιστή (ολικό ή μερικό πιστωτικό), όχι να εξοφλήσει αυτόματα το τιμολόγιο.")

    def already_paid_message() -> str:
        return (f"Το τιμολόγιο {number} έχει ήδη εξοφληθεί. Η κατάθεση {_eur(amount)} μπορεί να είναι διπλοπληρωμή "
                "ή πληρωμή που αφορά άλλο παραστατικό — ελέγξτε την πριν τη συνδέσετε.")

    key = str(oid)
    if status == "paid" and (invoice.get("paid_via") in ("stripe", "trust")
                             or await _claimed_by_other_deposit(firm_id, key, tx["_id"])):
        return refuse("already_paid", already_paid_message())

    due, _withheld = await outstanding_amount(firm_id, invoice, settings)
    difference = round(amount - due, 2)
    exact = deposit_cents(amount) == to_cents(due)
    if not exact and not accept_difference:
        return refuse(
            "amount_mismatch",
            f"Η κατάθεση ({_eur(amount)}) δεν ταιριάζει με το οφειλόμενο του {number} ({_eur(due)}): "
            f"διαφορά {'+' if difference > 0 else '−'}{_eur(abs(difference))}.",
            due=due, difference=difference)

    if dry_run:
        return {"status": "ok", "reason": "", "message": "", "due": due,
                "difference": 0.0 if exact else difference, "invoice_number": invoice.get("number", "")}

    outcome = "linked"
    if status == "issued":
        if await mark_paid(firm_id, oid, paid_via=paid_via):
            outcome = "paid"
        else:
            # Άλλη διεργασία πρόλαβε ανάμεσα στον έλεγχο και την εξόφληση: ξαναελέγχουμε την τρέχουσα εικόνα.
            fresh = await db.invoices.find_one(firm_query(firm_id, {"_id": oid})) or {}
            if (fresh.get("status") != "paid" or fresh.get("paid_via") in ("stripe", "trust")
                    or await _claimed_by_other_deposit(firm_id, key, tx["_id"])):
                return refuse("already_paid", already_paid_message())

    update = {"$set": {
        "matched_invoice_id": key, "matched_invoice_number": invoice.get("number", ""),
        "match_status": status_on_success, "match_ambiguous": False, "confirmed_at": _now(),
        **({"match_confidence": 100} if status_on_success == "manual_matched" else {}),
        **({} if exact else {"amount_difference": difference}),
    }}
    if exact:
        update["$unset"] = {"amount_difference": ""}
    await db.bank_transactions.update_one(firm_query(firm_id, {"_id": tx["_id"]}), update)
    return {"status": outcome, "reason": "", "message": "", "due": due, "difference": 0.0 if exact else difference,
            "invoice_number": invoice.get("number", "")}


# ------------------------------------------------- cancellation / credit ----

class InvoiceWorkflowError(Exception):
    """Μη επιτρεπτή μετάβαση κατάστασης τιμολογίου (μήνυμα προς τον χρήστη)."""


def _series_of(number: str) -> tuple[str, int] | None:
    """('ΤΠΥ', 2026) από το «ΤΠΥ-2026-0007»· None αν ο αριθμός δεν έχει αυτή τη μορφή."""
    try:
        prefix, year, _seq = number.rsplit("-", 2)
        return prefix, int(year)
    except (ValueError, AttributeError):
        return None


async def _previous_in_series(firm_id: str, prefix: str, year: int):
    """(αριθμός, ημερομηνία, κείμενο αριθμού) του τελευταίου **εκδοθέντος** της σειράς, ή None.

    Τα πρόχειρα με παλιό αριθμό δεν μετρούν: δεν είναι εκδοθέντα παραστατικά.
    """
    best = None
    query = firm_query(firm_id, {"number": {"$regex": f"^{prefix}-{year}-"},
                                 "status": {"$in": ["issued", "paid", "cancelled"]}})
    async for d in db.invoices.find(query, {"number": 1, "date": 1}):
        try:
            n = int(d["number"].rsplit("-", 1)[-1])
        except (ValueError, KeyError):
            continue
        if best is None or n > best[0]:
            best = (n, (d.get("date") or "")[:10], d["number"])
    return best


def _check_series_date(inv_date: _date, today: _date, previous) -> None:
    """Η ημερομηνία δεν μπορεί να είναι προγενέστερη του τελευταίου εκδοθέντος της σειράς.

    `previous` = αποτέλεσμα της `_previous_in_series`. Μια μελλοντική ημερομηνία σε παλιό παραστατικό δεν
    πρέπει να μπλοκάρει για πάντα τη σειρά, γι' αυτό συγκρίνουμε με το `min(ημερομηνία, σήμερα)`.
    """
    if not previous:
        return
    try:
        prev_date = _date.fromisoformat(previous[1])
    except ValueError:
        return
    if inv_date < min(prev_date, today):
        raise InvoiceWorkflowError(
            f"Η ημερομηνία {inv_date:%d/%m/%Y} είναι προγενέστερη του τελευταίου εκδοθέντος της σειράς "
            f"({previous[2]} — {prev_date:%d/%m/%Y}). Η αρίθμηση πρέπει να ακολουθεί τη χρονολογική "
            f"σειρά· αλλάξτε την ημερομηνία του πρόχειρου σε {min(prev_date, today):%d/%m/%Y} ή αργότερα."
        )


def _allocation_is_stale(allocated_at: str | None) -> bool:
    """Η τελευταία παραχώρηση αριθμού είναι παλαιότερη από το κλείδωμα έκδοσης (ή άγνωστη, από παλιό μετρητή):
    όποια έκδοση την έκανε, δεν είναι πια σε εξέλιξη."""
    if not allocated_at:
        return True
    try:
        then = datetime.fromisoformat(allocated_at)
    except (TypeError, ValueError):
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() > ISSUE_CLAIM_TTL_SECONDS


SERIES_BUSY_MESSAGE = "Η σειρά παραστατικών ενημερώθηκε ταυτόχρονα από άλλη έκδοση. Δοκιμάστε ξανά σε λίγο."


async def _allocate_number(firm_id: str, series: tuple[str, int], inv_date: _date, today: _date, previous) -> str:
    """Παραχωρεί αριθμό της σειράς **και ελέγχει την ημερομηνία στην ίδια ατομική εγγραφή** του μετρητή.

    Ο έλεγχος με την `_previous_in_series` γίνεται πριν από την παραχώρηση· ανάμεσα στους δύο μπορεί άλλο
    αίτημα να πάρει αριθμό με μεταγενέστερη ημερομηνία. Ο μετρητής θυμάται την τελευταία ημερομηνία και αρνείται.

    Η άρνηση όμως μπορεί να οφείλεται (α) σε αριθμό που δόθηκε σε άλλη έκδοση και **δεν έχει γραφτεί ακόμη** στο
    παραστατικό του — τότε η ημερομηνία του ΔΕΝ είναι ορφανή και δεν πρέπει να διορθωθεί· ή (β) σε ημερομηνία που
    άφησε μια αποτυχημένη έκδοση. Η διάκριση γίνεται από το αν ο μετρητής είναι πάνω από τον μεγαλύτερο αριθμό που
    έχει γραφτεί, και από το πόσο παλιά είναι η παραχώρηση (το κλείδωμα έκδοσης λήγει σε `ISSUE_CLAIM_TTL_SECONDS`):
    μόνο όταν δεν εκκρεμεί αριθμός, ή ο εκκρεμής είναι παλιότερος από το κλείδωμα, διορθώνουμε τη «τελευταία
    ημερομηνία» (υπό όρο: ο μετρητής να μην έχει αλλάξει) και ξαναδοκιμάζουμε μία φορά.
    """
    from numbering import SeriesBusy, SeriesDateOutOfOrder, reset_series_date
    prefix, year = series
    floor = previous[0] if previous else 0
    for attempt in (1, 2):
        try:
            return await next_invoice_number(firm_id, prefix, year, date_iso=inv_date.isoformat(), floor=floor)
        except SeriesBusy:
            raise InvoiceWorkflowError(SERIES_BUSY_MESSAGE)
        except SeriesDateOutOfOrder as refused:
            fresh = await _previous_in_series(firm_id, prefix, year)
            _check_series_date(inv_date, today, fresh)        # υπάρχει όντως παραστατικό που μπλοκάρει → σφάλμα προς τον χρήστη
            if attempt == 2:
                raise InvoiceWorkflowError(SERIES_BUSY_MESSAGE)
            persisted = fresh[0] if fresh else 0
            if refused.seq is not None and refused.seq > persisted and not _allocation_is_stale(refused.allocated_at):
                raise InvoiceWorkflowError(SERIES_BUSY_MESSAGE)
            actual = None
            if fresh:
                try:
                    actual = min(_date.fromisoformat(fresh[1]), today).isoformat()
                except ValueError:
                    actual = None
            await reset_series_date(_counter_id(firm_id, prefix, year), expected=refused.last_date, to=actual,
                                    seq=refused.seq)
            floor = max(floor, persisted)
    raise AssertionError("unreachable")  # pragma: no cover


ISSUE_CLAIM_TTL_SECONDS = 120   # μετά από crash ένα «κλείδωμα έκδοσης» λήγει μόνο του
NUMBER_ATTEMPTS = 3              # φορές που ξαναδοκιμάζεται η έκδοση όταν η βάση αρνείται έναν αριθμό


def not_being_issued() -> dict:
    """Φίλτρο: το πρόχειρο δεν εκδίδεται αυτή τη στιγμή (δεν υπάρχει ζωντανό κλείδωμα έκδοσης)."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ISSUE_CLAIM_TTL_SECONDS)).isoformat()
    return {"$or": [{"issue_claim_at": {"$exists": False}}, {"issue_claim_at": None},
                    {"issue_claim_at": {"$lt": cutoff}}]}


async def _release_issue_claim(firm_id: str, invoice_id, token: str) -> None:
    try:
        await db.invoices.update_one(
            firm_query(firm_id, {"_id": invoice_id, "status": "draft", "issue_claim": token}),
            {"$unset": {"issue_claim": "", "issue_claim_at": ""}})
    except Exception:  # noqa: BLE001 — λήγει μόνο του μετά το TTL
        logger.warning("Δεν αποδεσμεύτηκε το κλείδωμα έκδοσης του %s", invoice_id)


async def issue_draft(firm_id: str, doc: dict, settings: dict) -> dict:
    """Μετατρέπει ένα πρόχειρο σε εκδοθέν παραστατικό και επιστρέφει το ενημερωμένο έγγραφο.

    Εδώ και μόνο εδώ το παραστατικό παίρνει:

    * **αριθμό** από τη σειρά του είδους του (ΤΠΥ / ΑΠΥ / ΠΙΣ) — βλ. σχόλιο στα SERIES_PREFIX·
    * **είδος** (`invoice_type`), παγωμένο ώστε μια μεταγενέστερη αλλαγή στην καρτέλα του
      πελάτη να μη μεταβάλλει το είδος ενός ήδη εκδοθέντος παραστατικού·
    * **παρακράτηση** (`withholding_amount`), παγωμένη με τις ρυθμίσεις της στιγμής εκείνης,
      ώστε PDF, XML προς ΑΑΔΕ, πληρωμή και αντιστοίχιση τράπεζας να βλέπουν το ίδιο ποσό.

    Ελέγχει επίσης την ημερομηνία: όχι μελλοντική, και όχι προγενέστερη του τελευταίου
    εκδοθέντος της ίδιας σειράς — αλλιώς η αρίθμηση παύει να είναι χρονολογική.

    **Δύο ατομικοί έλεγχοι στην αρίθμηση.** Ο έλεγχος «όχι πριν τον προηγούμενο αριθμό» γίνεται ΚΑΙ μέσα στην ίδια
    εγγραφή που δίνει τον αριθμό (ο μετρητής θυμάται την τελευταία ημερομηνία της σειράς): δύο πρόχειρα με
    διαφορετική ημερομηνία που εκδίδονται ταυτόχρονα δεν μπορούν πια να περάσουν και τα δύο. Και η τελική εγγραφή
    προστατεύεται από το μοναδικό ευρετήριο `(firm_id, number)`: αν ο αριθμός υπάρχει ήδη σε άλλο παραστατικό
    (επαναφορά αντιγράφου, χειροκίνητη διόρθωση), η έκδοση παίρνει νέο αριθμό (έως `NUMBER_ATTEMPTS` φορές).
    Ο αριθμός που «κάηκε» δεν επιστρέφεται ποτέ: ένα κενό είναι προτιμότερο από διπλότυπο.

    **Σειρά ενεργειών.** Πρώτα «κλειδώνεται» ατομικά το πρόχειρο, και μόνο ο νικητής παίρνει αριθμό.
    Αν ο αριθμός δινόταν πριν από το κλείδωμα, δύο ταυτόχρονα αιτήματα (διπλό κλικ) θα έπαιρναν
    δύο διαδοχικούς αριθμούς και ο χαμένος θα άφηνε κενό στη σειρά — που δεν μπορεί να
    επιστραφεί αν στο μεταξύ εκδόθηκε άλλο παραστατικό. Ό,τι ελέγχεται και υπολογίζεται
    (είδος, παρακράτηση, ημερομηνία, ποσό) προέρχεται από το κλειδωμένο έγγραφο, όχι από παλιά ανάγνωση.
    """
    import uuid
    from pymongo import ReturnDocument

    if doc.get("status") != "draft":
        raise InvoiceWorkflowError("Μόνο πρόχειρα τιμολόγια μπορούν να εκδοθούν.")

    token = uuid.uuid4().hex
    claimed = await db.invoices.find_one_and_update(
        firm_query(firm_id, {"_id": doc["_id"], "status": "draft", **not_being_issued()}),
        {"$set": {"issue_claim": token, "issue_claim_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:      # εκδόθηκε ήδη, διαγράφηκε, ή το εκδίδει αυτή τη στιγμή άλλο αίτημα (π.χ. διπλό κλικ)
        raise InvoiceWorkflowError("Το τιμολόγιο έχει ήδη εκδοθεί ή εκδίδεται αυτή τη στιγμή.")

    allocated = ""
    try:
        doc = claimed
        client = await load_client(firm_id, doc)
        invoice_type = resolve_invoice_type(doc, client or {})
        # Το είδος βγαίνει από τον πελάτη της στιγμής: ένα πρόχειρο φτιαγμένο για εταιρεία με ρητή παρακράτηση μπορεί να
        # έγινε ΑΠΥ λιανικής. Ελέγχεται πριν δοθεί αριθμός, ώστε η άρνηση να μην αφήνει κενό στη σειρά.
        check_explicit_withholding(doc, invoice_type)

        total = float(doc.get("total") or 0)
        if invoice_type == TYPE_CREDIT_NOTE or doc.get("credit_note_for"):
            if total >= 0:
                raise InvoiceWorkflowError("Το πιστωτικό τιμολόγιο πρέπει να έχει αρνητικό σύνολο.")
        elif total <= 0:
            raise InvoiceWorkflowError("Το σύνολο του τιμολογίου πρέπει να είναι θετικό για να εκδοθεί.")

        if doc.get("credit_note_for"):
            # Το αρχικό μπορεί να ακυρώθηκε αφότου φτιάχτηκε το πρόχειρο πιστωτικό (π.χ. ακύρωση και πιστωτικό
            # ταυτόχρονα από δύο χρήστες): πιστωτικό πάνω σε ακυρωμένο τιμολόγιο θα αναιρούσε το ίδιο ποσό δύο φορές.
            try:
                original = await db.invoices.find_one(
                    firm_query(firm_id, {"_id": ObjectId(doc["credit_note_for"])}), {"status": 1, "number": 1})
            except Exception:  # noqa: BLE001 — άκυρο id: το πιστωτικό δεν δένεται με υπαρκτό τιμολόγιο
                original = None
            if original is not None and original.get("status") == "cancelled":
                raise InvoiceWorkflowError(
                    f"Το αρχικό τιμολόγιο {original.get('number') or '(χωρίς αριθμό)'} έχει ακυρωθεί· το πιστωτικό "
                    "δεν χρειάζεται. Διαγράψτε το πρόχειρο πιστωτικό.")

        try:
            inv_date = _date.fromisoformat((doc.get("date") or "")[:10])
        except ValueError:
            raise InvoiceWorkflowError("Μη έγκυρη ημερομηνία παραστατικού — διορθώστε την πριν την έκδοση.")
        today = athens_today()
        if inv_date > today:
            raise InvoiceWorkflowError(
                f"Η ημερομηνία του παραστατικού ({inv_date:%d/%m/%Y}) είναι μελλοντική. Το παραστατικό φέρει "
                "την ημερομηνία έκδοσής του· διορθώστε την ή εκδώστε το όταν έρθει η ημέρα."
            )

        # Πρόχειρο από παλαιότερη έκδοση της εφαρμογής: κρατά τον αριθμό που ήδη έχει.
        number = (doc.get("number") or "").strip()
        given = bool(number)
        series = _series_of(number) if given else (series_prefix(invoice_type), inv_date.year)
        previous = await _previous_in_series(firm_id, *series) if series else None
        _check_series_date(inv_date, today, previous)

        issued = None
        for _attempt in range(NUMBER_ATTEMPTS):
            if not given:
                number = allocated = await _allocate_number(firm_id, series, inv_date, today, previous)
            withheld = withholding_for(doc, settings, invoice_type, float(doc.get("subtotal") or 0))
            try:
                issued = await db.invoices.find_one_and_update(
                    firm_query(firm_id, {"_id": doc["_id"], "status": "draft", "issue_claim": token}),
                    {"$set": {"status": "issued", "issued_at": _now(), "number": number,
                              "invoice_type": invoice_type, "withholding_amount": withheld},
                     "$unset": {"issue_claim": "", "issue_claim_at": ""}},
                    return_document=ReturnDocument.AFTER,
                )
                break
            except DuplicateKeyError:
                # Η βάση αρνείται τον αριθμό: τον έχει ήδη άλλο παραστατικό της σειράς.
                if given:
                    raise InvoiceWorkflowError(
                        f"Ο αριθμός {number} υπάρχει ήδη σε άλλο παραστατικό της σειράς· το πρόχειρο δεν εκδόθηκε. "
                        "Ανοίξτε τον έλεγχο αρίθμησης (GET /api/invoices/numbering-check) και αποφασίστε με τον λογιστή.")
                # Ο μετρητής είχε μείνει πίσω. Ο αριθμός δεν επιστρέφεται σε αυτόν — θα τον έδινε ξανά — και η
                # σειρά διαβάζεται ξανά ώστε ο επόμενος αριθμός να πάει πάνω από όλους τους υπάρχοντες.
                allocated = ""
                previous = await _previous_in_series(firm_id, *series)
                _check_series_date(inv_date, today, previous)
        else:
            raise InvoiceWorkflowError(
                f"Δεν βρέθηκε ελεύθερος αριθμός στη σειρά {series[0]}-{series[1]} μετά από {NUMBER_ATTEMPTS} "
                "προσπάθειες· ο αριθμός υπάρχει ήδη σε άλλο παραστατικό. Τρέξτε τον έλεγχο αρίθμησης "
                "(GET /api/invoices/numbering-check) και ενημερώστε τον διαχειριστή.")
        if issued is None:   # το κλείδωμα χάθηκε (έληξε και το πήρε άλλο αίτημα, ή διαγράφηκε το πρόχειρο)
            raise InvoiceWorkflowError("Το τιμολόγιο έχει ήδη εκδοθεί ή δεν υπάρχει πια.")
        return issued
    except BaseException:
        # Και σε ακύρωση του αιτήματος (CancelledError): η αποδέσμευση ολοκληρώνεται ούτως ή άλλως.
        async def cleanup():
            await _release_issue_claim(firm_id, doc["_id"], token)
            if not allocated:
                return
            # Η τελική εγγραφή μπορεί να έχει ήδη εφαρμοστεί και να απέτυχε μόνο η αναμονή της (ακύρωση
            # αιτήματος, διακοπή σύνδεσης): ο αριθμός τότε ανήκει στο εκδοθέν παραστατικό. Αν επιστρεφόταν, ο
            # επόμενος θα έπαιρνε τον ίδιο αριθμό — διπλός αριθμός σε φορολογική σειρά. Ένα κενό είναι
            # ασφαλέστερο από ένα διπλότυπο, γι' αυτό αν η κατάσταση δεν μπορεί να διαβαστεί, δεν επιστρέφεται.
            try:
                current = await db.invoices.find_one(firm_query(firm_id, {"_id": doc["_id"]}), {"status": 1, "number": 1})
            except Exception:  # noqa: BLE001
                logger.warning("Δεν επιστράφηκε ο αριθμός %s: δεν διαβάστηκε η κατάσταση του τιμολογίου", allocated)
                return
            if current is not None and current.get("status") != "draft" and current.get("number") == allocated:
                return
            await release_invoice_number(firm_id, allocated)
        await asyncio.shield(cleanup())
        raise


async def _warn_trust_money(firm_id: str, invoice: dict, what: str) -> None:
    """Ειδοποίηση όταν ακυρώνεται/πιστώνεται τιμολόγιο που έχει εξοφληθεί (και) από πελατολογικό λογαριασμό.

    Η εφαρμογή δεν επιστρέφει μόνη της χρήματα πελάτη στον λογαριασμό του: ποιο ποσό δικαιούται
    ο πελάτης (όλο, μέρος, τίποτα — π.χ. μερικό πιστωτικό) είναι απόφαση του γραφείου. Δεν πρέπει
    όμως να περάσει απαρατήρητο, γιατί τα χρήματα του πελάτη έχουν ήδη αφαιρεθεί από το υπόλοιπό του.
    """
    applied = round(float(invoice.get("trust_applied") or 0), 2)
    if applied <= 0:
        return
    from automation import notify
    number = invoice.get("number") or "τιμολόγιο"
    try:
        await notify(
            firm_id, "trust_money_stranded", "critical",
            f"{what} το {number} που είχε εξοφληθεί με {_eur(applied)} από πελατολογικό λογαριασμό",
            f"Ο πελάτης «{invoice.get('client_name') or ''}» είχε καλύψει {_eur(applied)} του {number} από τα χρήματα που "
            "κρατά το γραφείο γι' αυτόν. Το ποσό ΔΕΝ επιστρέφεται αυτόματα στον πελατολογικό λογαριασμό: αποφασίστε αν "
            "δικαιούται επιστροφή και καταχωρίστε την (κατάθεση στον λογαριασμό του ή επιστροφή στον πελάτη).",
            entity="invoice", entity_id=str(invoice["_id"]), requires_action=True,
            dedupe_key=f"trust_money_stranded:{invoice['_id']}")
    except Exception:  # noqa: BLE001 — η ειδοποίηση δεν πρέπει να μπλοκάρει την ακύρωση/το πιστωτικό
        logger.exception("Δεν καταχωρήθηκε ειδοποίηση πελατολογικού για το %s", invoice.get("number"))


async def cancel_in_mydata(firm_id: str, invoice: dict, settings: dict) -> dict:
    """Ακυρώνει το παραστατικό στην ΑΑΔΕ και σημειώνει το τιμολόγιο ως ακυρωμένο.

    Η ακύρωση επιτρέπεται μόνο για εκδοθέντα (και, αν έχουν διαβιβαστεί, διαβιβασμένα) και μη
    εξοφλημένα παραστατικά· για εξοφλημένα ο σωστός δρόμος είναι πιστωτικό τιμολόγιο. Το πρόχειρο
    δεν ακυρώνεται (δεν είναι παραστατικό — διαγράφεται) και όταν υπάρχει πιστωτικό για το
    τιμολόγιο η διόρθωση γίνεται από αυτό, όχι με δεύτερη αναίρεση.
    """
    if invoice.get("status") == "draft":
        raise InvoiceWorkflowError("Το πρόχειρο δεν ακυρώνεται· διαγράψτε το.")
    if invoice.get("status") == "paid":
        raise InvoiceWorkflowError(
            "Το τιμολόγιο είναι εξοφλημένο — δεν ακυρώνεται. Εκδώστε πιστωτικό τιμολόγιο."
        )
    if invoice.get("status") == "cancelled":
        raise InvoiceWorkflowError("Το τιμολόγιο είναι ήδη ακυρωμένο.")
    if invoice.get("credit_note_id"):
        raise InvoiceWorkflowError(
            "Για το τιμολόγιο υπάρχει πιστωτικό· η διόρθωση γίνεται από το πιστωτικό. "
            "Αν δεν χρειάζεται πια, διαγράψτε το πρόχειρο πιστωτικό πρώτα."
        )

    update = {"status": "cancelled", "cancelled_at": _now()}
    if invoice.get("mydata_status") == "synced" and invoice.get("mydata_mark"):
        if not mydata_ready(settings):
            raise InvoiceWorkflowError(
                "Το παραστατικό έχει διαβιβαστεί στο myDATA αλλά λείπουν τα credentials ΑΑΔΕ "
                "για την ακύρωση. Συμπληρώστε τα στις Ρυθμίσεις."
            )
        result = await cancel_invoice(settings, invoice["mydata_mark"])
        update.update({
            "mydata_status": "cancelled",
            "mydata_cancellation_mark": result["cancellation_mark"],
            "mydata_cancelled_at": _now(),
        })
    # Όσο περιμέναμε την ΑΑΔΕ το τιμολόγιο μπορεί να άλλαξε (π.χ. να εξοφλήθηκε από πελατολογικό). Η ακύρωση
    # στην ΑΑΔΕ έχει ήδη γίνει και πρέπει να καταγραφεί· ό,τι εισπράχθηκε στο μεταξύ όμως δεν πρέπει να περάσει
    # απαρατήρητο, γι' αυτό οι ειδοποιήσεις βγαίνουν από την κατάσταση **τη στιγμή της εγγραφής**, όχι από την
    # ανάγνωση που έγινε πριν την κλήση.
    from pymongo import ReturnDocument
    before = await db.invoices.find_one_and_update(
        firm_query(firm_id, {"_id": invoice["_id"]}), {"$set": update}, return_document=ReturnDocument.BEFORE)
    current = before or invoice
    await _warn_trust_money(firm_id, current, "Ακυρώθηκε")
    if current.get("status") == "paid" and current.get("paid_via") != "trust":
        await _warn_paid_then_cancelled(firm_id, current)
    return update


async def _warn_paid_then_cancelled(firm_id: str, invoice: dict) -> None:
    """Ειδοποίηση: το τιμολόγιο ακυρώθηκε στην ΑΑΔΕ τη στιγμή που εξοφλούνταν από άλλο κανάλι."""
    from automation import notify
    number = invoice.get("number") or "τιμολόγιο"
    try:
        await notify(
            firm_id, "cancelled_after_payment", "critical",
            f"Ακυρώθηκε το {number} ενώ εξοφλήθηκε στο μεταξύ",
            f"Το {number} ακυρώθηκε στην ΑΑΔΕ τη στιγμή που εξοφλούνταν (τρόπος: {invoice.get('paid_via') or 'άγνωστος'}). "
            "Ελέγξτε αν ο πελάτης πλήρωσε και αποφασίστε για επιστροφή ή συμψηφισμό.",
            entity="invoice", entity_id=str(invoice["_id"]), requires_action=True,
            dedupe_key=f"cancelled_after_payment:{invoice['_id']}")
    except Exception:  # noqa: BLE001 — η ειδοποίηση δεν πρέπει να μπλοκάρει την ακύρωση
        logger.exception("Δεν καταχωρήθηκε ειδοποίηση για το %s", number)


CREDIT_CLAIM_TTL_SECONDS = 120


async def _claim_for_credit_note(firm_id: str, invoice_id) -> str | None:
    """Κλειδώνει ατομικά το αρχικό τιμολόγιο ώστε να δημιουργηθεί **ένα** πιστωτικό. None = δεν χωρά.

    Δύο ταυτόχρονα αιτήματα (διπλό κλικ, δύο χρήστες) έβλεπαν και τα δύο «δεν έχει πιστωτικό»,
    έφτιαχναν από ένα πρόχειρο, και το δεύτερο έγραφε πάνω στο `credit_note_id` του πρώτου: το πρώτο
    πρόχειρο έμενε ορφανό και, αν εκδιδόταν, πίστωνε το ίδιο τιμολόγιο δεύτερη φορά. Το κλείδωμα
    λήγει μόνο του (TTL) αν η διεργασία πεθάνει στη μέση.
    """
    import uuid
    from datetime import timedelta
    from pymongo import ReturnDocument
    token = uuid.uuid4().hex
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=CREDIT_CLAIM_TTL_SECONDS)).isoformat()
    claimed = await db.invoices.find_one_and_update(
        {"$and": [
            {"_id": invoice_id, "firm_id": firm_id},
            {"$or": [{"credit_note_id": {"$exists": False}}, {"credit_note_id": None}, {"credit_note_id": ""}]},
            {"$or": [{"credit_claim_at": {"$exists": False}}, {"credit_claim_at": None}, {"credit_claim_at": {"$lt": cutoff}}]},
        ]},
        {"$set": {"credit_claim": token, "credit_claim_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    return token if claimed else None


async def build_credit_note(firm_id: str, invoice: dict, user_email: str,
                            reason: str = "", amount: float | None = None) -> dict:
    """Δημιουργεί πιστωτικό τιμολόγιο συσχετιζόμενο με το αρχικό.

    Πλήρες πιστωτικό από προεπιλογή· με ``amount`` γίνεται μερικό (η καθαρή αξία
    κατανέμεται στο πρώτο συντελεστή ΦΠΑ του αρχικού).
    """
    from models import Invoice, InvoiceLineItem
    from tax_utils import calculate_invoice_totals

    if invoice.get("mydata_status") != "synced" or not invoice.get("mydata_mark"):
        raise InvoiceWorkflowError(
            "Το πιστωτικό απαιτεί διαβιβασμένο αρχικό παραστατικό (MARK). "
            "Περιμένετε να ολοκληρωθεί η διαβίβαση στο myDATA."
        )
    if invoice.get("credit_note_id"):
        raise InvoiceWorkflowError("Έχει ήδη εκδοθεί πιστωτικό για αυτό το τιμολόγιο.")
    if invoice.get("credit_note_for") or invoice.get("invoice_type") == TYPE_CREDIT_NOTE:
        raise InvoiceWorkflowError("Πιστωτικό τιμολόγιο δεν πιστώνεται· εκδώστε νέο τιμολόγιο αν χρειάζεται.")
    if invoice.get("status") == "cancelled":
        raise InvoiceWorkflowError("Το τιμολόγιο είναι ακυρωμένο — δεν χρειάζεται πιστωτικό.")

    # Τα ποσά αποθηκεύονται **αρνητικά** σκόπιμα: έτσι το πιστωτικό μειώνει έσοδα, ΦΠΑ
    # και τζίρο σε κάθε άθροισμα, χωρίς καμία συγκεντρωτική να χρειάζεται να το εξαιρέσει.
    # Προς την ΑΑΔΕ διαβιβάζονται θετικά — βλ. CREDIT_TYPES στο mydata_client.
    original_lines = invoice.get("line_items") or []
    if amount is None:
        lines = [
            InvoiceLineItem(
                description=f"Πίστωση: {ln.get('description', '')}"[:200],
                quantity=float(ln.get("quantity", 1)),
                unit_price=-abs(float(ln.get("unit_price", 0))),
                vat_rate=int(ln.get("vat_rate", 24)),
            )
            for ln in original_lines
        ]
    else:
        # Δεν πιστώνεται περισσότερο από όσο τιμολογήθηκε (καθαρή αξία).
        original_net = round(abs(calculate_invoice_totals(original_lines)["subtotal"]), 2)
        if round(abs(float(amount)), 2) > original_net:
            raise InvoiceWorkflowError(
                f"Το ποσό της πίστωσης ({abs(float(amount)):.2f} €) υπερβαίνει την καθαρή αξία του "
                f"αρχικού τιμολογίου ({original_net:.2f} €)."
            )
        rate = int(original_lines[0].get("vat_rate", 24)) if original_lines else 24
        lines = [InvoiceLineItem(description=f"Μερική πίστωση τιμολογίου {invoice.get('number')}",
                                 quantity=1, unit_price=-abs(float(amount)), vat_rate=rate)]
    if not lines:
        raise InvoiceWorkflowError("Το αρχικό τιμολόγιο δεν έχει γραμμές για πίστωση.")

    credit = Invoice(
        firm_id=firm_id,
        client_id=invoice.get("client_id", ""),
        client_name=invoice.get("client_name", ""),
        date=athens_today().isoformat(),
        line_items=lines,
        invoice_type="5.1",
        credit_note_for=str(invoice["_id"]),
        correlated_mark=invoice.get("mydata_mark"),
        notes=reason or f"Πιστωτικό για το {invoice.get('number')}",
        payment_method=invoice.get("payment_method", ""),
        created_by=user_email,
    )
    totals = calculate_invoice_totals(credit.line_items)
    credit.subtotal, credit.vat_amount, credit.total = totals["subtotal"], totals["vat_amount"], totals["total"]
    credit.vat_breakdown = totals["vat_breakdown"]
    # Χωρίς αριθμό μέχρι την έκδοση (σειρά ΠΙΣ) — ένα πρόχειρο πιστωτικό που θα διαγραφεί
    # δεν πρέπει να αφήνει κενό στη σειρά.
    #
    # Σειρά: κλείδωμα του αρχικού → έλεγχος στο φρέσκο έγγραφο → εισαγωγή πρόχειρου → σύνδεση.
    # Αν κάτι αποτύχει (ή ακυρωθεί το αίτημα) αφαιρείται το πρόχειρο και λύνεται το κλείδωμα.
    token = await _claim_for_credit_note(firm_id, invoice["_id"])
    if token is None:
        raise InvoiceWorkflowError(
            "Για αυτό το τιμολόγιο εκδίδεται ήδη πιστωτικό (ή έχει ήδη εκδοθεί). Ανανεώστε τη σελίδα και ελέγξτε.")
    # Το id του πρόχειρου ορίζεται πριν την εισαγωγή: αν η εγγραφή εφαρμοστεί και χαθεί μόνο η απάντηση (ακύρωση
    # αιτήματος, διακοπή σύνδεσης), ο καθαρισμός πρέπει να ξέρει ποιο έγγραφο να αφαιρέσει.
    credit_doc = credit.to_mongo()
    credit_doc["_id"] = inserted_id = ObjectId()
    insert_attempted = False

    async def cleanup() -> None:
        try:
            if insert_attempted:
                current = await db.invoices.find_one(firm_query(firm_id, {"_id": invoice["_id"]}), {"credit_note_id": 1})
                if current is not None and current.get("credit_note_id") == str(inserted_id):
                    return                       # η σύνδεση πρόλαβε να γραφτεί: το πρόχειρο μένει
                await db.invoices.delete_one(firm_query(firm_id, {"_id": inserted_id, "status": "draft"}))
        except Exception:  # noqa: BLE001
            logger.warning("Δεν αφαιρέθηκε το ημιτελές πρόχειρο πιστωτικό του %s", invoice.get("number"))
        finally:
            try:
                await db.invoices.update_one(
                    firm_query(firm_id, {"_id": invoice["_id"], "credit_claim": token}),
                    {"$unset": {"credit_claim": "", "credit_claim_at": ""}})
            except Exception:  # noqa: BLE001 — λήγει μόνο του μετά το TTL
                logger.warning("Δεν λύθηκε το κλείδωμα πιστωτικού του %s", invoice.get("number"))

    try:
        fresh = await db.invoices.find_one(firm_query(firm_id, {"_id": invoice["_id"]}))
        if (not fresh or fresh.get("status") == "cancelled" or fresh.get("mydata_status") != "synced"
                or not fresh.get("mydata_mark")):
            raise InvoiceWorkflowError("Το τιμολόγιο άλλαξε στο μεταξύ (ακυρώθηκε ή δεν είναι πια διαβιβασμένο) — δεν δημιουργήθηκε πιστωτικό.")
        insert_attempted = True
        await db.invoices.insert_one(credit_doc)
        linked = await db.invoices.update_one(
            firm_query(firm_id, {"_id": invoice["_id"], "credit_claim": token}),
            {"$set": {"credit_note_id": str(inserted_id)}, "$unset": {"credit_claim": "", "credit_claim_at": ""}})
        if linked.matched_count == 0:
            raise InvoiceWorkflowError("Το κλείδωμα του πιστωτικού έληξε πριν ολοκληρωθεί — δεν δημιουργήθηκε πιστωτικό. Δοκιμάστε ξανά.")
    except BaseException:
        await asyncio.shield(cleanup())
        raise
    await _warn_trust_money(firm_id, invoice, "Εκδίδεται πιστωτικό για")
    return await db.invoices.find_one(firm_query(firm_id, {"_id": inserted_id}))
