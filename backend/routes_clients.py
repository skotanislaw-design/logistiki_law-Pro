from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from db import db, fetch_all
from models import Client
from auth import get_current_user
from tax_utils import afm_error, is_valid_afm
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/clients", tags=["clients"])


def _validate_client_payload(payload: Client) -> None:
    """Έλεγχος στη διαδρομή, όχι στο μοντέλο.

    Το μοντέλο χρησιμοποιείται και για **ανάγνωση** υπαρχουσών εγγραφών: αν ο έλεγχος
    έμπαινε εκεί, ένας παλιός πελάτης με λανθασμένο ΑΦΜ θα γινόταν αόρατος στη λίστα
    αντί να διορθωθεί. Εδώ ελέγχεται μόνο ό,τι γράφεται τώρα.
    """
    afm = (payload.afm or "").strip()
    payload.afm = afm               # αποθηκεύεται όπως ελέγχθηκε: το « 094259216 » δεν ταίριαζε με τίποτα (διπλότυπα, τράπεζα)
    if not afm:
        return                      # ιδιώτης χωρίς ΑΦΜ: νόμιμο, εκδίδεται ΑΠΥ λιανικής
    if (payload.country or "GR").upper() != "GR":
        return                      # αλλοδαπός: άλλος κανόνας αρίθμησης, δεν ελέγχεται εδώ
    if not is_valid_afm(afm):
        raise HTTPException(status_code=400, detail=afm_error(afm, "ΑΦΜ πελάτη"))


@router.get("")
async def list_clients(user: dict = Depends(get_current_user)):
    # Όλοι οι πελάτες: με όριο, ο πελάτης «Ω…» χανόταν από τη λίστα και δεν μπορούσε να επιλεγεί σε νέο τιμολόγιο.
    docs = await fetch_all(db.clients.find(tenant_query(user)).sort("name", 1))
    return [Client.from_mongo(d) for d in docs]


@router.post("")
async def create_client(payload: Client, user: dict = Depends(get_current_user)):
    _validate_client_payload(payload)
    payload.id = None
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    result = await db.clients.insert_one(payload.to_mongo())
    doc = await db.clients.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Client.from_mongo(doc)


@router.put("/{client_id}")
async def update_client(client_id: str, payload: Client, user: dict = Depends(get_current_user)):
    _validate_client_payload(payload)
    try:
        oid = ObjectId(client_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    payload.id = client_id
    payload.firm_id = firm_id_for(user)
    data = payload.to_mongo()
    data.pop("_id", None)
    result = await db.clients.update_one(tenant_query(user, {"_id": oid}), {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    doc = await db.clients.find_one(tenant_query(user, {"_id": oid}))
    return Client.from_mongo(doc)


@router.delete("/{client_id}")
async def delete_client(client_id: str, user: dict = Depends(get_current_user)):
    try:
        oid = ObjectId(client_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    result = await db.clients.delete_one(tenant_query(user, {"_id": oid}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Ο πελάτης δεν βρέθηκε")
    return {"message": "Ο πελάτης διαγράφηκε"}
