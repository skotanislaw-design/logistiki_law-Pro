# Ανάλυση Ετοιμότητας — Nomos One / Logistiki Pro

Ημερομηνία ανάλυσης: 2026-09-04
Κλάδος: `main` (commit 6530c45)

## 1. Τι είναι το πρόγραμμα

Web εφαρμογή λογιστικής και νομικής διαχείρισης για ελληνικά δικηγορικά γραφεία.

| Στρώμα | Τεχνολογία | Μέγεθος |
|---|---|---|
| Backend | FastAPI + Motor (MongoDB), APScheduler | 41 αρχεία Python, ~6.250 γραμμές, 31 routers |
| Frontend | React 19 (CRA + craco), Tailwind, shadcn/ui, PWA | 27 σελίδες, 50+ components |
| Υποδομή | docker-compose (mongo, backend, frontend, backup) | |
| Εξωτερικά | ΑΑΔΕ myDATA REST, Stripe, SMTP, Emergent LLM/Storage | |

Λειτουργικά περιλαμβάνει: τιμολόγηση με σειροποίηση και PDF, έξοδα με OCR, ΦΠΑ, μισθοδοσία,
trust accounts, myDATA, KYC/AML, υποθέσεις/πινάκια/γραμμάτια/παράβολα, AI copilot,
client portal με Stripe, ψηφιακές υπογραφές, backup/GDPR, εισαγωγή τραπεζικών κινήσεων με auto-matching.

## 2. Βαθμολογία ετοιμότητας

Κλίμακα 0-10 ανά άξονα. Συνολικός βαθμός: **5,4 / 10 — «λειτουργικό πρωτότυπο, όχι παραγωγή»**.

| Άξονας | Βαθμός | Σχόλιο |
|---|---|---|
| Λειτουργική κάλυψη | 8,5 | Εξαιρετικά πλούσιο feature set για τον κλάδο. |
| Ορθότητα κώδικα | 4,5 | Κρίσιμο bug στο `/api/settings` (βλ. 3.1), tenant leaks, mock myDATA που δείχνει «synced». |
| Ασφάλεια / πολυ-μισθωτής | 4,0 | Tenant isolation ημιτελής σε 6 modules. Default admin `admin123`. CORS `*` με credentials. |
| Αυτοματισμός («χωρίς διάδραση») | 3,0 | Μόνο ένας scheduled job (ΣΟΛΩΝ). Όλα τα λογιστικά είναι χειροκίνητα: υπενθυμίσεις, myDATA retry, reconciliation, ΦΠΑ, ενημέρωση χρήστη. |
| Δοκιμές / CI | 3,0 | 67 tests, όλα E2E σε live περιβάλλον Emergent (`/app/...`). Κανένα unit test, κανένα CI pipeline. |
| Deployability | 4,5 | `docker-compose` υπάρχει, αλλά: `yarn.lock` λείπει (το Dockerfile απαιτεί `--frozen-lockfile`), `.env.example` δεν υπάρχει (αγνοείται από `.gitignore`), healthcheck χτυπά endpoint που απαιτεί auth. |
| Ανεξαρτησία από vendor | 3,5 | AI, STT, OCR, object storage και `litellm` wheel δεμένα σε Emergent. Χωρίς `EMERGENT_LLM_KEY` το backend δεν ξεκινά καθαρά. |
| Τεκμηρίωση | 7,5 | README και USER_GUIDE πλήρη και στα ελληνικά. |
| Λογιστική πληρότητα (ΕΛ) | 6,0 | Σωστή προσέγγιση ΦΠΑ/ΕΦΚΑ/κλίμακας φόρου, αλλά χωρίς περιοδικό κλείσιμο ΦΠΑ, χωρίς αρχείο δήλωσης, χωρίς ιστορικό περιόδων. |

## 3. Ευρήματα

### 3.1 Κρίσιμα (μπλοκάρουν την παραγωγή)

1. **`routes_settings.py` χρησιμοποιεί `tenant_query`/`firm_id_for` χωρίς import.** Κάθε κλήση στο
   `GET /api/settings` επιστρέφει 500 (`NameError`). Το Dashboard, το Onboarding, το ΦΠΑ και οι
   Ρυθμίσεις καλούν αυτό το endpoint. Η εφαρμογή είναι πρακτικά μη λειτουργική μετά το merge του PR #1.
2. **Tenant leaks** (δεδομένα άλλων γραφείων ορατά ή τροποποιήσιμα):
   `routes_mydata.py` (status counts, sync), `routes_kyc.py` (stats, client lookup),
   `routes_backup.py` (GDPR export, full export/import χωρίς firm scoping),
   `routes_case_tracker.py` (enable/disable/check-now), `copilot.py::build_context`
   (στέλνει δεδομένα ΟΛΩΝ των γραφείων στο LLM).
3. **Μη-αναπαραγώγιμο build**: `frontend/yarn.lock` απουσιάζει ενώ το Dockerfile τρέχει
   `yarn install --frozen-lockfile`.

### 3.2 Σοβαρά

4. **myDATA mock εμφανίζεται ως «synced».** Χωρίς credentials, η έκδοση γράφει `MOCK-…` MARK και
   status `synced`. Ο χρήστης νομίζει ότι διαβίβασε στην ΑΑΔΕ. Πρέπει να είναι `not_sent`/`pending`.
5. **Καμία αυτόματη ροή.** Υπενθυμίσεις, αποστολή τιμολογίου, επανάληψη αποτυχημένου myDATA,
   επιβεβαίωση bank auto-matches, προθεσμίες: όλα απαιτούν κλικ από τον χρήστη.
6. **Reset password** τυπώνει το link στο stdout αντί να το στέλνει email.
7. **Default admin** `admin@logistiki.gr / admin123` σπέρνεται πάντα.
8. **CORS** default `*` με `allow_credentials=True` (οι browsers το απορρίπτουν, αλλά είναι λάθος config).
9. **Unique index `bank_transactions.external_id`** χωρίς `firm_id` → δύο γραφεία με ίδια κίνηση συγκρούονται.
10. **Emergent lock-in**: `ai_helpers`, `storage`, `copilot` κάνουν import από `emergentintegrations`
    στο module level. Χωρίς το πακέτο ο server δεν φορτώνει.

### 3.3 Μέτρια

11. Tests εξαρτώνται από `/app/frontend/.env`, `/app/memory/test_credentials.md` και live backend.
12. `pytest.ini` απαιτεί `-n 2 --dist loadscope` (xdist) — τα tests δεν τρέχουν χωρίς αυτό.
13. Docker healthcheck backend → `/api/settings` (401 χωρίς cookie → unhealthy).
14. Δεν υπάρχει `.github/workflows` (CI), ούτε `.env.example` (README το αναφέρει).
15. `routes_case_tracker.run_all_tracked_checks` τρέχει ανά instance (2 uvicorn workers → διπλά jobs).

## 4. Τι σημαίνει «ελάχιστη διάδραση» για λογιστικό πρόγραμμα

Ο χρήστης (δικηγόρος) πρέπει να κάνει μόνο τρία πράγματα:
1. Να εκδίδει τιμολόγιο (ή να το πει φωνητικά).
2. Να φωτογραφίζει/προωθεί αποδείξεις.
3. Να απαντά σε **εξαιρέσεις** που του παρουσιάζει το σύστημα (π.χ. «η κίνηση 1.240 € ταιριάζει με 2 τιμολόγια — ποιο;»).

Όλα τα υπόλοιπα (αποστολή, myDATA, υπενθυμίσεις, reconciliation, ΦΠΑ περιόδου, προθεσμίες, ενημέρωση)
πρέπει να τρέχουν μόνα τους στον server και να τον ενημερώνουν με **ένα** email την ημέρα ή την εβδομάδα.

## 5. Σχέδιο απογείωσης (υλοποιήθηκε σε αυτό το branch)

Βλ. `docs/AUTOPILOT.md` για την πλήρη περιγραφή του νέου μηχανισμού «Autopilot».

## 6. Μετά την απογείωση (αυτό το branch)

| Άξονας | Πριν | Μετά | Τι άλλαξε |
|---|---|---|---|
| Ορθότητα κώδικα | 4,5 | 7,5 | Διορθώθηκε το 500 στο `/api/settings`, το mock myDATA δεν εμφανίζεται πλέον ως «synced», έκδοση μόνο από πρόχειρο |
| Ασφάλεια / πολυ-μισθωτής | 4,0 | 7,5 | Tenant scoping σε myDATA, KYC, backup/GDPR, case tracker, copilot context. CORS από `PUBLIC_URL`. Τυχαίος admin κωδικός. Index `(firm_id, external_id)`. Reset password μέσω email. Αφαιρέθηκε session recording τρίτου (PostHog/Emergent) από το frontend |
| Αυτοματισμός | 3,0 | 8,5 | Autopilot: 9 scheduled jobs, inbox αποφάσεων, digest, IMAP→OCR→έξοδα, κλείσιμο ΦΠΑ, cluster lock |
| Δοκιμές / CI | 3,0 | 7,0 | 30 offline unit tests (mongomock, χωρίς δίκτυο), GitHub Actions για backend + frontend build |
| Deployability | 4,5 | 7,5 | `yarn.lock`, `.env.example`, `/api/health`, compose env vars, local storage backend |
| Ανεξαρτησία από vendor | 3,5 | 7,0 | AI προαιρετικό (503 αντί για crash), storage local, `requirements-ci.txt` χωρίς Emergent |
| Λογιστική πληρότητα | 6,0 | 7,5 | Περίοδοι ΦΠΑ με ιστορικό και «υποβλήθηκε», `paid_via` σε κάθε εξόφληση, backoff myDATA |
| **Σύνολο** | **5,4** | **7,7** | |

Τι απομένει για 9+: πραγματικό E2E test με sandbox ΑΑΔΕ, open banking (PSD2) αντί για CSV, 2FA,
μετάβαση σε FastAPI lifespan, ξεχωριστός worker process για τον scheduler σε πολλαπλά replicas.
