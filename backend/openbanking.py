"""Open banking (PSD2) — οι τραπεζικές κινήσεις έρχονται μόνες τους.

Μέχρι τώρα ο χρήστης κατέβαζε CSV από το e-banking και το ανέβαζε. Εδώ οι κινήσεις
τραβιούνται μέσω PSD2 από τον πάροχο **GoCardless Bank Account Data** (πρώην
Nordigen), που καλύπτει τις ελληνικές τράπεζες με δωρεάν βαθμίδα.

Ροή σύνδεσης (μία φορά ανά λογαριασμό, ισχύει ~90 ημέρες):

    1. `list_institutions("GR")`      → ο χρήστης διαλέγει τράπεζα
    2. `create_requisition(...)`      → τον στέλνουμε στη σελίδα της τράπεζας
    3. ο χρήστης εγκρίνει και επιστρέφει στο `/settings`
    4. `get_requisition(...)`         → παίρνουμε τα account ids
    5. `fetch_transactions(account)`  → κάθε κύκλος του Autopilot

Το module είναι καθαρός HTTP client χωρίς πρόσβαση στη βάση, ώστε να δοκιμάζεται
με ψεύτικες αποκρίσεις· η αποθήκευση γίνεται στο `automation.job_bank_sync`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta

import httpx

from crypto import decrypt

logger = logging.getLogger(__name__)

BASE_URL = "https://bankaccountdata.gocardless.com/api/v2"
TOKEN_TTL_SAFETY = timedelta(minutes=5)


class OpenBankingError(Exception):
    """Σφάλμα επικοινωνίας ή ρύθμισης, με μήνυμα προς τον χρήστη."""


def openbanking_ready(settings: dict) -> bool:
    return bool((settings.get("gocardless_secret_id") or "").strip()
                and (settings.get("gocardless_secret_key") or "").strip())


def _credentials(settings: dict) -> tuple[str, str]:
    return ((settings.get("gocardless_secret_id") or "").strip(),
            decrypt(settings.get("gocardless_secret_key") or "").strip())


async def _request(method: str, path: str, *, token: str | None = None,
                   json: dict | None = None, params: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=45) as http:
            resp = await http.request(method, f"{BASE_URL}{path}", headers=headers, json=json, params=params)
    except httpx.RequestError as e:
        raise OpenBankingError(f"Αδυναμία επικοινωνίας με τον πάροχο open banking: {e}")
    if resp.status_code in (401, 403):
        raise OpenBankingError("Μη έγκυρα διαπιστευτήρια open banking. Ελέγξτε Secret ID & Secret Key στις Ρυθμίσεις.")
    if resp.status_code == 429:
        raise OpenBankingError("Υπέρβαση ορίου κλήσεων προς τον πάροχο. Θα γίνει νέα προσπάθεια αργότερα.")
    if resp.status_code >= 400:
        raise OpenBankingError(f"Σφάλμα open banking (HTTP {resp.status_code}): {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError:
        raise OpenBankingError("Μη αναγνώσιμη απόκριση από τον πάροχο open banking.")


async def get_token(settings: dict) -> str:
    """Access token. Ο caller μπορεί να το θυμάται μέχρι το `expires_at` που επιστρέφεται."""
    secret_id, secret_key = _credentials(settings)
    if not secret_id or not secret_key:
        raise OpenBankingError("Δεν έχουν καταχωρηθεί διαπιστευτήρια open banking.")
    data = await _request("POST", "/token/new/", json={"secret_id": secret_id, "secret_key": secret_key})
    token = data.get("access")
    if not token:
        raise OpenBankingError("Ο πάροχος δεν επέστρεψε access token.")
    return token


async def list_institutions(settings: dict, country: str = "GR") -> list[dict]:
    token = await get_token(settings)
    data = await _request("GET", "/institutions/", token=token, params={"country": country})
    items = data if isinstance(data, list) else data.get("results", [])
    return [{"id": i.get("id"), "name": i.get("name"), "logo": i.get("logo"),
             "days": i.get("transaction_total_days")} for i in items]


async def create_requisition(settings: dict, institution_id: str, redirect_url: str,
                             reference: str | None = None) -> dict:
    """Ξεκινά τη σύνδεση· επιστρέφει το link όπου πάει ο χρήστης για έγκριση."""
    token = await get_token(settings)
    payload = {"institution_id": institution_id, "redirect": redirect_url,
               "reference": reference or f"nomos-{datetime.now(timezone.utc).timestamp():.0f}"}
    data = await _request("POST", "/requisitions/", token=token, json=payload)
    if not data.get("link"):
        raise OpenBankingError("Ο πάροχος δεν επέστρεψε σύνδεσμο έγκρισης.")
    return {"requisition_id": data.get("id"), "link": data["link"], "reference": payload["reference"]}


async def get_requisition(settings: dict, requisition_id: str) -> dict:
    token = await get_token(settings)
    data = await _request("GET", f"/requisitions/{requisition_id}/", token=token)
    return {"status": data.get("status"), "accounts": data.get("accounts") or [],
            "institution_id": data.get("institution_id")}


async def account_details(settings: dict, account_id: str) -> dict:
    token = await get_token(settings)
    data = await _request("GET", f"/accounts/{account_id}/details/", token=token)
    account = data.get("account") or {}
    return {"iban": account.get("iban", ""), "name": account.get("name") or account.get("ownerName", ""),
            "currency": account.get("currency", "EUR")}


def _amount(raw: dict) -> float:
    value = (raw.get("transactionAmount") or {}).get("amount")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _description(raw: dict) -> str:
    """Ο πάροχος γεμίζει διαφορετικά πεδία ανά τράπεζα — τα ενώνουμε με σειρά προτεραιότητας."""
    parts = [
        raw.get("remittanceInformationUnstructured"),
        " ".join(raw.get("remittanceInformationUnstructuredArray") or []),
        raw.get("creditorName"), raw.get("debtorName"),
        raw.get("additionalInformation"),
    ]
    seen, out = set(), []
    for part in parts:
        text = (part or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return " · ".join(out)[:500]


def normalize_transaction(raw: dict) -> dict:
    """Μία κίνηση του παρόχου → η μορφή που ήδη χρησιμοποιεί το `bank_transactions`."""
    booking = (raw.get("bookingDate") or raw.get("valueDate") or "")[:10]
    if not booking:
        booking = date.today().isoformat()
    external_id = (raw.get("transactionId") or raw.get("internalTransactionId")
                   or f"{booking}|{_amount(raw):.2f}|{_description(raw)[:60]}")
    return {
        "date": booking,
        "amount": _amount(raw),
        "description": _description(raw) or "Τραπεζική κίνηση",
        "reference": raw.get("endToEndId") or raw.get("checkId") or "",
        "external_id": str(external_id),
    }


async def fetch_transactions(settings: dict, account_id: str, date_from: str | None = None) -> list[dict]:
    """Κινήσεις λογαριασμού (booked μόνο — οι pending αλλάζουν και δεν συμφωνούνται)."""
    token = await get_token(settings)
    params = {"date_from": date_from} if date_from else None
    data = await _request("GET", f"/accounts/{account_id}/transactions/", token=token, params=params)
    booked = ((data.get("transactions") or {}).get("booked")) or []
    return [normalize_transaction(t) for t in booked]


def requisition_expiring(settings: dict, within_days: int = 7, today: date | None = None) -> bool:
    """Η συγκατάθεση PSD2 λήγει· ειδοποιούμε πριν σταματήσουν οι κινήσεις."""
    expires = settings.get("gocardless_access_valid_until")
    if not expires:
        return False
    try:
        expiry = date.fromisoformat(str(expires)[:10])
    except ValueError:
        return False
    return (expiry - (today or date.today())).days <= within_days
