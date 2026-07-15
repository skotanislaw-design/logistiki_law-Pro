import json
import re
import uuid
from datetime import date
from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
from ai_helpers import EMERGENT_LLM_KEY
from db import db
from tax_utils import next_vat_deadline, next_efka_deadline


def _fmt(v):
    return f"{(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


async def build_context() -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    settings = await db.settings.find_one({}) or {}
    invoices = await db.invoices.find({}).sort("created_at", -1).to_list(500)
    expenses = await db.expenses.find({}).sort("created_at", -1).to_list(500)
    clients = await db.clients.find({}).to_list(200)

    issued = [i for i in invoices if i.get("status") in ("issued", "paid")]
    month_inv = [i for i in issued if i.get("date", "") >= month_start]
    month_exp = [e for e in expenses if e.get("date", "") >= month_start]
    unpaid = [i for i in invoices if i.get("status") == "issued"]

    output_vat = round(sum(i.get("vat_amount", 0) for i in month_inv), 2)
    input_vat = round(sum(e.get("vat_amount", 0) for e in month_exp), 2)

    return {
        "σήμερα": today.isoformat(),
        "εταιρεία": settings.get("company_name", ""),
        "περίοδος_δήλωσης_ΦΠΑ": settings.get("vat_period", "monthly"),
        "τρέχων_μήνας": {
            "καθαρά_έσοδα": _fmt(sum(i.get("subtotal", 0) for i in month_inv)),
            "ΦΠΑ_εκροών": _fmt(output_vat),
            "καθαρά_έξοδα": _fmt(sum(e.get("amount", 0) - e.get("vat_amount", 0) for e in month_exp)),
            "ΦΠΑ_εισροών": _fmt(input_vat),
            "ΦΠΑ_προς_πληρωμή_εκτίμηση": _fmt(output_vat - input_vat),
        },
        "επόμενη_προθεσμία_ΦΠΑ": next_vat_deadline(today, settings.get("vat_period", "monthly")).isoformat(),
        "επόμενη_προθεσμία_ΕΦΚΑ": next_efka_deadline(today).isoformat(),
        "πελάτες": [c["name"] for c in clients],
        "ανεξόφλητα_τιμολόγια": [
            {"αριθμός": i.get("number"), "πελάτης": i.get("client_name"), "σύνολο": _fmt(i.get("total")), "ημερομηνία": i.get("date")}
            for i in unpaid[:20]
        ],
        "πρόχειρα_τιμολόγια": sum(1 for i in invoices if i.get("status") == "draft"),
        "τελευταία_τιμολόγια": [
            {"αριθμός": i.get("number"), "πελάτης": i.get("client_name"), "σύνολο": _fmt(i.get("total")), "κατάσταση": i.get("status"), "ημερομηνία": i.get("date")}
            for i in invoices[:10]
        ],
        "τελευταία_έξοδα": [
            {"προμηθευτής": e.get("vendor"), "ποσό": _fmt(e.get("amount")), "κατηγορία": e.get("category"), "ημερομηνία": e.get("date")}
            for e in expenses[:10]
        ],
    }


SYSTEM_TEMPLATE = """Είσαι ο «Nomos AI» — ο ψηφιακός νομικός-λογιστής της δικηγορικής εταιρείας {company}. Απαντάς ΠΑΝΤΑ στα ελληνικά, επαγγελματικά αλλά φιλικά, σύντομα και πρακτικά. Ποσά σε μορφή 1.234,56 €.

Έχεις πρόσβαση στα ΖΩΝΤΑΝΑ δεδομένα της εταιρείας:
{context}

ΕΝΕΡΓΕΙΕΣ — όταν ο χρήστης ζητά ρητά:
1. Δημιουργία/έκδοση τιμολογίου → απάντησε ΜΟΝΟ με JSON (τίποτα άλλο):
{{"action":"create_invoice","client_name":"...","description":"...","amount":0.0,"vat_rate":24}}
   - amount = ΚΑΘΑΡΗ αξία χωρίς ΦΠΑ. Αν ο χρήστης δώσει συνολικό ποσό «με ΦΠΑ», υπολόγισε την καθαρή αξία.
   - vat_rate ένα από: 24, 13, 6, 0 (default 24). description σύντομη περιγραφή υπηρεσίας.
2. Σύνταξη επιστολής/εγγράφου (π.χ. όχληση, ενημέρωση, εξώδικο σχέδιο) → απάντησε ΜΟΝΟ με JSON:
{{"action":"draft_document","recipient":"...","subject":"...","body":"πλήρες κείμενο σε επίσημα ελληνικά, με παραγράφους χωρισμένες με \\n\\n, ΧΩΡΙΣ χαιρετισμό κλεισίματος/υπογραφή (προστίθενται αυτόματα)"}}

Διαφορετικά απάντησε κανονικά με κείμενο (όχι JSON). Αν λείπουν απαραίτητα στοιχεία (πελάτης, ποσό), ζήτησέ τα πρώτα. Μην εκτελείς ενέργεια χωρίς σαφές αίτημα.
Για νομικά/φορολογικά θέματα δίνεις πληροφόρηση, όχι δεσμευτική συμβουλή — ο τελικός έλεγχος ανήκει σε πιστοποιημένο λογιστή/δικηγόρο."""


def _try_action(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) and data.get("action") else None
    except Exception:
        return None


async def copilot_llm(history: list, message: str) -> str:
    context = await build_context()
    system = SYSTEM_TEMPLATE.format(company=context.get("εταιρεία") or "Nomos One", context=json.dumps(context, ensure_ascii=False))
    transcript = ""
    for m in history[-12:]:
        role = "Χρήστης" if m["role"] == "user" else "Nomos AI"
        transcript += f"{role}: {m['content']}\n"
    prompt = f"{transcript}Χρήστης: {message}\nNomos AI:"
    chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"copilot-{uuid.uuid4()}", system_message=system).with_model("openai", "gpt-5.4-mini")
    full_text = ""
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            full_text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return full_text.strip()


async def draft_letter_llm(instruction: str) -> dict:
    settings = await db.settings.find_one({}) or {}
    company = settings.get("company_name") or "Nomos One"
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"draft-{uuid.uuid4()}",
        system_message=f"Είσαι έμπειρος Έλληνας δικηγόρος της εταιρείας {company}. Συντάσσεις επίσημες επιστολές/έγγραφα σε άψογα ελληνικά.",
    ).with_model("openai", "gpt-5.4-mini")
    prompt = (
        f"Οδηγία: {instruction}\n"
        "Σύνταξε το έγγραφο και επίστρεψε ΜΟΝΟ JSON:\n"
        '{"recipient":"παραλήπτης ή κενό","subject":"θέμα","body":"πλήρες κείμενο με παραγράφους χωρισμένες με \\n\\n, χωρίς χαιρετισμό κλεισίματος (προστίθεται αυτόματα)"}'
    )
    full_text = ""
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            full_text += ev.content
        elif isinstance(ev, StreamDone):
            break
    match = re.search(r"\{.*\}", full_text, re.DOTALL)
    if not match:
        raise ValueError("Αποτυχία σύνταξης εγγράφου")
    return json.loads(match.group(0))
