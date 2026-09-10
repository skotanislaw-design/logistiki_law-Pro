from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from db import BaseDocument, PyObjectId
from tenant import LEGACY_FIRM_ID

VAT_RATES = [24, 13, 6, 0]
EXPENSE_CATEGORIES = [
    "Ενοίκιο", "Λογαριασμοί ΔΕΚΟ", "Προμήθειες γραφείου", "Δικηγορικά έξοδα",
    "Αμοιβές τρίτων", "Μεταφορικά", "Τηλεπικοινωνίες", "Ασφάλιστρα",
    "Συνδρομές / Λογισμικό", "Φιλοξενία / Εστίαση", "Λοιπά",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class User(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    email: str
    password_hash: str
    name: str
    role: Literal["admin", "accountant", "lawyer"] = "lawyer"
    # --- Δύο παράγοντες ταυτοποίησης (TOTP) ---
    mfa_enabled: bool = False
    mfa_secret: str = ""                 # κρυπτογραφημένο (crypto.encrypt)
    mfa_pending_secret: str = ""         # κρυπτογραφημένο, μέχρι την επιβεβαίωση
    mfa_backup_codes: List[str] = Field(default_factory=list)  # μόνο bcrypt hashes
    mfa_last_counter: Optional[int] = None   # anti-replay για το ίδιο χρονικό βήμα
    mfa_enabled_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: str
    firm_id: str = LEGACY_FIRM_ID
    mfa_enabled: bool = False


class Client(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    name: str
    afm: str = ""
    doy: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    client_type: Literal["individual", "company"] = "individual"
    created_at: str = Field(default_factory=now_iso)
    created_by: Optional[str] = None


class InvoiceLineItem(BaseModel):
    description: str
    quantity: float = 1
    unit_price: float
    vat_rate: int = 24


class Invoice(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    number: str = ""
    client_id: str
    client_name: str = ""
    date: str
    line_items: List[InvoiceLineItem]
    subtotal: float = 0
    vat_amount: float = 0
    total: float = 0
    vat_breakdown: dict = Field(default_factory=dict)
    status: Literal["draft", "issued", "paid", "cancelled"] = "draft"
    mydata_status: Literal["not_sent", "pending", "synced", "failed", "cancelled"] = "not_sent"
    # Είδος παραστατικού ΑΑΔΕ· κενό = αυτόματη επιλογή βάσει πελάτη (βλ. mydata_client)
    invoice_type: str = ""
    mydata_mark: Optional[str] = None
    mydata_uid: Optional[str] = None
    mydata_error: Optional[str] = None
    payment_method: str = "Τραπεζική κατάθεση"
    notes: str = ""
    source: Literal["manual", "voice", "copilot", "autopilot"] = "manual"
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    issued_at: Optional[str] = None
    paid_at: Optional[str] = None
    email_sent_at: Optional[str] = None
    email_sent_to: Optional[str] = None
    reminder_sent_at: Optional[str] = None
    reminder_count: int = 0
    mydata_attempts: int = 0
    mydata_synced_at: Optional[str] = None
    mydata_cancellation_mark: Optional[str] = None
    mydata_cancelled_at: Optional[str] = None
    # Πιστωτικό: το τιμολόγιο που ακυρώνει/διορθώνει και το MARK του
    credit_note_for: Optional[str] = None
    correlated_mark: Optional[str] = None
    credit_note_id: Optional[str] = None   # στο αρχικό: ποιο πιστωτικό το κάλυψε
    cancelled_at: Optional[str] = None
    cancellation_reason: str = ""
    withholding_amount: Optional[float] = None  # None = υπολογισμός από τις Ρυθμίσεις
    paid_via: Optional[str] = None  # manual / bank_auto / bank_manual / stripe / trust


class Expense(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    vendor: str
    date: str
    amount: float
    vat_amount: float = 0
    vat_rate: int = 24
    category: str = "Λοιπά"
    description: str = ""
    receipt_image_url: Optional[str] = None
    source: Literal["manual", "photo_ocr", "email_ocr", "autopilot", "mydata"] = "manual"
    review_status: Literal["ok", "needs_review"] = "ok"
    # Παραστατικό που κατέβηκε από το myDATA (RequestDocs)
    mydata_mark: Optional[str] = None
    supplier_afm: str = ""
    mydata_invoice_type: str = ""
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class PayrollEmployee(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    name: str
    afm: str = ""
    amka: str = ""
    position: str = "Δικηγόρος"
    gross_salary: float
    dependents: int = 0
    hire_date: str = ""
    active: bool = True
    created_at: str = Field(default_factory=now_iso)


class PayrollRun(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    employee_id: str
    employee_name: str = ""
    period: str
    gross_salary: float
    employee_efka: float
    employer_efka: float
    taxable_income: float
    income_tax: float
    tax_credit: float
    net_salary: float
    employer_total_cost: float
    created_at: str = Field(default_factory=now_iso)


class Case(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    number: str = ""
    title: str
    case_type: str = "Αστικό"
    client_id: str = ""
    client_name: str = ""
    court: str = ""
    opposing_party: str = ""
    gak: str = ""
    eak: str = ""
    gak_year: str = ""
    status: Literal["active", "pending", "completed", "archived"] = "active"
    next_action: str = ""
    next_action_date: str = ""
    notes: List[dict] = Field(default_factory=list)
    history: List[dict] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Grammatio(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    number: str = ""
    case_id: str = ""
    case_title: str = ""
    client_id: str = ""
    client_name: str = ""
    court_type: str
    procedure: str = ""
    reference_amount: float
    contributions_amount: float = 0
    vat_amount: float = 0
    total: float = 0
    status: Literal["draft", "issued", "cancelled"] = "draft"
    portal_ref: str = ""
    notes: str = ""
    issued_at: Optional[str] = None
    invoice_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Paravolo(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    number: str = ""
    case_id: str = ""
    case_title: str = ""
    category: str
    description: str = ""
    amount: float
    eparavolo_code: str = ""
    status: Literal["draft", "issued", "paid", "cancelled"] = "draft"
    issued_at: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Hearing(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    case_id: str = ""
    case_title: str = ""
    court: str
    room: str = ""
    date: str
    time: str = ""
    pinakio_number: int
    current_number: int = 0
    status: Literal["scheduled", "in_progress", "interrupted", "completed", "postponed"] = "scheduled"
    notify_threshold: int = 5
    source_url: str = ""
    events: List[dict] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Settings(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    company_name: str = "Δικηγορική Εταιρεία"
    afm: str = ""
    doy: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    iban: str = ""
    bank_name: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = ""
    vat_period: Literal["monthly", "quarterly"] = "monthly"
    aade_user_id: str = ""
    aade_subscription_key: str = ""
    aade_environment: Literal["sandbox", "production"] = "sandbox"
    mydata_connected: bool = False
    # Κωδικοί χαρακτηρισμού εσόδων (πίνακες ΑΑΔΕ — επαληθεύστε στο sandbox)
    income_classification_type: str = "E3_561_001"
    income_classification_category: str = "category1_3"
    # Παρακράτηση φόρου αμοιβών ελεύθερων επαγγελματιών
    withholding_enabled: bool = False
    withholding_rate: float = 0.20
    withholding_min_net: float = 300.0
    withholding_category: str = "2"
    # Αυτόματη λήψη εξόδων από το myDATA (παραστατικά προμηθευτών)
    mydata_expense_sync: bool = True
    mydata_expense_last_mark: str = "0"
    # Κωδικοί εντύπου Φ2 (αλλάζουν με αποφάσεις ΑΑΔΕ) — {γραμμή: κωδικός}
    vat_form_codes: dict = Field(default_factory=dict)
    # Open banking (PSD2) — GoCardless Bank Account Data
    openbanking_enabled: bool = False
    gocardless_secret_id: str = ""
    gocardless_secret_key: str = ""      # κρυπτογραφημένο
    gocardless_institution_id: str = ""
    gocardless_institution_name: str = ""
    gocardless_requisition_id: str = ""
    gocardless_account_ids: List[str] = Field(default_factory=list)
    gocardless_access_valid_until: str = ""
    bank_sync_last_date: str = ""
    onboarding_completed: bool = False
    public_url: str = ""
    # ---- Autopilot (zero-touch accounting) ----
    autopilot_enabled: bool = True
    auto_email_on_issue: bool = True          # στέλνει PDF στον πελάτη μόλις εκδοθεί
    auto_reminders: bool = True               # αυτόματες υπενθυμίσεις ανείσπρακτων
    reminder_after_days: int = 30             # πρώτη υπενθύμιση Χ ημέρες μετά την έκδοση
    reminder_interval_days: int = 15          # επόμενες υπενθυμίσεις κάθε Χ ημέρες
    reminder_max: int = 3                     # μέγιστος αριθμός υπενθυμίσεων ανά τιμολόγιο
    auto_mydata_retry: bool = True            # επανάληψη αποτυχημένων/εκκρεμών διαβιβάσεων
    auto_reconcile_min_confidence: int = 90   # auto-επιβεβαίωση τραπεζικών matches ≥ Χ% (0 = off)
    stale_draft_days: int = 7                 # ειδοποίηση για πρόχειρα που ξεχάστηκαν
    deadline_alert_days: List[int] = Field(default_factory=lambda: [7, 1])
    digest_frequency: Literal["daily", "weekly", "off"] = "weekly"
    digest_email: str = ""                    # κενό = smtp_user
    digest_hour: int = 7                      # τοπική ώρα Αθήνας
    # Εισερχόμενα παραστατικά από email (IMAP) → έξοδα με OCR
    imap_enabled: bool = False
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    updated_at: str = Field(default_factory=now_iso)


class TrustAccount(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Ξεχωριστός λογαριασμός πελάτη — Ν.4194/2013 άρθρο 32.
    Παρακολουθεί χρήματα που ανήκουν στον πελάτη και τηρούνται από τη δικηγορική εταιρεία
    (προκαταβολές, εγγυοδοσίες, εισπραχθέντα ποσά από αντιδίκους κτλ)."""
    client_id: str
    client_name: str = ""
    balance: float = 0.0
    total_deposits: float = 0.0
    total_withdrawals: float = 0.0
    notes: str = ""
    created_at: str = Field(default_factory=now_iso)


class TrustTransaction(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    account_id: str
    client_id: str = ""
    client_name: str = ""
    type: Literal["deposit", "withdrawal", "apply_to_invoice", "refund"]
    amount: float
    balance_after: float = 0.0
    date: str
    description: str = ""
    reference: str = ""  # π.χ. αριθμός επιταγής, IBAN, κτλ
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    case_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class KycRecord(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """KYC/AML — Ν.4557/2018. Ταυτοποίηση πελάτη + Risk Assessment."""
    client_id: str
    client_name: str = ""
    id_type: Literal["passport", "id_card", "residence_permit"] = "id_card"
    id_number: str = ""
    id_issue_date: str = ""
    id_issuer: str = ""
    birth_date: str = ""
    nationality: str = "Ελληνική"
    profession: str = ""
    pep: bool = False  # Politically Exposed Person
    high_risk_country: bool = False
    source_of_funds: str = ""
    purpose_of_business: str = ""
    risk_score: int = 0  # 0-10
    risk_level: Literal["low", "medium", "high"] = "low"
    verified_at: Optional[str] = None
    verified_by: Optional[str] = None
    notes: str = ""
    documents: List[dict] = Field(default_factory=list)  # [{name, uploaded_at}]
    updated_at: str = Field(default_factory=now_iso)
    created_at: str = Field(default_factory=now_iso)


class ESignature(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Ψηφιακή υπογραφή (Ν.4727/2020 απλή ηλεκτρονική υπογραφή)."""
    document_id: str = ""
    document_title: str = ""
    signer_name: str
    signer_email: str = ""
    signer_role: Literal["client", "lawyer", "witness"] = "client"
    signature_data: str  # base64 image
    # Υπολογίζεται από τον server κατά την καταχώρηση (routes_signatures) — ο client δεν το στέλνει.
    signature_hash: str = ""  # SHA-256 hash για ακεραιότητα
    ip_address: str = ""
    user_agent: str = ""
    signed_at: str = Field(default_factory=now_iso)


class PaymentTransaction(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    session_id: str
    invoice_id: str = ""
    invoice_number: str = ""
    client_id: str = ""
    client_name: str = ""
    amount: float
    currency: str = "eur"
    status: Literal["initiated", "completed", "failed", "expired", "refunded"] = "initiated"
    payment_status: Literal["pending", "paid", "failed", "expired", "refunded"] = "pending"
    stripe_payment_intent_id: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class ClientPortalToken(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Magic-link token για πρόσβαση πελάτη στο portal του."""
    token: str
    client_id: str
    client_name: str = ""
    expires_at: str
    created_at: str = Field(default_factory=now_iso)
    last_used_at: Optional[str] = None


class AuditLog(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    action: str
    entity: str = ""  # π.χ. invoice, case, trust_tx
    entity_id: str = ""
    user_email: str = ""
    details: str = ""
    created_at: str = Field(default_factory=now_iso)


class CaseTrackerEvent(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Καταγραφή αλλαγής σε ΣΟΛΩΝ/εξωτερικό portal για συγκεκριμένη υπόθεση."""
    case_id: str
    case_number: str = ""
    case_title: str = ""
    source: str = "solon"  # solon / e-justice / custom
    change_type: str = "content_change"  # content_change / new_document / status_change
    old_hash: str = ""
    new_hash: str = ""
    diff_summary: str = ""  # AI-generated summary of what changed
    fetched_at: str = Field(default_factory=now_iso)
    read: bool = False


class BankTransaction(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Τραπεζική κίνηση από CSV/OFX import."""
    date: str
    description: str
    amount: float  # + κατάθεση, − χρέωση
    balance_after: Optional[float] = None
    reference: str = ""
    bank: str = ""  # Piraeus / Alpha / Eurobank / NBG / Other
    account: str = ""
    external_id: str = ""  # για deduplication
    matched_invoice_id: Optional[str] = None
    matched_invoice_number: Optional[str] = None
    match_confidence: int = 0  # 0-100
    match_status: Literal["unmatched", "auto_matched", "auto_confirmed", "manual_matched", "ignored"] = "unmatched"
    review_notified: bool = False
    confirmed_at: Optional[str] = None
    imported_at: str = Field(default_factory=now_iso)
    imported_by: Optional[str] = None


class PortalMessage(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Ασφαλής συνομιλία δικηγόρου ↔ πελάτη μέσω portal."""
    client_id: str
    client_name: str = ""
    sender: Literal["lawyer", "client"]
    sender_name: str = ""
    text: str
    read_at: Optional[str] = None
    case_id: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Notification(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Στοιχείο «εισερχομένων» Autopilot: ενημέρωση ή εξαίρεση που χρειάζεται απόφαση."""
    type: str                                  # invoice_email_failed / reconcile_review / deadline / …
    level: Literal["info", "success", "warning", "critical"] = "info"
    title: str
    message: str = ""
    entity: str = ""                           # invoice / bank_tx / expense / vat_period
    entity_id: str = ""
    requires_action: bool = False
    actions: List[dict] = Field(default_factory=list)   # [{key,label,payload}]
    dedupe_key: str = ""
    read: bool = False
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class AutomationRun(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    job: str
    status: Literal["ok", "error", "skipped"] = "ok"
    stats: dict = Field(default_factory=dict)
    error: Optional[str] = None
    started_at: str = Field(default_factory=now_iso)
    finished_at: Optional[str] = None
    duration_ms: int = 0


class VatPeriod(BaseDocument):
    firm_id: str = LEGACY_FIRM_ID
    """Κλείσιμο περιόδου ΦΠΑ — υπολογίζεται αυτόματα από το Autopilot."""
    period: str                                # 2026-08 ή 2026-Q3
    period_start: str
    period_end: str
    due_date: str
    output_vat: float = 0
    input_vat: float = 0
    vat_due: float = 0
    income_net: float = 0
    expense_net: float = 0
    invoice_count: int = 0
    expense_count: int = 0
    vat_breakdown: dict = Field(default_factory=dict)
    status: Literal["computed", "submitted"] = "computed"
    submitted_at: Optional[str] = None
    computed_at: str = Field(default_factory=now_iso)
