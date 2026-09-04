import json
import re
import uuid
from ai_runtime import LlmChat, UserMessage, ImageContent, TextDelta, StreamDone, EMERGENT_LLM_KEY, OpenAISpeechToText



def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Δεν βρέθηκε JSON στην απάντηση του AI")
    return json.loads(match.group(0))


async def ocr_receipt(image_base64: str) -> dict:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"ocr-{uuid.uuid4()}",
        system_message="Είσαι έμπειρος Έλληνας λογιστής. Εξάγεις δεδομένα από φωτογραφίες αποδείξεων/τιμολογίων παροχής υπηρεσιών με ακρίβεια.",
    ).with_model("openai", "gpt-5.4-mini")
    prompt = (
        "Ανάλυσε την εικόνα αυτής της απόδειξης/παραστατικού εξόδου και επίστρεψε ΜΟΝΟ ένα JSON object, "
        "χωρίς επεξήγηση, με ακριβώς τα πεδία:\n"
        '{"vendor": "όνομα προμηθευτή", "date": "YYYY-MM-DD", "amount": 0.0, "vat_amount": 0.0, '
        '"vat_rate": 24, "category": "μία από: Ενοίκιο, Λογαριασμοί ΔΕΚΟ, Προμήθειες γραφείου, '
        'Δικηγορικά έξοδα, Αμοιβές τρίτων, Μεταφορικά, Τηλεπικοινωνίες, Ασφάλιστρα, Συνδρομές / Λογισμικό, '
        'Φιλοξενία / Εστίαση, Λοιπά", "description": "σύντομη περιγραφή"}\n'
        "amount είναι το ΣΥΝΟΛΙΚΟ ποσό (με ΦΠΑ). vat_rate πρέπει να είναι 24, 13, 6 ή 0. "
        "Αν κάποιο πεδίο δεν είναι ορατό, κάνε τη λογικότερη εκτίμηση."
    )
    msg = UserMessage(text=prompt, file_contents=[ImageContent(image_base64=image_base64)])
    full_text = ""
    async for ev in chat.stream_message(msg):
        if isinstance(ev, TextDelta):
            full_text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return _extract_json(full_text)


async def transcribe_audio(file_path: str) -> str:
    stt = OpenAISpeechToText(api_key=EMERGENT_LLM_KEY)
    with open(file_path, "rb") as f:
        response = await stt.transcribe(file=f, model="whisper-1", response_format="json", language="el")
    return response.text


async def parse_invoice_from_text(transcript: str, client_names: list) -> dict:
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"voice-invoice-{uuid.uuid4()}",
        system_message="Είσαι βοηθός έκδοσης τιμολογίων για ελληνικό δικηγορικό γραφείο.",
    ).with_model("openai", "gpt-5.4-mini")
    prompt = (
        f'Ο χρήστης είπε φωνητικά: "{transcript}"\n'
        f"Διαθέσιμοι πελάτες στο σύστημα: {', '.join(client_names) if client_names else 'κανένας ακόμα'}\n"
        "Εξάγαγε ΜΟΝΟ ένα JSON object με τα πεδία:\n"
        '{"client_name": "το πλησιέστερο όνομα πελάτη από τη λίστα ή αυτό που ειπώθηκε", '
        '"description": "περιγραφή υπηρεσίας", "amount": 0.0, "vat_rate": 24}\n'
        "amount είναι η καθαρή αξία (χωρίς ΦΠΑ). vat_rate πρέπει να είναι 24, 13, 6 ή 0 (default 24)."
    )
    full_text = ""
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            full_text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return _extract_json(full_text)
