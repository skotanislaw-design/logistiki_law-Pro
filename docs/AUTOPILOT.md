# Autopilot — Αυτόματη λογιστική χωρίς διάδραση

Το Autopilot είναι ο μηχανισμός που κάνει το Nomos One να «τρέχει μόνο του».
Ζει στον server (`backend/automation.py`), εκτελείται κάθε 15 λεπτά (ρυθμιζόμενο) για κάθε γραφείο (tenant),
και ενοχλεί τον χρήστη **μόνο** όταν χρειάζεται απόφαση που δεν μπορεί να πάρει μόνο του.

## Τι κάνει μόνο του

| Εργασία | Πότε | Τι κάνει | Αν αποτύχει |
|---|---|---|---|
| Αποστολή τιμολογίου | Αμέσως μετά την έκδοση και σε κάθε κύκλο | Στέλνει PDF στο email του πελάτη | «Απόφαση»: πελάτης χωρίς email / λάθος SMTP |
| Υπενθυμίσεις | 30 ημέρες μετά την έκδοση, μετά κάθε 15, έως 3 φορές (ρυθμίσιμο) | Ευγενική υπενθύμιση με PDF και payment link (Stripe) | «Απόφαση» |
| myDATA | Κάθε κύκλο, με exponential backoff (15′, 30′, 1h, 2h, 4h, 8h) | Διαβιβάζει εκκρεμή/αποτυχημένα παραστατικά στην ΑΑΔΕ | Μετά 6 προσπάθειες → κρίσιμη «απόφαση» με το μήνυμα της ΑΑΔΕ |
| Συμφωνία τράπεζας | Κάθε κύκλο | Auto-match ≥ 90% → τιμολόγιο εξοφλημένο. Κάτω από το όριο → ερώτηση «Ταιριάζει;» με κουμπιά Ναι/Όχι/Αγνόησε | — |
| Κλείσιμο ΦΠΑ | Μόλις κλείσει μήνας/τρίμηνο | Υπολογίζει εκροές, εισροές, ΦΠΑ περιόδου, προθεσμία. Αποθηκεύει στο `vat_periods` | — |
| Προθεσμίες | 7 και 1 ημέρα πριν (ρυθμίσιμο) | Ειδοποίηση ΦΠΑ/ΕΦΚΑ/παρακρατούμενου με το εκτιμώμενο ποσό | — |
| Πρόχειρα | Μετά 7 ημέρες | «Ξέχασες να εκδώσεις το ΤΠΥ-…;» | — |
| Παραστατικά από email | Κάθε κύκλο (αν ενεργό IMAP) | Διαβάζει συνημμένα PDF/εικόνες, κάνει OCR, καταχωρεί έξοδο | Χωρίς AI/ποσό → έξοδο «προς έλεγχο» + απόφαση |
| Digest | Καθημερινά ή κάθε Δευτέρα 07:00 (ρυθμίσιμο) | Ένα email: KPIs μήνα, τι έκανε, τι χρειάζεται απόφαση, προθεσμίες, ανείσπρακτα | — |

## Πώς ενημερώνεται ο χρήστης

1. **Digest email** — ένα μήνυμα την ημέρα ή την εβδομάδα. Αν δεν χρειάζεται τίποτα, λέει «Τίποτα — όλα τρέχουν αυτόματα ✔».
2. **Autopilot inbox** (`/autopilot`) — λίστα «Χρειάζονται απόφαση» με κουμπιά μιας κίνησης.
3. **Καμπανάκι** — τα ίδια θέματα εμφανίζονται στο bell του topbar.
4. **Κάρτα στο Dashboard** — «Autopilot ενεργό — N θέματα χρειάζονται την απόφασή σας».

## Ρυθμίσεις (Settings model, PATCH `/api/settings`)

| Πεδίο | Default | Περιγραφή |
|---|---|---|
| `autopilot_enabled` | true | Γενικός διακόπτης |
| `auto_email_on_issue` | true | Αποστολή PDF στην έκδοση |
| `auto_reminders` | true | Αυτόματες υπενθυμίσεις |
| `reminder_after_days` / `reminder_interval_days` / `reminder_max` | 30 / 15 / 3 | Πολιτική υπενθυμίσεων |
| `auto_mydata_retry` | true | Επανάληψη διαβίβασης |
| `auto_reconcile_min_confidence` | 90 | Όριο auto-εξόφλησης (0 = ποτέ αυτόματα) |
| `stale_draft_days` | 7 | Ειδοποίηση για πρόχειρα |
| `deadline_alert_days` | [7, 1] | Πότε ειδοποιεί για προθεσμίες |
| `digest_frequency` / `digest_hour` / `digest_email` | weekly / 7 / (SMTP user) | Ενημερωτικό |
| `imap_*` | off | Σάρωση γραμματοκιβωτίου για παραστατικά |

## API

| Endpoint | Ρόλος | Περιγραφή |
|---|---|---|
| `GET /api/automation/status` | user | Κατάσταση, δυνατότητες, στατιστικά 7 ημερών, τελευταίες εκτελέσεις ανά job |
| `GET /api/automation/notifications?unresolved=1` | user | Inbox |
| `POST /api/automation/notifications/{id}/resolve` `{decision}` | user | Απόφαση: `done`, `confirm`, `unmatch`, `ignore`, `delete` |
| `POST /api/automation/notifications/read-all` | user | Σήμανση ως αναγνωσμένα |
| `POST /api/automation/notifications/dismiss-informational` | user | Καθαρισμός feed |
| `POST /api/automation/run` `{jobs?}` | admin | Εκτέλεση τώρα για το γραφείο |
| `GET /api/automation/runs` | user | Ιστορικό εκτελέσεων |
| `GET /api/automation/digest/preview` / `POST …/send-now` | user / admin | Προεπισκόπηση / αποστολή digest |
| `GET /api/automation/vat-periods` / `POST …/{period}/submitted` | user | Περίοδοι ΦΠΑ |
| `POST /api/automation/cycle` | admin | Πλήρης κύκλος για όλα τα γραφεία |
| `GET /api/health` | public | Liveness probe (χρησιμοποιείται από Docker) |

## Αρχιτεκτονική

```
server.py ── APScheduler (κάθε 15′) ──► automation.run_autopilot_cycle()
                                           │  Mongo lease lock (μόνο ένας worker)
                                           ▼
                                    για κάθε firm_id ──► run_firm()
                                           │
              ┌────────────────────────────┼────────────────────────────┐
              ▼                            ▼                            ▼
     invoice_services.py           notifications (inbox)         automation_runs (audit)
   email / reminder / myDATA        dedupe_key, actions           status, stats, error
```

* Όλα τα jobs είναι **idempotent**: το ίδιο τιμολόγιο δεν στέλνεται δύο φορές, η ίδια ειδοποίηση δεν
  δημιουργείται ξανά όσο είναι ανοιχτή (`dedupe_key`).
* Το `invoice_services.py` είναι ο **μοναδικός** κώδικας που στέλνει email/ΑΑΔΕ. Τον καλούν και τα routes και το Autopilot.
* Το lock (`automation_locks`) εγγυάται ότι με 2 uvicorn workers ή πολλά replicas ο κύκλος τρέχει μία φορά.
* Χωρίς SMTP → τα jobs που στέλνουν email σημειώνονται `skipped` (όχι error). Χωρίς credentials ΑΑΔΕ → μία ανοιχτή
  «απόφαση» που κλείνει αυτόματα μόλις γίνει η πρώτη επιτυχής διαβίβαση.

## Δοκιμές

```bash
cd backend
pip install -r requirements-ci.txt
python -m pytest            # 30 offline tests, in-memory Mongo, χωρίς δίκτυο
```

`tests/test_unit_autopilot.py` καλύπτει τις πολιτικές (υπενθυμίσεις, backoff, περίοδοι ΦΠΑ, digest schedule)
και έναν πλήρη κύκλο end-to-end με ψεύτικο SMTP/ΑΑΔΕ.
