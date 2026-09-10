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
from tenant import LEGACY_FIRM_ID
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
from routes_hearings import router as hearings_router
from routes_trust import router as trust_router
from routes_import import router as import_router
from routes_backup import router as backup_router
from routes_kyc import router as kyc_router
from routes_analytics import router as analytics_router
from routes_payments import router as payments_router, public_router as payments_public_router
from routes_signatures import router as signatures_router
from routes_client_portal import router as client_portal_router, public_router as client_portal_public_router
from routes_drafting import router as drafting_router
from routes_ocr import router as ocr_router
from routes_legal_search import router as legal_search_router
from routes_case_tracker import router as case_tracker_router, run_all_tracked_checks
from routes_bank import router as bank_router
from routes_portal_chat import router as portal_chat_router, public_router as portal_chat_public_router
from routes_automation import router as automation_router
from ai_runtime import AIUnavailable

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
api_router.include_router(hearings_router)
api_router.include_router(trust_router)
api_router.include_router(import_router)
api_router.include_router(backup_router)
api_router.include_router(kyc_router)
api_router.include_router(analytics_router)
api_router.include_router(payments_router)
api_router.include_router(signatures_router)
api_router.include_router(client_portal_router)
api_router.include_router(client_portal_public_router)
api_router.include_router(drafting_router)
api_router.include_router(ocr_router)
api_router.include_router(legal_search_router)
api_router.include_router(case_tracker_router)
api_router.include_router(bank_router)
api_router.include_router(portal_chat_router)
api_router.include_router(portal_chat_public_router)
api_router.include_router(automation_router)


@api_router.get("/")
async def root():
    return {"message": "Logistiki Pro API - Ψηφιακός Λογιστής"}


@api_router.get("/health")
async def health():
    """Unauthenticated liveness/readiness probe for Docker & load balancers."""
    try:
        await db.command("ping")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "autopilot": bool(getattr(app.state, "scheduler", None))}


@app.exception_handler(AIUnavailable)
async def _ai_unavailable(_, exc: AIUnavailable):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(api_router)
# Payment webhook & public status live στη ρίζα (χωρίς /api prefix από router — ήδη ενσωματωμένο)
app.include_router(payments_public_router)

def _cors_origins() -> list[str]:
    """Credentials + wildcard is rejected by browsers; default to the configured public URL."""
    raw = os.environ.get("CORS_ORIGINS") or os.environ.get("PUBLIC_URL") or os.environ.get("PREVIEW_ENDPOINT") or ""
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:3000", "http://localhost:8080"]


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def validate_environment() -> None:
    """Αποτυγχάνουμε στην εκκίνηση, όχι στο πρώτο login.

    Το docker-compose ορίζει JWT_SECRET="" όταν λείπει από το .env. Ο server ξεκινούσε
    τότε «υγιής» (το /api/health δεν αγγίζει JWT) και κάθε login επέστρεφε 500 με
    InvalidKeyError — η χειρότερη δυνατή εμπειρία για τον διαχειριστή.
    """
    secret = os.environ.get("JWT_SECRET") or ""
    if not secret.strip():
        raise RuntimeError(
            "Το JWT_SECRET δεν έχει οριστεί. Ορίστε το στο .env (τυχαία συμβολοσειρά "
            "τουλάχιστον 32 χαρακτήρων) — χωρίς αυτό καμία σύνδεση δεν λειτουργεί."
        )
    if len(secret) < 32:
        logger.warning(
            "Το JWT_SECRET έχει μόνο %d χαρακτήρες. Συνιστώνται τουλάχιστον 32 για HS256.",
            len(secret),
        )
    if not (os.environ.get("SECRETS_KEY") or "").strip():
        logger.warning(
            "Το SECRETS_KEY δεν έχει οριστεί — τα αποθηκευμένα μυστικά κρυπτογραφούνται με "
            "κλειδί παραγόμενο από το JWT_SECRET. Αλλαγή του JWT_SECRET θα τα καταστήσει μη αναγνώσιμα."
        )
    if os.environ.get("STRIPE_SECRET_KEY") and not os.environ.get("STRIPE_WEBHOOK_SECRET"):
        logger.warning(
            "Το STRIPE_WEBHOOK_SECRET λείπει ενώ οι πληρωμές είναι ενεργές: κάθε webhook θα "
            "απορρίπτεται και οι εξοφλήσεις θα εντοπίζονται μόνο μέσω polling."
        )


validate_environment()


async def seed_admin():
    """First-run admin. A password is only forced from the environment when ADMIN_PASSWORD is
    explicitly set; otherwise a random one is generated once and printed to the log."""
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@logistiki.gr")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    existing = await db.users.find_one({"email": admin_email})
    if existing is None:
        import secrets
        from datetime import datetime, timezone
        generated = admin_password is None
        password = admin_password or secrets.token_urlsafe(12)
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(password),
            "name": "Διαχειριστής",
            "role": "admin",
            "firm_id": LEGACY_FIRM_ID,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        if generated:
            logger.warning("Seeded admin user %s with a generated password: %s  (set ADMIN_PASSWORD to control it)", admin_email, password)
        else:
            logger.info("Seeded admin user: %s", admin_email)
    elif admin_password and not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_password)}})


@app.on_event("startup")
async def startup_event():
    # Existing deployments were single-tenant. Keep their records together
    # under a stable tenant rather than exposing unscoped legacy documents.
    # Backfill the tenant marker for every persisted firm-owned collection.
    for collection in (
        db.users, db.clients, db.invoices, db.expenses, db.payroll_employees,
        db.payroll_runs, db.cases, db.grammatia, db.paravola, db.hearings,
        db.settings, db.trust_accounts, db.trust_transactions, db.kyc,
        db.signatures, db.payment_transactions, db.client_portal_tokens,
        db.audit_logs, db.case_tracker_events, db.bank_transactions,
        db.portal_messages, db.copilot_messages, db.drafting_templates,
        db.drafting_clauses,
    ):
        await collection.update_many(
            {"firm_id": {"$exists": False}},
            {"$set": {"firm_id": LEGACY_FIRM_ID}},
        )
    await db.users.create_index("email", unique=True)
    await db.users.create_index("firm_id")
    await db.clients.create_index("firm_id")
    await db.invoices.create_index("firm_id")
    for collection in (
        db.expenses, db.payroll_employees, db.payroll_runs, db.cases,
        db.grammatia, db.paravola, db.hearings, db.settings,
        db.trust_accounts, db.trust_transactions, db.kyc, db.signatures,
        db.payment_transactions, db.client_portal_tokens, db.audit_logs,
        db.case_tracker_events, db.bank_transactions, db.portal_messages,
        db.copilot_messages, db.drafting_templates, db.drafting_clauses,
    ):
        await collection.create_index("firm_id")
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=3600)
    await db.login_attempts.create_index("identifier")
    # The original unique index on external_id alone made two firms collide on identical
    # bank lines; scope it by firm.
    try:
        await db.bank_transactions.drop_index("external_id_1")
    except Exception:
        pass
    await db.bank_transactions.create_index([("firm_id", 1), ("external_id", 1)], unique=True, sparse=True)
    await db.notifications.create_index([("firm_id", 1), ("resolved_at", 1), ("created_at", -1)])
    await db.notifications.create_index([("firm_id", 1), ("dedupe_key", 1)])
    await db.automation_runs.create_index([("firm_id", 1), ("job", 1), ("started_at", -1)])
    await db.vat_periods.create_index([("firm_id", 1), ("period", 1)], unique=True)
    await db.inbox_processed.create_index([("firm_id", 1), ("key", 1)], unique=True)
    # Ένα παραστατικό ΑΑΔΕ γίνεται έξοδο μία φορά, ακόμη κι αν το RequestDocs το επιστρέψει ξανά.
    await db.expenses.create_index([("firm_id", 1), ("mydata_mark", 1)], unique=True, sparse=True)
    await db.outbox.create_index([("status", 1), ("next_attempt_at", 1)])
    await db.outbox.create_index([("firm_id", 1), ("dedupe_key", 1)])
    await db.case_tracker_events.create_index([("fetched_at", -1)])
    await db.portal_messages.create_index([("client_id", 1), ("created_at", 1)])
    await seed_admin()
    try:
        init_storage()
        logger.info("Object storage initialized")
    except Exception as e:
        logger.error(f"Storage init failed: {e}")

    # Ο scheduler ανήκει πλέον στο worker.py (βλ. docker-compose: υπηρεσία "worker").
    # Μένει εδώ μόνο για single-process εγκαταστάσεις που το ζητούν ρητά.
    if os.environ.get("AUTOPILOT_SCHEDULER", "0").lower() not in ("0", "false", "no"):
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from automation import run_autopilot_cycle
            interval = int(os.environ.get("AUTOPILOT_INTERVAL_MINUTES", "15"))
            scheduler = AsyncIOScheduler(timezone="Europe/Athens")
            scheduler.add_job(run_all_tracked_checks, "interval", hours=12, id="case_tracker", replace_existing=True)
            scheduler.add_job(run_autopilot_cycle, "interval", minutes=interval, id="autopilot",
                              replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=300)
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("Scheduler started: case tracker every 12h, Autopilot every %s min", interval)
        except Exception as e:
            logger.error(f"Scheduler start failed: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    try:
        if hasattr(app.state, "scheduler"):
            app.state.scheduler.shutdown(wait=False)
    except Exception:
        pass
    db.client.close()
