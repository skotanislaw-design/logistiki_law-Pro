from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from db import db
from auth import hash_password, verify_password
from storage import init_storage
from routes_auth import router as auth_router
from routes_clients import router as clients_router
from routes_invoices import router as invoices_router
from routes_expenses import router as expenses_router
from routes_payroll import router as payroll_router
from routes_dashboard import router as dashboard_router
from routes_settings import router as settings_router
from routes_mydata import router as mydata_router
from routes_tax_calendar import router as tax_calendar_router
from routes_copilot import router as copilot_router
from routes_documents import router as documents_router
from routes_cases import router as cases_router
from routes_grammatia import router as grammatia_router
from routes_paravola import router as paravola_router

app = FastAPI(title="Logistiki Pro API")
api_router = APIRouter(prefix="/api")

api_router.include_router(auth_router)
api_router.include_router(clients_router)
api_router.include_router(invoices_router)
api_router.include_router(expenses_router)
api_router.include_router(payroll_router)
api_router.include_router(dashboard_router)
api_router.include_router(settings_router)
api_router.include_router(mydata_router)
api_router.include_router(tax_calendar_router)
api_router.include_router(copilot_router)
api_router.include_router(documents_router)
api_router.include_router(cases_router)
api_router.include_router(grammatia_router)
api_router.include_router(paravola_router)


@api_router.get("/")
async def root():
    return {"message": "Logistiki Pro API - Ψηφιακός Λογιστής"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def seed_admin():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@logistiki.gr")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if existing is None:
        from datetime import datetime, timezone
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Διαχειριστής",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Seeded admin user: {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})


@app.on_event("startup")
async def startup_event():
    await db.users.create_index("email", unique=True)
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=3600)
    await db.login_attempts.create_index("identifier")
    await seed_admin()
    try:
        init_storage()
        logger.info("Object storage initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    db.client.close()
