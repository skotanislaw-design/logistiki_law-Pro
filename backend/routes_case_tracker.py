"""ΣΟΛΩΝ / e-Justice Auto-Tracker.
Παρακολουθεί αυτόματα το status των υποθέσεων στα εθνικά portals.
Ο χρήστης δίνει URL (ή ΓΑΚ/ΕΑΚ) της υπόθεσης — το σύστημα κάνει polling κάθε 12ώρες,
συγκρίνει hash του περιεχομένου και ειδοποιεί σε αλλαγές.
"""
import hashlib
import httpx
import uuid
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from bs4 import BeautifulSoup
from db import db
from models import CaseTrackerEvent
from auth import get_current_user
from tenant import tenant_query, firm_id_for

router = APIRouter(prefix="/case-tracker", tags=["case-tracker"])


def _extract_meaningful_text(html: str) -> str:
    """Απομονώνει μόνο το ουσιαστικό περιεχόμενο για stable hashing (αφαιρεί timestamps, css)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Αφαιρώ timestamps/session ids που αλλάζουν σε κάθε request
    text = re.sub(r"\d{2}:\d{2}:\d{2}", "", text)
    text = re.sub(r"session[_-]?id\s*=\s*\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _fetch_case_page(url: str) -> tuple[str, str]:
    """Επιστρέφει (text_content, hash). Πετάει exception αν αποτύχει."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "NomosOne-CaseTracker/1.0"})
        r.raise_for_status()
        content = _extract_meaningful_text(r.text)
        return content, _hash(content)


async def _diff_summary(old: str, new: str) -> str:
    """Απλή σύνοψη — τι λέξεις εμφανίζονται νέες στο new και δεν υπήρχαν στο old."""
    if not old:
        return "Αρχική καταχώρηση παρακολούθησης"
    old_words = set(old.split())
    new_words = set(new.split())
    added = new_words - old_words
    removed = old_words - new_words
    parts = []
    if added:
        sample = list(added)[:10]
        parts.append("Νέα στοιχεία: " + ", ".join(sample))
    if removed:
        parts.append(f"Αφαιρέθηκαν {len(removed)} στοιχεία")
    return " · ".join(parts) if parts else "Ανιχνεύθηκε αλλαγή περιεχομένου"


class EnableRequest(BaseModel):
    solon_url: str = ""


@router.post("/enable/{case_id}")
async def enable_tracking(case_id: str, payload: EnableRequest, user: dict = Depends(get_current_user)):
    try:
        case = await db.cases.find_one({"_id": ObjectId(case_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id υπόθεσης")
    if not case:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")

    solon_url = payload.solon_url or case.get("solon_url", "")
    if not solon_url:
        # Αν έχει ΓΑΚ, προτείνω το generic search URL
        gak = case.get("gak", "")
        if gak:
            solon_url = f"https://www.solon.gov.gr/webcenter/portal/prosolonspa/prosolonpanel/prosolonanazitisi?gak={gak}"
        else:
            raise HTTPException(status_code=400, detail="Δώστε solon_url ή συμπληρώστε ΓΑΚ στην υπόθεση")

    await db.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {
            "tracker_enabled": True,
            "solon_url": solon_url,
            "tracker_snapshot_hash": "",
            "tracker_last_check": None,
        }},
    )
    return {"message": "Η παρακολούθηση ενεργοποιήθηκε — πρώτος έλεγχος σε λίγα δευτερόλεπτα.", "solon_url": solon_url}


@router.post("/disable/{case_id}")
async def disable_tracking(case_id: str, user: dict = Depends(get_current_user)):
    await db.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {"tracker_enabled": False}},
    )
    return {"message": "Η παρακολούθηση απενεργοποιήθηκε"}


@router.post("/check-now/{case_id}")
async def check_now(case_id: str, user: dict = Depends(get_current_user)):
    try:
        case = await db.cases.find_one({"_id": ObjectId(case_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Μη έγκυρο id")
    if not case:
        raise HTTPException(status_code=404, detail="Η υπόθεση δεν βρέθηκε")
    if not case.get("solon_url"):
        raise HTTPException(status_code=400, detail="Δεν έχει οριστεί solon_url για την υπόθεση")

    result = await _run_check(case)
    return result


async def _run_check(case: dict) -> dict:
    """Runs a single check on a case. Idempotent — safe να κληθεί από scheduler ή manual."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        content, new_hash = await _fetch_case_page(case["solon_url"])
    except Exception as e:
        await db.cases.update_one(
            {"_id": case["_id"]},
            {"$set": {"tracker_last_check": now_iso, "tracker_last_error": str(e)[:200]}},
        )
        return {"status": "error", "message": f"Αποτυχία fetch: {e}", "case_id": str(case["_id"])}

    old_hash = case.get("tracker_snapshot_hash", "")
    changed = bool(old_hash) and old_hash != new_hash

    if changed:
        old_content = case.get("tracker_snapshot_content", "")
        summary = await _diff_summary(old_content, content)
        ev = CaseTrackerEvent(
            case_id=str(case["_id"]),
            case_number=case.get("number", ""),
            case_title=case.get("title", ""),
            source="solon",
            change_type="content_change",
            old_hash=old_hash,
            new_hash=new_hash,
            diff_summary=summary,
            fetched_at=now_iso,
        )
        await db.case_tracker_events.insert_one(ev.to_mongo())
        # ενημέρωσε και το events log της υπόθεσης αν υπάρχει
        try:
            from routes_cases import log_case_event
            await log_case_event(str(case["_id"]), f"🔔 Αλλαγή στο ΣΟΛΩΝ — {summary}")
        except Exception:
            pass

    await db.cases.update_one(
        {"_id": case["_id"]},
        {"$set": {
            "tracker_last_check": now_iso,
            "tracker_snapshot_hash": new_hash,
            "tracker_snapshot_content": content[:5000],  # cap για να μη γίνει huge
            "tracker_last_change": now_iso if changed else case.get("tracker_last_change"),
            "tracker_last_error": None,
        }},
    )
    return {
        "status": "changed" if changed else "unchanged",
        "message": "Ανιχνεύθηκε αλλαγή!" if changed else "Καμία αλλαγή",
        "case_id": str(case["_id"]),
        "diff_summary": summary if changed else "",
    }


@router.get("/events")
async def list_events(unread_only: bool = False, user: dict = Depends(get_current_user)):
    query = {"read": False} if unread_only else {}
    docs = await db.case_tracker_events.find(tenant_query(user, query)).sort("fetched_at", -1).limit(100).to_list(100)
    return [CaseTrackerEvent.from_mongo(d) for d in docs]


@router.post("/events/{event_id}/read")
async def mark_read(event_id: str, user: dict = Depends(get_current_user)):
    try:
        await db.case_tracker_events.update_one(tenant_query(user, {"_id": ObjectId(event_id)}), {"$set": {"read": True}})
    except Exception:
        raise HTTPException(status_code=404, detail="Δεν βρέθηκε")
    return {"message": "OK"}


@router.post("/events/mark-all-read")
async def mark_all_read(user: dict = Depends(get_current_user)):
    result = await db.case_tracker_events.update_many(tenant_query(user, {"read": False}), {"$set": {"read": True}})
    return {"marked": result.modified_count}


@router.get("/status")
async def tracker_status(user: dict = Depends(get_current_user)):
    tracked = await db.cases.count_documents(tenant_query(user, {"tracker_enabled": True}))
    unread = await db.case_tracker_events.count_documents(tenant_query(user, {"read": False}))
    total_events = await db.case_tracker_events.count_documents(tenant_query(user))
    return {"tracked_cases": tracked, "unread_events": unread, "total_events": total_events}


async def run_all_tracked_checks():
    """Called by scheduler — τρέχει έλεγχο σε όλες τις παρακολουθούμενες υποθέσεις."""
    cases = await db.cases.find({"tracker_enabled": True}).to_list(500)
    results = {"checked": 0, "changed": 0, "errors": 0}
    for c in cases:
        if not c.get("solon_url"):
            continue
        r = await _run_check(c)
        results["checked"] += 1
        if r["status"] == "changed":
            results["changed"] += 1
        elif r["status"] == "error":
            results["errors"] += 1
    return results
