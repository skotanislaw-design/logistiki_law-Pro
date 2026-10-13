"""Προστασία από πλαστά αιτήματα άλλου ιστότοπου (CSRF) — εύρημα D-01 του δεύτερου ελέγχου του γύρου 5.

Η σύνδεση γίνεται με cookie (`access_token`, `refresh_token`) που ο browser επισυνάπτει ΜΟΝΟΣ του σε κάθε αίτημα προς την
εφαρμογή. Αν μια σελίδα τρίτου (σύνδεσμος σε email, διαφήμιση, ιστότοπος που ο δικηγόρος ανοίγει ενώ η εφαρμογή μένει ανοιχτή
σε άλλο παράθυρο) στείλει αίτημα προς την εφαρμογή, ο browser το συνοδεύει με το cookie και ο server το εκτελεί σαν να το έκανε
ο ίδιος ο χρήστης. Το CORS δεν το εμποδίζει: ορίζει ποιος μπορεί να ΔΙΑΒΑΣΕΙ την απάντηση, όχι ποιος μπορεί να ΣΤΕΙΛΕΙ.

Με τα cookies ως `SameSite=None` (όπως ήταν) αρκούσε μια φόρμα που στέλνεται μόνη της, ή ένα `fetch(..., {mode: "no-cors"})`, για
να εκτελεστεί οποιαδήποτε ενέργεια χωρίς σώμα (έκδοση τιμολογίου, «Συμφωνία» τραπεζικής κίνησης, εκτέλεση του Autopilot) — και,
επειδή η FastAPI διαβάζει ως JSON ένα σώμα που ΔΕΝ δηλώνει Content-Type, κάθε ενέργεια με σώμα JSON, καθώς και η επαναφορά
αντιγράφου (multipart), που αντικαθιστά IBAN, SMTP και `public_url` του γραφείου. Τρία στρώματα:

1. **`SameSite=Lax`** στα cookies (`auth.cookie_samesite`, ρύθμιση `COOKIE_SAMESITE`): ο browser δεν τα στέλνει σε αίτημα POST που
   ξεκινά από άλλο site. Η εγκατάσταση του docker-compose είναι ίδιας προέλευσης (ο nginx σερβίρει και την εφαρμογή και το `/api`),
   οπότε δεν κοστίζει τίποτα.
2. **Έλεγχος προέλευσης** (εδώ): αίτημα που ΑΛΛΑΖΕΙ δεδομένα και φέρει cookie σύνδεσης εκτελείται μόνο αν προέρχεται από την ίδια
   προέλευση ή από προέλευση της λίστας `http_utils.allowed_origins()` (η ίδια που ορίζει το CORS). Αλλιώς 403, ΠΡΙΝ διαβαστεί το σώμα.
   Ο κανόνας εφαρμόζεται και στο `/api/auth/…` χωρίς cookie, ώστε να μη «συνδέει» ξένη σελίδα τον χρήστη σε λογαριασμό του επιτιθέμενου.
3. **Σώμα χωρίς Content-Type** (εδώ): αίτημα με cookie σύνδεσης και σώμα αλλά χωρίς Content-Type απορρίπτεται με 415 — η εφαρμογή
   δεν στέλνει ποτέ τέτοιο αίτημα, ενώ είναι ο δρόμος με τον οποίο ένα `fetch` χωρίς προκαταρκτικό έλεγχο (preflight) περνούσε ως JSON.

Δεν επηρεάζονται: τα αιτήματα χωρίς cookie σύνδεσης (webhook του Stripe, πύλη πελάτη με token στη διαδρομή, κλήσεις με κεφαλίδα
`Authorization` μόνο — ένας επιτιθέμενος δεν μπορεί να βάλει δική του κεφαλίδα χωρίς προκαταρκτικό έλεγχο CORS), τα αιτήματα
ανάγνωσης (GET/HEAD/OPTIONS) και οι πελάτες που δεν είναι browser (curl, scripts, tests) αφού δεν στέλνουν `Origin` ούτε `Sec-Fetch-*`.

Πώς αποφασίζεται αν ένα αίτημα είναι «ξένο» (`is_foreign`):

* `Sec-Fetch-Site: same-origin` ή `none` → δικό μας. Την κεφαλίδα την βάζει ο browser και δεν μπορεί να τη ρυθμίσει σελίδα.
* `same-site` ή `cross-site` (ή άγνωστη τιμή) → δικό μας ΜΟΝΟ αν το `Origin` (ή, αν λείπει, η προέλευση του `Referer`) ανήκει στη λίστα
  επιτρεπτών. Ένα «αδελφό» subdomain (`evil.firma.gr` δίπλα στο `app.firma.gr`) είναι same-site αλλά όχι επιτρεπτό.
* Χωρίς `Sec-Fetch-Site` (παλιός browser, ή δεν είναι browser): αν δεν υπάρχει ούτε `Origin` ούτε `Referer` → δικό μας (δεν ξεκίνησε από
  σελίδα)· αν υπάρχουν, η προέλευση πρέπει να είναι επιτρεπτή ή να έχει το ίδιο όνομα υπολογιστή με το `Host` / `X-Forwarded-Host`
  (ο nginx του docker-compose στέλνει `Host` χωρίς θύρα, γι' αυτό η σύγκριση γίνεται μόνο στο όνομα).
* `Origin: null` και ό,τι δεν διαβάζεται ως διεύθυνση → ξένο (δεν ταυτοποιεί κανέναν).

Γράφεται ως καθαρό ASGI middleware (όχι `BaseHTTPMiddleware`), όπως το `json_guard`: δεν αγγίζει τη ροή του σώματος.
"""
import logging
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from http_utils import allowed_origins, origin_of

logger = logging.getLogger("csrf")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
AUTH_COOKIES = frozenset({"access_token", "refresh_token"})
API_PREFIX = "/api/"
AUTH_PREFIX = "/api/auth/"

FOREIGN_MESSAGE = "Το αίτημα προήλθε από άλλον ιστότοπο και δεν εκτελέστηκε."
NO_CONTENT_TYPE_MESSAGE = "Το αίτημα έχει σώμα αλλά δεν δηλώνει τύπο περιεχομένου (Content-Type)."


def _first_headers(scope) -> dict[str, str]:
    """Η πρώτη τιμή κάθε κεφαλίδας, με πεζά ονόματα (στο ASGI είναι ήδη πεζά, αλλά δεν το στηριζόμαστε σε αυτό)."""
    found: dict[str, str] = {}
    for key, value in scope.get("headers") or ():
        name = key.decode("latin-1").lower()
        if name not in found:
            found[name] = value.decode("latin-1")
    return found


def cookie_names(scope) -> set[str]:
    """Τα ονόματα των cookies του αιτήματος (σε HTTP/2 μπορεί να υπάρχουν περισσότερες από μία κεφαλίδες `Cookie`)."""
    names: set[str] = set()
    for key, value in scope.get("headers") or ():
        if key.decode("latin-1").lower() == "cookie":
            for part in value.decode("latin-1").split(";"):
                name = part.split("=", 1)[0].strip()
                if name:
                    names.add(name)
    return names


def _host_names(headers: dict[str, str]) -> set[str]:
    """Τα ονόματα υπολογιστή (χωρίς θύρα) με τα οποία ο browser απευθύνθηκε στην εφαρμογή."""
    names: set[str] = set()
    for key in ("host", "x-forwarded-host"):
        first = headers.get(key, "").split(",", 1)[0].strip()
        if not first:
            continue
        try:
            host = urlsplit("//" + first).hostname
        except ValueError:
            host = None
        if host:
            names.add(host)
    return names


def is_foreign(headers: dict[str, str], allowed: set[str]) -> bool:
    """Προέρχεται το αίτημα από σελίδα που δεν ανήκει στην εφαρμογή; (βλ. την περιγραφή στην αρχή του αρχείου)"""
    fetch_site = headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site in ("same-origin", "none"):
        return False

    claimed = headers.get("origin")
    if claimed is None:
        claimed = headers.get("referer")
    origin = origin_of(claimed) if claimed is not None else None

    if fetch_site:                                    # same-site, cross-site ή άγνωστη (μελλοντική) τιμή
        return origin is None or origin not in allowed
    if claimed is None:                               # ούτε Fetch Metadata ούτε Origin/Referer: δεν ξεκίνησε από σελίδα
        return False
    if origin is None:
        return True
    if origin in allowed:
        return False
    return urlsplit(origin).hostname not in _host_names(headers)


def _has_body(headers: dict[str, str]) -> bool:
    length = headers.get("content-length")
    if length is not None:
        try:
            return int(length.strip()) > 0
        except ValueError:
            return True
    return "transfer-encoding" in headers


class CsrfGuard:
    """ASGI middleware: απορρίπτει αιτήματα με cookie σύνδεσης που αλλάζουν δεδομένα και δεν προέρχονται από την εφαρμογή."""

    def __init__(self, app, allowed=None):
        self.app = app
        self._allowed = allowed or allowed_origins    # καλούμενο που δίνει τη λίστα (αλλάζει με τις μεταβλητές περιβάλλοντος)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        has_session = bool(cookie_names(scope) & AUTH_COOKIES)
        if has_session or path.startswith(AUTH_PREFIX):
            headers = _first_headers(scope)
            allowed = {o for o in (origin_of(entry) for entry in self._allowed()) if o}
            if is_foreign(headers, allowed):
                logger.warning(
                    "Απορρίφθηκε αίτημα %s %s από ξένη προέλευση (Origin=%r, Referer=%r, Sec-Fetch-Site=%r)",
                    scope["method"], path[:200], headers.get("origin", "")[:120], headers.get("referer", "")[:120],
                    headers.get("sec-fetch-site", "")[:30],
                )
                await JSONResponse({"detail": FOREIGN_MESSAGE}, status_code=403)(scope, receive, send)
                return
        else:
            headers = None

        if has_session and path.startswith(API_PREFIX):
            headers = headers or _first_headers(scope)
            if _has_body(headers) and not headers.get("content-type", "").strip():
                await JSONResponse({"detail": NO_CONTENT_TYPE_MESSAGE}, status_code=415)(scope, receive, send)
                return

        await self.app(scope, receive, send)
