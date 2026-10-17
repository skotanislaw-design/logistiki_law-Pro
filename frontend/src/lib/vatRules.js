/**
 * Κατηγορίες εξόδων και ποσοστά εκπτωσιμότητας ΦΠΑ εισροών.
 *
 * Η λίστα πρέπει να συμπίπτει με το `EXPENSE_CATEGORIES` του backend/models.py (ο συγχρονισμός ελέγχεται από το
 * backend/tests/test_unit_vat_rules_api.py). Δεν υπάρχει εδώ κανένα προεπιλεγμένο ποσοστό: ποια κατηγορία εκπίπτει και πόσο
 * το αποφασίζει ο λογιστής του γραφείου και καταχωρείται στις Ρυθμίσεις — βλ. backend/vat_rules.py.
 */
export const EXPENSE_CATEGORIES = [
  "Ενοίκιο", "Λογαριασμοί ΔΕΚΟ", "Προμήθειες γραφείου", "Δικηγορικά έξοδα",
  "Αμοιβές τρίτων", "Μεταφορικά", "Τηλεπικοινωνίες", "Ασφάλιστρα",
  "Συνδρομές / Λογισμικό", "Φιλοξενία / Εστίαση", "Λοιπά",
];

/**
 * Το ποσοστό (0–100) που πληκτρολογήθηκε: αριθμός, `undefined` αν το πεδίο είναι κενό («δεν υπάρχει κανόνας»), `null` αν
 * δεν είναι έγκυρο. Το άκυρο κείμενο δεν σημαίνει «κράτα την παλιά τιμή» — η οθόνη το δείχνει ως σφάλμα και δεν αφήνει να
 * αποθηκευτούν οι ρυθμίσεις. Δέχεται και κόμμα ως υποδιαστολή.
 */
export function parsePercent(text) {
  const clean = String(text ?? "").trim().replace(",", ".");
  if (clean === "") return undefined;
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(clean)) return null;
  const percent = Number(clean);
  return percent >= 0 && percent <= 100 ? percent : null;
}

/** Υπογραφή του πίνακα, ανεξάρτητη από τη σειρά των κατηγοριών — για να φαίνεται αν άλλαξε. */
export const rulesSignature = (rules) =>
  JSON.stringify(Object.entries(rules || {}).sort(([a], [b]) => a.localeCompare(b)));

/** Ο πίνακας και η επιβεβαίωση του λογιστή όπως τα έστειλε ο server (φόρτωση ή αποθήκευση) — η βάση σύγκρισης για ό,τι αλλάζει η φόρμα. */
export const vatBaseline = (settings) => ({
  rules: (settings && settings.vat_deductibility_rules) || {},
  confirmedBy: (settings && settings.vat_rules_confirmed_by) || "",
});

/** Άλλαξε ο πίνακας σε αυτή τη φόρμα, σε σχέση με όπως φορτώθηκε; (Χωρίς βάση σύγκρισης δεν θεωρείται ότι άλλαξε.) */
export const vatRulesEdited = (form, baseline) =>
  !!baseline && rulesSignature(form.vat_deductibility_rules) !== rulesSignature(baseline.rules);

/**
 * Το σώμα του `PUT /settings` της οθόνης Ρυθμίσεων.
 *
 * Η φόρμα φορτώνεται μία φορά και μένει ανοιχτή για ώρα. Ο πίνακας εκπτωσιμότητας ΦΠΑ και η επιβεβαίωση του λογιστή
 * στέλνονται **μόνο αν τα άλλαξε ο χρήστης σε αυτή τη φόρμα**: αλλιώς μια παλιά καρτέλα, που αποθηκεύει π.χ. τη διεύθυνση του
 * γραφείου, θα έγραφε πάνω στον πίνακα όσα άλλαξαν στο μεταξύ (από άλλη καρτέλα ή άλλον διαχειριστή), και μαζί και την
 * επιβεβαίωση που είχε δοθεί για κάποιον άλλον πίνακα. Ό,τι δεν στέλνεται δεν αλλάζει στον server.
 * Αν αλλάξει ο πίνακας και δεν αλλάξει το όνομα, ο server αποσύρει την επιβεβαίωση (βλ. backend/routes_settings.py).
 */
export function settingsPayload(form, baseline) {
  const payload = { ...form };
  if (!vatRulesEdited(form, baseline)) delete payload.vat_deductibility_rules;
  if (!baseline || (form.vat_rules_confirmed_by || "") === baseline.confirmedBy) delete payload.vat_rules_confirmed_by;
  return payload;
}

/** Το σώμα του `PUT /settings` για οθόνες που δεν δείχνουν τον πίνακα ΦΠΑ (π.χ. Onboarding): ο πίνακας και η επιβεβαίωση δεν στέλνονται ποτέ. */
export function settingsPayloadWithoutVatRules(settings) {
  const payload = { ...settings };
  delete payload.vat_deductibility_rules;
  delete payload.vat_rules_confirmed_by;
  return payload;
}

/** Ανακλήθηκε η επιβεβαίωση του λογιστή από τον server, επειδή άλλαξε ο πίνακας χωρίς νέα επιβεβαίωση; */
export const confirmationWithdrawn = (form, baseline, saved) =>
  vatRulesEdited(form, baseline) && !!baseline.confirmedBy.trim() && !((saved && saved.vat_rules_confirmed_by) || "").trim();
