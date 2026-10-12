/**
 * Κατηγορίες παρακράτησης του myDATA (Παράρτημα 8.4 του οδηγού ERP) και το συνήθως ισχύον ποσοστό τους.
 *
 * Πρέπει να συμπίπτουν με το `WITHHOLDING_CATEGORIES` του backend/mydata_client.py: ο server απορρίπτει κάθε άλλη τιμή
 * (PUT/PATCH /api/settings), οπότε μια επιλογή εδώ που λείπει από εκεί θα αποθηκευόταν με σφάλμα. Ο συγχρονισμός
 * ελέγχεται από το backend/tests/test_unit_payroll_settings_r5.py.
 *
 * Το ποσοστό είναι η προτεινόμενη τιμή όταν αλλάζει η κατηγορία — ο λογιστής μπορεί να το διορθώσει.
 */
export const WITHHOLDING_CATEGORIES = [
  { value: "2", label: "2 — Δικαιώματα", rate: 0.2 },
  { value: "3", label: "3 — Αμοιβές συμβούλων διοίκησης", rate: 0.2 },
  { value: "10", label: "10 — Προκαταβλητέος φόρος αμοιβών δικηγόρων", rate: 0.15 },
];

// Ο server απορρίπτει ποσοστό πάνω από 50% (backend/routes_settings.py: MAX_WITHHOLDING_RATE = 0.5).
export const MAX_WITHHOLDING_PERCENT = 50;

/**
 * Το ποσοστό (%) που πληκτρολογήθηκε στο πεδίο: αριθμός από 0 έως 50, αλλιώς `null`.
 *
 * Κενό ή μη αριθμητικό κείμενο **δεν** είναι «κράτα την παλιά τιμή»: η οθόνη το δείχνει ως σφάλμα και δεν αφήνει να
 * αποθηκευτούν οι ρυθμίσεις, αλλιώς το πεδίο θα έδειχνε κενό ενώ θα αποθηκευόταν σιωπηλά το προηγούμενο ποσοστό.
 * Δέχεται και κόμμα ως υποδιαστολή («12,5»).
 */
export function parseRatePercent(text) {
  const clean = String(text ?? "").trim().replace(",", ".");
  // Μόνο δεκαδική μορφή: το `Number()` θα δεχόταν και «0x10», «1e1» ή «Infinity».
  if (!/^(\d+(\.\d*)?|\.\d+)$/.test(clean)) return null;
  const percent = Number(clean);
  return percent >= 0 && percent <= MAX_WITHHOLDING_PERCENT ? percent : null;
}

// Το κλάσμα που αποθηκεύεται (0,125 για «12,5»), στρογγυλεμένο στα 2 δεκαδικά του ποσοστού ώστε να μη φτάνουν
// στον server αριθμοί όπως 0,07100000000000001.
export function percentToRate(percent) {
  return Math.round(percent * 100) / 10000;
}
