"""Φραγμός για `NaN` και άπειρα σε σώματα JSON.

Η `json.loads` της Python δέχεται τα `NaN`, `Infinity`, `-Infinity` — και το `1e999`, που γίνεται `inf` — ενώ το πρότυπο
JSON (RFC 8259) δεν τα επιτρέπει. Η Pydantic τα δέχεται ως `float`. Και ένα `NaN` σε ποσό είναι ύπουλο:

* κάθε σύγκριση με `NaN` είναι ψευδής, οπότε ο έλεγχος «αρνητικό ή πάνω από το όριο» δεν το πιάνει·
* περνά από τον υπολογισμό ΦΠΑ (`Decimal('NaN')` δεν σκάει στο `quantize`) και αποθηκεύεται ως σύνολο τιμολογίου·
* από εκεί, το άθροισμα κάθε αναφοράς γίνεται `NaN` και η απάντηση JSON αποτυγχάνει (500) — σε όλη τη λίστα, για
  όλο το γραφείο, μέχρι να διορθωθεί το έγγραφο με το χέρι στη βάση.

Η λύση ανήκει στην είσοδο και όχι σε κάθε route χωριστά: κανένα πεδίο της εφαρμογής δεν χρειάζεται τέτοια τιμή.
Το αίτημα απορρίπτεται με 400 πριν φτάσει στο route· ό,τι άλλο (μη έγκυρο JSON) αφήνεται στην ίδια τη FastAPI.
"""
import json
import math

from starlette.responses import JSONResponse

_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

MESSAGE = "Το αίτημα περιέχει μη έγκυρο αριθμό (NaN ή άπειρο). Ελέγξτε τα ποσά και τις ποσότητες."


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


def _is_json(scope) -> bool:
    """Θα το διαβάσει η FastAPI ως JSON; Ναι όταν ο τύπος είναι `application/json` / `*+json` ή λείπει εντελώς."""
    content_type = ""
    for name, value in scope.get("headers") or ():
        if name == b"content-type":
            content_type = value.decode("latin-1").split(";", 1)[0].strip().lower()
            break
    return content_type in ("", "application/json") or content_type.endswith("+json")


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

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":      # ο client έκλεισε τη σύνδεση πριν ολοκληρώσει το αίτημα
                return
            chunks.append(message.get("body", b""))
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
