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
- Συμφωνεί τις τραπεζικές κινήσεις και εξοφλεί τιμολόγια αυτόματα όταν η βεβαιότητα είναι ≥ 90%
- Κλείνει κάθε περίοδο ΦΠΑ και σας λέει τι πληρώνετε και πότε, 7 και 1 ημέρα πριν
- Διαβάζει αποδείξεις που φτάνουν στο email του γραφείου (IMAP + OCR) και τις καταχωρεί ως έξοδα
- Σας στέλνει **ένα** ενημερωτικό email (καθημερινά ή κάθε Δευτέρα) με ό,τι έκανε και ό,τι περιμένει απόφαση

Σελίδα `/autopilot`, τεκμηρίωση: [`docs/AUTOPILOT.md`](./docs/AUTOPILOT.md). Ανάλυση ετοιμότητας: [`docs/READINESS_ANALYSIS.md`](./docs/READINESS_ANALYSIS.md).

---

## 🌟 Κύρια Χαρακτηριστικά

### 💼 Οικονομικά & Λογιστικά
- **Τιμολόγηση** με αυτόματη σειροποίηση (ΤΠΥ-YYYY-NNNN), PDF letterhead, email αποστολή, 1-click υπενθύμιση ανείσπρακτων
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
Τα Mongo backups γράφονται αυτόματα καθημερινά στο `./backups/`. Διατηρούνται για 14 ημέρες.

Επίσης, από το UI (Ρυθμίσεις → Backup) μπορείτε να κατεβάσετε πλήρες ZIP οποιαδήποτε στιγμή.

---

## 🔧 Development

### Local Setup (χωρίς Docker)
```bash
# Backend
cd backend
pip install -r requirements.txt        # ή requirements-ci.txt για ελαφριά, vendor-free εγκατάσταση
export MONGO_URL=mongodb://localhost:27017 DB_NAME=nomos_one JWT_SECRET=dev
uvicorn server:app --reload --port 8001

# Unit tests (χωρίς Mongo/δίκτυο)
python -m pytest

# Frontend (σε άλλο terminal)
cd frontend
yarn install
yarn start
```

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
├── docker-compose.yml      # 4 services: mongo, backend, frontend, backup
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
- JWT authentication με bcrypt password hashing
- Rate limiting σε login endpoints
- CORS restricted σε PUBLIC_URL
- Stripe webhook signature verification
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
