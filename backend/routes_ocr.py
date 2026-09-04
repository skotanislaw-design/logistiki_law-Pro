"""OCR / Ανάλυση Νομικών Εγγράφων.
Δέχεται PDF ή εικόνα → LLM vision extract → επιστρέφει δομημένα πεδία
(αριθμός απόφασης, δικαστήριο, ημερομηνία, διάδικοι, περίληψη).
"""
import base64
import io
import json
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ai_runtime import LlmChat, UserMessage, ImageContent, TextDelta, StreamDone
from ai_helpers import EMERGENT_LLM_KEY
from auth import get_current_user

router = APIRouter(prefix="/ocr", tags=["ocr"])

MAX_BYTES = 8 * 1024 * 1024  # 8 MB


SYSTEM_PROMPT = """Είσαι έμπειρος νομικός βοηθός που εξειδικεύεται στην ανάλυση ελληνικών νομικών εγγράφων
(δικαστικές αποφάσεις, δικόγραφα, εξώδικα, συμφωνητικά).
Θα σου δοθεί εικόνα εγγράφου. Απάντησε ΜΟΝΟ με JSON στην παρακάτω δομή:

{
  "document_type": "απόφαση / αγωγή / εξώδικο / συμβόλαιο / άλλο",
  "court": "δικαστήριο (αν πρόκειται για δικαστικό έγγραφο)",
  "case_number": "π.χ. 1234/2026",
  "gak": "αριθμός Γενικού Αριθμού Κατάθεσης",
  "eak": "αριθμός Ειδικού Αριθμού Κατάθεσης",
  "decision_number": "αριθμός απόφασης",
  "date": "YYYY-MM-DD",
  "parties": {
    "plaintiff": "ονοματεπώνυμο ενάγοντος/αιτούντος",
    "defendant": "ονοματεπώνυμο εναγόμενου/καθ'ου"
  },
  "subject": "αντικείμενο δίκης σε 1 φράση",
  "amount": "χρηματικό ποσό αν αναφέρεται",
  "key_facts": ["σημαντικό γεγονός 1", "σημαντικό γεγονός 2"],
  "summary": "σύνοψη 2-3 προτάσεων",
  "next_actions": ["πιθανή ενέργεια/προθεσμία"],
  "confidence": "high/medium/low"
}

Χρησιμοποίησε null για πεδία που δεν εμφανίζονται στο έγγραφο. Μην εφευρίσκεις δεδομένα."""


async def _pdf_first_page_bytes(pdf_bytes: bytes) -> bytes:
    """Παίρνει την πρώτη σελίδα PDF και την επιστρέφει ως PNG. Fallback: το ίδιο το PDF."""
    try:
        # Δοκιμή με pypdfium2 αν είναι διαθέσιμο
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(pdf_bytes)
        page = pdf[0]
        pil_image = page.render(scale=2).to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Fallback — LLM δέχεται και PDF ως raw image content τύπου PDF
        return pdf_bytes


@router.post("/analyze")
async def analyze_document(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Το αρχείο είναι κενό.")
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"Το αρχείο υπερβαίνει το όριο {MAX_BYTES // 1024 // 1024} MB.")

    filename = (file.filename or "").lower()
    mime = "image/png"
    if filename.endswith(".pdf"):
        content = await _pdf_first_page_bytes(content)
        mime = "image/png"
    elif filename.endswith(".jpg") or filename.endswith(".jpeg"):
        mime = "image/jpeg"
    elif filename.endswith(".webp"):
        mime = "image/webp"
    elif filename.endswith(".heic") or filename.endswith(".heif"):
        mime = "image/heic"
    else:
        mime = "image/png"

    b64 = base64.b64encode(content).decode("utf-8")
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"ocr-{uuid.uuid4()}",
            system_message=SYSTEM_PROMPT,
        ).with_model("openai", "gpt-5.4-mini")
        image = ImageContent(image_base64=b64)
        msg = UserMessage(text="Ανάλυσε το επισυναπτόμενο νομικό έγγραφο.", file_contents=[image])
        full_text = ""
        async for ev in chat.stream_message(msg):
            if isinstance(ev, TextDelta):
                full_text += ev.content
            elif isinstance(ev, StreamDone):
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία ανάλυσης: {e}")

    # Parse JSON
    match = re.search(r"\{.*\}", full_text, re.DOTALL)
    if not match:
        raise HTTPException(status_code=422, detail="Το AI δεν επέστρεψε δομημένα δεδομένα. Δοκιμάστε καλύτερης ποιότητας εικόνα.")
    try:
        data = json.loads(match.group(0))
    except Exception:
        raise HTTPException(status_code=422, detail="Μη έγκυρη μορφή JSON από το AI.")

    return {
        "filename": file.filename,
        "size_bytes": len(content),
        "extracted": data,
    }
