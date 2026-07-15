from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from db import db
from auth import get_current_user
from docx_utils import build_letter_docx
from copilot import draft_letter_llm

router = APIRouter(prefix="/documents", tags=["documents"])


class LetterPayload(BaseModel):
    recipient: str = ""
    subject: str = ""
    body: str


class DraftPayload(BaseModel):
    instruction: str


@router.post("/word")
async def letter_word(payload: LetterPayload, user: dict = Depends(get_current_user)):
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Το περιεχόμενο του εγγράφου είναι κενό")
    settings = await db.settings.find_one({}) or {}
    docx_bytes = build_letter_docx(settings, payload.recipient, payload.subject, payload.body)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="nomos-one-letter.docx"'},
    )


@router.post("/pdf")
async def letter_pdf(payload: LetterPayload, user: dict = Depends(get_current_user)):
    from pdf_utils import build_letter_pdf
    if not payload.body.strip():
        raise HTTPException(status_code=400, detail="Το περιεχόμενο του εγγράφου είναι κενό")
    settings = await db.settings.find_one({}) or {}
    pdf_bytes = build_letter_pdf(settings, payload.recipient, payload.subject, payload.body)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="nomos-one-letter.pdf"'},
    )


@router.post("/ai-draft")
async def ai_draft(payload: DraftPayload, user: dict = Depends(get_current_user)):
    if not payload.instruction.strip():
        raise HTTPException(status_code=400, detail="Δώστε οδηγία για τη σύνταξη")
    try:
        return await draft_letter_llm(payload.instruction)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Αποτυχία σύνταξης με AI: {e}")
