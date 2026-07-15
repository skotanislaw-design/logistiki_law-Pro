from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from db import BaseDocument, PyObjectId

VAT_RATES = [24, 13, 6, 0]
EXPENSE_CATEGORIES = [
    "Ενοίκιο", "Λογαριασμοί ΔΕΚΟ", "Προμήθειες γραφείου", "Δικηγορικά έξοδα",
    "Αμοιβές τρίτων", "Μεταφορικά", "Τηλεπικοινωνίες", "Ασφάλιστρα",
    "Συνδρομές / Λογισμικό", "Φιλοξενία / Εστίαση", "Λοιπά",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class User(BaseDocument):
    email: str
    password_hash: str
    name: str
    role: Literal["admin", "accountant", "lawyer"] = "lawyer"
    created_at: str = Field(default_factory=now_iso)


class UserPublic(BaseModel):
    id: str
    email: str
    name: str
    role: str
    created_at: str


class Client(BaseDocument):
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
    mydata_status: Literal["not_sent", "pending", "synced", "failed"] = "not_sent"
    mydata_mark: Optional[str] = None
    mydata_uid: Optional[str] = None
    payment_method: str = "Τραπεζική κατάθεση"
    notes: str = ""
    source: Literal["manual", "voice"] = "manual"
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    issued_at: Optional[str] = None
    email_sent_at: Optional[str] = None
    email_sent_to: Optional[str] = None


class Expense(BaseDocument):
    vendor: str
    date: str
    amount: float
    vat_amount: float = 0
    vat_rate: int = 24
    category: str = "Λοιπά"
    description: str = ""
    receipt_image_url: Optional[str] = None
    source: Literal["manual", "photo_ocr"] = "manual"
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class PayrollEmployee(BaseDocument):
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
    number: str = ""
    title: str
    case_type: str = "Αστικό"
    client_id: str = ""
    client_name: str = ""
    court: str = ""
    opposing_party: str = ""
    status: Literal["active", "pending", "completed", "archived"] = "active"
    next_action: str = ""
    next_action_date: str = ""
    notes: List[dict] = Field(default_factory=list)
    history: List[dict] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Grammatio(BaseDocument):
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


class Settings(BaseDocument):
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
    mydata_connected: bool = False
    updated_at: str = Field(default_factory=now_iso)
