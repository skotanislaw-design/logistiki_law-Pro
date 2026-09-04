"""Νομολογιακή Αναζήτηση — LLM-based.
Δίνει σχετικές αποφάσεις ελληνικής νομολογίας για ένα θέμα.
Δεν αντικαθιστά τη νομική βιβλιοθήκη — παρέχει αρχικό προσανατολισμό.
"""
import json
import re
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ai_runtime import LlmChat, UserMessage, TextDelta, StreamDone
from ai_helpers import EMERGENT_LLM_KEY
from auth import get_current_user

router = APIRouter(prefix="/legal-search", tags=["legal-search"])


SYSTEM = """Είσαι Έλληνας νομικός ερευνητής με βαθιά γνώση της ελληνικής νομολογίας και νομοθεσίας.
Ο χρήστης ζητά νομικό προσανατολισμό για ένα θέμα.

Επίστρεψε ΜΟΝΟ JSON με τη δομή:
{
  "topic_summary": "σύντομη περίληψη του νομικού θέματος",
  "applicable_law": [
    {"code": "π.χ. ΑΚ 914", "description": "τι ρυθμίζει"}
  ],
  "key_precedents": [
    {"court": "π.χ. ΑΠ Ολομ.", "number": "123/2020", "principle": "νομολογιακή αρχή σε 1-2 προτάσεις"}
  ],
  "practical_tips": ["πρακτική συμβουλή για δικηγόρο"],
  "risks_defenses": ["πιθανές αντιρρήσεις/κίνδυνοι"],
  "recommended_actions": ["επόμενα βήματα"],
  "disclaimer": "Το παρόν αποτελεί ενημερωτικό υλικό βασισμένο σε γενικές γνώσεις. Επιβεβαιώστε τη νομολογία σε Ισοκράτη/Νομική Βιβλιοθήκη πριν καταθέσετε."
}

- Ανάφερε ΜΟΝΟ αποφάσεις για τις οποίες είσαι εύλογα βέβαιος. Αν δεν είσαι σίγουρος, γράψε "χωρίς συγκεκριμένη αναφορά" στο number.
- Χρησιμοποίησε ελληνική νομική ορολογία και επίσημα ελληνικά.
- Απόφυγε εικασίες για συγκεκριμένους αριθμούς αποφάσεων εάν δεν έχεις πραγματική βεβαιότητα."""


class SearchRequest(BaseModel):
    query: str
    court: str = ""  # optional filter π.χ. "Άρειος Πάγος", "Συμβούλιο της Επικρατείας"
    legal_area: str = ""  # π.χ. "Αστικό", "Εργατικό", "Ποινικό"


@router.post("/query")
async def search(payload: SearchRequest, user: dict = Depends(get_current_user)):
    if not payload.query or len(payload.query.strip()) < 4:
        raise HTTPException(status_code=400, detail="Δώστε πιο συγκεκριμένο ερώτημα.")

    filters = []
    if payload.court:
        filters.append(f"Δικαστήριο: {payload.court}")
    if payload.legal_area:
        filters.append(f"Κλάδος: {payload.legal_area}")
    filter_str = " · ".join(filters) if filters else ""

    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"legal-{uuid.uuid4()}",
            system_message=SYSTEM,
        ).with_model("anthropic", "claude-sonnet-5")
        prompt = f"Νομικό ερώτημα: {payload.query}\n" + (f"\nΦίλτρα: {filter_str}\n" if filter_str else "")
        full_text = ""
        async for ev in chat.stream_message(UserMessage(text=prompt)):
            if isinstance(ev, TextDelta):
                full_text += ev.content
            elif isinstance(ev, StreamDone):
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Αποτυχία αναζήτησης: {e}")

    match = re.search(r"\{.*\}", full_text, re.DOTALL)
    if not match:
        raise HTTPException(status_code=422, detail="Δεν ήταν δυνατή η ανάλυση της απάντησης του AI.")
    try:
        data = json.loads(match.group(0))
    except Exception:
        raise HTTPException(status_code=422, detail="Μη έγκυρη μορφή αποτελέσματος.")

    return {
        "query": payload.query,
        "filters": {"court": payload.court, "legal_area": payload.legal_area},
        "result": data,
    }
