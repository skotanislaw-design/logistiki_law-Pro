# Logistiki.gr — Ψηφιακός Λογιστής για Δικηγορική Εταιρεία

## Original Problem Statement (verbatim, Greek)
Ο χρήστης ζήτησε μία πλήρως λειτουργική, αυτόνομη λογιστική πλατφόρμα που να λειτουργεί σαν έμπειρος λογιστής για τη δικηγορική εταιρεία του χρήστη ("εμείς"). Καλύπτει: ρύθμιση χρεών, ρύθμιση ΦΠΑ, υπολογισμό ΦΠΑ προς πληρωμή, έκδοση τιμολογίων, αγορές/έξοδα, σύνδεση myDATA (ΑΑΔΕ), ΕΦΚΑ, μισθοδοσία, κατηγοριοποίηση παραστατικών, real-time υπολογισμό ΦΠΑ/ΕΦΚΑ/φόρου, έκδοση τιμολογίων με φωνή & φωτο αποδείξεων, προσαρμοσμένη στην ελληνική φορολογία. Ανέφερε επίσης integration με Ολομέλεια.gr (γραμμάτια προείσπραξης δικηγόρων) και αναζήτηση/πληρωμή παραβόλων.

## User Clarifications (gathered via ask_human)
1. Χρήστης: Δικηγορική εταιρεία (single-org, multi-user: admin/accountant/lawyer roles)
2. myDATA/AADE: Δεν υπάρχουν ακόμα real credentials → MOCK/simulated sync flow, clearly labeled, με δυνατότητα real connection μέσω Ρυθμίσεων όταν υπάρχουν credentials
3. Ολομέλεια.gr / παράβολα: **Phase 2 — δεν υλοποιήθηκε** σε αυτή την έκδοση
4. Μισθοδοσία: Πλήρες βάθος (gross-to-net, ΕΦΚΑ εργαζ./εργοδότη, προοδευτικά κλιμάκια φόρου) — εκτιμήσεις, όχι πραγματική ΑΠΔ e-filing
5. AI: OpenAI gpt-5.4-mini (OCR αποδείξεων + φωνητική εντολή τιμολογίου), Whisper (whisper-1) για speech-to-text — μέσω Emergent LLM Key

## Architecture
- **Backend**: FastAPI + MongoDB (Motor), JWT httpOnly-cookie auth, modular routers (auth/clients/invoices/expenses/payroll/dashboard/settings/mydata/tax-calendar), `db.py` BaseDocument/PyObjectId pattern, `tax_utils.py` (Greek VAT/EFKA/tax logic), `ai_helpers.py` (LLM OCR + Whisper via emergentintegrations + EMERGENT_LLM_KEY)
- **Frontend**: React + Tailwind + Shadcn UI, Chivo/IBM Plex Sans/JetBrains Mono fonts, Swiss Brutalist high-contrast theme, collapsible sidebar layout, Recharts for income/expense chart, Sonner toasts, Phosphor icons
- **Auth**: JWT access(30min)/refresh(7d) httpOnly cookies, bcrypt hashing, brute-force lockout (5 attempts/15min), seeded admin

## Core Requirements (static)
- Greek VAT (24/13/6/0%) invoicing & expense tracking with automatic VAT breakdown
- myDATA (AADE) e-invoice submission — MOCK simulation with clear labeling until real credentials provided
- Payroll: gross-to-net with EFKA (13.87% employee / 22.29% employer) + progressive income tax brackets + tax credits
- Greek Tax Calendar: VAT filing/payment, EFKA contribution, withholding tax deadlines
- AI: Photo receipt OCR (gpt-5.4-mini vision) + Voice invoice creation (Whisper + gpt-5.4-mini parsing)
- Responsibility disclaimer shown throughout app (final accuracy/compliance responsibility remains with certified accountant — addresses user's question about liability)

## Implemented (2026-07-14)
- Full auth system (login/logout/me/refresh/forgot-reset password), admin seeded: admin@logistiki.gr / Admin@2026!
- Clients CRUD (ΑΦΜ, ΔΟΥ, τύπος)
- Invoices: create with multi-line items, auto VAT calc, Issue action → mock myDATA MARK/UID generation, voice-command creation (Whisper transcribe → LLM parse → prefill form)
- Expenses: manual entry + AI photo OCR (upload receipt → gpt-5.4-mini extracts vendor/date/amount/vat/category)
- VAT page: output vs input VAT, net due, monthly/quarterly period toggle, next deadline
- myDATA page: mock connection status, per-invoice sync button
- Payroll: employee management, run payroll per period, payslip list with EFKA/tax/net breakdown
- Tax Calendar: dynamic VAT/EFKA/income-tax deadlines
- Settings: company info, VAT period, AADE credentials fields (mock placeholders)
- Dashboard: KPIs, 6-month income/expense chart, recent activity, disclaimer banner
- Fixed: MongoDB `_id`→`id` serialization bug (FastAPI jsonable_encoder by_alias default), useEffect async pattern warnings

## Testing Status
Testing agent run (iteration_1): Backend 17/17 pytest passed (auth, CRUD, VAT/payroll calc, mock myDATA, real AI OCR call verified end-to-end with EMERGENT_LLM_KEY). Frontend smoke-tested (all pages/data-testids present, login/logout flow works). Voice-parse endpoint code-reviewed but not tested with real audio (no sample file available in that run).

## Prioritized Backlog
- **P0**: None outstanding — MVP functional end-to-end
- **P1**: Real AADE myDATA API integration (once user obtains ΑΑΔΕ API credentials); test voice-invoice flow with real Greek audio; friendly error handling for invalid image uploads in OCR
- **P2**: Ολομέλεια.gr integration — search & issue γραμμάτια προείσπραξης; παράβολα search & payment; real ΕΦΚΑ ΑΠΔ e-filing; multi-firm/multi-tenant support if needed later; invoice PDF export/print

## Next Tasks
1. User to review MVP and provide feedback
2. When ready, gather real ΑΑΔΕ myDATA API credentials to replace mock flow
3. Plan Phase 2: Ολομέλεια.gr γραμμάτια + παράβολα module (needs research into whether Ολομέλεια.gr / e-paravolo.gr expose any usable API or require scraping — should be scoped carefully due to legal/portal access constraints)

## Implemented (2026-06 fork — Rebranding & PDF)
- **Rebranding σε "Nomos One — Legal Operations Platform"**: dark navy + gold θέμα σε όλη την εφαρμογή (index.css tokens), λογότυπο χρήστη (/public/nomos-logo.png + favicon), sidebar με ομάδες πλοήγησης (ΚΥΡΙΑ/ΟΙΚΟΝΟΜΙΚΑ/ΣΥΜΜΟΡΦΩΣΗ/ΓΡΑΦΕΙΟ) & χρυσά active states, νέο Topbar (τίτλος σελίδας, live ρολόι, avatar χρήστη), footer "Nomos One v1.0 — Σκοτάνης & Συνεργάτες", redesigned Login. Font: Cormorant Garamond (brand).
- **PDF Export Τιμολογίων**: GET /api/invoices/{id}/pdf (reportlab, FreeSans ελληνικά, navy/gold layout: header, στοιχεία πελάτη, γραμμές, ανάλυση ΦΠΑ, ΠΛΗΡΩΤΕΟ, στοιχεία πληρωμής IBAN/τράπεζα, myDATA MARK). Κουμπί PDF σε κάθε γραμμή τιμολογίου + στο detail dialog + ΑΥΤΟΜΑΤΗ λήψη PDF μετά την έκδοση τιμολογίου.
- **Settings επεκτάθηκαν**: address, phone, email, IBAN, bank_name (εμφανίζονται στο PDF).
- Tested: curl login→invoices→PDF (200, έγκυρο PDF, σωστά ελληνικά επιβεβαιωμένα με extraction), settings PUT με νέα πεδία OK, UI smoke screenshots (login/dashboard/invoices) OK.

## User decisions (2026-06 fork)
- Άντληση παραστατικών: ΚΑΙ Telegram bot ΚΑΙ Email (IMAP) → OCR → Έξοδα → αντιπαραβολή με myDATA (πάνω στο mock τώρα, έτοιμο για live AADE μετά).
- Επιβεβαίωση χρήστη ΠΡΙΝ το τελικό βήμα έκδοσης γραμματίου προείσπραξης (olomeleia.gr) και παραβόλου (e-paravolo) — υποχρεωτικό confirm step στο Phase 2.
- Ο χρήστης έχει ήδη Telegram (χρειάζεται bot token από @BotFather στο επόμενο βήμα).

## Next Tasks (updated)
1. **P0**: Άντληση παραστατικών από Telegram bot + Email (IMAP) → OCR → Έξοδα + αντιπαραβολή myDATA (mock). Χρειάζεται: Telegram bot token, email IMAP credentials.
2. **P0**: Ολομέλεια.gr γραμμάτια + e-paravolo (με υποχρεωτική επιβεβαίωση πριν το τελικό βήμα έκδοσης).
3. **P2**: Real AADE myDATA σύνδεση όταν δοθούν διαπιστευτήρια.

## Implemented (2026-06 fork — Email αποστολή + Premium UI)
- **One-click αποστολή τιμολογίου PDF στον πελάτη**: POST /api/invoices/{id}/send-email (aiosmtplib, SMTP με App Password του γραφείου, STARTTLS 587 / SSL 465 auto). Ελληνικό HTML email με navy/gold branding, ποσό, τρόπο πληρωμής, IBAN + συνημμένο PDF. Κουμπί φακέλου σε κάθε γραμμή τιμολογίου (πράσινο όταν έχει σταλεί, tooltip με παραλήπτη) + κουμπί «Αποστολή στον πελάτη» στο detail dialog. Πεδία email_sent_at/email_sent_to στο Invoice.
- **SMTP Settings**: νέα κάρτα στις Ρυθμίσεις (host/port/user/app password/όνομα αποστολέα) + κουμπί «Αποστολή δοκιμαστικού email» (POST /api/settings/test-email). Φιλικά ελληνικά μηνύματα λάθους (400, όχι 502 — το Cloudflare καταπίνει 502). SMTP egress 587/465 επιβεβαιωμένα ΑΝΟΙΧΤΑ από το cluster. ΕΚΚΡΕΜΕΙ: ο χρήστης να βάλει πραγματικό App Password για live αποστολή.
- **Premium UI redesign** (design_agent blueprint στο /app/design_guidelines.json): lux-card (gold-tinted borders, hover lift+glow), stagger entrance animations, font-brand (Cormorant Garamond) σε τίτλους σελίδων με gold overlines «NOMOS ONE · ...», gold-rule διακοσμητικά με ◆, χρυσές μπάρες στο chart, glassmorphic topbar με ρολόι + gold ring avatar, gradient active states στο sidebar, uppercase table headers.
- **Settings προσυμπληρωμένα** από letterhead: Σκοτάνης & Συνεργάτες, Αθήνα Βαλαωρίτου 17 Κολωνάκι, +30 2289 100 111, info@skotanislaw.gr.
- Νέα assets χρήστη: 2 Word templates (.dotx), Case Intelligence Engine οδηγός (HTML), SKOTANIS_Letterhead_Premium.pdf — URLs στο chat/artifacts.
- Testing: testing_agent iteration_3 — frontend 100% pass (όλες οι σελίδες, PDF downloads, email error paths, settings persistence). Radix aria warning διορθώθηκε.

## Implemented (2026-06 fork — Nomos AI Copilot + Έγγραφα Letterhead)
- **Nomos AI Copilot** (/copilot, sidebar ομάδα «AI ΕΡΓΑΛΕΙΑ»): chat με gpt-5.4-mini (emergentintegrations, EMERGENT_LLM_KEY) που έχει ΖΩΝΤΑΝΟ οικονομικό context (έσοδα/έξοδα/ΦΠΑ μήνα, ανεξόφλητα, πελάτες, προθεσμίες — copilot.py build_context). Ενέργειες μέσω JSON protocol: create_invoice (φυσική γλώσσα → πραγματικό draft τιμολόγιο με σωστά totals + number) και draft_document (σύνταξη επιστολής → preview card με λήψη Word/PDF). Multi-turn με session_id (sessionStorage), ιστορικό σε db.copilot_messages, GET /api/copilot/history/{session}. Endpoints: POST /api/copilot/chat.
- **Έγγραφα** (/documents): φόρμα (παραλήπτης/θέμα/περιεχόμενο) + «Σύνταξη με AI» (POST /api/documents/ai-draft) + ζωντανή λευκή προεπισκόπηση letterhead + Λήψη Word (python-docx, POST /api/documents/word) και PDF (reportlab, POST /api/documents/pdf) με το ΠΡΑΓΜΑΤΙΚΟ logo SKOTANIS & ASSOCIATES (εξήχθη από το letterhead PDF του χρήστη → /app/backend/assets/letterhead_logo.png + /app/frontend/public/letterhead-logo.png), χρυσές γραμμές, ελληνική ημερομηνία, footer με στοιχεία από Settings.
- Tested via curl E2E: ΦΠΑ ερώτηση με σωστά live ποσά, «Φτιάξε τιμολόγιο 800€ για τον Παπαδόπουλο» → ΤΠΥ-2026-0005 (992€ με ΦΠΑ), multi-turn follow-up OK, επιστολή όχλησης → Word 372KB + PDF 445KB με άψογα ελληνικά (verified extraction). UI screenshots: copilot chat στέλνει/λαμβάνει live, documents preview OK.
- Libraries added: python-docx, pymupdf (logo extraction).
