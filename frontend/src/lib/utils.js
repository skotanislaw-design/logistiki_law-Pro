import { clsx } from "clsx";
import { twMerge } from "tailwind-merge"

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

// Ο αριθμός του παραστατικού δίνεται στην έκδοση· το πρόχειρο δεν έχει ακόμη.
export function invoiceLabel(invoice) {
  return invoice?.number || "Πρόχειρο";
}

// Η προεπιλεγμένη ημερομηνία παραστατικού είναι η σημερινή **στην Ελλάδα**. Το
// `toISOString()` δίνει ημερομηνία UTC, που μετά τις 00:00 τοπική ώρα είναι ακόμη η χθεσινή.
export function todayAthens() {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Athens" });
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
