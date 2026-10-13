"""Φραγμός για `NaN` και άπειρα σε σώματα JSON.

Η `json.loads` της Python δέχεται τα `NaN`, `Infinity`, `-Infinity` — και το `1e999`, που γίνεται `inf` — ενώ το πρότυπο
JSON (RFC 8259) δεν τα επιτρέπει. Η Pydantic τα δέχεται ως `float`. Και ένα `NaN` σε ποσό είναι ύπουλο:

* κάθε σύγκριση με `NaN` είναι ψευδής, οπότε ο έλεγχος «αρνητικό ή πάνω από το όριο» δεν το πιάνει·
* περνά από τον υπολογισμό ΦΠΑ (`Decimal('NaN')` δεν σκάει στο `quantize`) και αποθηκεύεται ως σύνολο τιμολογίου·
* από εκεί, το άθροισμα κάθε αναφοράς γίνεται `NaN` και η απάντηση JSON αποτυγχάνει (500) — σε όλη τη λίστα, για
  όλο το γραφείο, μέχρι να διορθωθεί το έγγραφο με το χέρι στη βάση.

Η λύση ανήκει στην είσοδο και όχι σε κάθε route χωριστά: κανένα πεδίο της εφαρμογής δεν χρειάζεται τέτοια τιμή.
Το αίτημα απορρίπτεται με 400 πριν φτάσει στο route· ό,τι άλλο (μη έγκυρο JSON) αφήνεται στην ίδια τη FastAPI.

Το ίδιο σημείο βάζει και **όριο στο μέγεθος του σώματος JSON**. Ο φραγμός διαβάζει και αναλύει ολόκληρο το σώμα πριν από
τον έλεγχο ταυτότητας, στον βρόχο γεγονότων που μοιράζονται όλα τα γραφεία· η FastAPI το αναλύει μετά ξανά. Για 20 MB
(το όριο του nginx) αυτό κοστίζει 1–2 δευτερόλεπτα χωρίς να έχει ταυτοποιηθεί κανείς. Κανένα φυσιολογικό αίτημα JSON δεν
πλησιάζει αυτό το μέγεθος: οι φόρμες είναι χιλιάδες byte. Το μοναδικό που στέλνει μεγάλο JSON είναι η φωτογραφία απόδειξης
ως base64 (`LARGE_JSON_PATHS`)· τα αρχεία (αντίγραφα, CSV, ήχος) ανεβαίνουν ως multipart και δεν περνούν από εδώ.
"""
import json
import math

from starlette.responses import JSONResponse

_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

MESSAGE = "Το αίτημα περιέχει μη έγκυρο αριθμό (NaN ή άπειρο). Ελέγξτε τα ποσά και τις ποσότητες."
TOO_LARGE = "Το αίτημα είναι υπερβολικά μεγάλο."

MAX_JSON_BODY = 2 * 1024 * 1024
# Η φωτογραφία απόδειξης ως base64 μέσα σε JSON: μια φωτογραφία κινητού 7 MB γίνεται 9,5 MB. Ίσο με το όριο του nginx.
LARGE_JSON_BODY = 20 * 1024 * 1024
LARGE_JSON_PATHS = frozenset({"/api/expenses/ocr-parse"})


class NonFiniteNumber(ValueError):
    """Βρέθηκε `NaN` ή άπειρο μέσα στο JSON."""


def _reject_constant(name: str):
    raise NonFiniteNumber(name)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise NonFiniteNumber(text)
    return value


def has_non_finite_number(body: bytes) -> bool:
    """Περιέχει το σώμα JSON `NaN`, `Infinity` ή αριθμό που υπερχειλίζει σε άπειρο;

    Μη έγκυρο JSON δεν είναι δική μας υπόθεση: επιστρέφει `False` και το σφάλμα το δίνει η FastAPI όπως πάντα.
    """
    try:
        json.loads(body, parse_constant=_reject_constant, parse_float=_finite_float)
    except NonFiniteNumber:
        return True
    except (ValueError, RecursionError):
        return False
    return False


def contains_non_finite(value) -> bool:
    """Περιέχει μια ήδη αναλυμένη τιμή (έγγραφο, λίστα, αριθμός) `NaN` ή άπειρο σε οποιοδήποτε βάθος;

    Για ό,τι δεν περνά από σώμα αιτήματος — π.χ. τα έγγραφα ενός αρχείου αντιγράφου ασφαλείας: η `json.loads` δέχεται
    `NaN` και `Infinity`, και ένα τέτοιο σύνολο τιμολογίου θα χαλούσε κάθε αναφορά του γραφείου.
    """
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(contains_non_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_non_finite(v) for v in value)
    return False


def _is_json(scope) -> bool:
    """Θα το διαβάσει η FastAPI ως JSON; Ναι όταν ο τύπος είναι `application/json` / `*+json` ή λείπει εντελώς."""
    content_type = ""
    for name, value in scope.get("headers") or ():
        if name == b"content-type":
            content_type = value.decode("latin-1").split(";", 1)[0].strip().lower()
            break
    return content_type in ("", "application/json") or content_type.endswith("+json")


def _declared_length(scope) -> int | None:
    """Το Content-Length που δήλωσε ο client, αν διαβάζεται ως ακέραιος (αλλιώς μετράμε τη ροή όπως έρχεται)."""
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value.decode("latin-1").strip())
            except ValueError:
                return None
    return None


class RejectNonFiniteJson:
    """ASGI middleware: αρνείται σώμα JSON με `NaN`/άπειρο. Το υπόλοιπο σώμα το ξαναπαίζει αυτούσιο στην εφαρμογή.

    Γράφεται ως καθαρό ASGI (όχι `BaseHTTPMiddleware`) ώστε να μη διαβάζεται δύο φορές η ροή του αιτήματος.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in _METHODS or not _is_json(scope):
            await self.app(scope, receive, send)
            return

        limit = LARGE_JSON_BODY if scope.get("path", "").rstrip("/") in LARGE_JSON_PATHS else MAX_JSON_BODY
        declared = _declared_length(scope)
        if declared is not None and declared > limit:     # δεν διαβάζεται καν το σώμα
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":      # ο client έκλεισε τη σύνδεση πριν ολοκληρώσει το αίτημα
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:                            # χωρίς Content-Length (chunked): μετράμε όσο έρχεται
                await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        if body and has_non_finite_number(body):
            await JSONResponse({"detail": MESSAGE}, status_code=400)(scope, receive, send)
            return

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
