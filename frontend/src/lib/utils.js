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
