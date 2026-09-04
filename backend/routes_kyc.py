"""KYC / AML — Ν.4557/2018 (μέτρα δέουσας επιμέλειας πελάτη).
Κάθε πελάτης πρέπει να ταυτοποιείται και να αξιολογείται με βάση κίνδυνο πριν την ανάληψη υπόθεσης.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from db import db
from models import KycRecord
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/kyc", tags=["kyc"])

# Χώρες υψηλού κινδύνου (FATF grey/black list — απλοποιημένη λίστα)
HIGH_RISK_COUNTRIES = {
    "Ιράν", "Βόρεια Κορέα", "Μιανμάρ", "Συρία", "Υεμένη", "Αφγανιστάν",
    "Νιγηρία", "Ζιμπάμπουε", "Λιβύη", "Βενεζουέλα", "Μαυριτανία",
    "Ρωσία", "Λευκορωσία",
}


def _calc_risk(kyc: dict) -> tuple[int, str]:
    """Υπολογισμός Risk Score βάσει AML methodology (0-10)."""
    score = 0
    if kyc.get("pep"):
        score += 5
    if kyc.get("high_risk_country"):
        score += 4
    profession = (kyc.get("profession") or "").lower()
    high_risk_professions = ["τζόγος", "casino", "crypto", "εικονικά νομίσματα", "όπλα"]
    if any(w in profession for w in high_risk_professions):
        score += 3
    sof = (kyc.get("source_of_funds") or "").lower()
    if not sof or len(sof) < 10:
        score += 2  # ανεπαρκής δήλωση πηγής πόρων
    if not kyc.get("id_number"):
        score += 1

    score = min(10, score)
    level = "low" if score <= 2 else "medium" if score <= 5 else "high"
    return score, level


@router.get("/status")
async def kyc_status(user: dict = Depends(get_current_user)):
    """Επισκόπηση KYC — πόσοι πελάτες έχουν συμπληρωθεί, ελλείψεις."""
    total_clients = await db.clients.count_documents({})
    kyc_records = await db.kyc.count_documents({})
    verified = await db.kyc.count_documents({"verified_at": {"$ne": None}})
    high_risk = await db.kyc.count_documents({"risk_level": "high"})
    medium_risk = await db.kyc.count_documents({"risk_level": "medium"})
    missing = total_clients - kyc_records
    return {
        "total_clients": total_clients,
        "kyc_completed": kyc_records,
        "kyc_verified": verified,
        "kyc_missing": max(0, missing),
        "high_risk_count": high_risk,
        "medium_risk_count": medium_risk,
    }


@router.get("/records")
async def list_records(user: dict = Depends(get_current_user)):
    docs = await db.kyc.find(tenant_query(user, {})).sort("updated_at", -1).to_list(1000)
    return [KycRecord.from_mongo(d) for d in docs]


@router.get("/records/{client_id}")
async def get_record(client_id: str, user: dict = Depends(get_current_user)):
    doc = await db.kyc.find_one(tenant_query(user, {"client_id": client_id}))
    if not doc:
        return None
    return KycRecord.from_mongo(doc)


@router.post("/records")
async def upsert_record(payload: KycRecord, user: dict = Depends(get_current_user)):
    if not payload.client_id:
        raise HTTPException(status_code=400, detail="Απαιτείται client_id")
    try:
        client = await db.clients.find_one({"_id": ObjectId(payload.client_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id πελάτη")
    if not client:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    payload.client_name = client.get("name", "")
    # auto-detect high-risk country
    if not payload.high_risk_country and payload.nationality in HIGH_RISK_COUNTRIES:
        payload.high_risk_country = True
    data = payload.model_dump(exclude={"id", "firm_id"})
    data["risk_score"], data["risk_level"] = _calc_risk(data)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing = await db.kyc.find_one(tenant_query(user, {"client_id": payload.client_id}))
    if existing:
        await db.kyc.update_one(tenant_query(user, {"_id": existing["_id"]}), {"$set": data})
        doc = await db.kyc.find_one(tenant_query(user, {"_id": existing["_id"]}))
    else:
        data["firm_id"] = firm_id_for(user)
        result = await db.kyc.insert_one(data)
        doc = await db.kyc.find_one(tenant_query(user, {"_id": result.inserted_id}))
    # ενημερώνω και τον πελάτη με το risk level για γρήγορη εμφάνιση
    await db.clients.update_one(
        tenant_query(user, {"_id": ObjectId(payload.client_id)}),
        {"$set": {"kyc_risk_level": data["risk_level"], "kyc_verified": bool(data.get("verified_at"))}},
    )
    return KycRecord.from_mongo(doc)


@router.post("/records/{client_id}/verify")
async def verify_record(client_id: str, user: dict = Depends(get_current_user)):
    doc = await db.kyc.find_one(tenant_query(user, {"client_id": client_id}))
    if not doc:
        raise HTTPException(status_code=404, detail="Δεν υπάρχει καρτέλα KYC για αυτόν τον πελάτη.")
    now = datetime.now(timezone.utc).isoformat()
    await db.kyc.update_one(
        {"_id": doc["_id"]},
        {"$set": {"verified_at": now, "verified_by": user["email"]}},
    )
    await db.clients.update_one(    tenant_query(user, {"_id": ObjectId(client_id)}), {"$set": {"kyc_verified": True}})
    updated = await db.kyc.find_one(tenant_query(user, {"_id": doc["_id"]}))
    return KycRecord.from_mongo(updated)


@router.get("/high-risk-countries")
async def high_risk_countries(user: dict = Depends(get_current_user)):
    return {"countries": sorted(HIGH_RISK_COUNTRIES)}
