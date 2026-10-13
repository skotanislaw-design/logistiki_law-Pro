from fastapi import FastAPI, APIRouter
from bson.errors import InvalidId
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import logging
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from db import db
from auth import hash_password, verify_password, cookie_samesite, COOKIE_SAMESITE_VALUES
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
from routes_audit import router as audit_router
from ai_runtime import AIUnavailable
from numbering import SeriesBusy
from json_guard import RejectNonFiniteJson
from csrf_guard import CsrfGuard
from invoice_services import flag_unchecked_bank_suggestions  # noqa: F401 — επίσης: τρέχει στην εκκίνηση
from invoice_services import ensure_invoice_number_index, INVOICE_NUMBER_INDEX  # noqa: F401 — επίσης: τρέχει στην εκκίνηση

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
api_router.include_router(audit_router)


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


@app.exception_handler(SeriesBusy)
async def _series_busy(_, exc: SeriesBusy):
    """Ο μετρητής αρίθμησης ήταν συνεχώς απασχολημένος (50 γύροι) και δεν δόθηκε αριθμός — ούτε χάθηκε κάποιος. Είναι
    προσωρινή κατάσταση που διορθώνεται με νέα προσπάθεια: 503 με `Retry-After`, όχι 500 «κάτι χάλασε». Καλύπτει τις
    υποθέσεις, τα γραμμάτια και τα παράβολα (η έκδοση τιμολογίου το μετατρέπει μόνη της σε σφάλμα εργασίας)."""
    from fastapi.responses import JSONResponse
    from numbering import BUSY_MESSAGE
    return JSONResponse(status_code=503, headers={"Retry-After": "2"}, content={"detail": BUSY_MESSAGE})


@app.exception_handler(InvalidId)
async def _invalid_id(_, exc: InvalidId):
    """Αναγνωριστικό που δεν είναι έγκυρο ObjectId (`/api/hearings/abc`, κομμένος σύνδεσμος) δεν αντιστοιχεί σε καμία εγγραφή:
    404 «δεν βρέθηκε», όπως για ένα έγκυρο αναγνωριστικό που δεν υπάρχει — όχι 500 «κάτι χάλασε» με ίχνος στο log. Ισχύει για
    όλες τις διαδρομές (πάνω από εκατό σημεία κάνουν `ObjectId(<κείμενο του αιτήματος>)`)."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404, content={"detail": "Η εγγραφή δεν βρέθηκε."})


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc: RequestValidationError):
    """Ένας αριθμός γραμμένος ως κείμενο (`"NaN"`, `"Infinity"`, `"1e999"`) ξεφεύγει από τον φραγμό JSON, που βλέπει μόνο
    τις σταθερές του JSON. Τα μοντέλα τον απορρίπτουν (`allow_inf_nan=False`)· εδώ παίρνει το ίδιο 400 και το ίδιο
    μήνυμα με τις σταθερές, ώστε ο χρήστης να βλέπει πάντα την ίδια εξήγηση. Κάθε άλλο σφάλμα μένει όπως το δίνει η FastAPI."""
    if any(error.get("type") == "finite_number" for error in exc.errors()):
        from fastapi.responses import JSONResponse
        from json_guard import MESSAGE
        return JSONResponse(status_code=400, content={"detail": MESSAGE})
    return await request_validation_exception_handler(request, exc)


app.include_router(api_router)
# Payment webhook & public status live στη ρίζα (χωρίς /api prefix από router — ήδη ενσωματωμένο)
app.include_router(payments_public_router)

def _cors_origins() -> list[str]:
    """Credentials + wildcard is rejected by browsers; default to the configured public URL.

    Η ίδια λίστα ορίζει και τις επιτρεπτές διευθύνσεις επιστροφής της πληρωμής (http_utils.allowed_origins)."""
    from http_utils import allowed_origins
    return allowed_origins()


@app.middleware("http")
async def _audit_mutations(request, call_next):
    """Κάθε αίτημα που αλλάζει κατάσταση αφήνει ίχνος — χωρίς εξαιρέσεις που ξεχνιούνται."""
    import time
    from audit import log_request

    started = time.monotonic()
    response = await call_next(request)
    try:
        await log_request(request, response, int((time.monotonic() - started) * 1000))
    except Exception:
        logging.getLogger("audit").exception("Το middleware ελεγκτικού ίχνους απέτυχε")
    return response


# Σειρά (από έξω προς τα μέσα): CORS → CsrfGuard → RejectNonFiniteJson → ίχνος ελέγχου → εφαρμογή. Τα δύο φίλτρα είναι εσωτερικότερα
# από το CORS (ώστε και η απόρριψη να φέρει τις κεφαλίδες του) και εξωτερικότερα από το ίχνος ελέγχου. Ο έλεγχος προέλευσης
# προηγείται του φραγμού JSON: ένα ξένο αίτημα απορρίπτεται πριν διαβαστεί (και αναλυθεί) το σώμα του.
app.add_middleware(RejectNonFiniteJson)
app.add_middleware(CsrfGuard)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    # Σε αίτημα από άλλη προέλευση ο browser κρύβει κάθε κεφαλίδα που δεν αναφέρεται εδώ. Το frontend διαβάζει την
    # `X-Closed-Truncated` για να δείξει ότι η λίστα τιμολογίων/τράπεζας δεν περιλαμβάνει παλαιότερα εξοφλημένα — χωρίς
    # αυτό (dev server, frontend και API σε διαφορετικά domains) η ειδοποίηση δεν θα εμφανιζόταν ποτέ.
    expose_headers=["X-Closed-Truncated", "X-List-Truncated"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Οι τιμές-παραδείγματα του `.env.example` («change-me-to-a-long-random-string») είναι δημόσιες: όποιος έχει διαβάσει το αποθετήριο
# τις γνωρίζει. Μια εγκατάσταση που τις κρατά δεν έχει στην πράξη μυστικό.
PLACEHOLDER_VALUE = re.compile(r"change[-_ ]?me", re.IGNORECASE)


def validate_environment() -> None:
    """Αποτυγχάνουμε στην εκκίνηση, όχι στο πρώτο login.

    Το docker-compose ορίζει JWT_SECRET="" όταν λείπει από το .env. Ο server ξεκινούσε
    τότε «υγιής» (το /api/health δεν αγγίζει JWT) και κάθε login επέστρεφε 500 με
    InvalidKeyError — η χειρότερη δυνατή εμπειρία για τον διαχειριστή.

    Το ίδιο ισχύει και για την τιμή-παράδειγμα του `.env.example`: με γνωστό JWT_SECRET ο καθένας υπογράφει δικό του token
    διαχειριστή· με γνωστό κωδικό MongoDB διαβάζει όλη τη βάση. Και στις δύο περιπτώσεις η εφαρμογή δεν ξεκινά.
    """
    secret = os.environ.get("JWT_SECRET") or ""
    if not secret.strip():
        raise RuntimeError(
            "Το JWT_SECRET δεν έχει οριστεί. Ορίστε το στο .env (τυχαία συμβολοσειρά "
            "τουλάχιστον 32 χαρακτήρων) — χωρίς αυτό καμία σύνδεση δεν λειτουργεί."
        )
    if PLACEHOLDER_VALUE.search(secret):
        raise RuntimeError(
            "Το JWT_SECRET έχει ακόμη την τιμή-παράδειγμα του .env.example («change-me…»), που είναι δημόσια: οποιοσδήποτε "
            "θα μπορούσε να υπογράψει δικό του token διαχειριστή. Ορίστε τυχαία συμβολοσειρά τουλάχιστον 32 χαρακτήρων, π.χ. "
            "με:  python -c \"import secrets; print(secrets.token_urlsafe(48))\"  (αν δεν είχε οριστεί SECRETS_KEY, οι "
            "συνδέσεις θα λήξουν και τα αποθηκευμένα μυστικά SMTP/IMAP/ΑΑΔΕ θα ξανακαταχωρηθούν από τις Ρυθμίσεις)."
        )
    if PLACEHOLDER_VALUE.search(os.environ.get("MONGO_URL") or ""):
        raise RuntimeError(
            "Ο κωδικός της MongoDB (MONGO_PASSWORD) έχει ακόμη την τιμή-παράδειγμα του .env.example («change-me…»), που είναι "
            "δημόσια: όποιος φτάνει στη βάση θα διάβαζε όλο το αρχείο του γραφείου. Ορίστε μακρύ τυχαίο κωδικό. Σε υπάρχουσα "
            "εγκατάσταση αλλάξτε πρώτα τον κωδικό στη MongoDB (db.changeUserPassword) και μετά το .env."
        )
    requested_samesite = (os.environ.get("COOKIE_SAMESITE") or "").strip().lower()
    if requested_samesite and requested_samesite not in COOKIE_SAMESITE_VALUES:
        logger.warning("Η τιμή COOKIE_SAMESITE=%r δεν είναι έγκυρη (lax | strict | none) — χρησιμοποιείται lax.", requested_samesite[:30])
    if cookie_samesite() == "none":
        logger.warning(
            "COOKIE_SAMESITE=none: τα cookies σύνδεσης στέλνονται και σε αιτήματα από άλλους ιστότοπους. Η προστασία από "
            "CSRF στηρίζεται τότε μόνο στον έλεγχο προέλευσης — το CORS_ORIGINS πρέπει να περιέχει ΜΟΝΟ την προέλευση της εφαρμογής."
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
            "Το STRIPE_WEBHOOK_SECRET λείπει ενώ οι πληρωμές είναι ενεργές: το webhook του Stripe απενεργοποιείται "
            "(απάντηση 503, για λόγους ασφαλείας — χωρίς μυστικό δεν μπορεί να ελεγχθεί η υπογραφή). Οι εξοφλήσεις "
            "εντοπίζονται τότε από τη σελίδα επιτυχίας του πελάτη και από τον Autopilot, που ρωτά το Stripe κάθε ~30 λεπτά "
            "(εργασία card_payments)· ορίστε το μυστικό για άμεση καταγραφή."
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


EXPENSE_MARK_INDEX = "uniq_firm_mydata_mark"


async def ensure_expense_mark_index(collection=None) -> None:
    """Μοναδικότητα (firm_id, mydata_mark) μόνο για έξοδα που ΕΧΟΥΝ MARK.

    Το προηγούμενο index ήταν `unique + sparse` πάνω σε σύνθετο κλειδί. Στη MongoDB ένα
    σύνθετο sparse index καταχωρεί κάθε έγγραφο που έχει *έστω ένα* από τα πεδία — εδώ το
    `firm_id` υπάρχει πάντα — άρα το έξοδο χωρίς MARK μετρούσε ως (firm, null) και το δεύτερο
    χειροκίνητο έξοδο του ίδιου γραφείου αποτύγχανε με HTTP 500. Το partial index
    καταχωρεί μόνο τα έγγραφα με MARK τύπου string.

    Δεν ρίχνει την εκκίνηση: αν η δημιουργία αποτύχει (π.χ. υπάρχοντα διπλά MARK στα
    δεδομένα) καταγράφεται σφάλμα. Η μοναδικότητα ελέγχεται και από τον κώδικα εφαρμογής
    (`routes_expenses`), οπότε το index είναι η δεύτερη γραμμή άμυνας, όχι η μοναδική.
    Το `collection` δίνεται μόνο στα τεστ.
    """
    expenses = collection if collection is not None else db.expenses
    wanted_key = [("firm_id", 1), ("mydata_mark", 1)]
    try:
        for name, info in (await expenses.index_information()).items():
            if list(info.get("key", [])) == wanted_key and not info.get("partialFilterExpression"):
                await expenses.drop_index(name)
                logger.info("expenses: αντικαταστάθηκε το index %s (sparse) από partial", name)
        await expenses.create_index(
            wanted_key,
            name=EXPENSE_MARK_INDEX,
            unique=True,
            partialFilterExpression={"mydata_mark": {"$type": "string"}},
        )
    except Exception as e:  # noqa: BLE001 — η εκκίνηση της εφαρμογής δεν πρέπει να εξαρτάται από αυτό
        logger.error("expenses: αποτυχία δημιουργίας του unique index για mydata_mark: %s", e)


TRUST_ACCOUNT_INDEX = "uniq_firm_client_trust_account"


async def ensure_trust_account_index() -> None:
    """Ένας πελατολογικός λογαριασμός ανά πελάτη και γραφείο.

    Χωρίς unique index, δύο πρώτες κινήσεις του ίδιου πελάτη την ίδια στιγμή δημιουργούσαν δύο
    λογαριασμούς και το υπόλοιπο μοιραζόταν ανάμεσά τους. Αν υπάρχουν ήδη διπλότυποι, η
    δημιουργία αποτυγχάνει: καταγράφεται σφάλμα και χρειάζεται χειροκίνητη συγχώνευση.
    """
    try:
        await db.trust_accounts.create_index(
            [("firm_id", 1), ("client_id", 1)], name=TRUST_ACCOUNT_INDEX, unique=True)
    except Exception as e:  # noqa: BLE001 — η εκκίνηση δεν πρέπει να εξαρτάται από αυτό
        logger.error("trust_accounts: αποτυχία δημιουργίας του unique index (firm_id, client_id) — "
                     "ελέγξτε για διπλούς λογαριασμούς του ίδιου πελάτη: %s", e)


SETTINGS_INDEX = "uniq_firm_settings"


async def ensure_settings_index(collection=None) -> None:
    """Ένα έγγραφο ρυθμίσεων ανά γραφείο.

    Η εφαρμογή διαβάζει τις ρυθμίσεις με `find_one({"firm_id": …})`, δηλαδή το πρώτο έγγραφο που θα βρει. Δύο
    έγγραφα για το ίδιο γραφείο (δύο ταυτόχρονα πρώτα αιτήματα, ή μια επαναφορά που έγραψε δίπλα στο προεπιλεγμένο)
    σημαίνουν ότι ό,τι αποθηκεύεται δεν φαίνεται ποτέ. Το μοναδικό ευρετήριο το αποκλείει.

    Το απλό ευρετήριο `firm_id_1` αντικαθίσταται (η MongoDB δεν δέχεται δύο ευρετήρια με το ίδιο κλειδί και άλλες
    επιλογές). Αν υπάρχουν ήδη διπλά έγγραφα η δημιουργία αποτυγχάνει: καταγράφεται σφάλμα, το απλό ευρετήριο
    ξαναδημιουργείται και χρειάζεται χειροκίνητη συγχώνευση (ή μια επαναφορά αντιγράφου, που τα συγχωνεύει).
    Το `collection` δίνεται μόνο στα τεστ.
    """
    settings = collection if collection is not None else db.settings
    try:
        for name, info in (await settings.index_information()).items():
            if list(info.get("key", [])) == [("firm_id", 1)] and not info.get("unique"):
                await settings.drop_index(name)
        await settings.create_index("firm_id", name=SETTINGS_INDEX, unique=True)
    except Exception as e:  # noqa: BLE001 — η εκκίνηση της εφαρμογής δεν πρέπει να εξαρτάται από αυτό
        logger.error("settings: αποτυχία δημιουργίας του unique index (firm_id) — ελέγξτε για διπλά έγγραφα "
                     "ρυθμίσεων του ίδιου γραφείου: %s", e)
        try:
            await settings.create_index("firm_id")
        except Exception:  # noqa: BLE001
            pass


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
        db.case_tracker_events, db.bank_transactions,
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
        db.grammatia, db.paravola, db.hearings,
        db.trust_accounts, db.trust_transactions, db.kyc, db.signatures,
        db.payment_transactions, db.client_portal_tokens,
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
    # Ελεγκτικό ίχνος: append-only, εξαιρείται σκόπιμα από τον βρόχο backfill παραπάνω.
    await db.audit_logs.create_index([("firm_id", 1), ("created_at", -1)])
    await db.audit_logs.create_index([("firm_id", 1), ("entity", 1), ("entity_id", 1)])
    await db.audit_logs.create_index([("firm_id", 1), ("user_email", 1), ("created_at", -1)])
    await db.notifications.create_index([("firm_id", 1), ("resolved_at", 1), ("created_at", -1)])
    await db.notifications.create_index([("firm_id", 1), ("dedupe_key", 1)])
    await db.automation_runs.create_index([("firm_id", 1), ("job", 1), ("started_at", -1)])
    await db.vat_periods.create_index([("firm_id", 1), ("period", 1)], unique=True)
    await db.inbox_processed.create_index([("firm_id", 1), ("key", 1)], unique=True)
    # Ένα παραστατικό ΑΑΔΕ γίνεται έξοδο μία φορά, ακόμη κι αν το RequestDocs το επιστρέψει ξανά.
    await ensure_expense_mark_index()
    await ensure_trust_account_index()
    await ensure_settings_index()
    await ensure_invoice_number_index()
    await flag_unchecked_bank_suggestions()
    await db.outbox.create_index([("status", 1), ("next_attempt_at", 1)])
    await db.outbox.create_index([("firm_id", 1), ("dedupe_key", 1)])
    await db.case_tracker_events.create_index([("fetched_at", -1)])
    await db.portal_messages.create_index([("client_id", 1), ("created_at", 1)])
    # Κάθε δημόσιο αίτημα του portal ψάχνει με το token. Χωρίς index ήταν σάρωση όλης
    # της collection — δηλαδή ο φθηνότερος τρόπος να επιβαρύνει κάποιος τη βάση από έξω.
    # Unique: δύο ίδια tokens θα σήμαιναν δύο πελάτες πίσω από τον ίδιο σύνδεσμο.
    await db.client_portal_tokens.create_index("token", unique=True, sparse=True)
    # Οι μετρητές ρυθμού καθαρίζονται μόνοι τους στη λήξη του παραθύρου.
    await db.rate_limits.create_index("expires_at", expireAfterSeconds=0)
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
