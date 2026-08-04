import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from openpyxl import load_workbook
from db import db
from models import Client
from auth import get_current_user

router = APIRouter(prefix="/import", tags=["import"])


def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace(".", "").replace(" ", "_")


CLIENT_KEY_MAP = {
    "name": "name", "onoma": "name", "όνομα": "name", "επωνυμία": "name",
    "afm": "afm", "αφμ": "afm",
    "doy": "doy", "δου": "doy", "δοϋ": "doy", "δ.ο.υ": "doy",
    "email": "email", "e-mail": "email", "e_mail": "email",
    "phone": "phone", "τηλέφωνο": "phone", "τηλ": "phone", "τηλ.": "phone",
    "address": "address", "διεύθυνση": "address",
    "type": "client_type", "τύπος": "client_type",
    "client_type": "client_type",
}


def _parse_client_row(row: dict) -> dict:
    parsed = {}
    for k, v in row.items():
        key = _norm_key(str(k))
        if key in CLIENT_KEY_MAP and v is not None and str(v).strip():
            parsed[CLIENT_KEY_MAP[key]] = str(v).strip()
    ctype = parsed.get("client_type", "").lower()
    if any(w in ctype for w in ["εταιρ", "company", "α.ε", "ε.π.ε", "ike"]):
        parsed["client_type"] = "company"
    else:
        parsed["client_type"] = "individual"
    return parsed


def _read_rows(file_bytes: bytes, filename: str):
    """Επιστρέφει list[dict] από .csv ή .xlsx."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp1253", "iso-8859-7", "windows-1252"):
            try:
                text = file_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise HTTPException(status_code=400, detail="Αδυναμία αποκωδικοποίησης CSV — αποθηκεύστε το ως UTF-8.")
        # auto-detect delimiter
        sample = text[:1024]
        delimiter = ","
        for cand in [",", ";", "\t", "|"]:
            if sample.count(cand) > sample.count(delimiter):
                delimiter = cand
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        return [row for row in reader if any((v or "").strip() for v in row.values())]
    elif name.endswith(".xlsx") or name.endswith(".xlsm"):
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        result = []
        for r in rows[1:]:
            if not any(c is not None and str(c).strip() for c in r):
                continue
            result.append({headers[i]: r[i] for i in range(len(headers))})
        return result
    else:
        raise HTTPException(status_code=400, detail="Υποστηρίζονται μόνο αρχεία .csv ή .xlsx")


@router.get("/clients/template")
async def clients_template(user: dict = Depends(get_current_user)):
    """Παράδειγμα για τον χρήστη — να ξέρει τι στήλες περιμένουμε."""
    from fastapi.responses import Response
    csv_text = "Ονομα,ΑΦΜ,ΔΟΥ,Email,Τηλέφωνο,Διεύθυνση,Τύπος\n"
    csv_text += "Παπαδόπουλος Ιωάννης,123456789,ΑΘΗΝΩΝ,ipapadopoulos@example.gr,2101234567,Πανεπιστημίου 10 Αθήνα,individual\n"
    csv_text += "ABC Ε.Π.Ε.,999888777,ΦΑΕ ΑΘΗΝΩΝ,info@abc.gr,2109999999,Λ. Κηφισίας 100 Μαρούσι,company\n"
    return Response(
        content=("\ufeff" + csv_text).encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="clients_template.csv"'},
    )


@router.post("/clients")
async def import_clients(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Το αρχείο είναι κενό.")
    rows = _read_rows(content, file.filename or "upload.csv")
    if not rows:
        raise HTTPException(status_code=400, detail="Δεν βρέθηκαν εγγραφές στο αρχείο.")

    created, skipped, errors = 0, 0, []
    for idx, raw in enumerate(rows, start=2):  # start=2 για να ταιριάζει με τη γραμμή του Excel (μετά το header)
        try:
            data = _parse_client_row(raw)
            if not data.get("name"):
                errors.append(f"Γραμμή {idx}: λείπει όνομα/επωνυμία")
                skipped += 1
                continue
            existing = None
            if data.get("afm"):
                existing = await db.clients.find_one({"afm": data["afm"]})
            if not existing:
                existing = await db.clients.find_one({"name": data["name"]})
            if existing:
                skipped += 1
                continue
            client = Client(**data, created_by=user["email"])
            await db.clients.insert_one(client.to_mongo())
            created += 1
        except Exception as e:
            errors.append(f"Γραμμή {idx}: {e}")
            skipped += 1

    return {
        "message": f"Εισήχθησαν {created} νέοι πελάτες, παραλείφθηκαν {skipped}.",
        "created": created,
        "skipped": skipped,
        "errors": errors[:15],
        "total_rows": len(rows),
    }
