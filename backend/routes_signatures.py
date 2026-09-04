"""Ψηφιακές Υπογραφές — Ν.4727/2020 απλή ηλεκτρονική υπογραφή.
Αποθηκεύει signature ως base64, hash SHA-256, timestamp, IP.
"""
import hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from bson import ObjectId
from db import db
from models import ESignature
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/signatures", tags=["signatures"])


@router.post("")
async def create_signature(payload: ESignature, request: Request, user: dict = Depends(get_current_user)):
    if not payload.signature_data or not payload.signature_data.startswith("data:image"):
        raise HTTPException(status_code=400, detail="Άκυρο signature — απαιτείται base64 image data URL.")
    if not payload.signer_name:
        raise HTTPException(status_code=400, detail="Απαιτείται το όνομα του υπογράφοντα.")
    # SHA-256 hash της υπογραφής (base64 chunk μετά το prefix)
    b64 = payload.signature_data.split(",", 1)[-1]
    payload.signature_hash = hashlib.sha256(b64.encode()).hexdigest()
    payload.ip_address = request.client.host if request.client else ""
    payload.user_agent = request.headers.get("user-agent", "")[:200]
    payload.signed_at = datetime.now(timezone.utc).isoformat()
    payload.firm_id = firm_id_for(user)
    result = await db.signatures.insert_one(payload.to_mongo())
    doc = await db.signatures.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return ESignature.from_mongo(doc)


@router.get("")
async def list_signatures(user: dict = Depends(get_current_user)):
    docs = await db.signatures.find(tenant_query(user, {})).sort("signed_at", -1).limit(200).to_list(200)
    return [ESignature.from_mongo(d) for d in docs]


@router.get("/{signature_id}")
async def get_signature(signature_id: str, user: dict = Depends(get_current_user)):
    try:
        doc = await db.signatures.find_one(tenant_query(user, {"_id": ObjectId(signature_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Η υπογραφή δεν βρέθηκε")
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπογραφή δεν βρέθηκε")
    return ESignature.from_mongo(doc)


@router.post("/{signature_id}/verify")
async def verify_signature(signature_id: str, user: dict = Depends(get_current_user)):
    """Επαληθεύει ότι το hash της αποθηκευμένης υπογραφής ταιριάζει με το SHA-256 του δεδομένου."""
    try:
        doc = await db.signatures.find_one(tenant_query(user, {"_id": ObjectId(signature_id)}))
    except Exception:
        raise HTTPException(status_code=404, detail="Η υπογραφή δεν βρέθηκε")
    if not doc:
        raise HTTPException(status_code=404, detail="Η υπογραφή δεν βρέθηκε")
    b64 = doc["signature_data"].split(",", 1)[-1]
    calc = hashlib.sha256(b64.encode()).hexdigest()
    valid = calc == doc.get("signature_hash")
    return {
        "valid": valid,
        "signed_at": doc.get("signed_at"),
        "signer_name": doc.get("signer_name"),
        "hash": doc.get("signature_hash"),
    }
