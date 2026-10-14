# 🏛️ Nomos One — Personal Logistics

**Ολοκληρωμένη ψηφιακή πλατφόρμα λογιστικών, νομικής διαχείρισης και επιχειρηματικής ευφυΐας για ελληνικά δικηγορικά γραφεία.**

Ένας «ψηφιακός λογιστής» με ενσωματωμένο AI copilot, myDATA integration, πινάκια, γραμμάτια, KYC/AML, trust accounts, client portal με online πληρωμές, ψηφιακές υπογραφές και πολλά ακόμη.

![Aurora Glass Theme](https://img.shields.io/badge/design-Aurora%20Glass-c9a227)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-React%2019-61dafb)
![Database](https://img.shields.io/badge/db-MongoDB-47a248)
![PWA](https://img.shields.io/badge/PWA-installable-5A0FC8)

---

## 🤖 Autopilot — λογιστική χωρίς διάδραση

Το Nomos One τρέχει μόνο του στον server και σας ενοχλεί μόνο για αποφάσεις:

- Στέλνει το τιμολόγιο στον πελάτη μόλις εκδοθεί, το διαβιβάζει στο myDATA και ξαναπροσπαθεί μέχρι να πετύχει
- Κατεβάζει από την ΑΑΔΕ τα παραστατικά των προμηθευτών σας και τα καταχωρεί ως έξοδα με τα επίσημα ποσά
- Στέλνει υπενθυμίσεις ανείσπρακτων με payment link, με δική σας πολιτική (30 ημέρες / κάθε 15 / έως 3)
- Συμφωνεί τις τραπεζικές κινήσεις και εξοφλεί τιμολόγια αυτόματα μόνο όταν η κατάθεση είναι **ίση με το οφειλόμενο**, η πρόταση είναι **μονοσήμαντη** και η βεβαιότητα ≥ 90%· κάθε άλλη περίπτωση περιμένει απόφαση, με γραμμένη την αιτία
- Κλείνει κάθε περίοδο ΦΠΑ και σας λέει τι πληρώνετε και πότε, 7 και 1 ημέρα πριν
- Διαβάζει αποδείξεις που φτάνουν στο email του γραφείου (IMAP + OCR) και τις καταχωρεί ως έξοδα
- Κατεβάζει τις τραπεζικές κινήσεις μέσω **open banking (PSD2)** και συμφωνεί τις εισπράξεις χωρίς CSV
- Ετοιμάζει τη **δήλωση ΦΠΑ (Φ2)** ανά κωδικό εντύπου, σε PDF, έτοιμη για το TAXISnet
- Σας στέλνει **ένα** ενημερωτικό email (καθημερινά ή κάθε Δευτέρα) με ό,τι έκανε και ό,τι περιμένει απόφαση
- Ελέγχει ότι τα **αντίγραφα ασφαλείας** υπάρχουν και ανοίγουν, και σας ειδοποιεί αν χαλάσουν

Σελίδα `/autopilot`, τεκμηρίωση: [`docs/AUTOPILOT.md`](./docs/AUTOPILOT.md). Ασφάλεια (2FA, μυστικά, CSRF, δίκτυο, logs): [`docs/SECURITY.md`](./docs/SECURITY.md). Ανεξάρτητος έλεγχος και **τρέχουσα ετυμηγορία ετοιμότητας**: [`docs/AUDIT.md`](./docs/AUDIT.md) (ενότητα «Ετυμηγορία»). Η ανάλυση ετοιμότητας της 4/9/2026 είναι ιστορική και δεν δείχνει την τρέχουσα κατάσταση: [`docs/READINESS_ANALYSIS.md`](./docs/READINESS_ANALYSIS.md).

> **Κατάσταση.** Το Nomos One είναι **βοηθητικό εργαλείο υπό εποπτεία**, όχι αυτόνομος λογιστής: ο λογιστής του γραφείου παραμένει το σύστημα αναφοράς για κάθε παραστατικό, κάθε δήλωση ΦΠΑ και κάθε διαβίβαση στο myDATA. Ο κώδικας ελέγχεται με αυτοματοποιημένα tests πάνω σε προσομοιωμένες υπηρεσίες· **δεν έχει δοκιμαστεί ακόμη** σε πραγματική MongoDB, Stripe, browser, sandbox του myDATA και πλήρες `docker compose`, το GitHub CI δεν έχει τρέξει ακόμη πάνω στις αλλαγές του πέμπτου γύρου (τρέχει με pull request), και δεν έχει γίνει ποτέ δοκιμή επαναφοράς αντιγράφου. Τι έχει και τι δεν έχει ελεγχθεί, και τι πρέπει να γίνει πριν από παραγωγική χρήση, περιγράφεται στο [`docs/AUDIT.md`](./docs/AUDIT.md).

---

## 🌟 Κύρια Χαρακτηριστικά

### 💼 Οικονομικά & Λογιστικά
- **Τιμολόγηση** με αυτόματη σειροποίηση ανά είδος (ΤΠΥ-/ΑΠΥ-/ΠΙΣ-YYYY-NNNN, αριθμός στην έκδοση — το πρόχειρο δεν έχει), PDF letterhead, email αποστολή, 1-click υπενθύμιση ανείσπρακτων
- **Έξοδα** με OCR από φωτογραφία αποδείξεων
- **Μισθοδοσία** — υπολογισμός καθαρών, εισφορές ΕΦΚΑ, φόρος
- **ΦΠΑ** — αυτόματος υπολογισμός εκροών/εισροών, μηνιαία/τριμηνιαία δήλωση
- **Trust Accounts** (Πελατολογικός Λογαριασμός) — Ν.4194/2013 άρθρο 32

### ⚖️ Νομική Διαχείριση
- **Υποθέσεις** με ΓΑΚ/ΕΑΚ, deep links σε ΣΟΛΩΝ/Ολομέλεια
- **Πινάκια** live tracking με ειδοποιήσεις όταν πλησιάζει η σειρά σας
- **Πινάκιο Τσέπης** — mobile-first PWA για χρήση στο δικαστήριο
- **Γραμμάτια Προείσπραξης** — δημιουργία και παρακολούθηση
- **Παράβολα** — e-Παράβολο integration

### 🤖 AI Εργαλεία (Emergent LLM)
- **Nomos AI Copilot** — φωνητικός/κειμενικός βοηθός με πρόσβαση στα ζωντανά δεδομένα σας
- **Σύνταξη Εγγράφων** — 6 built-in πρότυπα + custom clause library + AI εξατομίκευση
- **OCR Νομικών Εγγράφων** — ανεβάζετε δικαστική απόφαση/δικόγραφο → auto-extract fields
- **Νομολογιακή Αναζήτηση** — AI-assisted έρευνα ελληνικής νομολογίας

### 🛡️ Συμμόρφωση
- **KYC / AML** (Ν.4557/2018) — δέουσα επιμέλεια, risk scoring, PEP tracking
- **myDATA / ΑΑΔΕ** live διαβίβαση (ΤΠΥ 2.1, ΑΠΥ λιανικής 11.2, πιστωτικό 5.1), ακύρωση παραστατικού,
  παρακράτηση φόρου αμοιβών, και λήψη εισερχόμενων παραστατικών εξόδων
- **Ψηφιακές Υπογραφές** (Ν.4727/2020) με SHA-256 hash + timestamp + IP
- **GDPR export** ανά πελάτη (Άρθρα 15 & 20 ΓΚΠΔ)
- **Φορολογικό Ημερολόγιο** με auto-reminders

### 📊 Επιχειρηματική Ευφυΐα
- **Πρόβλεψη Ταμείου 3μήνου** — ανείσπρακτα + λειτουργικά + ΦΠΑ/ΕΦΚΑ
- **Κερδοφορία** — top clients, revenue per case type, collection rate

### 🌐 Client Portal
- **Magic link** πρόσβαση για κάθε πελάτη
- Ο πελάτης βλέπει τιμολόγιά του, υποθέσεις, πληρώνει online με Stripe

---

## 🚀 Γρήγορη Εγκατάσταση (Docker)

### Προαπαιτούμενα
- Docker & Docker Compose
- Domain με SSL (Let's Encrypt συνιστάται)
- Ένα από:
  - **Emergent LLM Key** (για AI features) — βλ. [Emergent](https://emergent.sh)
  - **Stripe account** (test mode λειτουργεί χωρίς κόστος)

### 1. Clone & Configure
```bash
git clone https://github.com/your-org/nomos-one.git
cd nomos-one
cp .env.example .env
# Επεξεργαστείτε το .env με τα δικά σας κλειδιά
```

Το `.env.example` περιέχει τιμές-παραδείγματα `change-me…` για το `JWT_SECRET` και το `MONGO_PASSWORD`. Είναι δημόσιες, γι' αυτό
**ο server αρνείται να ξεκινήσει** όσο μένουν. Ορίστε τυχαίες τιμές (π.χ. `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
και, ξεχωριστά, ένα `SECRETS_KEY` για την κρυπτογράφηση των μυστικών των Ρυθμίσεων. Ορίστε επίσης `PUBLIC_URL` (η διεύθυνση που ανοίγετε
στον browser): από αυτήν προκύπτει η επιτρεπόμενη προέλευση για CORS και για τον έλεγχο CSRF.

### 2. Εκκίνηση
```bash
docker compose up -d
```

Το frontend είναι διαθέσιμο στο `http://localhost:8080` (ή στο `PUBLIC_PORT` που ορίσατε).
Το backend είναι εσωτερικό στο δίκτυο Docker (δεν εκτίθεται δημόσια — προσβάσιμο μόνο μέσω nginx στο frontend).

### 3. Πρώτη Είσοδος
Ο πρώτος admin (`ADMIN_EMAIL`) δημιουργείται αυτόματα. Αν δεν ορίσετε `ADMIN_PASSWORD` στο `.env`,
ο κωδικός παράγεται τυχαία και τυπώνεται **μία φορά** στα logs του backend (`docker compose logs backend`).
Συνδεθείτε και ολοκληρώστε τον **Onboarding Wizard**. Έλεγχος υγείας: `GET /api/health`.

### 4. Backup
Η υπηρεσία `backup` γράφει `mongodump` στον φάκελο `./backups/` κάθε 24 ώρες και κρατά τα αρχεία των τελευταίων 14 ημερών.
Βρίσκονται στον **ίδιο διακομιστή** με τη βάση — αντιγράψτε τον φάκελο και σε δεύτερο μέσο.

Επίσης, από το UI (Ρυθμίσεις → Backup & Επαναφορά) κατεβάζετε ZIP με τα δεδομένα του γραφείου οποιαδήποτε στιγμή. Δεν περιλαμβάνει
χρήστες και κωδικούς, συνδέσμους της πύλης πελατών, αρχεία που έχουν ανέβει ούτε το κλειδί `SECRETS_KEY`. Η επαναφορά **προσθέτει
μόνο όσα λείπουν**: τιμολόγια, πληρωμές, τράπεζα, πελατολογικοί λογαριασμοί, ΦΠΑ και ελεγκτικό ίχνος δεν αντικαθίστανται ποτέ· για
επιστροφή ολόκληρης της βάσης σε παλαιότερη κατάσταση επαναφέρετε σε κενή βάση. Οδηγίες: [`USER_GUIDE.md`](./USER_GUIDE.md),
ενότητα 11. **Η επαναφορά δεν έχει δοκιμαστεί ακόμη σε πραγματικά δεδομένα** — κάντε δοκιμή πριν βασιστείτε σε αντίγραφο.

---

## 🔧 Development

### Local Setup (χωρίς Docker)
```bash
# Backend
cd backend
pip install -r requirements.txt        # ή requirements-ci.txt για ελαφριά, vendor-free εγκατάσταση
export MONGO_URL=mongodb://localhost:27017 DB_NAME=nomos_one JWT_SECRET=dev
uvicorn server:app --reload --port 8001

# Frontend (σε άλλο terminal)
cd frontend
yarn install
yarn start
```

### Tests

```bash
# Backend: 2.903 tests, χωρίς Mongo/δίκτυο (mongomock) — τρέξτε τα σε ΞΕΧΩΡΙΣΤΟ terminal από αυτό που έχει το export παραπάνω
cd backend
MONGO_URL=mongomock:// python -m pytest

# Frontend: 728 tests σε 34 αρχεία (vitest) και ό,τι τρέχει το CI
cd frontend
yarn lint && yarn test && yarn build
```

Πολλά tests σβήνουν συλλογές για να στήσουν τις συνθήκες τους, γι' αυτό **αρνούνται να τρέξουν** αν το `MONGO_URL` του
περιβάλλοντος δείχνει σε πραγματική βάση (εκτός αν ορίσετε ρητά `NOMOS_TESTS_ALLOW_REAL_MONGO=1`, μόνο για τα παλιά E2E που χρειάζονται
ζωντανή εγκατάσταση). Τα tests υποδομής (`tests/test_unit_nginx_lab_r5.py`) τρέχουν τον πραγματικό nginx· στο CI είναι υποχρεωτικά
(`REQUIRE_NGINX_LAB=1`), τοπικά παραλείπονται αν δεν υπάρχει nginx. Η ζώνη ώρας των tests επιλέγεται ώστε να έχει πάντα άλλη
ημερομηνία από την Ελλάδα (`NOMOS_TEST_TZ` για επιβολή συγκεκριμένης).

Βλέπε [`USER_GUIDE.md`](./USER_GUIDE.md) για αναλυτικές οδηγίες χρήσης.

---

## 📁 Αρχιτεκτονική

```
/app/
├── backend/                # FastAPI + Motor (async MongoDB)
│   ├── server.py           # Entry point
│   ├── models.py           # Pydantic models
│   ├── routes_*.py         # 30+ API routers
│   ├── automation.py       # Autopilot: scheduled jobs, inbox, digest
│   ├── worker.py           # Ξεχωριστή διεργασία για τις προγραμματισμένες εργασίες
│   ├── outbox.py           # Ουρά αποστολών με ανάκαμψη από διακοπή
│   ├── openbanking.py      # PSD2 (GoCardless Bank Account Data)
│   ├── vat_return.py       # Έντυπο Φ2 (δήλωση ΦΠΑ)
│   ├── scripts/            # verify_mydata_sandbox.py — επαλήθευση ΑΑΔΕ
│   ├── invoice_services.py # Κοινή λογική email/υπενθύμισης/myDATA (routes + Autopilot)
│   ├── ai_runtime.py       # Προαιρετικός LLM provider (503 αν λείπει)
│   ├── copilot.py          # Nomos AI orchestration
│   ├── mydata_client.py    # ΑΑΔΕ REST client
│   ├── email_utils.py      # SMTP με ελληνικό branding
│   ├── pdf_utils.py        # ReportLab PDF generation
│   └── Dockerfile
├── frontend/               # React 19 + Tailwind + Shadcn UI
│   ├── src/
│   │   ├── pages/          # 25+ pages (Dashboard, Invoices, Cases, KYC, ...)
│   │   ├── components/     # Reusable UI components
│   │   ├── context/        # AuthContext, ViewModeContext
│   │   └── lib/api.js      # Axios client + helpers
│   ├── public/
│   │   ├── manifest.json   # PWA
│   │   └── service-worker.js
│   └── Dockerfile
├── docs/                   # AUTOPILOT, SECURITY, AUDIT (έλεγχος και ετυμηγορία ετοιμότητας), READINESS_ANALYSIS (ιστορική)
├── docker-compose.yml      # 5 services: mongo, backend, worker, frontend, backup
├── .env.example
├── README.md               # (αυτό το αρχείο)
└── USER_GUIDE.md           # Εγχειρίδιο χρήσης στα Ελληνικά
```

---

## 🎨 Design System

- **Θέμα**: Aurora Glass — Deep navy (#0a0f1e) + Aged gold (#c9a227) + Glass morphism
- **Fonts**: Chivo (headings), IBM Plex Sans (body), JetBrains Mono (mono/numbers), Cormorant Garamond (elegant serif)
- **Έμπνευση**: Apple Liquid Glass · Bloomberg Terminal · Vintage νομικά έγγραφα

---

## 🔐 Ασφάλεια

- **Ταυτοποίηση δύο παραγόντων (TOTP)** με εφεδρικούς κωδικούς μιας χρήσης — ο κωδικός μόνος του δεν δίνει συνεδρία
- **Κρυπτογράφηση μυστικών στη βάση** (SMTP/IMAP passwords, subscription key ΑΑΔΕ) με κλειδί `SECRETS_KEY`· δεν επιστρέφονται ποτέ στον browser
- JWT authentication με bcrypt password hashing· ο server **αρνείται να ξεκινήσει** με `JWT_SECRET` ή κωδικό MongoDB ίσο με την τιμή-παράδειγμα `change-me…`
- Rate limiting σε login endpoints
- Προστασία από CSRF σε τρία επίπεδα: cookie `SameSite=Lax` (`COOKIE_SAMESITE`), έλεγχος προέλευσης σε κάθε αίτημα που αλλάζει δεδομένα με cookie συνεδρίας και σε όλα τα `POST /api/auth/…`, και απόρριψη (415) αιτήματος με σώμα και cookie συνεδρίας χωρίς `Content-Type` — λεπτομέρειες στο [`docs/SECURITY.md`](./docs/SECURITY.md)
- CORS και έλεγχος προέλευσης με βάση το `CORS_ORIGINS` (αν δεν οριστεί, το `PUBLIC_URL`)
- Το nginx αντικαθιστά το `X-Forwarded-For` με την πραγματική διεύθυνση του πελάτη· το access log δεν γράφει tokens, query strings ούτε Referer. Αν μπροστά από το nginx υπάρχει δεύτερος proxy, χρειάζεται `real_ip` (βλ. `docs/SECURITY.md`)
- Stripe webhook: υπογραφή υποχρεωτική — χωρίς `STRIPE_WEBHOOK_SECRET` απαντά 503 και δεν κάνει τίποτα· οι εξοφλήσεις τότε εντοπίζονται από τον Autopilot
- Client portal tokens λήγουν σε 30 ημέρες
- Digital signatures με SHA-256 hash επαλήθευση

---

## 📜 Άδεια Χρήσης

Copyright © 2026 — Nomos One. Ιδιωτικό λογισμικό για επαγγελματική χρήση.

Το λογισμικό αυτό δεν αποτελεί υποκατάστατο νομικής/λογιστικής συμβουλής. Οι τελικές αποφάσεις ανήκουν πάντα σε πιστοποιημένο δικηγόρο/λογιστή.

---

## 📞 Υποστήριξη

- Οδηγός Χρήσης: [`USER_GUIDE.md`](./USER_GUIDE.md)
- Issues: `https://github.com/your-org/nomos-one/issues`
