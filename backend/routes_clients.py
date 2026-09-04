from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from db import db
from models import Client
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("")
async def list_clients(user: dict = Depends(get_current_user)):
    docs = await db.clients.find(tenant_query(user)).sort("name", 1).to_list(1000)
    return [Client.from_mongo(d) for d in docs]


@router.post("")
async def create_client(payload: Client, user: dict = Depends(get_current_user)):
    payload.id = None
    payload.firm_id = firm_id_for(user)
    payload.created_by = user["email"]
    result = await db.clients.insert_one(payload.to_mongo())
    doc = await db.clients.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return Client.from_mongo(doc)


@router.put("/{client_id}")
async def update_client(client_id: str, payload: Client, user: dict = Depends(get_current_user)):
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
