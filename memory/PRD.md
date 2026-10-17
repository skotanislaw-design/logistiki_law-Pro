# Nomos One — Product Requirements Document

**Τελευταία ενημέρωση**: Φεβρουάριος 2026 · Version 2.0 (post Batch D)

## 🎯 Vision
Ολοκληρωμένη ψηφιακή πλατφόρμα λογιστικών, νομικής διαχείρισης και επιχειρηματικής ευφυΐας για ελληνικά δικηγορικά γραφεία. Απαιτεί εγκατάσταση σε 5 λεπτά (Docker), λειτουργεί offline, συμμορφώνεται με ΓΚΠΔ + Ν.4557/2018 + Ν.4194/2013 + Ν.4727/2020.

## 👥 Personas
- **Δικηγόρος-Ιδιοκτήτης**: Χρειάζεται overview υπόθεσης, ταμείου, ανείσπρακτων. Θέλει AI Copilot που εκτελεί εργασίες με φωνή.
- **Δικηγόρος-Συνεργάτης**: Χρειάζεται πινάκιο, γραμμάτια, drafting εγγράφων.
- **Γραμματέας**: Καταχώρηση εξόδων, ραντεβού, εισαγωγή πελατών.
- **Πελάτης**: Θέλει να βλέπει τιμολόγιά του και να πληρώνει online.

---

## ✅ Ολοκληρωμένα (2026-02-04)

### Batch A — Foundations
- [x] Trust Accounts (Πελατολογικός Λογαριασμός) — Ν.4194/2013
- [x] Onboarding Wizard 4 βημάτων
- [x] Import Πελατολογίου από Excel/CSV
- [x] Backup / Restore ZIP + GDPR export ανά πελάτη

### Batch B — Compliance & BI
- [x] KYC / AML — Ν.4557/2018 (risk scoring, PEP, FATF countries)
- [x] Cash Flow Forecast 3μήνου
- [x] Profitability Analytics (top clients, per case type, collection rate)

### Batch C — Client-facing
- [x] Client Portal με magic link (30 days expiry)
- [x] Stripe Online Πληρωμές (sandbox provisioned) — checkout + webhook
- [x] Payment link σε reminder emails
- [x] E-signature με SHA-256 hash + timestamp (Ν.4727/2020)

### Batch D — AI + Mobile + Deployment
- [x] AI Drafting με 6 built-in πρότυπα + 8 clause library + custom clauses
- [x] OCR Νομικών Εγγράφων (PDF/image vision LLM)
- [x] Νομολογιακή Αναζήτηση (Claude Sonnet 5)
- [x] PWA — manifest + service worker + installable
- [x] Πινάκιο Τσέπης — mobile-first live tracking
- [x] Docker + docker-compose + nginx + auto backup
- [x] README.md + USER_GUIDE.md στα Ελληνικά

### Προηγούμενα (Phase 1 + 2)
- [x] JWT Auth + admin bootstrap
- [x] Dashboard με KPIs + AI advice
- [x] Τιμολόγηση (atomic numbering, PDF, email, myDATA, reminder, mark-paid)
- [x] Έξοδα με OCR
- [x] Μισθοδοσία
- [x] ΦΠΑ Auto-calc
- [x] Cases με ΓΑΚ/ΕΑΚ + deep links ΣΟΛΩΝ
- [x] Πινάκια live tracking + alerts
- [x] Γραμμάτια + Παράβολα + integration links
- [x] Nomos AI Copilot (φωνή + κείμενο)
- [x] Έγγραφα με letterhead (DOCX + PDF)
- [x] Aurora Glass UI theme
- [x] Test Connection ΑΑΔΕ

---

## 🎨 Design System
- **Colors**: Deep navy (#0a0f1e), Aged gold (#c9a227), Success #4ade80, Destructive #ef4444
- **Fonts**: Chivo (brand), IBM Plex Sans (body), JetBrains Mono (numbers), Cormorant Garamond (elegant)
- **Aesthetic**: Aurora Glass + Liquid — Apple-inspired luxury feel

---

## 🔧 Tech Stack
- **Backend**: FastAPI (Python 3.11), Motor (async MongoDB), Pydantic v2, Uvicorn
- **Database**: MongoDB 7
- **Frontend**: React 19, Tailwind CSS, Shadcn UI, Framer Motion, Recharts, Axios
- **AI**: Emergent LLM Key (OpenAI GPT-5.4-mini, Anthropic Claude Sonnet 5)
- **PDF/DOCX**: ReportLab, python-docx
- **Email**: aiosmtplib με ελληνικό branding
- **Payments**: Stripe (test + live)
- **Auth**: JWT + bcrypt
- **Deployment**: Docker + docker-compose + nginx

---

## 📦 Νέες Συλλογές MongoDB (Batch B+C+D)
- `kyc` — KYC records ανά πελάτη
- `trust_accounts` + `trust_transactions`
- `signatures` — Ψηφιακές υπογραφές
- `payment_transactions` — Stripe payment tracking
- `client_portal_tokens` — Magic links
- `drafting_clauses` + `drafting_templates` — Custom clauses/templates
- `counters` — Atomic invoice numbering
- `audit_log` — (placeholder — future)

---

## 🤖 Ψηφιακός λογιστής (σε εξέλιξη — 17/10/2026)

Στόχος: όσο το δυνατόν μεγαλύτερη αυτοματοποίηση της δουλειάς του λογιστή για τα απλογραφικά βιβλία δικηγόρου
(εισροές–εκροές, ΦΠΑ, τιμολόγηση ανά υπόθεση, έξοδα ανά πελάτη/δικηγόρο), με κανάλι Telegram.
**Αποφάσεις Managing Partner:** Telegram πρώτο κανάλι· συνομιλητής μόνο ο ίδιος· **αυτόματη καταχώριση (επίπεδο Γ)
απορρίφθηκε** — ο agent λειτουργεί υπό διαρκή επιτήρηση (πρόταση + ένα πάτημα)· τίποτα στο `main` χωρίς εντολή του.
Σχεδιασμός, κόκκινες γραμμές και φάσεις: [`docs/ACCOUNTANT_AGENT.md`](../docs/ACCOUNTANT_AGENT.md).

- [x] **Φάση 1 — μοντέλο δεδομένων** (branch `feat/accountant-agent-phase1`, δεν έχει συγχωνευθεί): έξοδα και τιμολόγια
  ανά πελάτη/υπόθεση/δικηγόρο, υπεύθυνος δικηγόρος υπόθεσης, ΦΠΑ εισροών μόνο στο εκπιπτόμενο μέρος με πίνακα κανόνων
  που ορίζει ο λογιστής. Μετά από ανεξάρτητη ανασκόπηση του κώδικα (16 ευρήματα, κανένα κρίσιμο· όλα διορθώθηκαν ή
  καταγράφηκαν στο `docs/AUDIT.md`): ό,τι δεν σταλεί δεν αλλάζει (και οι οθόνες στέλνουν μόνο ό,τι άλλαξε ο χρήστης),
  η «Υποβλήθηκε» ΦΠΑ είναι μόνο για διαχειριστή/λογιστή και παγώνει και τους αριθμούς, η επιβεβαίωση του λογιστή
  ανακαλείται όταν αλλάζει ο πίνακας, τα ποσά εξόδων στρογγυλοποιούνται στο λεπτό.
- [ ] Φάση 2 — Telegram bot (προτάσεις με Έγκριση/Απόρριψη, μόνο ο Managing Partner) — **προς έγκριση σχεδιασμού**
- [ ] Φάσεις 3–5 — καταγραφή εξόδων με πρόταση, μηνιαίο κλείσιμο/Φ2, τιμολόγηση υπόθεσης (ενδεικτικά)

---

## 🔮 Backlog / Future

### P1 (πιθανή επόμενη φάση)
- **Telegram Bot** για push notifications πινακίων (το bot συνομιλίας του «ψηφιακού λογιστή» είναι ξεχωριστό — βλ. παραπάνω)
- **Deadline Calculator** (προθεσμίες Ν.4335/2015)
- **Conflict-of-interest check** στους πελάτες
- **Multi-user + roles** με granular permissions
- **Two-factor auth**

### P2 (nice to have)
- **Real Isokratis API** integration (αν διαθέσιμο)
- **Voice-to-drafting** — υπαγόρευση δικογράφου
- **Time-tracking** με billable hours + auto-invoice generation
- **iOS/Android native apps** (React Native)
- **Multi-language** (EN έκδοση)

### P3 (long-term)
- **Multi-tenant SaaS** μοντέλο
- **Λογιστική ροή Ε3/Ε5** αυτόματης υποβολής
- **Trust account με MΟΝΤΕΛΟ blockchain** για audit trail

---

## 📝 Κρίσιμες Σημειώσεις
- **Emergent LLM Key** στο `/app/backend/.env` — απαιτούμενο για όλα τα AI features
- **Stripe sandbox** ήδη provisioned — αντικαταστήστε με production keys όταν είστε έτοιμοι
- **Onboarding** εμφανίζεται αυτόματα την πρώτη είσοδο (settings.onboarding_completed=false)
- **AI Copilot** πάντα ζητά επιβεβαίωση πριν εκτελέσει ενέργειες (create_invoice, draft_document κ.λπ.)
- Ο Backup ZIP περιλαμβάνει 20 collections — για πλήρη restore
