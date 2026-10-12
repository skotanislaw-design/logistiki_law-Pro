import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

// Ο αριθμός του παραστατικού δίνεται στην έκδοση· το πρόχειρο δεν έχει ακόμη.
export function invoiceLabel(invoice) {
  return invoice?.number || "Πρόχειρο";
}

// Όλες οι ημερομηνίες της εφαρμογής είναι ημερομηνίες **Ελλάδας**: οι προθεσμίες, η αρίθμηση και οι αναφορές του server
// υπολογίζονται εκεί. Ο browser όμως έχει τη ζώνη της συσκευής — και το `toISOString()` δίνει πάντα UTC, που μετά τις
// 00:00 τοπική ώρα είναι ακόμη η χθεσινή ημέρα. Γι' αυτό καμία οθόνη δεν διαβάζει `new Date().getDate()/toISOString()`
// για «σήμερα»· περνούν όλες από τα παρακάτω.
export const ATHENS_TZ = "Europe/Athens";

// Η σημερινή ημερομηνία στην Ελλάδα (ΕΕΕΕ-ΜΜ-ΗΗ). Το ισοδύναμο της `athens_today()` του server.
export function todayAthens() {
  return new Date().toLocaleDateString("sv-SE", { timeZone: ATHENS_TZ });
}

// Ο τρέχων μήνας στην Ελλάδα (ΕΕΕΕ-ΜΜ) και το τρέχον έτος.
export function monthAthens() {
  return todayAthens().slice(0, 7);
}
export function yearAthens() {
  return Number(todayAthens().slice(0, 4));
}

// Ημερομηνία ή σφραγίδα του server ως ΗΗ/ΜΜ/ΕΕΕΕ **ώρας Ελλάδας**. Οι σφραγίδες του server είναι UTC (`…+00:00`)·
// στη ζώνη της συσκευής μια κίνηση στις 00:30 της 1ης Ιανουαρίου Ελλάδας θα εμφανιζόταν ως 31 Δεκεμβρίου. Οι απλές
// ημερομηνίες (ΕΕΕΕ-ΜΜ-ΗΗ) διαβάζονται ως μεσάνυχτα UTC, δηλαδή 02:00/03:00 στην Ελλάδα — η ίδια ημέρα. Ό,τι δεν
// διαβάζεται ως ημερομηνία επιστρέφεται όπως είναι (ποτέ «Invalid Date»).
function inAthens(value, render) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return render(parsed);
}

export const formatDate = (value) => inAthens(value, (d) => d.toLocaleDateString("el-GR", { timeZone: ATHENS_TZ }));

// Η ώρα (π.χ. «1:30:00 π.μ.») και η ημερομηνία με ώρα μιας σφραγίδας του server, πάλι σε ώρα Ελλάδας: η ροή μιας
// συνεδρίασης ή η εκτέλεση μιας αυτόματης εργασίας δεν αλλάζει ώρα επειδή ο δικηγόρος ανοίγει την εφαρμογή από το εξωτερικό.
export const formatTime = (value, options) =>
  inAthens(value, (d) => d.toLocaleTimeString("el-GR", { ...options, timeZone: ATHENS_TZ }));
export const formatDateTime = (value) => inAthens(value, (d) => d.toLocaleString("el-GR", { timeZone: ATHENS_TZ }));

// Πόσες ημερολογιακές ημέρες (ώρας Ελλάδας) πέρασαν από μια ημερομηνία ΕΕΕΕ-ΜΜ-ΗΗ μέχρι σήμερα· αρνητικό για μελλοντική.
// Και τα δύο άκρα μετριούνται ως ημερομηνίες (μεσάνυχτα UTC), άρα η αλλαγή ώρας (θερινή/χειμερινή) δεν επηρεάζει το
// αποτέλεσμα. Άκυρη ή κενή ημερομηνία: 0.
export function daysSince(isoDay) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDay || "");
  if (!match) return 0;
  const [y, m, d] = todayAthens().split("-").map(Number);
  const DAY = 24 * 60 * 60 * 1000;
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))) / DAY);
}

// Τι απομένει να εισπραχθεί από ένα εκδοθέν τιμολόγιο: το `outstanding` που υπολογίζει ο server (πληρωτέο μείον
// ό,τι εξοφλήθηκε από πελατολογικό). Server παλαιότερης έκδοσης δεν το στέλνει — τότε το σύνολο, όπως πριν.
export function owedAmount(invoice) {
  return invoice?.outstanding != null ? invoice.outstanding : (invoice?.total || 0);
}

// «Ανείσπρακτα»: άθροισμα ανά πελάτη των εκδοθέντων (το πιστωτικό συμψηφίζεται με τα υπόλοιπα του ΙΔΙΟΥ πελάτη),
// με κάτω όριο το μηδέν για κάθε πελάτη — ο ίδιος ορισμός με τον server (invoice_services.open_total).
export function unpaidTotal(invoices) {
  const perClient = new Map();
  for (const inv of invoices || []) {
    if (inv.status !== "issued") continue;
    const key = inv.client_id || "";
    perClient.set(key, (perClient.get(key) || 0) + owedAmount(inv));
  }
  let total = 0;
  for (const v of perClient.values()) total += Math.max(Math.round(v * 100) / 100, 0);
  return Math.round(total * 100) / 100;
}
